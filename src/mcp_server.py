#!/usr/bin/env python3
"""
Flyto Indexer MCP Server — Protocol handler.

Handles MCP JSON-RPC communication, rate limiting, and tool dispatch.
Tool definitions are in tool_registry.py (single source of truth).
Tool implementations are in tools/ package, quality.py, and diff_impact.py.
Index loading and caching are in index_store.py.

Usage:
    python -m src.mcp_server

Claude Code config (~/.claude/mcp_servers.json):
{
    "flyto-indexer": {
        "command": "python",
        "args": ["-m", "src.mcp_server"],
        "cwd": "/path/to/flyto-indexer"
    }
}
"""

import json
import logging
import os
import sys
import time as _time
from collections import deque
from typing import Any

logger = logging.getLogger("flyto-indexer.mcp")


# =============================================================================
# MCP Protocol — JSON-RPC communication
# =============================================================================

def send_response(id: Any, result: Any):
    response = {"jsonrpc": "2.0", "id": id, "result": result}
    print(json.dumps(response), flush=True)

def send_error(id: Any, code: int, message: str):
    response = {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}
    print(json.dumps(response), flush=True)

def send_notification(method: str, params: dict):
    """Send an MCP notification (no id, no response expected)."""
    msg = {"jsonrpc": "2.0", "method": method, "params": params}
    print(json.dumps(msg), flush=True)


# =============================================================================
# Rate Limiting (per-process, sliding window, O(1) popleft with deque)
# =============================================================================

_RATE_LIMIT_MAX = int(os.environ.get("FLYTO_INDEXER_RATE_LIMIT", "100"))
_RATE_LIMIT_SESSION_MAX = int(os.environ.get("FLYTO_INDEXER_SESSION_RATE_LIMIT", "30"))
_RATE_LIMIT_WINDOW = 60.0
_rate_limit_timestamps: deque = deque()
_session_rate_limits: dict = {}


def _check_rate_limit(session_id: str = "") -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = _time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW

    # Global rate limit — O(1) popleft with deque
    while _rate_limit_timestamps and _rate_limit_timestamps[0] < cutoff:
        _rate_limit_timestamps.popleft()
    if len(_rate_limit_timestamps) >= _RATE_LIMIT_MAX:
        return False

    # Per-session rate limit (check BEFORE appending to global)
    if session_id:
        if session_id not in _session_rate_limits:
            _session_rate_limits[session_id] = deque()
        session_ts = _session_rate_limits[session_id]
        while session_ts and session_ts[0] < cutoff:
            session_ts.popleft()
        if len(session_ts) >= _RATE_LIMIT_SESSION_MAX:
            return False
        session_ts.append(now)

        # Evict old session buckets (prevent memory leak)
        if len(_session_rate_limits) > 200:
            oldest_key = min(_session_rate_limits, key=lambda k: _session_rate_limits[k][-1] if _session_rate_limits[k] else 0)
            del _session_rate_limits[oldest_key]

    # Append to global only after all checks pass
    _rate_limit_timestamps.append(now)

    return True


# =============================================================================
# MCP Tool Definitions — imported from tool_registry (single source of truth)
# =============================================================================

try:
    from .tool_registry import SMART_TOOLS as TOOLS
except ImportError:
    from tool_registry import SMART_TOOLS as TOOLS


# =============================================================================
# MCP Resources
# =============================================================================

RESOURCES = [
    {
        "uri": "indexer://projects",
        "name": "Indexed Projects",
        "description": "List of all indexed projects with symbol counts.",
        "mimeType": "application/json",
    },
    {
        "uri": "indexer://recent-changes",
        "name": "Recent Changes",
        "description": "Files changed since last index (dry-run check).",
        "mimeType": "application/json",
    },
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "indexer://project/{name}/health",
        "name": "Project Health Score",
        "description": "Code health score (0-100) with letter grade for a specific project.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "indexer://project/{name}/stale",
        "name": "Project Stale Files",
        "description": "Changed files since last index for a specific project.",
        "mimeType": "application/json",
    },
]


