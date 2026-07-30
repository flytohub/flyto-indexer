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
EXPECTED_TOOL_COUNT = 20
EXPECTED_TOOL_NAMES = sorted([
    "search",
    "impact",
    "audit",
    "task",
    "structure",
    "verify",
    "verify_workspace",
    "project_profile",
    "scan_secrets",
    "scan_licenses",
    "scan_documentation",
    "analyze_pr_risk",
    "detect_frameworks",
    "call_hierarchy",
    "check_layers",
    "add_layer",
    "add_taint_source",
    "add_taint_sink",
    "add_taint_sanitizer",
    "list_taint_rules",
])

# Test project name used in synthetic index
TEST_PROJECT = "test-integration"
MODERN_PROTOCOL_VERSION = "2026-07-28"


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
    env["FLYTO_INDEXER_GRILL_DIR"] = str(index_dir / "grill-state")
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


def _modern_params(**values) -> dict:
    return {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientInfo": {
                "name": "modern-test-client",
                "version": "1.0",
            },
            "io.modelcontextprotocol/clientCapabilities": {},
        },
        **values,
    }


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


class TestProtocolVersionNegotiation:
    """Regression: server must echo client's supported MCP protocol version
    (flyto-core/issues/16). Older clients on 2025-06-18 / 2025-03-26 must not
    receive 2025-11-25 forced back."""

    def test_negotiate_helper_supported(self):
        from src.mcp_server import negotiate_protocol_version, SUPPORTED_PROTOCOL_VERSIONS
        for v in SUPPORTED_PROTOCOL_VERSIONS:
            assert negotiate_protocol_version(v) == v

    def test_negotiate_helper_fallback(self):
        from src.mcp_server import negotiate_protocol_version, SUPPORTED_PROTOCOL_VERSIONS
        assert negotiate_protocol_version("1999-01-01") == SUPPORTED_PROTOCOL_VERSIONS[0]
        assert negotiate_protocol_version(None) == SUPPORTED_PROTOCOL_VERSIONS[0]
        assert negotiate_protocol_version("") == SUPPORTED_PROTOCOL_VERSIONS[0]

    def test_initialize_echoes_2025_06_18(self, mcp_server):
        resp = _call(mcp_server, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "lm-studio-like", "version": "0.1"},
        })
        assert resp["result"]["protocolVersion"] == "2025-06-18"

    def test_initialize_echoes_2025_03_26(self, mcp_server):
        resp = _call(mcp_server, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "older-client", "version": "0.1"},
        })
        assert resp["result"]["protocolVersion"] == "2025-03-26"

    def test_initialize_echoes_2025_11_25(self, mcp_server):
        resp = _call(mcp_server, "initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "latest", "version": "0.1"},
        })
        assert resp["result"]["protocolVersion"] == "2025-11-25"

    def test_initialize_unsupported_falls_back(self, mcp_server):
        resp = _call(mcp_server, "initialize", {
            "protocolVersion": "1999-01-01",
            "capabilities": {},
            "clientInfo": {"name": "fake", "version": "0.1"},
        })
        # Server returns its preferred (newest) version.
        assert resp["result"]["protocolVersion"] == "2025-11-25"

    def test_initialize_does_not_negotiate_modern_revision(self, mcp_server):
        resp = _call(mcp_server, "initialize", {
            "protocolVersion": MODERN_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "wrong-era", "version": "0.1"},
        })
        assert resp["result"]["protocolVersion"] == "2025-11-25"


