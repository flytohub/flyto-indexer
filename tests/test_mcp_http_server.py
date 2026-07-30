"""Tests for the optional loopback Streamable HTTP bridge."""

import http.client
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_http_server import (
    BridgeRequestCancelled,
    StdioMCPBridge,
    _is_loopback_authority,
    _is_loopback_origin,
    _is_replay_safe,
    create_server,
)


class _FakeBridge:
    def __init__(self):
        self.requests = []
        self.alive = False

    def start(self):
        self.alive = True

    def stop(self):
        self.alive = False

    def request(self, payload):
        self.requests.append(payload)
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": {"ok": True},
        }

    def health(self):
        return {
            "status": "ok",
            "transport": "streamable-http",
            "stdio_child_alive": self.alive,
            "restart_count": 0,
        }


class _TimeoutBridge(_FakeBridge):
    def request(self, payload):
        raise TimeoutError("deadline exceeded")


def test_server_rejects_non_loopback_bind():
    with pytest.raises(ValueError, match="loopback"):
        create_server("0.0.0.0", 0)


def test_authority_and_origin_checks_block_dns_rebinding():
    assert _is_loopback_authority("127.0.0.1:8765")
    assert _is_loopback_authority("[::1]:8765")
    assert _is_loopback_origin("http://localhost:8765")
    assert not _is_loopback_authority("attacker.example:8765")
    assert not _is_loopback_origin("https://attacker.example")
    assert not _is_loopback_origin("null")


def test_only_read_only_tool_calls_are_replay_safe():
    assert _is_replay_safe({"method": "initialize"})
    assert _is_replay_safe({
        "method": "tools/call",
        "params": {"name": "structure"},
    })
    assert not _is_replay_safe({
        "method": "tools/call",
        "params": {"name": "task"},
    })


def test_http_mcp_and_health_endpoints():
    bridge = _FakeBridge()
    server = create_server("127.0.0.1", 0, bridge=bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        connection = http.client.HTTPConnection(host, port, timeout=3)
        payload = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/list",
            "params": {},
        }
        connection.request(
            "POST",
            "/mcp",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200
        assert body["id"] == 7
        assert body["result"]["ok"] is True
        assert bridge.requests == [payload]

        connection.request("GET", "/health")
        health_response = connection.getresponse()
        health = json.loads(health_response.read())
        assert health_response.status == 200
        assert health["transport"] == "streamable-http"
        assert health["stdio_child_alive"] is True

        connection.request(
            "GET",
            "/health",
            headers={"Origin": "https://attacker.example"},
        )
        rejected = connection.getresponse()
        rejected_body = json.loads(rejected.read())
        assert rejected.status == 403
        assert "Origin" in rejected_body["error"]
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_http_timeout_maps_to_gateway_timeout():
    server = create_server("127.0.0.1", 0, bridge=_TimeoutBridge())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request(
            "POST",
            "/mcp",
            body=json.dumps({
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/list",
            }),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 504
        assert body["error"]["code"] == -32001
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_stdio_bridge_restarts_and_replays_safe_request(tmp_path):
    marker = tmp_path / "crashed-once"
    child = (
        "import json,pathlib,sys\n"
        "marker=pathlib.Path(sys.argv[1])\n"
        "for line in sys.stdin:\n"
        " payload=json.loads(line)\n"
        " if not marker.exists():\n"
        "  marker.write_text('1')\n"
        "  raise SystemExit(0)\n"
        " print(json.dumps({'jsonrpc':'2.0','id':payload.get('id'),"
        "'result':{'ok':True}}), flush=True)\n"
    )
    bridge = StdioMCPBridge(
        timeout=3,
        command=[sys.executable, "-u", "-c", child, str(marker)],
    )

    try:
        response = bridge.request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert response["result"]["ok"] is True
        assert bridge.restart_count >= 1
        assert bridge.alive is True
        health = bridge.health()
        assert health["request_count"] == 1
        assert health["failure_count"] == 0
        assert health["last_restart_reason"].startswith("protocol_error:")
        assert health["latency_ms"]["samples"] == 1
    finally:
        bridge.stop()


def test_stdio_bridge_restarts_on_corrupt_live_child(tmp_path):
    marker = tmp_path / "corrupted-once"
    child = (
        "import json,pathlib,sys\n"
        "marker=pathlib.Path(sys.argv[1])\n"
        "for line in sys.stdin:\n"
        " payload=json.loads(line)\n"
        " if not marker.exists():\n"
        "  marker.write_text('1')\n"
        "  print('not-json', flush=True)\n"
        "  continue\n"
        " print(json.dumps({'jsonrpc':'2.0','id':payload.get('id'),"
        "'result':{'ok':True}}), flush=True)\n"
    )
    bridge = StdioMCPBridge(
        timeout=3,
        command=[sys.executable, "-u", "-c", child, str(marker)],
    )

    try:
        response = bridge.request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {},
        })
        assert response["result"]["ok"] is True
        assert bridge.restart_count == 1
        assert bridge.health()["last_restart_reason"] == (
            "protocol_error:JSONDecodeError"
        )
    finally:
        bridge.stop()


