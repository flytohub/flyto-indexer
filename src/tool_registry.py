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
    "impact_from_diff",
    "batch_impact_analysis",
    "validate_changes",
    "git_hotspots",
    "git_cochange",
    "git_churn",
    "git_risk_commits",
    "coverage_report",
    "coverage_gaps",
    "untested_changes",
    "extract_type_schema",
    "check_api_contracts",
    "contract_drift",
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
        {
            "type": "function",
            "function": {
                "name": "impact_from_diff",
                "description": (
                    "Parse git diff, match changed hunks to indexed symbols, classify each change "
                    "(signature_change, body_change, rename), and run impact analysis. "
                    "Use this to assess blast radius of uncommitted or recent changes. "
                    "Chain with: edit_impact_preview (detailed call sites for high-risk symbols)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["unstaged", "staged", "committed", "branch"],
                            "description": "What to diff: unstaged (default), staged, committed (base=SHA), branch (base=branch)",
                        },
                        "base": {"type": "string", "description": "Base ref for committed/branch mode. Examples: 'HEAD~1', 'main'"},
                        "project": {"type": "string", "description": "Filter to a specific project"},
                    },
                },
            },
        },
    ]


# =============================================================================
# Lazy imports for tool dispatch
# =============================================================================

def _search():
    try:
        from .tools import search
    except ImportError:
        from tools import search
    return search


def _refs():
    try:
        from .tools import references
    except ImportError:
        from tools import references
    return references


def _info():
    try:
        from .tools import code_info
    except ImportError:
        from tools import code_info
    return code_info


def _maint():
    try:
        from .tools import maintenance
    except ImportError:
        from tools import maintenance
    return maintenance


def _quality():
    try:
        from . import quality
    except ImportError:
        import quality
    return quality


def _diff():
    try:
        from . import diff_impact
    except ImportError:
        import diff_impact
    return diff_impact


def _task():
    try:
        from .tools import task_analysis
    except ImportError:
        from tools import task_analysis
    return task_analysis


def _validation():
    try:
        from .tools import validation
    except ImportError:
        from tools import validation
    return validation


def _git_intel():
    try:
        from .tools import git_intel
    except ImportError:
        from tools import git_intel
    return git_intel


def _coverage_intel():
    try:
        from .tools import coverage_intel
    except ImportError:
        from tools import coverage_intel
    return coverage_intel


def _type_contracts():
    try:
        from .tools import type_contracts
    except ImportError:
        from tools import type_contracts
    return type_contracts


# =============================================================================
# Unified tool dispatch
# =============================================================================