class TestModernProtocol:
    """MCP 2026-07-28 requests are stateless and self-describing."""

    def test_server_discover_advertises_dual_era_support(self, mcp_server):
        resp = _call(mcp_server, "server/discover", _modern_params())
        result = resp["result"]

        assert result["resultType"] == "complete"
        assert result["supportedVersions"][0] == MODERN_PROTOCOL_VERSION
        assert "2025-11-25" in result["supportedVersions"]
        assert result["capabilities"]["tools"]["listChanged"] is False
        assert "logging" not in result["capabilities"]
        assert result["ttlMs"] > 0
        assert result["cacheScope"] == "public"
        server_info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
        assert server_info["name"] == "flyto-indexer"

    def test_modern_tools_list_has_required_result_fields(self, mcp_server):
        resp = _call(mcp_server, "tools/list", _modern_params())
        result = resp["result"]

        assert result["resultType"] == "complete"
        assert result["cacheScope"] == "public"
        assert result["ttlMs"] > 0
        assert len(result["tools"]) == EXPECTED_TOOL_COUNT
        assert (
            result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"]
            == "flyto-indexer"
        )

    def test_modern_tool_call_keeps_typed_runtime_payload(self, mcp_server):
        resp = _call(
            mcp_server,
            "tools/call",
            _modern_params(
                name="search",
                arguments={"query": "login_user", "project": TEST_PROJECT},
            ),
        )
        result = resp["result"]

        assert result["resultType"] == "complete"
        assert result["structuredContent"]["result_count"] >= 1
        assert "flyto/indexerRuntime" in result["_meta"]
        assert "io.modelcontextprotocol/serverInfo" in result["_meta"]

    def test_unknown_modern_version_returns_standard_error(self, mcp_server):
        params = _modern_params()
        params["_meta"]["io.modelcontextprotocol/protocolVersion"] = "1900-01-01"
        resp = _call(mcp_server, "tools/list", params)

        assert resp["error"]["code"] == -32022
        assert resp["error"]["data"]["requested"] == "1900-01-01"
        assert MODERN_PROTOCOL_VERSION in resp["error"]["data"]["supported"]

    def test_modern_request_requires_client_capabilities(self, mcp_server):
        params = _modern_params()
        del params["_meta"]["io.modelcontextprotocol/clientCapabilities"]
        resp = _call(mcp_server, "tools/list", params)

        assert resp["error"]["code"] == -32602
        assert "clientCapabilities" in resp["error"]["message"]

    def test_removed_modern_ping_is_method_not_found(self, mcp_server):
        resp = _call(mcp_server, "ping", _modern_params())
        assert resp["error"]["code"] == -32601


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


