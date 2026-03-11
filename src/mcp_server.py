#!/usr/bin/env python3
"""
Flyto Indexer MCP Server — Protocol handler.

Handles MCP JSON-RPC communication, rate limiting, and tool dispatch.
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
import os
import sys
import time as _time
from typing import Any


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
# Rate Limiting (per-process, sliding window)
# =============================================================================

_RATE_LIMIT_MAX = int(os.environ.get("FLYTO_INDEXER_RATE_LIMIT", "100"))
_RATE_LIMIT_SESSION_MAX = int(os.environ.get("FLYTO_INDEXER_SESSION_RATE_LIMIT", "30"))
_RATE_LIMIT_WINDOW = 60.0
_rate_limit_timestamps: list = []
_session_rate_limits: dict = {}


def _check_rate_limit(session_id: str = "") -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = _time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW

    # Global rate limit
    while _rate_limit_timestamps and _rate_limit_timestamps[0] < cutoff:
        _rate_limit_timestamps.pop(0)
    if len(_rate_limit_timestamps) >= _RATE_LIMIT_MAX:
        return False
    _rate_limit_timestamps.append(now)

    # Per-session rate limit
    if session_id:
        if session_id not in _session_rate_limits:
            _session_rate_limits[session_id] = []
        session_ts = _session_rate_limits[session_id]
        while session_ts and session_ts[0] < cutoff:
            session_ts.pop(0)
        if len(session_ts) >= _RATE_LIMIT_SESSION_MAX:
            return False
        session_ts.append(now)

        # Evict old session buckets (prevent memory leak)
        if len(_session_rate_limits) > 200:
            oldest_key = min(_session_rate_limits, key=lambda k: _session_rate_limits[k][-1] if _session_rate_limits[k] else 0)
            del _session_rate_limits[oldest_key]

    return True


# =============================================================================
# MCP Tool Definitions
# =============================================================================

TOOLS = [
    # Reference & Dependency Analysis
    {
        "name": "find_references",
        "title": "Find References",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find all places that call or import a specific symbol. "
            "Use this BEFORE modifying a function/component to understand who depends on it. "
            "Uses pre-computed reverse index for fast, accurate results. "
            "Each reference includes confidence level: high (from dependency analysis), medium (from imports), low (from content regex). "
            "Returns: list of callers with file path, line number, and confidence, grouped by project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or just the symbol name. Examples: 'flyto-pro:src/agent/browser_agent.py:class:BrowserAgent' or just 'useToast'",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "impact_analysis",
        "title": "Impact Analysis",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Analyze the blast radius of modifying a symbol. "
            "Use this to assess risk BEFORE making changes to shared code. "
            "Returns: count of affected locations, list of affected symbols with paths, "
            "and a risk assessment (safe / moderate / high risk) with suggestions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or name to analyze. Format: project:path:type:name",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "dependency_graph",
        "title": "Dependency Graph",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get the dependency graph for a file, symbol, or entire project. "
            "Shows what a module imports (dependencies) and what imports it (dependents). "
            "Use direction='imports' to see what a file depends on, 'dependents' to see what depends on it, 'both' for full picture. "
            "Returns: lists of import and dependent relationships with file paths and dependency types."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File path to analyze. Example: 'src/composables/useToast.js'"},
                "symbol_id": {"type": "string", "description": "Symbol ID (file path is auto-extracted from it)"},
                "project": {"type": "string", "description": "Project name to show all dependencies for the entire project"},
                "direction": {
                    "type": "string",
                    "enum": ["both", "imports", "dependents"],
                    "default": "both",
                    "description": "'imports' = what this file depends on, 'dependents' = what depends on this file, 'both' = full graph",
                },
                "max_depth": {"type": "integer", "default": 2, "description": "Max traversal depth"},
            },
        },
    },
    {
        "name": "cross_project_impact",
        "title": "Cross-Project Impact",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Track cross-project API usage. When a function/class in one project changes, "
            "find all other projects that need to be updated. "
            "Use this before changing shared APIs (e.g. a function in flyto-core used by flyto-pro and flyto-cloud). "
            "Returns: list of cross-project references, affected projects, and risk level (low/medium/high)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_name": {
                    "type": "string",
                    "description": "Symbol name to track. Example: 'useModuleSchema', 'ValidationError', 'BaseModule'",
                },
                "source_project": {
                    "type": "string",
                    "description": "Limit search to symbols defined in this project (optional)",
                },
            },
            "required": ["symbol_name"],
        },
    },
    # Project Overview & Status
    {
        "name": "list_projects",
        "title": "List Projects",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "List all indexed projects with statistics. "
            "Use this FIRST to discover available projects and their sizes. "
            "Returns: project names, file counts, symbol counts, and breakdown by symbol type (function/class/component/etc)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    # Code Quality
    {
        "name": "find_dead_code",
        "title": "Find Dead Code",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find unreferenced functions, classes, and components (dead code). "
            "These symbols are never imported or called by any other code and can likely be removed. "
            "Automatically excludes entry points, lifecycle hooks, private methods, and test files. "
            "Returns: list of dead symbols sorted by line count (largest first), with total dead lines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "symbol_type": {
                    "type": "string",
                    "description": "Filter to a specific symbol type",
                    "enum": ["function", "method", "composable", "component", "class"],
                },
                "min_lines": {"type": "integer", "description": "Minimum line count to report. Default: 5", "default": 5},
            },
        },
    },
    {
        "name": "edit_impact_preview",
        "title": "Edit Impact Preview",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Preview the impact of editing a symbol before making changes. "
            "Shows all call sites with actual code lines, risk assessment, and suggestions. "
            "Use this BEFORE renaming, deleting, or changing a function/class signature. "
            "change_type: rename, delete, signature_change, add_param, modify."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or name. Example: 'useAuth' or 'flyto-cloud:src/composables/useAuth.js:composable:useAuth'",
                },
                "change_type": {
                    "type": "string",
                    "enum": ["rename", "delete", "signature_change", "add_param", "modify"],
                    "default": "modify",
                    "description": "Type of change: rename, delete, signature_change, add_param, modify",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "check_and_reindex",
        "title": "Check & Reindex",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "description": (
            "Detect file changes since last index and optionally clear caches. "
            "dry_run=true (default): only report which files changed. "
            "dry_run=false: clear all caches (must run 'python index_all.py' after). "
            "auto_reindex=true: detect changes AND perform live incremental reindex in-process. "
            "Returns: changed files grouped by type (modified/added/deleted) and project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean", "default": True, "description": "If true, only report changes. If false, also clear caches."},
                "project": {"type": "string", "description": "Filter to a specific project"},
                "auto_reindex": {"type": "boolean", "default": False, "description": "If true, perform live incremental reindex when changes are detected."},
            },
        },
    },
    {
        "name": "impact_from_diff",
        "title": "Impact from Diff",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Parse git diff output, match changed hunks to indexed symbols, classify each change "
            "(signature_change, body_change, rename, etc.), and run impact analysis. "
            "Use this to assess the blast radius of uncommitted or recent changes. "
            "Requires git. Modes: unstaged (default), staged, committed (base=SHA), branch (base=branch). "
            "Returns: changed symbols with risk level, caller count, and affected projects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["unstaged", "staged", "committed", "branch"],
                    "default": "unstaged",
                    "description": "What to diff: 'unstaged' (working tree), 'staged' (git add), 'committed' (base..HEAD), 'branch' (base...HEAD)",
                },
                "base": {"type": "string", "default": "", "description": "Base ref for committed/branch mode. Examples: 'HEAD~1', 'main', 'abc1234'."},
                "project": {"type": "string", "description": "Filter to a specific project"},
            },
        },
    },
    {
        "name": "find_complex_functions",
        "title": "Find Complex Functions",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find overly complex functions and methods across indexed projects. "
            "Scores each function based on: line count (>50), nesting depth (>3), "
            "parameter count (>5), and branch count (>10). "
            "Returns: ranked list with complexity score, issues, and symbol_id for follow-up."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 20, "description": "Max results to return (default 20)"},
                "min_score": {"type": "integer", "default": 1, "description": "Minimum complexity score to include (default 1)"},
            },
        },
    },
    {
        "name": "find_duplicates",
        "title": "Find Duplicate Code",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find copy-pasted code blocks across project files. "
            "Uses sliding-window hash comparison to detect duplicate code blocks (default min 6 lines). "
            "Returns: duplicate blocks with file locations, line ranges, and code preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "min_lines": {"type": "integer", "default": 6, "description": "Minimum duplicate block size in lines (default 6)"},
                "max_results": {"type": "integer", "default": 20, "description": "Max duplicate blocks to return (default 20)"},
            },
        },
    },
    {
        "name": "security_scan",
        "title": "Security Scan",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Scan project files for potential security issues: hardcoded secrets, SQL injection risks, "
            "unsafe function usage (eval, exec, pickle.loads), and sensitive data leaks. "
            "Multi-language: Python, JS/TS, Java, Go. "
            "Returns: issues sorted by severity (critical/high/medium/low) with code snippets and fix recommendations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"], "description": "Filter by severity level."},
                "max_results": {"type": "integer", "default": 50, "description": "Max issues to return (default 50)"},
            },
        },
    },
    {
        "name": "find_stale_files",
        "title": "Find Stale Files",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Find source files untouched for a long time using git history. "
            "Returns: stale files sorted by age with last author and modification date."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "stale_days": {"type": "integer", "default": 180, "description": "Days without changes to consider stale (default 180)"},
                "max_results": {"type": "integer", "default": 30, "description": "Max results to return (default 30)"},
            },
        },
    },
    {
        "name": "code_health_score",
        "title": "Code Health Score",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Compute an aggregate code health score (0-100) with letter grade (A-F). "
            "Breakdown: complexity (25 pts), dead code (25 pts), documentation (25 pts), modularity (25 pts). "
            "Works entirely from the index — fast, no filesystem access."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project."},
            },
        },
    },
    {
        "name": "suggest_refactoring",
        "title": "Suggest Refactoring",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get prioritized refactoring suggestions combining complexity analysis, dead code detection, "
            "and large file identification. Each suggestion includes type, priority, reason, and actionable fix."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 20, "description": "Max suggestions to return (default 20)"},
            },
        },
    },
    # Search & Discovery
    {
        "name": "search_code",
        "title": "Search Code",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Search for functions, classes, components, and composables across all indexed projects. "
            "Use this as the FIRST step when you need to find code by name or keyword. "
            "Results are ranked by relevance (name match > summary match > content match) "
            "and grouped by project. "
            "Returns: symbol_id, path, line number, type, summary, score. "
            "Use the symbol_id in follow-up calls to get_symbol_content, find_references, or impact_analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword to search (function name, class name, etc.). Example: 'useAuth', 'LoginForm', 'validate'"},
                "max_results": {"type": "integer", "default": 20, "description": "Max results to return (default 20)"},
                "symbol_type": {
                    "type": "string",
                    "enum": ["function", "class", "method", "composable", "component", "interface", "type"],
                    "description": "Filter by symbol type. Omit to search all types.",
                },
                "project": {"type": "string", "description": "Filter by project name. Use list_projects to see available projects."},
                "include_content": {"type": "boolean", "default": False, "description": "Include first 500 chars of source code in results"},
                "session_id": {"type": "string", "description": "Optional session ID for search boost."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_symbol_content",
        "title": "Get Symbol Content",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get the full source code of a specific symbol (function, class, component). "
            "Use this AFTER search_code to read the actual implementation. "
            "Supports fuzzy matching: you can pass a partial symbol_id and it will find the best match. "
            "Returns: full source code, file path, line range, summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID from search_code results. Format: project:path:type:name.",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "get_file_symbols",
        "title": "Get File Symbols",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "List all symbols defined in a specific file. "
            "Use this to get an overview of what a file contains. "
            "Returns: symbol id, name, type, line number, and summary for each symbol."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root. Example: 'src/composables/useAuth.js'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_file_info",
        "title": "Get File Info",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get semantic metadata for a file: purpose, category, keywords, APIs used, and dependencies. "
            "Returns: purpose description, category, keywords, API endpoints, dependencies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. Example: 'src/api/auth.py'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "fulltext_search",
        "title": "Fulltext Search",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Full-text search across all indexed source code. Searches inside comments, strings, and TODO/FIXME markers. "
            "Use search_type='todo' to find all TODO/FIXME items, 'comment' for comments only, 'string' for string literals. "
            "Returns: matching symbols with context snippets, grouped by project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for."},
                "search_type": {
                    "type": "string",
                    "enum": ["all", "todo", "comment", "string"],
                    "default": "all",
                    "description": "What to search: 'all', 'todo', 'comment', 'string'",
                },
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 50, "description": "Max results to return"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_categories",
        "title": "List Categories",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "List all code categories and how many files belong to each. "
            "Returns: category names sorted by file count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "list_apis",
        "title": "List APIs",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "List all API endpoints found in indexed code, along with which files use them. "
            "Returns: API paths sorted by usage count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "check_index_status",
        "title": "Check Index Status",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Check if the code index is up-to-date or stale. "
            "Returns: status (fresh/slightly_stale/stale), changed files, and recommendation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "find_todos",
        "title": "Find TODOs",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find all TODO, FIXME, HACK, and XXX markers across indexed code. "
            "Priority: FIXME/HACK = high, TODO/XXX = medium, NOTE = low. "
            "Returns: markers with text, file path, line number, grouped by priority and project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "Filter by priority level"},
                "max_results": {"type": "integer", "default": 100, "description": "Max results to return"},
            },
        },
    },
    {
        "name": "get_description",
        "title": "Get Description",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get the semantic one-liner description for a file. "
            "Returns the latest summary, staleness status, and metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root."},
                "project": {"type": "string", "description": "Project name (optional, auto-detected if omitted)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "update_description",
        "title": "Update Description",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "description": (
            "Write or update a semantic description for a file. "
            "Stored in .flyto/descriptions.jsonl with content hash for staleness tracking."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root."},
                "summary": {"type": "string", "description": "One-liner description."},
                "project": {"type": "string", "description": "Project name (optional, auto-detected if omitted)"},
            },
            "required": ["path", "summary"],
        },
    },
    {
        "name": "get_file_context",
        "title": "Get File Context",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get a complete context package for a file in one call. "
            "Returns file info, symbols, imports, dependents, test file mapping, and related files. "
            "All data comes from cached index, zero I/O."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root."},
                "include_content": {"type": "boolean", "default": False, "description": "Include first 500 chars of each symbol's source code"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_test_file",
        "title": "Find Test File",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find the corresponding test file for a source file, or the source file for a test file. "
            "Uses naming conventions and import analysis as fallback."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Source or test file path."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "session_track",
        "title": "Session Track",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "description": (
            "Track a workspace event for search boosting. "
            "Tracked files get +8 score boost in search_code results. Sessions expire after 24h."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Unique session identifier"},
                "event_type": {"type": "string", "enum": ["file_open", "query", "edit"], "description": "Type of event"},
                "target": {"type": "string", "description": "Target of the event (file path or query string)"},
                "workspace_root": {"type": "string", "description": "Workspace root path (optional)"},
            },
            "required": ["session_id", "event_type", "target"],
        },
    },
    {
        "name": "session_get",
        "title": "Session Get",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get the current state of a workspace session. "
            "Returns: open files, recent queries, recent edits, and boost path count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session identifier"},
            },
            "required": ["session_id"],
        },
    },
    # Task Analysis
    {
        "name": "analyze_task",
        "title": "Analyze Task",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Analyze a task across 6 dimensions and produce a task contract. "
            "Dimensions: blast_radius, breaking_risk, test_coverage, cross_coupling, complexity, rollback_difficulty. "
            "Automatically derives constraints (must_run_impact_review, must_add_tests, etc.) and execution strategy. "
            "Use this BEFORE starting any non-trivial task to understand risk and get a structured plan. "
            "Returns: profile, dimensions (scored 0-10), constraints, and strategy with phases."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What the task is about. Example: 'Refactor useAuth composable to centralize token refresh'"},
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Symbol names, symbol IDs, or file paths to analyze. Example: ['useAuth', 'useToken']",
                },
                "intent": {
                    "type": "string",
                    "enum": ["refactor", "bugfix", "feature", "cleanup", "migration"],
                    "default": "refactor",
                    "description": "Task intent: refactor, bugfix, feature, cleanup, migration",
                },
                "project": {"type": "string", "description": "Filter to a specific project"},
            },
            "required": ["description", "targets"],
        },
    },
    {
        "name": "task_gate_check",
        "title": "Task Gate Check",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Check whether a task can proceed to the next phase based on its contract. "
            "Validates that required analyses, tests, and reviews have been completed. "
            "Returns: pass/blocked decision with reason_codes and required_actions. "
            "Phases: inspect, plan_changes, apply_changes, expand_changes, finalize."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_contract": {
                    "type": "object",
                    "description": "The task contract object returned by analyze_task",
                },
                "next_phase": {
                    "type": "string",
                    "enum": ["inspect", "plan_changes", "apply_changes", "expand_changes", "finalize"],
                    "description": "Phase to check entry for",
                },
                "current_state": {
                    "type": "object",
                    "description": "Boolean flags: impact_analysis_done, cross_project_check_done, tests_reviewed, human_review_completed, validation_passed, public_contract_change_detected",
                },
            },
            "required": ["task_contract"],
        },
    },
]


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
                "logging": {},
            },
            "serverInfo": {
                "name": "flyto-indexer",
                "title": "Flyto Code Indexer",
                "version": "2.1.0",
                "description": "Code analysis MCP server — impact analysis, dependency tracking, dead code detection, security scanning, and code health scoring across any project.",
                "websiteUrl": "https://github.com/flytohub/flyto-indexer",
            },
            "instructions": (
                "flyto-indexer provides 32 code analysis tools. "
                "ALWAYS use these tools — do NOT fall back to Grep/Read for tasks they cover.\n\n"
                "When asked to AUDIT or REVIEW a project:\n"
                "  1. code_health_score → overall quality score\n"
                "  2. security_scan → hardcoded secrets, injection risks\n"
                "  3. find_dead_code → unreferenced code safe to delete\n"
                "  4. find_complex_functions → functions needing refactoring\n"
                "  5. suggest_refactoring → prioritized improvement list\n\n"
                "When asked to MODIFY or REFACTOR code:\n"
                "  1. analyze_task → risk assessment + execution plan (call FIRST)\n"
                "  2. MANDATORY: Execute EVERY step in the execution_plan sequentially.\n"
                "     Each step has: tool name, pre-filled args, and dependencies.\n"
                "     Do NOT skip steps. Do NOT edit code until all inspect/assess steps complete.\n"
                "  3. task_gate_check → call at EVERY gate step before proceeding.\n"
                "     If gate returns pass=false, STOP and report to user.\n"
                "  4. Only after all gates pass, proceed to make changes.\n\n"
                "When asked to UNDERSTAND or EXPLORE code:\n"
                "  1. search_code → find symbols by name\n"
                "  2. list_projects → discover indexed projects\n"
                "  3. list_apis → API endpoints + cross-language callers\n"
                "  4. dependency_graph → imports and dependents\n\n"
                "When checking IMPACT of changes:\n"
                "  1. impact_analysis → what breaks if you change this\n"
                "  2. find_references → who calls this function\n"
                "  3. edit_impact_preview → exact lines affected\n"
                "  4. cross_project_impact → which other repos use this\n"
                "  5. impact_from_diff → blast radius of uncommitted changes"
            ),
        })

    elif method == "tools/list":
        send_response(id, {"tools": TOOLS})

    elif method == "tools/call":
        # Auto-reindex check
        try:
            try:
                from .index_store import _maybe_auto_reindex
            except ImportError:
                from index_store import _maybe_auto_reindex
            _maybe_auto_reindex()
        except Exception:
            pass

        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Rate limiting
        _session_id = str(arguments.get("session_id", ""))[:64] if isinstance(arguments.get("session_id"), str) else ""
        if not _check_rate_limit(session_id=_session_id):
            send_error(id, -32000, f"Rate limit exceeded ({_RATE_LIMIT_MAX} req/{int(_RATE_LIMIT_WINDOW)}s). Please slow down.")
            return

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

            result_text = json.dumps(result, ensure_ascii=False, indent=2)

            # Structural enforcement: inject directive after analyze_task
            if tool_name == "analyze_task" and isinstance(result, dict) and "execution_plan" in result:
                plan = result["execution_plan"]
                if plan:
                    steps = []
                    for step in plan:
                        args_str = json.dumps(step.get("args", {}), ensure_ascii=False)
                        is_gate = step.get("tool") == "task_gate_check"
                        marker = " ⛔ GATE — MUST CALL" if is_gate else ""
                        steps.append(f"  {step['id']}: {step['tool']}({args_str}){marker}")
                    directive = (
                        "\n\n⚠️ MANDATORY: Execute these steps IN ORDER before editing any code:\n"
                        + "\n".join(steps)
                        + "\n\n"
                        "RULES:\n"
                        "1. Call each tool above sequentially with the pre-filled args.\n"
                        "2. At ⛔ GATE steps, call task_gate_check. If pass=false → STOP.\n"
                        "3. Do NOT read/edit source files until all gates pass.\n"
                        "4. After completing all steps, proceed with changes."
                    )
                    result_text += directive

            send_response(id, {
                "content": [{"type": "text", "text": result_text}],
            })
        except Exception as e:
            send_error(id, -32000, str(e))

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

    elif method == "logging/setLevel":
        send_response(id, {})

    elif method in ("notifications/initialized", "notifications/cancelled"):
        pass

    else:
        send_error(id, -32601, f"Method not found: {method}")


# =============================================================================
# Backward Compatibility — re-export functions for existing tests/imports
# =============================================================================

try:
    from . import index_store as _index_store_mod
    from .index_store import (
        INDEX_DIR, load_index, load_project_map, load_content_file,
        get_symbol_content_text, TYPE_WEIGHTS, LOW_PRIORITY_PATHS,
        _load_bm25, _get_test_mapper, _get_session_store,
    )
    from .tools.search import search_by_keyword, fulltext_search
    from .tools.references import (
        find_references, impact_analysis, edit_impact_preview,
        cross_project_impact, dependency_graph,
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
    )
    from .diff_impact import impact_from_diff
except ImportError:
    import index_store as _index_store_mod
    from index_store import (
        INDEX_DIR, load_index, load_project_map, load_content_file,
        get_symbol_content_text, TYPE_WEIGHTS, LOW_PRIORITY_PATHS,
        _load_bm25, _get_test_mapper, _get_session_store,
    )
    from tools.search import search_by_keyword, fulltext_search
    from tools.references import (
        find_references, impact_analysis, edit_impact_preview,
        cross_project_impact, dependency_graph,
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
            print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32000, "message": str(e)}}), flush=True)


if __name__ == "__main__":
    main()