def execute_tool(name: str, arguments: Dict[str, Any], _idx_module=None) -> Dict[str, Any]:
    """
    Execute an indexer tool by canonical name. Returns the tool result dict.

    This is the single dispatch point used by both mcp_server.py and the VPS bridge.
    Raises KeyError for unknown tool names.

    Args:
        _idx_module: Deprecated. Kept for backward compatibility with VPS bridge.
    """
    _DISPATCH = {
        # Search tools
        "search_code": lambda args: _search().search_by_keyword(
            query=args.get("query", ""),
            max_results=args.get("max_results", 20),
            symbol_type=args.get("symbol_type"),
            project=args.get("project"),
            include_content=args.get("include_content", False),
            session_id=args.get("session_id"),
        ),
        "fulltext_search": lambda args: _search().fulltext_search(
            query=args.get("query", ""),
            search_type=args.get("search_type", "all"),
            project=args.get("project"),
            max_results=args.get("max_results", 50),
        ),

        # Reference & impact tools
        "find_references": lambda args: _refs().find_references(
            args.get("symbol_id", ""),
        ),
        "impact_analysis": lambda args: _refs().impact_analysis(
            args.get("symbol_id", ""),
        ),
        "batch_impact_analysis": lambda args: _refs().batch_impact_analysis(
            symbol_ids=args.get("symbol_ids", []),
        ),
        "edit_impact_preview": lambda args: _refs().edit_impact_preview(
            symbol_id=args.get("symbol_id", ""),
            change_type=args.get("change_type", "modify"),
        ),
        "cross_project_impact": lambda args: _refs().cross_project_impact(
            symbol_name=args.get("symbol_name", ""),
            source_project=args.get("source_project"),
        ),
        "dependency_graph": lambda args: _refs().dependency_graph(
            file_path=args.get("file_path"),
            symbol_id=args.get("symbol_id"),
            project=args.get("project"),
            direction=args.get("direction", "both"),
            max_depth=args.get("max_depth", 2),
        ),

        # Code info tools
        "get_symbol_content": lambda args: _info().get_symbol_content(
            args.get("symbol_id", ""),
        ),
        "get_file_info": lambda args: _info().get_file_info(
            args.get("path", ""),
        ),
        "get_file_symbols": lambda args: _info().get_file_symbols(
            args.get("path", ""),
        ),
        "get_file_context": lambda args: _info().get_file_context(
            path=args.get("path", ""),
            include_content=args.get("include_content", False),
        ),
        "list_categories": lambda args: _info().list_categories(),
        "list_apis": lambda args: _info().list_apis(),
        "list_projects": lambda args: _info().list_projects(),
        "get_description": lambda args: _info().get_description(
            path=args.get("path", ""),
            project=args.get("project"),
        ),
        "update_description": lambda args: _info().update_description(
            path=args.get("path", ""),
            summary=args.get("summary", ""),
            project=args.get("project"),
        ),
        "find_test_file": lambda args: _info().find_test_file(
            path=args.get("path", ""),
        ),

        # Maintenance tools
        "find_dead_code": lambda args: _maint().find_dead_code(
            project=args.get("project"),
            symbol_type=args.get("symbol_type"),
            min_lines=args.get("min_lines", 5),
        ),
        "find_todos": lambda args: _maint().find_todos(
            project=args.get("project"),
            priority=args.get("priority"),
            max_results=args.get("max_results", 100),
        ),
        "check_index_status": lambda args: _maint().check_index_status(),
        "check_and_reindex": lambda args: _maint().check_and_reindex(
            dry_run=args.get("dry_run", True),
            project=args.get("project"),
            auto_reindex=args.get("auto_reindex", False),
        ),
        "session_track": lambda args: _maint().session_track(
            session_id=args.get("session_id", ""),
            event_type=args.get("event_type", ""),
            target=args.get("target", ""),
            workspace_root=args.get("workspace_root", ""),
        ),
        "session_get": lambda args: _maint().session_get(
            session_id=args.get("session_id", ""),
        ),

        # Code quality tools (from quality.py)
        "find_complex_functions": lambda args: _quality().find_complex_functions(
            project=args.get("project"),
            max_results=args.get("max_results", 20),
            min_score=args.get("min_score", 1),
        ),
        "find_duplicates": lambda args: _quality().find_duplicates(
            project=args.get("project"),
            min_lines=args.get("min_lines", 6),
            max_results=args.get("max_results", 20),
        ),
        "security_scan": lambda args: _quality().security_scan(
            project=args.get("project"),
            severity=args.get("severity"),
            max_results=args.get("max_results", 50),
        ),
        "find_stale_files": lambda args: _quality().find_stale_files(
            project=args.get("project"),
            stale_days=args.get("stale_days", 180),
            max_results=args.get("max_results", 30),
        ),
        "code_health_score": lambda args: _quality().code_health_score(
            project=args.get("project"),
        ),
        "suggest_refactoring": lambda args: _quality().suggest_refactoring(
            project=args.get("project"),
            max_results=args.get("max_results", 20),
        ),

        # Diff impact tools (from diff_impact.py)
        "impact_from_diff": lambda args: _diff().impact_from_diff(
            mode=args.get("mode", "unstaged"),
            base=args.get("base", ""),
            project=args.get("project"),
        ),

        # Task analysis tools
        "analyze_task": lambda args: _task().analyze_task(
            description=args.get("description", ""),
            targets=args.get("targets", []),
            intent=args.get("intent", "refactor"),
            project=args.get("project"),
            options=args.get("options"),
        ),
        "task_gate_check": lambda args: _task().task_gate_check(
            task_contract=args.get("task_contract", {}),
            next_phase=args.get("next_phase"),
            current_state=args.get("current_state", {}),
        ),

        # Validation tools
        "validate_changes": lambda args: _validation().validate_changes(
            project=args.get("project"),
            run_tests=args.get("run_tests", True),
            test_path=args.get("test_path"),
        ),

        # Git intelligence tools
        "git_hotspots": lambda args: _git_intel().git_hotspots(
            project=args.get("project"),
            max_results=args.get("max_results", 20),
        ),
        "git_cochange": lambda args: _git_intel().git_cochange(
            path=args.get("path", ""),
            project=args.get("project"),
            max_results=args.get("max_results", 10),
        ),
        "git_churn": lambda args: _git_intel().git_churn(
            path=args.get("path"),
            project=args.get("project"),
            days=args.get("days", 90),
        ),
        "git_risk_commits": lambda args: _git_intel().git_risk_commits(
            project=args.get("project"),
            days=args.get("days", 30),
            max_results=args.get("max_results", 15),
        ),

        # Coverage intelligence tools
        "coverage_report": lambda args: _coverage_intel().coverage_report(
            project=args.get("project"),
            min_coverage=args.get("min_coverage"),
        ),
        "coverage_gaps": lambda args: _coverage_intel().coverage_gaps(
            project=args.get("project"),
            max_results=args.get("max_results", 20),
        ),
        "untested_changes": lambda args: _coverage_intel().untested_changes(
            project=args.get("project"),
            mode=args.get("mode", "unstaged"),
        ),

        # Type contract tools
        "extract_type_schema": lambda args: _type_contracts().extract_type_schema(
            symbol_id=args.get("symbol_id", ""),
        ),
        "check_api_contracts": lambda args: _type_contracts().check_api_contracts(
            source_project=args.get("source_project"),
            consumer_project=args.get("consumer_project"),
        ),
        "contract_drift": lambda args: _type_contracts().contract_drift(
            project=args.get("project"),
        ),
    }

    handler = _DISPATCH.get(name)
    if handler is None:
        raise KeyError(f"Unknown tool: {name}")
    return handler(arguments)
