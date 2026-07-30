"""Loopback-only MCP Streamable HTTP transport backed by the stdio server."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:
    from .tool_registry import SMART_TOOLS
    from .version import __version__
except ImportError:  # Direct source execution.
    from tool_registry import SMART_TOOLS
    from version import __version__

logger = logging.getLogger("flyto-indexer.mcp-http")

_MAX_BODY_BYTES = 2 * 1024 * 1024
_READ_ONLY_TOOLS = {
    tool["name"]
    for tool in SMART_TOOLS
    if tool.get("annotations", {}).get("readOnlyHint") is True
}
_SAFE_METHODS = {
    "initialize", "ping", "tools/list", "resources/list", "resources/read",
    "prompts/list", "prompts/get",
}


def _is_loopback_host(host: str) -> bool:
    return host.casefold() in {"localhost", "127.0.0.1", "::1"}


def _is_replay_safe(payload: dict) -> bool:
    method = payload.get("method", "")
    if method in _SAFE_METHODS:
        return True
    if method != "tools/call":
        return False
    params = payload.get("params", {})
    return isinstance(params, dict) and params.get("name") in _READ_ONLY_TOOLS


class StdioMCPBridge:
    """Serialize HTTP requests through one persistent stdio MCP child."""

    def __init__(
        self,
        *,
        timeout: float = 120.0,
        command: list[str] | None = None,
    ):
        package = __package__ or ""
        module = f"{package}.mcp_server" if package else "mcp_server"
        self.command = command or [sys.executable, "-m", module]
        self.timeout = timeout
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self.restart_count = 0

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.alive:
            return
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _restart(self) -> None:
        self.stop()
        self.restart_count += 1
        self.start()

    def _request_once(self, payload: dict) -> dict | None:
        self.start()
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("MCP stdio child is unavailable")

        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()
        if "id" not in payload:
            return None

        line_result: list[str] = []
        read_done = threading.Event()

        def _read_response_line() -> None:
            line_result.append(process.stdout.readline())
            read_done.set()

        reader = threading.Thread(target=_read_response_line, daemon=True)
        reader.start()
        if not read_done.wait(self.timeout):
            self._restart()
            raise TimeoutError(f"MCP stdio child timed out after {self.timeout:g}s")
        reader.join(timeout=0)
        line = line_result[0] if line_result else ""
        if not line:
            self._restart()
            raise RuntimeError("MCP stdio child closed its output")
        response = json.loads(line)
        if not isinstance(response, dict):
            raise RuntimeError("MCP stdio child returned a non-object response")
        return response

    def request(self, payload: dict) -> dict | None:
        with self._lock:
            try:
                return self._request_once(payload)
            except (BrokenPipeError, OSError, RuntimeError, json.JSONDecodeError):
                if not self.alive:
                    self._restart()
                if _is_replay_safe(payload):
                    return self._request_once(payload)
                raise

    def health(self) -> dict:
        return {
            "status": "ok" if self.alive else "starting",
            "runtime_version": __version__,
            "transport": "streamable-http",
            "stdio_child_alive": self.alive,
            "restart_count": self.restart_count,
        }


class MCPHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, bridge: StdioMCPBridge):
        super().__init__(address, handler)
        self.bridge = bridge

    def server_close(self) -> None:
        self.bridge.stop()
        super().server_close()


class MCPRequestHandler(BaseHTTPRequestHandler):
    server_version = f"flyto-indexer/{__version__}"

    @property
    def bridge(self) -> StdioMCPBridge:
        return self.server.bridge  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.bridge.start()
            self._send_json(200, self.bridge.health())
            return
        if self.path == "/mcp":
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.end_headers()
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self._send_json(404, {"error": "not found"})
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            self._send_json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        if content_length <= 0 or content_length > _MAX_BODY_BYTES:
            self._send_json(413, {"error": "request body is empty or too large"})
            return

        request_id: Any = None
        try:
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("JSON-RPC payload must be an object")
            request_id = payload.get("id")
            response = self.bridge.request(payload)
            if response is None:
                self.send_response(202)
                self.end_headers()
                return
            self._send_json(200, response)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32700, "message": str(exc)},
            })
        except Exception as exc:
            logger.warning("MCP HTTP bridge request failed: %s", exc)
            self._send_json(502, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": "MCP child unavailable"},
            })

    def log_message(self, format: str, *args) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    bridge: StdioMCPBridge | None = None,
) -> MCPHTTPServer:
    if not _is_loopback_host(host):
        raise ValueError("MCP HTTP transport only accepts loopback hosts")
    return MCPHTTPServer((host, port), MCPRequestHandler, bridge or StdioMCPBridge())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run flyto-indexer over loopback Streamable HTTP",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("FLYTO_INDEXER_LOG_LEVEL", "INFO"),
        format="%(levelname)s %(name)s: %(message)s",
    )
    bridge = StdioMCPBridge(timeout=max(1.0, args.timeout))
    server = create_server(args.host, args.port, bridge=bridge)
    logger.info("MCP Streamable HTTP listening on http://%s:%s/mcp", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
