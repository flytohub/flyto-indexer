"""Loopback-only MCP Streamable HTTP transport backed by the stdio server."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:
    from .mcp_server import MODERN_PROTOCOL_VERSION
    from .tool_registry import SMART_TOOLS
    from .version import __version__
except ImportError:  # Direct source execution.
    from mcp_server import MODERN_PROTOCOL_VERSION
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
_DEFAULT_P95_BUDGET_MS = 8_000.0
_PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
_NAMED_METHOD_FIELDS = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
}


class BridgeRequestCancelled(RuntimeError):
    """Raised when an HTTP cancellation restarts the active stdio child."""


def _is_loopback_host(host: str) -> bool:
    return host.casefold() in {"localhost", "127.0.0.1", "::1"}


def _authority_host(authority: str) -> str:
    """Extract a host from an HTTP authority without accepting userinfo."""
    value = authority.strip()
    if not value or "@" in value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else ""
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def _is_loopback_authority(authority: str) -> bool:
    return _is_loopback_host(_authority_host(authority))


def _is_loopback_origin(origin: str) -> bool:
    parts = origin.strip().split("://", 1)
    if len(parts) != 2 or parts[0].casefold() not in {"http", "https"}:
        return False
    authority = parts[1].split("/", 1)[0]
    return _is_loopback_authority(authority)


def _is_replay_safe(payload: dict) -> bool:
    method = payload.get("method", "")
    if method in _SAFE_METHODS:
        return True
    if method != "tools/call":
        return False
    params = payload.get("params", {})
    return isinstance(params, dict) and params.get("name") in _READ_ONLY_TOOLS


def _decode_mcp_header(value: str) -> str:
    """Decode the MCP Base64 sentinel form used by name-bearing headers."""
    prefix = "=?base64?"
    suffix = "?="
    if not value.startswith(prefix):
        return value
    if not value.endswith(suffix):
        raise ValueError("malformed Base64 sentinel")
    encoded = value[len(prefix):-len(suffix)]
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except ValueError as exc:
        raise ValueError("invalid Base64 header value") from exc


def _header_mismatch(payload: dict, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": payload.get("id"),
        "error": {
            "code": -32020,
            "message": f"Header mismatch: {message}",
        },
    }


def _validate_modern_http_headers(headers: Any, payload: dict) -> dict | None:
    """Validate the mirrored headers required by MCP 2026-07-28."""
    params = payload.get("params")
    metadata = params.get("_meta") if isinstance(params, dict) else None
    body_version = (
        metadata.get(_PROTOCOL_VERSION_META_KEY)
        if isinstance(metadata, dict)
        else None
    )
    header_version = headers.get("MCP-Protocol-Version")
    if body_version is None and header_version != MODERN_PROTOCOL_VERSION:
        return None

    if not isinstance(body_version, str):
        return _header_mismatch(
            payload,
            "request metadata is missing MCP protocol version",
        )
    if header_version is None:
        return _header_mismatch(
            payload,
            "required MCP-Protocol-Version header is missing",
        )
    if header_version != body_version:
        return _header_mismatch(
            payload,
            "MCP-Protocol-Version header does not match request metadata",
        )

    method = payload.get("method")
    method_header = headers.get("Mcp-Method")
    if not isinstance(method, str) or not method_header:
        return _header_mismatch(
            payload,
            "required Mcp-Method header is missing",
        )
    if method_header != method:
        return _header_mismatch(
            payload,
            "Mcp-Method header does not match the JSON-RPC method",
        )

    name_field = _NAMED_METHOD_FIELDS.get(method)
    if name_field is None:
        return None
    expected_name = params.get(name_field) if isinstance(params, dict) else None
    name_header = headers.get("Mcp-Name")
    if not isinstance(expected_name, str) or not name_header:
        return _header_mismatch(
            payload,
            "required Mcp-Name header is missing",
        )
    try:
        decoded_name = _decode_mcp_header(name_header)
    except ValueError as exc:
        return _header_mismatch(payload, str(exc))
    if decoded_name != expected_name:
        return _header_mismatch(
            payload,
            "Mcp-Name header does not match the request body",
        )
    return None


class StdioMCPBridge:
    """Serialize HTTP requests through one persistent stdio MCP child."""

    def __init__(
        self,
        *,
        timeout: float = 120.0,
        command: list[str] | None = None,
        p95_budget_ms: float = _DEFAULT_P95_BUDGET_MS,
    ):
        package = __package__ or ""
        module = f"{package}.mcp_server" if package else "mcp_server"
        self.command = command or [sys.executable, "-m", module]
        self.timeout = timeout
        self._lock = threading.Lock()
        self._process_lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._metrics_lock = threading.Lock()
        self._latencies_ms: deque[float] = deque(maxlen=256)
        self._active_request_id: Any = None
        self._cancelled_request_ids: set[Any] = set()
        self.restart_count = 0
        self.request_count = 0
        self.failure_count = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.last_failure: str | None = None
        self.last_restart_reason: str | None = None
        self.p95_budget_ms = max(1.0, float(p95_budget_ms))

    @property
    def alive(self) -> bool:
        with self._process_lock:
            return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        with self._process_lock:
            if self._process is not None and self._process.poll() is None:
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
        with self._process_lock:
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

    def _restart(self, reason: str) -> None:
        with self._process_lock:
            self.stop()
            self.restart_count += 1
            self.last_restart_reason = reason
            self.start()

    def _record_start(self) -> float:
        with self._metrics_lock:
            self.request_count += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        return time.perf_counter()

    def _record_finish(self, started: float, failure: Exception | None) -> None:
        elapsed_ms = (time.perf_counter() - started) * 1000
        with self._metrics_lock:
            self.in_flight = max(0, self.in_flight - 1)
            self._latencies_ms.append(round(elapsed_ms, 3))
            if failure is not None:
                self.failure_count += 1
                self.last_failure = (
                    f"{type(failure).__name__}: {failure}"
                )[:500]

    def _mark_active(self, request_id: Any) -> None:
        with self._metrics_lock:
            self._active_request_id = request_id

    def _clear_active(self, request_id: Any) -> None:
        with self._metrics_lock:
            if self._active_request_id == request_id:
                self._active_request_id = None
            self._cancelled_request_ids.discard(request_id)

    def _was_cancelled(self, request_id: Any) -> bool:
        with self._metrics_lock:
            return request_id in self._cancelled_request_ids

    def _cancel_active(self, payload: dict) -> bool:
        if payload.get("method") not in {
            "notifications/cancelled",
            "$/cancelRequest",
        }:
            return False
        params = payload.get("params") or {}
        request_id = params.get("requestId") if isinstance(params, dict) else None
        with self._metrics_lock:
            if request_id is None or request_id != self._active_request_id:
                return False
            self._cancelled_request_ids.add(request_id)
        self._restart(f"cancelled_request:{request_id}")
        return True

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
            raise TimeoutError(f"MCP stdio child timed out after {self.timeout:g}s")
        reader.join(timeout=0)
        line = line_result[0] if line_result else ""
        if not line:
            raise RuntimeError("MCP stdio child closed its output")
        response = json.loads(line)
        if not isinstance(response, dict):
            raise RuntimeError("MCP stdio child returned a non-object response")
        return response

    def request(self, payload: dict) -> dict | None:
        started = self._record_start()
        failure: Exception | None = None
        request_id = payload.get("id")
        try:
            if "id" not in payload and self._cancel_active(payload):
                return None
            with self._lock:
                self._mark_active(request_id)
                try:
                    return self._request_once(payload)
                except TimeoutError:
                    self._restart(f"timeout:{request_id}")
                    raise
                except (
                    BrokenPipeError,
                    OSError,
                    RuntimeError,
                    json.JSONDecodeError,
                ) as exc:
                    if self._was_cancelled(request_id):
                        raise BridgeRequestCancelled(
                            f"MCP request {request_id!r} was cancelled"
                        ) from exc
                    self._restart(f"protocol_error:{type(exc).__name__}")
                    if _is_replay_safe(payload):
                        return self._request_once(payload)
                    raise
                finally:
                    self._clear_active(request_id)
        except Exception as exc:
            failure = exc
            raise
        finally:
            self._record_finish(started, failure)

    @staticmethod
    def _percentile(samples: list[float], percentile: float) -> float:
        if not samples:
            return 0.0
        ordered = sorted(samples)
        index = max(0, min(len(ordered) - 1, int(
            (len(ordered) * percentile + 0.999999) - 1
        )))
        return round(ordered[index], 3)

    def health(self) -> dict:
        with self._metrics_lock:
            samples = list(self._latencies_ms)
            request_count = self.request_count
            failure_count = self.failure_count
            in_flight = self.in_flight
            max_in_flight = self.max_in_flight
            last_failure = self.last_failure
            active_request_id = self._active_request_id
        p50_ms = self._percentile(samples, 0.50)
        p95_ms = self._percentile(samples, 0.95)
        alive = self.alive
        return {
            "status": "ok" if alive else "starting",
            "activity": "busy" if in_flight else "idle",
            "runtime_version": __version__,
            "transport": "streamable-http",
            "stdio_child_alive": alive,
            "restart_count": self.restart_count,
            "last_restart_reason": self.last_restart_reason,
            "timeout_seconds": self.timeout,
            "request_count": request_count,
            "success_count": request_count - failure_count,
            "failure_count": failure_count,
            "in_flight": in_flight,
            "max_in_flight": max_in_flight,
            "active_request_id": active_request_id,
            "latency_ms": {
                "samples": len(samples),
                "p50": p50_ms,
                "p95": p95_ms,
                "budget_p95": self.p95_budget_ms,
                "within_budget": not samples or p95_ms <= self.p95_budget_ms,
            },
            "last_failure": last_failure,
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

    def _reject_nonlocal_headers(self) -> bool:
        """Block DNS-rebinding requests while allowing non-browser MCP clients."""
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if not _is_loopback_authority(host):
            self._send_json(403, {"error": "Host must resolve to loopback"})
            return True
        if origin and not _is_loopback_origin(origin):
            self._send_json(403, {"error": "Origin must resolve to loopback"})
            return True
        return False

    def do_GET(self) -> None:
        if self._reject_nonlocal_headers():
            return
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
        if self._reject_nonlocal_headers():
            return
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
            header_error = _validate_modern_http_headers(
                self.headers,
                payload,
            )
            if header_error is not None:
                self._send_json(400, header_error)
                return
            response = self.bridge.request(payload)
            if response is None:
                self.send_response(202)
                self.end_headers()
                return
            error = response.get("error")
            error_code = error.get("code") if isinstance(error, dict) else None
            status = 400 if error_code in {-32020, -32021, -32022} else 200
            self._send_json(status, response)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32700, "message": str(exc)},
            })
        except TimeoutError as exc:
            logger.warning("MCP HTTP bridge deadline exceeded: %s", exc)
            self._send_json(504, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32001, "message": "MCP child deadline exceeded"},
            })
        except BridgeRequestCancelled as exc:
            logger.info("MCP HTTP bridge request cancelled: %s", exc)
            self._send_json(409, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32800, "message": "Request cancelled"},
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
    parser.add_argument(
        "--p95-budget-ms",
        type=float,
        default=_DEFAULT_P95_BUDGET_MS,
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("FLYTO_INDEXER_LOG_LEVEL", "INFO"),
        format="%(levelname)s %(name)s: %(message)s",
    )
    bridge = StdioMCPBridge(
        timeout=max(1.0, args.timeout),
        p95_budget_ms=max(1.0, args.p95_budget_ms),
    )
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
