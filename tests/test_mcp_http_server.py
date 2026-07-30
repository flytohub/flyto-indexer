"""Tests for the optional loopback Streamable HTTP bridge."""

import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_http_server import (
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
    finally:
        bridge.stop()
