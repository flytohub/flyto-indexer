"""
VSCode tool schemas (OpenAI function-calling format).

These have intentionally different descriptions with chain hints for the
VSCode agent LLM context. Only a subset of tools is exposed to VSCode.
"""

from typing import Dict, Set

from .mcp_tools import MCP_TOOLS


# Tools exposed to VSCode agent (subset of MCP_TOOLS)
_VSCODE_TOOL_NAMES: Set[str] = {
    "search_code", "get_symbol_content", "get_file_context",
    "find_references", "impact_analysis", "fulltext_search",
    "semantic_search",
    "find_test_file", "dependency_graph", "cross_project_impact",
    "find_dead_code", "find_todos", "check_index_status",
    "impact_from_diff", "batch_impact_analysis", "validate_changes",
    "git_hotspots", "git_cochange", "git_churn", "git_risk_commits",
    "coverage_report", "coverage_gaps", "untested_changes",
    "extract_type_schema", "check_api_contracts", "contract_drift",
}

# Description overrides for VSCode agent (with chain hints)
_VSCODE_DESC_OVERRIDES: Dict[str, str] = {
    "search_code": (
        "Semantic code search across 10+ indexed projects (27,400+ symbols). "
        "This is your PRIMARY search tool — ALWAYS use this instead of grep_search when "
        "looking for functions, classes, components, or any named code entity. "
        "Returns ranked results with symbol_id, file path, line number, and relevance score. "
        "Chain with: get_symbol_content (read source), get_file_context (understand structure), "
        "find_references (trace usage). Use 'project' parameter to limit search to specific project."
    ),
    "get_symbol_content": (
        "Get the full source code of a specific function, class, or component by name or ID. "
        "Use after search_code to read the actual implementation. Much more efficient than "
        "file_read when you only need one function/class. Supports fuzzy matching: you can pass "
        "just a name like 'useAuth' or a full ID. "
        "Chain with: find_references (who calls it), impact_analysis (change risk)."
    ),
    "get_file_context": (
        "Get complete structural context for a file: all symbols (functions, classes, methods), "
        "dependency graph (imports + dependents), test file mapping, and related files. "
        "MUST call before editing any file. Returns everything needed to understand a file's role. "
        "Chain with: get_symbol_content (read specific function), find_references (trace callers), "
        "impact_analysis (assess change risk)."
    ),
    "find_references": (
        "Find all places that call or import a symbol across all indexed projects. "
        "Shows callers with file path, line number, and confidence level (high/medium/low). "
        "MUST call before modifying any public function or exported API. "
        "Chain with: file_read (read callers), impact_analysis (risk assessment)."
    ),
    "impact_analysis": (
        "Analyze the blast radius of modifying a symbol. Shows all call sites with actual code lines, "
        "affected file count, and risk assessment (safe/moderate/high risk). "
        "MUST call before renaming, deleting, or changing function signatures. "
        "Chain with: find_references (detailed caller list), file_read (read affected files)."
    ),
    "fulltext_search": (
        "Full-text search inside comments, strings, and TODO/FIXME/HACK markers across all indexed code. "
        "Use when search_code doesn't find what you need (search_code matches symbol names; "
        "this searches inside code content). Use search_type='todo' for technical debt markers."
    ),
    "impact_from_diff": (
        "Parse git diff, match changed hunks to indexed symbols, classify each change "
        "(signature_change, body_change, rename), and run impact analysis. "
        "Use this to assess blast radius of uncommitted or recent changes. "
        "Chain with: edit_impact_preview (detailed call sites for high-risk symbols)."
    ),
}

# Parameter overrides for VSCode schemas (simplified for LLM)
_VSCODE_PARAM_OVERRIDES: Dict[str, dict] = {
    "search_code": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keyword. Examples: 'useAuth', 'LoginForm', 'validate', 'scoring router'"},
            "symbol_type": {"type": "string", "description": "Filter: 'function', 'class', 'method', 'component', 'composable', 'interface', 'type'"},
            "project": {"type": "string", "description": "Limit search to specific project."},
            "max_results": {"type": "integer", "description": "Max results. Default: 10"},
        },
        "required": ["query"],
    },
    "dependency_graph": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to analyze. Example: 'src/composables/useAuth.js'"},
            "direction": {"type": "string", "enum": ["imports", "dependents", "both"], "description": "Direction: 'imports', 'dependents', or 'both'"},
        },
        "required": ["path"],
    },
    "fulltext_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for. Examples: 'deprecated', 'workaround', 'FIXME', 'api/v2'"},
            "search_type": {"type": "string", "description": "What to search: 'all' (default), 'todo' (TODO/FIXME/HACK), 'comment', 'string'"},
            "max_results": {"type": "integer", "description": "Max results. Default: 30"},
        },
        "required": ["query"],
    },
}


def _mcp_to_openai(tool: dict, desc_override: str = "", param_override: dict = None) -> dict:
    """Convert a single MCP tool definition to OpenAI function-calling format."""
    params = param_override if param_override else tool.get("inputSchema", {"type": "object", "properties": {}})
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": desc_override or tool["description"],
            "parameters": params,
        },
    }


def get_vscode_tool_schemas() -> list:
    """
    Return tool definitions in OpenAI function-calling format for VSCODE_TOOLS.

    Generated from MCP_TOOLS with description and parameter overrides
    for the VSCode agent LLM context.
    """
    mcp_by_name = {t["name"]: t for t in MCP_TOOLS}
    result = []
    for name in sorted(_VSCODE_TOOL_NAMES):
        tool = mcp_by_name.get(name)
        if not tool:
            continue
        result.append(_mcp_to_openai(
            tool,
            desc_override=_VSCODE_DESC_OVERRIDES.get(name, ""),
            param_override=_VSCODE_PARAM_OVERRIDES.get(name),
        ))
    return result