class TestProtocolLiveness:
    """Real stdio cancellation must leave the same process responsive."""

    def test_pre_cancelled_read_only_call_then_ping(self, mcp_server):
        global _next_id
        cancelled_id = _next_id
        _next_id += 1
        _send_jsonrpc(
            mcp_server,
            "notifications/cancelled",
            {"requestId": cancelled_id, "reason": "integration test"},
            req_id=None,
        )
        _send_jsonrpc(
            mcp_server,
            "tools/call",
            {"name": "search", "arguments": {"query": "login_user"}},
            req_id=cancelled_id,
        )

        cancelled = _read_response(mcp_server)
        assert cancelled["id"] == cancelled_id
        assert cancelled["error"]["code"] == -32800
        assert cancelled["error"]["data"]["cancelled"] is True
        assert cancelled["error"]["data"]["retryable"] is True

        ping = _call(mcp_server, "ping")
        assert ping == {"jsonrpc": "2.0", "id": ping["id"], "result": {}}


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

    def test_task_grill_real_mcp_closed_loop(self, mcp_server):
        """Run start → repository fact → answer → freeze through JSON-RPC."""
        decisions = [
            {
                "id": "login_exists",
                "kind": "fact",
                "severity": "critical",
                "question": "Is authentication implementation indexed?",
                "recommendation": "Resolve it from repository evidence.",
                "evidence_queries": ["login_user"],
            },
            {
                "id": "failure_policy",
                "kind": "decision",
                "severity": "critical",
                "prerequisites": ["login_exists"],
                "question": "驗證失敗時應該怎麼處理？",
                "recommendation": "Fail closed and preserve auditable evidence.",
            },
        ]
        start_resp = _call(
            mcp_server,
            "tools/call",
            {
                "name": "task",
                "arguments": {
                    "action": "grill",
                    "grill_action": "start",
                    "description": "Harden authentication failure handling",
                    "project": TEST_PROJECT,
                    "decisions": decisions,
                    "locale": "und",
                },
            },
        )
        started = json.loads(start_resp["result"]["content"][0]["text"])
        assert started["resolved_from_code"] == ["login_exists"]
        assert started["next_question"]["id"] == "failure_policy"
        assert started["next_question"]["question"].startswith("驗證")

        answer_resp = _call(
            mcp_server,
            "tools/call",
            {
                "name": "task",
                "arguments": {
                    "action": "grill",
                    "grill_action": "answer",
                    "grill_session_id": started["session_id"],
                    "decision_id": "failure_policy",
                    "accept_recommendation": True,
                    "request_id": "mcp-e2e-answer-1",
                },
            },
        )
        answered = json.loads(answer_resp["result"]["content"][0]["text"])
        assert answered["readiness"]["ready_to_freeze"] is True

        freeze_resp = _call(
            mcp_server,
            "tools/call",
            {
                "name": "task",
                "arguments": {
                    "action": "grill",
                    "grill_action": "freeze",
                    "grill_session_id": started["session_id"],
                },
            },
        )
        frozen = json.loads(freeze_resp["result"]["content"][0]["text"])
        assert frozen["pass"] is True
        assert frozen["contract"]["status"] == "frozen"
        assert len(frozen["contract"]["fingerprint"]) == 64

        mutation_resp = _call(
            mcp_server,
            "tools/call",
            {
                "name": "task",
                "arguments": {
                    "action": "grill",
                    "grill_action": "answer",
                    "grill_session_id": started["session_id"],
                    "decision_id": "failure_policy",
                    "answer": "mutate frozen decision",
                },
            },
        )
        mutation = json.loads(mutation_resp["result"]["content"][0]["text"])
        assert mutation["pass"] is False
        assert "cannot answer a frozen" in mutation["error"]

    def test_verify_project(self, mcp_server, tmp_path):
        """verify runs the closed-loop gate through the MCP server."""
        project = tmp_path / "verify-demo"
        (project / "src").mkdir(parents=True)
        (project / ".gitignore").write_text(".flyto-index/\n", encoding="utf-8")
        (project / "AGENTS.md").write_text(
            "Use flyto-indexer. Run flyto-index verify before finishing.\n",
            encoding="utf-8",
        )
        (project / "README.md").write_text(
            "# Verify Demo\n\n## Installation\n\nInstall.\n\n## Usage\n\nRun.\n",
            encoding="utf-8",
        )
        (project / "src" / "app.py").write_text(
            "def verify_demo():\n"
            "    return True\n",
            encoding="utf-8",
        )

        resp = _call(mcp_server, "tools/call", {
            "name": "verify",
            "arguments": {
                "path": str(project),
                "full_scan": True,
                "query": "verify_demo",
            },
        })

        assert "error" not in resp, f"Got error: {resp.get('error')}"
        content = resp["result"]["content"]
        data = json.loads(content[0]["text"])
        assert data["pass"] is True
        checks = {check["name"]: check for check in data["checks"]}
        assert checks["index_integrity"]["status"] == "pass"
        assert checks["context_loop"]["status"] == "pass"
        assert checks["weak_scan_secrets"]["status"] == "pass"

    def test_verify_workspace(self, mcp_server, tmp_path):
        """verify_workspace runs the aggregate gate through the MCP server."""
        project = tmp_path / "workspace" / "app"
        (project / "src").mkdir(parents=True)
        (project / ".gitignore").write_text(".flyto-index/\n", encoding="utf-8")
        (project / "AGENTS.md").write_text(
            "Use flyto-indexer. Run flyto-index verify before finishing.\n",
            encoding="utf-8",
        )
        (project / "README.md").write_text(
            "# Workspace App\n\n## Installation\n\nInstall.\n\n## Usage\n\nRun.\n",
            encoding="utf-8",
        )
        (project / "src" / "app.py").write_text(
            "def workspace_demo():\n"
            "    return True\n",
            encoding="utf-8",
        )

        resp = _call(mcp_server, "tools/call", {
            "name": "verify_workspace",
            "arguments": {
                "path": str(tmp_path / "workspace"),
                "projects": [str(project)],
                "full_scan": True,
            },
        })

        assert "error" not in resp, f"Got error: {resp.get('error')}"
        content = resp["result"]["content"]
        data = json.loads(content[0]["text"])
        assert data["pass"] is True
        assert data["summary"]["projects"] == 1


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
