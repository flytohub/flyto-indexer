"""
Tool Registry — Single source of truth for flyto-indexer tool names and dispatch.

Used by:
  - mcp_server.py: MCP protocol dispatch
  - flyto-pro vscode.py: VPS bridge tool execution
  - flyto-vscode ChatHandler.ts: tool routing (names must match)

All canonical tool names are defined here. VPS and extension MUST use these names.
"""

from typing import Any, Dict, Set

# =============================================================================
# Canonical tool names that VPS bridges to the Extension
# =============================================================================

INDEXER_TOOL_NAMES: Set[str] = {
    "search_code",
    "get_symbol_content",
    "get_file_context",
    "impact_analysis",
    "find_test_file",
    "find_references",
    "fulltext_search",
    "dependency_graph",
    "cross_project_impact",
    "find_dead_code",
    "find_todos",
    "check_index_status",
}


def get_vscode_tool_schemas() -> list:
    """
    Return tool definitions in OpenAI function-calling format for VSCODE_TOOLS.

    These are the indexer tools as seen by the LLM in the VSCode agent loop.
    Descriptions include chain hints for the AI to use tools effectively.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": (
                    "Semantic code search across 10+ indexed projects (27,400+ symbols). "
                    "This is your PRIMARY search tool — ALWAYS use this instead of grep_search when "
                    "looking for functions, classes, components, or any named code entity. "
                    "Returns ranked results with symbol_id, file path, line number, and relevance score. "
                    "Chain with: get_symbol_content (read source), get_file_context (understand structure), "
                    "find_references (trace usage). Use 'project' parameter to limit search to specific project."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword. Examples: 'useAuth', 'LoginForm', 'validate', 'scoring router'"},
                        "symbol_type": {"type": "string", "description": "Filter: 'function', 'class', 'method', 'component', 'composable', 'interface', 'type'"},
                        "project": {"type": "string", "description": "Limit search to specific project."},
                        "max_results": {"type": "integer", "description": "Max results. Default: 10"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_symbol_content",
                "description": (
                    "Get the full source code of a specific function, class, or component by name or ID. "
                    "Use after search_code to read the actual implementation. Much more efficient than "
                    "file_read when you only need one function/class. Supports fuzzy matching: you can pass "
                    "just a name like 'useAuth' or a full ID. "
                    "Chain with: find_references (who calls it), impact_analysis (change risk)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol_id": {"type": "string", "description": "Symbol name or full ID. Examples: 'useToast', 'BrowserAgent', 'flyto-pro:src/agent.py:class:Agent'"},
                    },
                    "required": ["symbol_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_file_context",
                "description": (
                    "Get complete structural context for a file: all symbols (functions, classes, methods), "
                    "dependency graph (imports + dependents), test file mapping, and related files. "
                    "MUST call before editing any file. Returns everything needed to understand a file's role. "
                    "Chain with: get_symbol_content (read specific function), find_references (trace callers), "
                    "impact_analysis (assess change risk)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to project root. Example: 'src/api/auth.py'"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_references",
                "description": (
                    "Find all places that call or import a symbol across all indexed projects. "
                    "Shows callers with file path, line number, and confidence level (high/medium/low). "
                    "MUST call before modifying any public function or exported API. "
                    "Chain with: file_read (read callers), impact_analysis (risk assessment)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol_id": {"type": "string", "description": "Symbol name or ID. Examples: 'handleLogin', 'useToast', 'UserService'"},
                    },
                    "required": ["symbol_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "impact_analysis",
                "description": (
                    "Analyze the blast radius of modifying a symbol. Shows all call sites with actual code lines, "
                    "affected file count, and risk assessment (safe/moderate/high risk). "
                    "MUST call before renaming, deleting, or changing function signatures. "
                    "Chain with: find_references (detailed caller list), file_read (read affected files)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol_id": {"type": "string", "description": "Symbol name or ID. Examples: 'handleLogin', 'UserService', 'useToast'"},
                    },
                    "required": ["symbol_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fulltext_search",
                "description": (
                    "Full-text search inside comments, strings, and TODO/FIXME/HACK markers across all indexed code. "
                    "Use when search_code doesn't find what you need (search_code matches symbol names; "
                    "this searches inside code content). Use search_type='todo' for technical debt markers."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Text to search for. Examples: 'deprecated', 'workaround', 'FIXME', 'api/v2'"},
                        "search_type": {"type": "string", "description": "What to search: 'all' (default), 'todo' (TODO/FIXME/HACK), 'comment', 'string'"},
                        "max_results": {"type": "integer", "description": "Max results. Default: 30"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_test_file",
                "description": "Find the test file for a source file. Returns test path so you can run tests after editing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Source file path. Example: 'src/utils/validator.py'"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dependency_graph",
                "description": (
                    "Get the dependency graph for a file or symbol. Shows what a module imports and what imports it. "
                    "Use this to understand how components are connected across the codebase."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to analyze. Example: 'src/composables/useAuth.js'"},
                        "direction": {"type": "string", "enum": ["imports", "dependents", "both"], "description": "Direction: 'imports', 'dependents', or 'both'"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cross_project_impact",
                "description": "Track cross-project API usage. Find all other projects that use a specific function/class. Essential before changing shared APIs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol_name": {"type": "string", "description": "Symbol name to track. Example: 'useModuleSchema', 'ValidationError'"},
                    },
                    "required": ["symbol_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_dead_code",
                "description": "Find unreferenced functions, classes, and components (dead code). These symbols are never imported or called by any other code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Filter to a specific project. Example: 'flyto-pro'"},
                        "symbol_type": {"type": "string", "enum": ["function", "method", "composable", "component", "class"], "description": "Filter to a specific symbol type"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_todos",
                "description": "Find all TODO, FIXME, HACK, and XXX markers across indexed code. Use this to track technical debt.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Filter to a specific project"},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "Filter by priority: high (FIXME/HACK), medium (TODO/XXX), low (NOTE)"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_index_status",
                "description": "Check if the code index is up-to-date or stale. Returns status (fresh/stale) and list of changed files.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
    ]


# =============================================================================
# Unified tool dispatch
# =============================================================================

def execute_tool(name: str, arguments: Dict[str, Any], _idx_module=None) -> Dict[str, Any]:
    """
    Execute an indexer tool by canonical name. Returns the tool result dict.

    This is the single dispatch point used by both mcp_server.py and the VPS bridge.
    Raises KeyError for unknown tool names.

    Args:
        _idx_module: Optional pre-loaded mcp_server module (for VPS bridge which loads
                     mcp_server via importlib). If None, uses relative import.
    """
    if _idx_module is not None:
        _idx = _idx_module
    else:
        # Lazy import — works when running as package (python -m src.mcp_server)
        try:
            from . import mcp_server as _idx
        except ImportError:
            import mcp_server as _idx  # type: ignore[no-redef]

    _DISPATCH = {
        "search_code": lambda args: _idx.search_by_keyword(
            query=args.get("query", ""),
            max_results=args.get("max_results", 20),
            symbol_type=args.get("symbol_type"),
            project=args.get("project"),
            include_content=args.get("include_content", False),
            session_id=args.get("session_id"),
        ),
        "get_symbol_content": lambda args: _idx.get_symbol_content(
            args.get("symbol_id", ""),
        ),
        "get_file_info": lambda args: _idx.get_file_info(
            args.get("path", ""),
        ),
        "get_file_symbols": lambda args: _idx.get_file_symbols(
            args.get("path", ""),
        ),
        "impact_analysis": lambda args: _idx.impact_analysis(
            args.get("symbol_id", ""),
        ),
        "list_categories": lambda args: _idx.list_categories(),
        "list_apis": lambda args: _idx.list_apis(),
        "list_projects": lambda args: _idx.list_projects(),
        "find_references": lambda args: _idx.find_references(
            args.get("symbol_id", ""),
        ),
        "dependency_graph": lambda args: _idx.dependency_graph(
            file_path=args.get("file_path"),
            symbol_id=args.get("symbol_id"),
            project=args.get("project"),
            direction=args.get("direction", "both"),
            max_depth=args.get("max_depth", 2),
        ),
        "fulltext_search": lambda args: _idx.fulltext_search(
            query=args.get("query", ""),
            search_type=args.get("search_type", "all"),
            project=args.get("project"),
            max_results=args.get("max_results", 50),
        ),
        "check_index_status": lambda args: _idx.check_index_status(),
        "find_dead_code": lambda args: _idx.find_dead_code(
            project=args.get("project"),
            symbol_type=args.get("symbol_type"),
            min_lines=args.get("min_lines", 5),
        ),
        "find_todos": lambda args: _idx.find_todos(
            project=args.get("project"),
            priority=args.get("priority"),
            max_results=args.get("max_results", 100),
        ),
        "cross_project_impact": lambda args: _idx.cross_project_impact(
            symbol_name=args.get("symbol_name", ""),
            source_project=args.get("source_project"),
        ),
        "get_description": lambda args: _idx.get_description(
            path=args.get("path", ""),
            project=args.get("project"),
        ),
        "update_description": lambda args: _idx.update_description(
            path=args.get("path", ""),
            summary=args.get("summary", ""),
            project=args.get("project"),
        ),
        "get_file_context": lambda args: _idx.get_file_context(
            path=args.get("path", ""),
            include_content=args.get("include_content", False),
        ),
        "find_test_file": lambda args: _idx.find_test_file(
            path=args.get("path", ""),
        ),
        "edit_impact_preview": lambda args: _idx.edit_impact_preview(
            symbol_id=args.get("symbol_id", ""),
            change_type=args.get("change_type", "modify"),
        ),
        "check_and_reindex": lambda args: _idx.check_and_reindex(
            dry_run=args.get("dry_run", True),
            project=args.get("project"),
        ),
        "session_track": lambda args: _idx.session_track(
            session_id=args.get("session_id", ""),
            event_type=args.get("event_type", ""),
            target=args.get("target", ""),
            workspace_root=args.get("workspace_root", ""),
        ),
        "session_get": lambda args: _idx.session_get(
            session_id=args.get("session_id", ""),
        ),
    }

    handler = _DISPATCH.get(name)
    if handler is None:
        raise KeyError(f"Unknown tool: {name}")
    return handler(arguments)
