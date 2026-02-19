"""
MCP Integration Tests — Subprocess E2E

Spawns the real MCP server as a subprocess and communicates
via JSON-RPC over stdin/stdout. No mocks.
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
EXPECTED_TOOL_COUNT = 30
EXPECTED_TOOL_NAMES = sorted([
    "check_and_reindex",
    "check_index_status",
    "code_health_score",
    "cross_project_impact",
    "dependency_graph",
    "edit_impact_preview",
    "find_complex_functions",
    "find_dead_code",
    "find_duplicates",
    "find_references",
    "find_stale_files",
    "find_test_file",
    "find_todos",
    "fulltext_search",
    "get_description",
    "get_file_context",
    "get_file_info",
    "get_file_symbols",
    "get_symbol_content",
    "impact_analysis",
    "impact_from_diff",
    "list_apis",
    "list_categories",
    "list_projects",
    "search_code",
    "security_scan",
    "session_get",
    "session_track",
    "suggest_refactoring",
    "update_description",
])

# Test project name used in synthetic index
TEST_PROJECT = "test-integration"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_jsonrpc(proc, method: str, params: dict = None, req_id: int = 1):
    """Write a JSON-RPC request line to the subprocess stdin."""
    msg = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params is not None:
        msg["params"] = params
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()


def _read_response(proc, timeout: float = 10.0) -> dict:
    """Read one JSON-RPC response line from stdout with a timeout.

    Uses a background thread so we don't block forever if the server
    doesn't respond.
    """
    result = {}
    exc = []

    def _reader():
        try:
            raw = proc.stdout.readline()
            if raw:
                result["data"] = json.loads(raw.decode().strip())
        except Exception as e:
            exc.append(e)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        raise TimeoutError(f"No response within {timeout}s")
    if exc:
        raise exc[0]
    if "data" not in result:
        # Possibly process died — capture stderr for debugging
        stderr_out = ""
        if proc.stderr:
            try:
                stderr_out = proc.stderr.read(4096).decode(errors="replace")
            except Exception:
                pass
        raise RuntimeError(f"Empty response from MCP server. stderr: {stderr_out}")
    return result["data"]


# ---------------------------------------------------------------------------
# Synthetic index builder
# ---------------------------------------------------------------------------

def _build_synthetic_index(index_dir: Path):
    """Create a minimal but realistic index on disk.

    The index contains:
    - 1 project ('test-integration')
    - 3 files with cross-references (auth.py, routes.py, app.py)
    - symbols: login_user (function), UserRouter (class), create_app (function)
    - dependencies: routes.py calls auth.login_user, app.py calls routes.UserRouter
    - reverse_index matching the dependencies
    """
    index_dir.mkdir(parents=True, exist_ok=True)

    symbols = {
        f"{TEST_PROJECT}:src/auth.py:function:login_user": {
            "name": "login_user",
            "type": "function",
            "path": "src/auth.py",
            "start_line": 10,
            "end_line": 30,
            "summary": "Authenticate user with username and password",
            "ref_count": 1,
            "exports": True,
        },
        f"{TEST_PROJECT}:src/routes.py:class:UserRouter": {
            "name": "UserRouter",
            "type": "class",
            "path": "src/routes.py",
            "start_line": 5,
            "end_line": 50,
            "summary": "HTTP router for user endpoints",
            "ref_count": 1,
            "exports": True,
        },
        f"{TEST_PROJECT}:src/app.py:function:create_app": {
            "name": "create_app",
            "type": "function",
            "path": "src/app.py",
            "start_line": 1,
            "end_line": 20,
            "summary": "Create and configure the application instance",
            "ref_count": 0,
            "exports": True,
        },
    }

    # routes.py calls auth.login_user
    dependencies = {
        "dep-001": {
            "source": f"{TEST_PROJECT}:src/routes.py:class:UserRouter",
            "target": "login_user",
            "type": "calls",
            "line": 15,
            "metadata": {
                "resolved_target": f"{TEST_PROJECT}:src/auth.py:function:login_user",
            },
        },
        "dep-002": {
            "source": f"{TEST_PROJECT}:src/app.py:function:create_app",
            "target": "UserRouter",
            "type": "calls",
            "line": 8,
            "metadata": {
                "resolved_target": f"{TEST_PROJECT}:src/routes.py:class:UserRouter",
            },
        },
    }

    reverse_index = {
        f"{TEST_PROJECT}:src/auth.py:function:login_user": [
            f"{TEST_PROJECT}:src/routes.py:class:UserRouter",
        ],
        f"{TEST_PROJECT}:src/routes.py:class:UserRouter": [
            f"{TEST_PROJECT}:src/app.py:function:create_app",
        ],
    }

    index_data = {
        "project": TEST_PROJECT,
        "root_path": "/tmp/fake-project",
        "indexed_at": "2026-01-01T00:00:00",
        "projects": [TEST_PROJECT],
        "project_roots": {},
        "files": {},
        "symbols": symbols,
        "dependencies": dependencies,
        "reverse_index": reverse_index,
        "entry_points": [],
        "routes": [],
        "api_endpoints": [],
        "has_content_file": True,
    }

    # Write index.json
    (index_dir / "index.json").write_text(
        json.dumps(index_data, indent=2, ensure_ascii=False)
    )

    # Write PROJECT_MAP.json (minimal)
    project_map = {
        "files": {},
        "categories": {},
        "api_map": {},
    }
    (index_dir / "PROJECT_MAP.json").write_text(
        json.dumps(project_map, indent=2, ensure_ascii=False)
    )

    # Write content.jsonl
    content_records = [
        {
            "id": f"{TEST_PROJECT}:src/auth.py:function:login_user",
            "content": "def login_user(username: str, password: str) -> bool:\n    # TODO: add rate limiting\n    return check_password(username, password)\n",
        },
        {
            "id": f"{TEST_PROJECT}:src/routes.py:class:UserRouter",
            "content": "class UserRouter:\n    def post_login(self, request):\n        return login_user(request.username, request.password)\n",
        },
        {
            "id": f"{TEST_PROJECT}:src/app.py:function:create_app",
            "content": "def create_app():\n    router = UserRouter()\n    app = App(router)\n    return app\n",
        },
    ]
    with open(index_dir / "content.jsonl", "w", encoding="utf-8") as f:
        for record in content_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mcp_server(tmp_path_factory):
    """Start the MCP server subprocess once for the whole module.

    Creates a synthetic index, starts the server, yields the process,
    then cleans up.
    """
    index_dir = tmp_path_factory.mktemp("flyto_index")
    _build_synthetic_index(index_dir)

    env = os.environ.copy()
    env["FLYTO_INDEX_DIR"] = str(index_dir)
    # Ensure Python can find the src package
    env["PYTHONPATH"] = str(REPO_ROOT)

    proc = subprocess.Popen(
        [sys.executable, "-m", "src.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
        env=env,
    )

    # Send initialize + wait for response to confirm server is alive
    _send_jsonrpc(proc, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0.1"},
    }, req_id=0)

    try:
        resp = _read_response(proc, timeout=15.0)
        assert resp.get("id") == 0, f"Unexpected init response: {resp}"
    except Exception:
        proc.terminate()
        proc.wait(timeout=5)
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise RuntimeError(f"MCP server failed to initialize. stderr:\n{stderr}")

    # Send notifications/initialized (no response expected)
    _send_jsonrpc(proc, "notifications/initialized", {}, req_id=None)

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# Track request IDs to avoid collisions
_next_id = 1


def _call(proc, method: str, params: dict = None, timeout: float = 10.0) -> dict:
    """Send a request and return the parsed response."""
    global _next_id
    rid = _next_id
    _next_id += 1
    _send_jsonrpc(proc, method, params, req_id=rid)
    resp = _read_response(proc, timeout=timeout)
    assert resp.get("id") == rid, f"ID mismatch: expected {rid}, got {resp.get('id')}"
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMCPInitialize:
    """Test the initialize handshake."""

    def test_initialize(self, mcp_server):
        """initialize returns protocolVersion + serverInfo.name"""
        # The fixture already sent initialize (id=0). Send another to verify.
        resp = _call(mcp_server, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1"},
        })
        result = resp.get("result", {})
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "flyto-indexer"
        assert "version" in result["serverInfo"]


class TestToolsList:
    """Test tools/list responses."""

    def test_tools_list_count(self, mcp_server):
        """tools/list returns exactly 23 tools."""
        resp = _call(mcp_server, "tools/list")
        tools = resp["result"]["tools"]
        assert len(tools) == EXPECTED_TOOL_COUNT, (
            f"Expected {EXPECTED_TOOL_COUNT} tools, got {len(tools)}: "
            f"{sorted(t['name'] for t in tools)}"
        )

    def test_tools_list_names(self, mcp_server):
        """All 23 tool names match the expected canonical list."""
        resp = _call(mcp_server, "tools/list")
        actual = sorted(t["name"] for t in resp["result"]["tools"])
        assert actual == EXPECTED_TOOL_NAMES


class TestToolCalls:
    """Test tools/call with real tool dispatch."""

    def test_search_code(self, mcp_server):
        """search_code finds the login_user symbol in the fixture."""
        resp = _call(mcp_server, "tools/call", {
            "name": "search_code",
            "arguments": {"query": "login_user"},
        })
        assert "error" not in resp, f"Got error: {resp.get('error')}"
        content = resp["result"]["content"]
        assert len(content) == 1 and content[0]["type"] == "text"
        data = json.loads(content[0]["text"])
        assert data["total"] >= 1
        names = [r["name"] for r in data.get("results", [])]
        assert "login_user" in names

    def test_impact_analysis(self, mcp_server):
        """impact_analysis for login_user returns affected list."""
        resp = _call(mcp_server, "tools/call", {
            "name": "impact_analysis",
            "arguments": {
                "symbol_id": f"{TEST_PROJECT}:src/auth.py:function:login_user",
            },
        })
        assert "error" not in resp, f"Got error: {resp.get('error')}"
        content = resp["result"]["content"]
        data = json.loads(content[0]["text"])
        assert data["affected_count"] >= 1
        affected_names = [a["name"] for a in data.get("affected", [])]
        assert "UserRouter" in affected_names

    def test_list_projects(self, mcp_server):
        """list_projects returns our test project."""
        resp = _call(mcp_server, "tools/call", {
            "name": "list_projects",
            "arguments": {},
        })
        assert "error" not in resp, f"Got error: {resp.get('error')}"
        content = resp["result"]["content"]
        data = json.loads(content[0]["text"])
        project_names = [p["project"] for p in data.get("projects", [])]
        assert TEST_PROJECT in project_names


class TestErrorHandling:
    """Test error responses for invalid requests."""

    def test_unknown_tool(self, mcp_server):
        """Calling a non-existent tool returns error code -32601."""
        resp = _call(mcp_server, "tools/call", {
            "name": "nonexistent_tool_xyz",
            "arguments": {},
        })
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_unknown_method(self, mcp_server):
        """Calling an unknown JSON-RPC method returns error code -32601."""
        resp = _call(mcp_server, "evil/method", {"foo": "bar"})
        assert "error" in resp
        assert resp["error"]["code"] == -32601
