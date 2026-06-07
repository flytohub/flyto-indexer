"""
Unified tool dispatch — single entry point for all tool execution.
"""

import os
from pathlib import Path as _P
from typing import Any, Dict, Set

from .lazy_imports import (
    _search, _refs, _info, _maint, _quality, _diff, _task,
    _validation, _git_intel, _coverage_intel, _type_contracts,
    _dep_scanner, _profile, _secret_scanner, _license_scanner,
    _doc_scanner, _verify, _pr_analyzer, _framework_detector, _smart,
    _layers_mod, _taint_dsl_mod,
)


def _call_hierarchy_dispatch(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch call_hierarchy tool — resolves file/line/col -> LSP edges."""
    try:
        from ..lsp.call_graph import incoming_calls, outgoing_calls
    except ImportError:
        from lsp.call_graph import incoming_calls, outgoing_calls

    project_root = _P(args.get("project") or os.getcwd())
    path = args.get("path", "")
    source_file = _P(path)
    if not source_file.is_absolute():
        source_file = project_root / source_file
    line = int(args.get("line", 1)) - 1  # 1-based -> 0-based
    col = int(args.get("column", 0))
    direction = args.get("direction", "incoming")
    max_depth = min(int(args.get("max_depth", 2)), 5)

    fn = incoming_calls if direction == "incoming" else outgoing_calls
    edges = fn(project_root, source_file, line, col, max_depth=max_depth)
    return {
        "direction": direction,
        "max_depth": max_depth,
        "source_file": str(source_file),
        "source_line": line + 1,
        "edge_count": len(edges),
        "edges": [
            {
                "from_file": e.from_file, "from_name": e.from_name, "from_line": e.from_line,
                "to_file": e.to_file, "to_name": e.to_name, "to_line": e.to_line,
                "depth": e.depth,
            }
            for e in edges
        ],
    }


# Single source of truth for dispatch-table membership.
_TOOL_NAMES = frozenset({
    "search_code", "fulltext_search", "semantic_search",
    "find_references", "impact_analysis", "batch_impact_analysis",
    "edit_impact_preview", "cross_project_impact", "dependency_graph",
    "get_symbol_content", "get_file_info", "get_file_symbols",
    "get_file_context", "list_categories", "list_apis", "list_projects",
    "get_description", "update_description", "find_test_file",
    "find_dead_code", "find_todos", "check_index_status",
    "check_and_reindex", "session_track", "session_get",
    "find_complex_functions", "find_duplicates", "security_scan",
    "analyze_data_flow", "find_stale_files", "code_health_score",
    "suggest_refactoring", "impact_from_diff",
    "analyze_task", "task_gate_check", "validate_changes",
    "git_hotspots", "git_cochange", "git_churn", "git_risk_commits",
    "coverage_report", "coverage_gaps", "untested_changes",
    "extract_type_schema", "check_api_contracts", "contract_drift",
    "list_dependencies",
    # Smart tools (consolidated)
    "search", "impact", "audit", "task", "structure", "verify", "verify_workspace",
    "scan_secrets", "scan_licenses", "scan_documentation",
    "project_profile", "analyze_pr_risk", "detect_frameworks",
    "check_layers", "call_hierarchy",
    "add_layer", "add_taint_source", "add_taint_sink",
    "add_taint_sanitizer", "list_taint_rules",
})


def has_tool(name: str) -> bool:
    """Return True when `name` is a known dispatch entry."""
    return name in _TOOL_NAMES


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
        "semantic_search": lambda args: _search().semantic_search(
            query=args.get("query", ""),
            project=args.get("project"),
            max_results=args.get("max_results", 20),
            include_content=args.get("include_content", False),
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
        "analyze_data_flow": lambda args: _quality().analyze_data_flow(
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

        # Dependency scanner
        "list_dependencies": lambda args: _dep_scanner().scan_dependencies(
            args.get("path", os.getcwd()),
        ).to_dict(),

        # Smart tools (consolidated entry points)
        "search": lambda args: _smart().smart_search(
            query=args.get("query", ""),
            project=args.get("project"),
            include_content=args.get("include_content", False),
        ),
        "impact": lambda args: _smart().smart_impact(
            target=args.get("target"),
            mode=args.get("mode"),
            change_type=args.get("change_type", "modify"),
            project=args.get("project"),
        ),
        "audit": lambda args: _smart().smart_audit(
            project=args.get("project"),
            focus=args.get("focus"),
        ),
        "task": lambda args: _smart().smart_task(
            action=args.get("action", "plan"),
            description=args.get("description", ""),
            targets=args.get("targets"),
            intent=args.get("intent", "refactor"),
            task_contract=args.get("task_contract"),
            next_phase=args.get("next_phase"),
            current_state=args.get("current_state"),
            project=args.get("project"),
            run_tests=args.get("run_tests", True),
            test_path=args.get("test_path"),
        ),
        "structure": lambda args: _smart().smart_structure(
            project=args.get("project"),
            focus=args.get("focus"),
            symbol_id=args.get("symbol_id"),
            path=args.get("path"),
        ),
        "verify": lambda args: _verify().run_verification(
            project_path=args.get("path") or os.getcwd(),
            full_scan=args.get("full_scan", False),
            query=args.get("query"),
            symbol=args.get("symbol"),
            strict=args.get("strict", False),
            baseline_path=args.get("baseline"),
            regression_only=args.get("regression_only", False),
            policy_path=args.get("policy"),
        ),
        "verify_workspace": lambda args: _verify().run_workspace_verification(
            workspace_path=args.get("path") or os.getcwd(),
            project_paths=args.get("projects"),
            full_scan=args.get("full_scan", False),
            strict=args.get("strict", False),
            baseline_dir=args.get("baseline_dir"),
            regression_only=args.get("regression_only", False),
            changed_only=args.get("changed_only", False),
            base=args.get("base", ""),
            policy_path=args.get("policy"),
        ),

        # Analysis scanners
        "scan_secrets": lambda args: (lambda r: {
            "total_files_scanned": r.total_files_scanned,
            "total_findings": r.total_findings,
            "critical": r.critical,
            "high": r.high,
            "medium": r.medium,
            "findings": [{"file": f.file, "line": f.line, "pattern": f.pattern,
                          "severity": f.severity, "masked_value": f.masked_value}
                         for f in r.findings],
        })(_secret_scanner().scan_secrets(
            __import__("pathlib").Path(args.get("path") or os.getcwd()),
        )),
        "scan_licenses": lambda args: (lambda r: {
            "project_license": r.project_license,
            "project_license_file": r.project_license_file,
            "dependency_licenses": r.dependency_licenses,
            "copyleft_warning": r.copyleft_warning,
            "dependencies_without_license": r.dependencies_without_license,
        })(_license_scanner().scan_licenses(
            __import__("pathlib").Path(args.get("path") or os.getcwd()),
        )),
        "scan_documentation": lambda args: (lambda r: {
            "overall_score": r.overall_score,
            "readme_score": r.readme_score,
            "readme_sections": r.readme_sections,
            "api_doc_coverage": r.api_doc_coverage,
            "module_doc_coverage": r.module_doc_coverage,
            "inline_doc_coverage": r.inline_doc_coverage,
            "has_env_example": r.has_env_example,
            "has_changelog": r.has_changelog,
            "has_contributing": r.has_contributing,
            "suggestions": r.suggestions,
        })(_doc_scanner().scan_documentation(
            __import__("pathlib").Path(args.get("path") or os.getcwd()),
        )),

        # Project profile
        "project_profile": lambda args: _profile().build_project_profile(
            project_path=__import__("pathlib").Path(args.get("path") or os.getcwd()),
            compact=args.get("compact", False),
        ),

        # PR risk analysis
        "analyze_pr_risk": lambda args: _pr_analyzer().analyze_pr_risk(
            project_path=args.get("path") or os.getcwd(),
            base=args.get("base", ""),
            staged=args.get("staged", False),
        ).to_dict(),

        # Framework detection
        "detect_frameworks": lambda args: [
            fw.to_dict() for fw in _framework_detector().detect_frameworks(
                __import__("pathlib").Path(args.get("path") or os.getcwd()),
            )
        ],

        # Architecture layer rules
        "check_layers": lambda args: _layers_mod().check_layers_dict(
            __import__("pathlib").Path(args.get("path") or os.getcwd()),
        ),

        # LSP call hierarchy (Phase 3)
        "call_hierarchy": lambda args: _call_hierarchy_dispatch(args),
        "add_layer": lambda args: _layers_mod().add_layer(
            project_root=__import__("pathlib").Path(args.get("path") or os.getcwd()),
            name=args.get("name", ""),
            paths=args.get("paths", []),
            can_import=args.get("can_import"),
            cannot_import=args.get("cannot_import"),
            reason=args.get("reason"),
        ),

        # Taint DSL
        "add_taint_source": lambda args: _taint_dsl_mod().add_taint_source(
            project_root=__import__("pathlib").Path(args.get("path") or os.getcwd()),
            pattern=args.get("pattern", ""),
            language=args.get("language", "python"),
            taint_type=args.get("taint_type"),
        ),
        "add_taint_sink": lambda args: _taint_dsl_mod().add_taint_sink(
            project_root=__import__("pathlib").Path(args.get("path") or os.getcwd()),
            pattern=args.get("pattern", ""),
            vuln_type=args.get("vuln_type", "custom"),
            severity=args.get("severity", "high"),
            recommendation=args.get("recommendation", ""),
        ),
        "add_taint_sanitizer": lambda args: _taint_dsl_mod().add_taint_sanitizer(
            project_root=__import__("pathlib").Path(args.get("path") or os.getcwd()),
            pattern=args.get("pattern", ""),
            cleanses=args.get("cleanses"),
        ),
        "list_taint_rules": lambda args: _taint_dsl_mod().list_taint_rules(
            project_root=__import__("pathlib").Path(args.get("path") or os.getcwd()),
        ),
    }

    handler = _DISPATCH.get(name)
    if handler is None:
        raise KeyError(f"Unknown tool: {name}")
    return handler(arguments)