# =============================================================================
# MCP Prompts
# =============================================================================

PROMPTS = [
    {
        "name": "code-review",
        "description": "Review code changes for quality, security, and correctness",
        "arguments": [
            {"name": "focus", "description": "Focus area: security, performance, correctness, or all", "required": False},
        ],
    },
    {
        "name": "refactor-plan",
        "description": "Plan a refactoring task with risk assessment",
        "arguments": [
            {"name": "target", "description": "File or symbol to refactor", "required": True},
            {"name": "goal", "description": "What the refactoring should achieve", "required": False},
        ],
    },
    {
        "name": "impact-check",
        "description": "Check the impact of uncommitted changes before committing",
        "arguments": [],
    },
]


def _get_prompt(name: str, arguments: dict = None) -> dict:
    """Generate a prompt template with pre-filled tool calls."""
    arguments = arguments or {}

    if name == "code-review":
        focus = arguments.get("focus", "all")
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": f"Review my uncommitted changes. Focus: {focus}.\n\n"
                               "Steps:\n"
                               "1. Run impact(mode='unstaged') to see what changed\n"
                               "2. Run audit(focus='security') if focus is security\n"
                               "3. Summarize findings with risk level and actionable suggestions",
                    },
                },
            ],
        }
    elif name == "refactor-plan":
        target = arguments.get("target", "")
        goal = arguments.get("goal", "reduce complexity")
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": f"Plan a refactoring for: {target}\nGoal: {goal}\n\n"
                               "Steps:\n"
                               f"1. Run task(action='plan', targets=['{target}'], intent='refactor')\n"
                               "2. Follow the execution plan\n"
                               "3. After changes, run task(action='validate')",
                    },
                },
            ],
        }
    elif name == "impact-check":
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": "Check the impact of my uncommitted changes.\n\n"
                               "Steps:\n"
                               "1. Run impact(mode='unstaged') to analyze changes\n"
                               "2. For any high-impact changes, run impact(target='symbol_name') for details\n"
                               "3. Summarize: what's safe, what needs attention, suggested test commands",
                    },
                },
            ],
        }
    else:
        return {"error": f"Unknown prompt: {name}"}


def _read_resource(uri: str) -> dict:
    """Read an MCP resource by URI."""
    try:
        from .tools.code_info import list_projects
        from .tools.maintenance import check_and_reindex
        from .quality import code_health_score
        from .index_store import load_index
    except ImportError:
        from tools.code_info import list_projects
        from tools.maintenance import check_and_reindex
        from quality import code_health_score
        from index_store import load_index

    if uri == "indexer://projects":
        data = list_projects()
    elif uri == "indexer://recent-changes":
        data = check_and_reindex(dry_run=True)
    elif uri.startswith("indexer://project/") and uri.endswith("/health"):
        name = uri[len("indexer://project/"):-len("/health")]
        if not name:
            return {"error": "Missing project name in URI"}
        index = load_index()
        if name not in index.get("projects", []):
            return {"error": f"Project not found: {name}"}
        data = code_health_score(project=name)
    elif uri.startswith("indexer://project/") and uri.endswith("/stale"):
        name = uri[len("indexer://project/"):-len("/stale")]
        if not name:
            return {"error": "Missing project name in URI"}
        index = load_index()
        if name not in index.get("projects", []):
            return {"error": f"Project not found: {name}"}
        data = check_and_reindex(dry_run=True, project=name)
    else:
        return {"error": f"Unknown resource URI: {uri}"}

    return {
        "contents": [{
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(data, ensure_ascii=False, indent=2),
        }]
    }


# =============================================================================
# Request Handler
# =============================================================================

