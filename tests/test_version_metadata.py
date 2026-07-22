import json
from pathlib import Path

from src import __version__
from src.mcp_server import handle_request


ROOT = Path(__file__).resolve().parents[1]


def test_static_manifests_match_runtime_version():
    for relative in ("server.json", ".mcp/server.json"):
        data = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        assert data["version"] == __version__
        assert data["packages"][0]["version"] == __version__
        assert data["name"] == "io.github.flytohub/flyto-indexer"


def test_mcp_initialize_reports_runtime_version(monkeypatch):
    responses = []
    monkeypatch.setattr("src.mcp_server.send_response", lambda request_id, result: responses.append((request_id, result)))

    handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )

    assert responses[0][0] == 7
    assert responses[0][1]["serverInfo"]["version"] == __version__
