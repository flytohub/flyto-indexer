"""LSP client — manages a single language server subprocess over stdio."""

import json
import logging
import subprocess
import threading
from typing import Dict, List, Optional

from .protocol import (
    Location,
    Position,
    Range,
    encode_message,
    parse_content_length,
    path_to_uri,
)

logger = logging.getLogger("flyto-indexer.lsp.client")


class LSPClient:
    """Manages a single LSP server subprocess.

    Communication uses JSON-RPC 2.0 over stdio with Content-Length framing.
    All public methods return None/empty on error, never raise.

    Includes a process watchdog: if the server crashes mid-request, the client
    will attempt up to MAX_RESTARTS automatic restarts before giving up.
    """

    MAX_RESTARTS = 3

    def __init__(self, command: List[str], root_uri: str, timeout: float = 10.0):
        self._command = command
        self._root_uri = root_uri
        self._timeout = timeout
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._responses: Dict[int, Optional[dict]] = {}
        self._events: Dict[int, threading.Event] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._alive = False
        self._restart_count = 0
        self._permanently_dead = False

    @property
    def alive(self) -> bool:
        """Whether the LSP server process is running."""
        return self._alive and self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """Spawn the LSP server subprocess and send initialize/initialized."""
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as e:
            logger.debug("Failed to start LSP server %s: %s", self._command, e)
            return False

        self._alive = True

        # Start reader thread
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="lsp-reader"
        )
        self._reader_thread.start()

        # Send initialize
        init_result = self._send_request("initialize", {
            "processId": None,
            "rootUri": self._root_uri,
            "capabilities": {
                "textDocument": {
                    "references": {"dynamicRegistration": False},
                    "definition": {"dynamicRegistration": False},
                    "typeDefinition": {"dynamicRegistration": False},
                    "implementation": {"dynamicRegistration": False},
                    "hover": {
                        "dynamicRegistration": False,
                        "contentFormat": ["markdown", "plaintext"],
                    },
                    "callHierarchy": {"dynamicRegistration": False},
                },
                "workspace": {
                    "symbol": {"dynamicRegistration": False},
                },
            },
        })
        if init_result is None:
            logger.debug("LSP initialize failed for %s", self._command)
            self._kill()
            return False

        # Send initialized notification
        self._send_notification("initialized", {})
        return True

    def _check_alive(self) -> bool:
        """Check if the subprocess is still running.

        Returns True if alive. If dead and restarts available, attempts one
        restart and returns the result. If permanently dead, returns False.
        """
        if self._permanently_dead:
            return False
        if self._alive and self._process is not None and self._process.poll() is None:
            return True
        # Process is dead
        self._alive = False
        logger.debug("LSP server %s found dead (poll=%s)", self._command,
                     self._process.poll() if self._process else "no-process")
        return self._restart()

    def _restart(self) -> bool:
        """Attempt to restart the LSP server.

        Returns True if restart succeeded, False otherwise.
        Increments restart counter; marks permanently dead after MAX_RESTARTS.
        """
        if self._restart_count >= self.MAX_RESTARTS:
            logger.debug("LSP server %s exceeded max restarts (%d), marking permanently dead",
                         self._command, self.MAX_RESTARTS)
            self._permanently_dead = True
            self._cleanup_zombie()
            return False

        self._restart_count += 1
        logger.debug("LSP server %s restart attempt %d/%d",
                     self._command, self._restart_count, self.MAX_RESTARTS)

        # Clean up old process
        self._cleanup_zombie()

        # Try starting fresh
        return self.start()

    def _cleanup_zombie(self):
        """Ensure the subprocess is fully killed and reaped."""
        self._alive = False
        if self._process is not None:
            try:
                self._process.kill()
            except (OSError, ProcessLookupError):
                pass
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.debug("LSP process %s did not exit after kill+wait", self._command)
            except Exception:
                pass
            self._process = None

    def shutdown(self):
        """Send shutdown + exit, then clean up."""
        if not self.alive:
            self._cleanup_zombie()
            return
        try:
            self._send_request("shutdown", None)
            self._send_notification("exit", None)
        except Exception:
            pass
        self._cleanup_zombie()

    def _kill(self):
        """Force-kill the subprocess."""
        self._alive = False
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception:
                pass
            self._process = None

    def text_document_references(
        self, uri: str, line: int, col: int
    ) -> List[Location]:
        """textDocument/references — find all references to symbol at position."""
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
            "context": {"includeDeclaration": False},
        }
        result = self._send_request("textDocument/references", params)
        if not result or not isinstance(result, list):
            return []
        return self._parse_locations(result)

    def text_document_definition(
        self, uri: str, line: int, col: int
    ) -> List[Location]:
        """textDocument/definition — find definition of symbol at position."""
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        }
        result = self._send_request("textDocument/definition", params)
        if not result:
            return []
        # definition can return a single Location or a list
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            return []
        return self._parse_locations(result)

    def did_open(self, uri: str, language_id: str, text: str):
        """textDocument/didOpen notification."""
        self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": text,
            }
        })

    def text_document_hover(
        self, uri: str, line: int, col: int
    ) -> Optional[str]:
        """textDocument/hover — return markdown/plaintext content, or None."""
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        }
        result = self._send_request("textDocument/hover", params)
        if not result or not isinstance(result, dict):
            return None
        contents = result.get("contents")
        if contents is None:
            return None
        # LSP hover returns MarkupContent | MarkedString | MarkedString[]
        if isinstance(contents, str):
            return contents
        if isinstance(contents, dict):
            return contents.get("value", "")
        if isinstance(contents, list):
            parts: List[str] = []
            for item in contents:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("value", ""))
            return "\n".join(p for p in parts if p)
        return None

    def text_document_type_definition(
        self, uri: str, line: int, col: int
    ) -> List[Location]:
        """textDocument/typeDefinition — locate the type's definition."""
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        }
        result = self._send_request("textDocument/typeDefinition", params)
        if not result:
            return []
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            return []
        return self._parse_locations(result)

    def text_document_implementation(
        self, uri: str, line: int, col: int
    ) -> List[Location]:
        """textDocument/implementation — find interface implementations."""
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        }
        result = self._send_request("textDocument/implementation", params)
        if not result:
            return []
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            return []
        return self._parse_locations(result)

    def workspace_symbol(self, query: str) -> List[dict]:
        """workspace/symbol — project-wide symbol search.

        Returns raw LSP SymbolInformation dicts (name, kind, location) since
        different servers include different extra fields; callers narrow as needed.
        """
        result = self._send_request("workspace/symbol", {"query": query})
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    def text_document_prepare_call_hierarchy(
        self, uri: str, line: int, col: int
    ) -> List[dict]:
        """textDocument/prepareCallHierarchy — resolve a position to CallHierarchyItem(s)."""
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        }
        result = self._send_request("textDocument/prepareCallHierarchy", params)
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    def call_hierarchy_incoming_calls(self, item: dict) -> List[dict]:
        """callHierarchy/incomingCalls — who calls this symbol."""
        result = self._send_request("callHierarchy/incomingCalls", {"item": item})
        if not isinstance(result, list):
            return []
        return [c for c in result if isinstance(c, dict)]

    def call_hierarchy_outgoing_calls(self, item: dict) -> List[dict]:
        """callHierarchy/outgoingCalls — what this symbol calls."""
        result = self._send_request("callHierarchy/outgoingCalls", {"item": item})
        if not isinstance(result, list):
            return []
        return [c for c in result if isinstance(c, dict)]

    def _parse_locations(self, items: list) -> List[Location]:
        """Parse a list of LSP Location dicts into Location dataclasses."""
        locations = []
        for item in items:
            try:
                uri = item.get("uri", "")
                r = item.get("range", {})
                start = r.get("start", {})
                end = r.get("end", {})
                locations.append(Location(
                    uri=uri,
                    range=Range(
                        start=Position(
                            line=start.get("line", 0),
                            character=start.get("character", 0),
                        ),
                        end=Position(
                            line=end.get("line", 0),
                            character=end.get("character", 0),
                        ),
                    ),
                ))
            except (KeyError, TypeError, AttributeError):
                continue
        return locations

    def _send_request(self, method: str, params) -> Optional[dict]:
        """Send a JSON-RPC request and wait for the response.

        If the server is dead, attempts one auto-restart before giving up.
        Handles BrokenPipeError, JSONDecodeError, and timeout gracefully.
        """
        # Check alive with watchdog (may trigger restart)
        if not self._check_alive():
            return None

        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        event = threading.Event()
        self._events[req_id] = event
        self._responses[req_id] = None

        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params

        try:
            if not self._write_message(msg):
                return None
        except BrokenPipeError:
            logger.debug("LSP BrokenPipeError sending %s (id=%d)", method, req_id)
            self._alive = False
            return None

        if not event.wait(timeout=self._timeout):
            logger.debug("LSP request timed out: %s (id=%d)", method, req_id)
            return None

        result = self._responses.pop(req_id, None)
        self._events.pop(req_id, None)
        return result

    def _send_notification(self, method: str, params):
        """Send a JSON-RPC notification (no response expected)."""
        if not self._check_alive():
            return
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._write_message(msg)
        except BrokenPipeError:
            logger.debug("LSP BrokenPipeError sending notification %s", method)
            self._alive = False

    def _write_message(self, msg: dict) -> bool:
        """Serialize and write a JSON-RPC message to stdin."""
        try:
            body = json.dumps(msg).encode("utf-8")
            data = encode_message(body)
            self._process.stdin.write(data)
            self._process.stdin.flush()
            return True
        except (OSError, BrokenPipeError, AttributeError) as e:
            logger.debug("LSP write error: %s", e)
            self._alive = False
            return False

    def _read_loop(self):
        """Background thread: read JSON-RPC messages from stdout."""
        stdout = self._process.stdout
        try:
            while self._alive and self._process and self._process.poll() is None:
                # Read headers until \r\n\r\n
                header = b""
                while True:
                    byte = stdout.read(1)
                    if not byte:
                        self._alive = False
                        return
                    header += byte
                    if header.endswith(b"\r\n\r\n"):
                        break

                content_length = parse_content_length(header)
                if content_length is None:
                    logger.debug("LSP: missing Content-Length in header")
                    continue

                body = stdout.read(content_length)
                if len(body) < content_length:
                    self._alive = False
                    return

                try:
                    msg = json.loads(body)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.debug("LSP: malformed response body: %s", e)
                    continue

                # Handle response (has 'id' and either 'result' or 'error')
                msg_id = msg.get("id")
                if msg_id is not None and ("result" in msg or "error" in msg):
                    if msg_id in self._events:
                        if "error" in msg:
                            logger.debug(
                                "LSP error for id=%s: %s", msg_id, msg["error"]
                            )
                            self._responses[msg_id] = None
                        else:
                            self._responses[msg_id] = msg.get("result")
                        self._events[msg_id].set()
                # Server notifications/requests are ignored
        except (OSError, ValueError) as e:
            logger.debug("LSP read loop error: %s", e, exc_info=True)
        finally:
            self._alive = False