def test_stdio_bridge_timeout_is_deadline_and_self_heals():
    child = (
        "import json,sys,time\n"
        "for line in sys.stdin:\n"
        " payload=json.loads(line)\n"
        " time.sleep(5)\n"
    )
    bridge = StdioMCPBridge(
        timeout=0.1,
        command=[sys.executable, "-u", "-c", child],
    )

    started = time.perf_counter()
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            bridge.request({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "initialize",
                "params": {},
            })
        assert time.perf_counter() - started < 2
        health = bridge.health()
        assert health["stdio_child_alive"] is True
        assert health["restart_count"] == 1
        assert health["failure_count"] == 1
        assert health["last_restart_reason"] == "timeout:3"
    finally:
        bridge.stop()


def test_stdio_bridge_serializes_concurrency_and_tracks_p95():
    child = (
        "import json,sys,time\n"
        "for line in sys.stdin:\n"
        " payload=json.loads(line)\n"
        " time.sleep(0.03)\n"
        " print(json.dumps({'jsonrpc':'2.0','id':payload.get('id'),"
        "'result':{'ok':True}}), flush=True)\n"
    )
    bridge = StdioMCPBridge(
        timeout=2,
        p95_budget_ms=1000,
        command=[sys.executable, "-u", "-c", child],
    )
    responses = []

    def send(request_id):
        responses.append(bridge.request({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "ping",
        }))

    threads = [threading.Thread(target=send, args=(index,)) for index in range(4)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        assert len(responses) == 4
        health = bridge.health()
        assert health["request_count"] == 4
        assert health["max_in_flight"] >= 2
        assert health["latency_ms"]["p95"] > 0
        assert health["latency_ms"]["within_budget"] is True
    finally:
        bridge.stop()


def test_cancellation_interrupts_active_child_and_restarts():
    child = (
        "import json,sys,time\n"
        "for line in sys.stdin:\n"
        " payload=json.loads(line)\n"
        " if payload.get('id') == 41:\n"
        "  time.sleep(30)\n"
    )
    bridge = StdioMCPBridge(
        timeout=5,
        command=[sys.executable, "-u", "-c", child],
    )
    failures = []

    def send_slow_request():
        try:
            bridge.request({
                "jsonrpc": "2.0",
                "id": 41,
                "method": "tools/call",
                "params": {"name": "structure", "arguments": {}},
            })
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=send_slow_request)
    try:
        thread.start()
        deadline = time.time() + 2
        while bridge.health()["active_request_id"] != 41 and time.time() < deadline:
            time.sleep(0.01)
        bridge.request({
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 41, "reason": "client disconnected"},
        })
        thread.join(timeout=2)
        assert failures
        assert isinstance(failures[0], BridgeRequestCancelled)
        assert bridge.health()["last_restart_reason"] == "cancelled_request:41"
        assert bridge.alive is True
    finally:
        bridge.stop()