def handle_request(request: dict):
    """Handle MCP request."""
    method = request.get("method", "")
    id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        client_version = params.get("protocolVersion", "2024-11-05")
        server_version = "2025-11-25" if client_version >= "2025-06-18" else "2024-11-05"

        send_response(id, {
            "protocolVersion": server_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False},
                "prompts": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {
                "name": "flyto-indexer",
                "title": "Flyto Code Indexer",
                "version": "2.3.0",
                "description": "Code analysis MCP server — impact analysis, dependency tracking, dead code detection, security scanning, and code health scoring across any project.",
                "websiteUrl": "https://github.com/flytohub/flyto-indexer",
            },
            "instructions": (
                "flyto-indexer provides {tool_count} code analysis tools. "
                "ALWAYS use these tools — do NOT fall back to Grep/Read for tasks they cover.\n\n"
                "When asked to AUDIT or REVIEW a project:\n"
                "  1. audit → overall quality score + auto-expands weak dimensions\n\n"
                "When asked to MODIFY or REFACTOR code:\n"
                "  1. task(action='plan') → risk assessment + execution plan (call FIRST)\n"
                "  2. MANDATORY: Execute EVERY step in the execution_plan sequentially.\n"
                "     Each step has: tool name, pre-filled args, and dependencies.\n"
                "     Do NOT skip steps. Do NOT edit code until all inspect/assess steps complete.\n"
                "  3. task(action='gate') → call at EVERY gate step before proceeding.\n"
                "     If gate returns pass=false, STOP and report to user.\n"
                "  4. Only after all gates pass, proceed to make changes.\n"
                "  5. task(action='validate') → run ruff + pytest after making changes.\n\n"
                "When asked to UNDERSTAND or EXPLORE code:\n"
                "  1. search → find symbols by name or natural language\n"
                "  2. structure → discover projects, APIs, dependencies\n\n"
                "When checking IMPACT of changes:\n"
                "  1. impact(target='symbol_name') → references + blast radius + cross-project\n"
                "  2. impact(mode='unstaged') → blast radius of uncommitted changes"
            ).format(tool_count=len(TOOLS)),
        })

    elif method == "tools/list":
        send_response(id, {"tools": TOOLS})

    elif method == "tools/call":
        _handle_tool_call(id, params)

    elif method == "resources/list":
        send_response(id, {
            "resources": RESOURCES,
            "resourceTemplates": RESOURCE_TEMPLATES,
        })

    elif method == "resources/read":
        uri = params.get("uri", "")
        if not uri:
            send_error(id, -32602, "Missing 'uri' parameter")
            return
        result = _read_resource(uri)
        if "error" in result:
            send_error(id, -32002, result["error"])
        else:
            send_response(id, result)

    elif method == "prompts/list":
        send_response(id, {"prompts": PROMPTS})

    elif method == "prompts/get":
        prompt_name = params.get("name", "")
        prompt_args = params.get("arguments", {})
        result = _get_prompt(prompt_name, prompt_args)
        if "error" in result:
            send_error(id, -32602, result["error"])
        else:
            send_response(id, result)

    elif method == "logging/setLevel":
        send_response(id, {})

    elif method in ("notifications/initialized", "notifications/cancelled"):
        pass

    else:
        send_error(id, -32601, f"Method not found: {method}")


