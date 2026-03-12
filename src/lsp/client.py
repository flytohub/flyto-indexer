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
    """

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
                }
            },
        })
        if init_result is None:
            logger.debug("LSP initialize failed for %s", self._command)
            self._kill()
            return False

        # Send initialized notification
        self._send_notification("initialized", {})
        return True

    def shutdown(self):
        """Send shutdown + exit, then clean up."""
        if not self.alive:
            self._kill()
            return
        try:
            self._send_request("shutdown", None)
            self._send_notification("exit", None)
        except Exception:
            pass
        self._kill()

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
        """Send a JSON-RPC request and wait for the response."""
        if not self.alive:
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

        if not self._write_message(msg):
            return None

        if not event.wait(timeout=self._timeout):
            logger.debug("LSP request timed out: %s (id=%d)", method, req_id)
            return None

        result = self._responses.pop(req_id, None)
        self._events.pop(req_id, None)
        return result

    def _send_notification(self, method: str, params):
        """Send a JSON-RPC notification (no response expected)."""
        if not self.alive:
            return
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write_message(msg)

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
                except json.JSONDecodeError:
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