def _handle_tool_call(id: Any, params: dict):
    """Handle tools/call — extracted from handle_request for clarity."""
    # Auto-reindex check
    try:
        try:
            from .index_store import _maybe_auto_reindex
        except ImportError:
            from index_store import _maybe_auto_reindex
        _maybe_auto_reindex()
    except (OSError, RuntimeError) as e:
        logger.debug("Auto-reindex skipped: %s", e)

    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    # Rate limiting
    _session_id = str(arguments.get("session_id", ""))[:64] if isinstance(arguments.get("session_id"), str) else ""
    if not _check_rate_limit(session_id=_session_id):
        send_error(id, -32000, f"Rate limit exceeded ({_RATE_LIMIT_MAX} req/{int(_RATE_LIMIT_WINDOW)}s). Please slow down.")
        return

    # Execution guard: block tool calls that skip required gates
    try:
        try:
            from .execution_guard import check_enforcement, record_tool_call, register_task
        except ImportError:
            from execution_guard import check_enforcement, record_tool_call, register_task
        _guard_warning = check_enforcement(tool_name, arguments)
        if _guard_warning:
            send_response(id, {
                "content": [{"type": "text", "text": json.dumps(_guard_warning, ensure_ascii=False, indent=2)}],
            })
            return
    except (ImportError, AttributeError) as e:
        logger.debug("Execution guard unavailable: %s", e)

    try:
        try:
            from .tool_registry import execute_tool
        except ImportError:
            from tool_registry import execute_tool
        try:
            result = execute_tool(tool_name, arguments)
        except KeyError:
            send_error(id, -32601, f"Unknown tool: {tool_name}")
            return

        # Execution guard: record step completion + register new task
        _is_plan = (
            tool_name == "analyze_task"
            or (tool_name == "task" and arguments.get("action") == "plan")
        )
        try:
            record_tool_call(tool_name, arguments)
            if _is_plan and isinstance(result, dict):
                register_task(result)
        except (NameError, AttributeError, TypeError) as e:
            logger.debug("Guard record skipped: %s", e)

        result_text = json.dumps(result, ensure_ascii=False, indent=2)

        # Structural enforcement: inject directive after analyze_task / task(plan)
        if _is_plan and isinstance(result, dict):
            result_text += _build_analyze_task_directive(result)

        send_response(id, {
            "content": [{"type": "text", "text": result_text}],
        })
    except Exception as e:
        send_error(id, -32000, str(e))


def _build_analyze_task_directive(result: dict) -> str:
    """Build the enforcement directive appended to analyze_task results."""
    if result.get("task_profile", {}).get("compound"):
        # Compound contract: show per-sub-task plans
        parts = []
        for i, st in enumerate(result.get("sub_tasks", [])):
            plan = st.get("execution_plan", [])
            if plan:
                steps = []
                for step in plan:
                    args_str = json.dumps(step.get("args", {}), ensure_ascii=False)
                    is_gate = step.get("tool") == "task_gate_check"
                    marker = " ⛔ GATE — MUST CALL" if is_gate else ""
                    steps.append(f"    {step['id']}: {step['tool']}({args_str}){marker}")
                parts.append(f"\n  Sub-task {i+1} [{st['intent']}] ({len(st['targets'])} targets):\n" + "\n".join(steps))
        if parts:
            return (
                "\n\n⚠️ COMPOUND TASK: Execute sub-tasks IN ORDER (cleanup first, then refactor):\n"
                + "\n".join(parts)
                + "\n\n"
                "RULES:\n"
                "1. Complete each sub-task before starting the next.\n"
                "2. At ⛔ GATE steps, call task_gate_check. If pass=false → STOP.\n"
                "3. Do NOT edit code until all gates in the current sub-task pass."
            )

    elif "execution_plan" in result:
        # Standard (non-compound) contract
        plan = result["execution_plan"]
        if plan:
            steps = []
            for step in plan:
                args_str = json.dumps(step.get("args", {}), ensure_ascii=False)
                is_gate = step.get("tool") == "task_gate_check"
                marker = " ⛔ GATE — MUST CALL" if is_gate else ""
                steps.append(f"  {step['id']}: {step['tool']}({args_str}){marker}")
            return (
                "\n\n⚠️ MANDATORY: Execute these steps IN ORDER before editing any code:\n"
                + "\n".join(steps)
                + "\n\n"
                "RULES:\n"
                "1. Call each tool above sequentially with the pre-filled args.\n"
                "2. At ⛔ GATE steps, call task_gate_check. If pass=false → STOP.\n"
                "3. Do NOT read/edit source files until all gates pass.\n"
                "4. After completing all steps, proceed with changes."
            )

    return ""


# =============================================================================
# Backward Compatibility — re-export functions for existing tests/imports
# =============================================================================

try:
    from . import index_store as _index_store_mod
    from .index_store import (
        INDEX_DIR, load_index, load_project_map, load_content_file,
        get_symbol_content_text, TYPE_WEIGHTS, LOW_PRIORITY_PATHS,
        _load_bm25, _load_semantic, _get_test_mapper, _get_session_store,
    )
    from .tools.search import search_by_keyword, fulltext_search, semantic_search
    from .tools.references import (
        find_references, impact_analysis, batch_impact_analysis,
        edit_impact_preview, cross_project_impact, dependency_graph,
    )
    from .tools.code_info import (
        get_file_info, get_file_symbols, get_symbol_content,
        get_file_context, list_categories, list_apis, list_projects,
        get_description, update_description, find_test_file,
    )
    from .tools.maintenance import (
        find_dead_code, find_todos, check_index_status,
        check_and_reindex, _perform_live_reindex,
        session_track, session_get,
    )
    from .tools.task_analysis import analyze_task, task_gate_check
    from .quality import (
        find_complex_functions, find_duplicates, security_scan,
        find_stale_files, code_health_score, suggest_refactoring,
        analyze_data_flow,
    )
    from .diff_impact import impact_from_diff
except ImportError:
    import index_store as _index_store_mod
    from index_store import (
        INDEX_DIR, load_index, load_project_map, load_content_file,
        get_symbol_content_text, TYPE_WEIGHTS, LOW_PRIORITY_PATHS,
        _load_bm25, _load_semantic, _get_test_mapper, _get_session_store,
    )
    from tools.search import search_by_keyword, fulltext_search, semantic_search
    from tools.references import (
        find_references, impact_analysis, batch_impact_analysis,
        edit_impact_preview, cross_project_impact, dependency_graph,
    )
    from tools.code_info import (
        get_file_info, get_file_symbols, get_symbol_content,
        get_file_context, list_categories, list_apis, list_projects,
        get_description, update_description, find_test_file,
    )
    from tools.maintenance import (
        find_dead_code, find_todos, check_index_status,
        check_and_reindex, _perform_live_reindex,
        session_track, session_get,
    )
    from tools.task_analysis import analyze_task, task_gate_check
    from quality import (
        find_complex_functions, find_duplicates, security_scan,
        find_stale_files, code_health_score, suggest_refactoring,
        analyze_data_flow,
    )
    from diff_impact import impact_from_diff

# Expose index_store internal state for backward compat —
# tests set mcp_server._index_cache, _content_cache, _content_loaded,
# _test_mapper, _session_store directly.
# Uses __class__ swap to preserve the original module __dict__ (so mock.patch works).
_PROXIED_ATTRS = {"_index_cache", "_content_cache", "_content_loaded",
                  "_test_mapper", "_session_store"}

import types as _types

class _IndexStoreProxy(_types.ModuleType):
    def __setattr__(self, name, value):
        if name in _PROXIED_ATTRS:
            setattr(_index_store_mod, name, value)
        else:
            super().__setattr__(name, value)

    def __getattr__(self, name):
        if name in _PROXIED_ATTRS:
            return getattr(_index_store_mod, name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Swap the module class in-place — preserves __dict__ so mock.patch works
sys.modules[__name__].__class__ = _IndexStoreProxy


# =============================================================================
# Main
# =============================================================================

def main():
    """MCP Server main program."""
    try:
        try:
            from .safe_io import configure_logging
        except ImportError:
            from safe_io import configure_logging
        configure_logging()
    except (ImportError, OSError):
        pass
    sys.stderr.write(f"[flyto-indexer] Starting MCP server (pid={os.getpid()})\n")
    sys.stderr.flush()

    for line in sys.stdin:
        try:
            sys.stderr.write(f"[flyto-indexer] Received: {line[:100]}...\n")
            sys.stderr.flush()
            request = json.loads(line.strip())
            handle_request(request)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"[flyto-indexer] JSON decode error: {e}\n")
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"[flyto-indexer] Error: {e}\n")
            sys.stderr.flush()
            req_id = request.get("id") if isinstance(request, dict) else None
            print(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}), flush=True)


if __name__ == "__main__":
    main()
