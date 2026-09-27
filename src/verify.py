"""
Self-contained verification gate for flyto-indexer.

This module intentionally uses only stdlib + flyto-indexer internals. It is the
CLI/CI entry point an AI agent can run after code edits to prove the index,
impact graph, context lookup, and lightweight security scans still close.
"""

from __future__ import annotations

import ast
import json
import fnmatch
import hashlib
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .doc_scanner import scan_documentation
from .engine import IndexEngine
from .finding_identity import stable_finding_id
from .models import SymbolType
from .secret_scanner import scan_secrets
from .verification.regression import (
    _STATUS_RANK,
    _VERIFY_RESULT_SCHEMA_VERSION,
    _baseline_integrity,
    _check_regression_gate,
    _find_quality_metric_regressions,
    _find_status_regressions,
    _load_baseline,
    _nested_metric,
)
from .verification.reporting import (
    _finding_ids_from_check,
    _flatten_report_checks,
    _render_junit_report,
    _render_markdown_report,
    _render_sarif_report,
    format_verification,
    format_workspace_verification,
    render_report,
)
from .verification.support import (
    _CI_CANDIDATES,
    _PROJECT_MARKERS,
    _SKIP_WORKSPACE_DIRS,
    _checks_fingerprint,
    _decode_process_output,
    _discover_workspace_projects,
    _finalize,
    _git_changed_paths,
    _git_head,
    _load_index_json,
    _load_json_file,
    _looks_like_project,
    _project_has_changes,
    _read_ci_files,
    _summarize_checks,
    _verification_metadata,
)
from .verification.contracts import (
    _CONTRACT_SOURCE_EXTENSIONS,
    _FRONTEND_CONTRACT_SOURCE_EXTENSIONS,
    _FRONTEND_INDEX_SYMBOL_TYPES,
    _CONTRACT_SKIP_PARTS,
    _CONTRACT_SKIP_SUFFIXES,
    _CONTRACT_SURFACE_TERMS,
    _SINGLE_PROJECT_ISLAND_TYPES,
    _SINGLE_PROJECT_PRODUCT_PARTS,
    _SINGLE_PROJECT_FEATURE_PARTS,
    _SINGLE_PROJECT_ENTRY_PARTS,
    _SINGLE_PROJECT_ENTRY_NAMES,
    _PRODUCT_LOOP_SURFACES,
    _PRODUCT_LOOP_EVIDENCE_PARTS,
    _PRODUCT_LOOP_EVIDENCE_SUFFIXES,
    _DYNAMIC_VALIDATION_GUARDS,
    _RECIPE_ASSERTION_KINDS,
    _OPENAPI_METHODS,
    _OPENAPI_SKIP_DIRS,
    _check_single_project_islands,
    _collect_source_name_references,
    _extract_single_project_api_contract,
    _extract_openapi_api_contracts,
    _iter_openapi_contract_files,
    _extract_openapi_json_api_contracts,
    _extract_openapi_yaml_api_contracts,
    _openapi_contract_item,
    _match_single_project_api_calls,
    _check_cross_project_contract,
    _check_product_loop_closure,
    _collect_project_product_loop_signals,
    _workspace_unmatched_api_calls_by_surface,
    _iter_product_loop_files,
    _product_loop_file_kind,
    _empty_product_loops,
    _merge_product_loops,
    _add_product_loop_signal,
    _product_loop_is_active,
    _product_loop_counts,
    _classify_product_surfaces,
    _check_dynamic_validation_plan,
    _collect_dynamic_validation_project,
    _empty_dynamic_surface_report,
    _invalid_navbar_smoke_route,
    _recipe_has_dynamic_steps,
    _recipe_assertions_machine_checkable,
    _string_list,
    _looks_like_frontend,
    _looks_like_backend,
    _project_has_index_symbol_types,
    _has_dynamic_validation_contract,
    _extract_frontend_contract_signals,
    _extract_backend_contract_signals,
    _extract_backend_routes_from_index,
    _extract_api_calls_from_text,
    _iter_contract_source_files,
    _infer_http_method,
    _strip_url_to_api_path,
    _is_product_api_path,
    _normalize_api_path,
    _dedupe_contract_items,
    _count_surface_terms,
    _merge_term_counts,
    _contract_samples,
    _symbol_value,
    _symbol_path,
    _symbol_type,
    _symbol_name,
    _symbol_metadata,
    _symbol_ref_count,
    _dep_value,
    _dep_type,
    _dep_metadata,
    _symbol_id_path,
    _is_single_project_candidate,
    _has_single_project_feature_signal,
    _has_product_surface_signal,
    _is_single_project_entry,
    _is_contract_skipped_path,
)
from .verification.hygiene import (
    _CI_COMMENT_RE,
    _CI_COMMAND_PREFIX_RE,
    _CI_SEGMENT_SPLIT_RE,
    _CI_STDLIB_COMMANDS,
    _CI_VERIFY_SCRIPT_RE,
    _GENERATED_CHANGE_PATTERNS,
    _SOURCE_OWNED_CHANGE_PATHS,
    _HIGH_RISK_CHANGE_PATTERNS,
    _HIGH_RISK_CHANGE_EXEMPTIONS,
    _UNVERSIONED_SPEC_PREFIXES,
    _HASH,
    _TS_SUFFIXES,
    _PY_SUFFIXES,
    _BLANKET_SUPPRESSIONS,
    _SUPPRESSION_HEADER_LINES,
    _SUPPRESSION_MIN_FILES,
    _SUPPRESSION_MIN_RATIO,
    _SUPPRESSION_MATERIAL_RATIO,
    _REQUIRED_ENV_READS,
    _ENV_DECLARATION_RE,
    _ENV_SAMPLE_PARTS,
    _check_runtime_dependencies,
    _check_rules_policy,
    _check_weak_scanners,
    _check_no_external_runtime,
    _check_package_integrity,
    _ci_command_segments,
    _ci_runs_command,
    _ci_invokes_verify_script,
    _check_ci_closed_loop,
    _check_change_hygiene,
    _python_mcp_entry_points,
    _check_mcp_runtime_smoke,
    _breaking_version_axis,
    _check_dependency_drift,
    _quality_tool_is_configured,
    _file_opens_with_blanket_pragma,
    _check_suppression_drift,
    _declared_env_names,
    _check_env_contract,
    _check_agent_hygiene,
    _check_policy_budget,
    _check_quality_health,
    _check_mcp_registry,
    _generated_index_is_ignored,
    _read_package_scripts,
    _pyproject_name,
    _package_manifest_entries,
    _matches_any,
    _is_generated_change_path,
    _is_high_risk_change_path,
    _load_verify_policy,
    _parse_verify_yaml_block,
    _parse_policy_scalar,
    _as_list,
    _as_int,
)


def _invalidate_index_store_caches() -> None:
    try:
        from . import index_store
    except ImportError:
        import index_store  # type: ignore[no-redef]
    index_store.invalidate_caches()


# A standard-library Python repository closes its CI loop with conventional
# offline commands instead of third-party tools. These are matched only at
# command position — the start of a line or of a shell segment, after YAML
# `run:`/list decoration is removed — so a comment, a `name:` value, or any
# other prose that merely mentions the command cannot satisfy the check.
# The trailing guard keeps a similarly named neighbour such as
# `verify.sh.bak` from claiming the `verify.sh` this repository owns.
# Exact repository-relative paths that look generated but are source-owned
# policy. `.flyto/coding.yaml` is the committed `flyto.coding-config.v1`
# verification contract read by `flyto-ai code-mcp`; every other `.flyto/`
# path (indexes, navs, tags, runs, logs) stays generated runtime state.


def run_verification(
    project_path: str | Path,
    *,
    full_scan: bool = False,
    query: str | None = None,
    symbol: str | None = None,
    strict: bool = False,
    baseline_path: str | Path | None = None,
    regression_only: bool = False,
    policy_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the no-external-dependency verification suite."""
    root = Path(project_path).resolve()
    checks: list[dict[str, Any]] = []
    pass_override: bool | None = None

    def add_check(
        name: str,
        status: str,
        summary: str,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        if strict and status == "warn":
            status = "fail"
        checks.append({
            "name": name,
            "status": status,
            "summary": summary,
            "metrics": metrics or {},
        })

    if not root.exists():
        add_check("project_path", "fail", f"Path does not exist: {root}")
        return _finalize(root, checks)

    _check_runtime_dependencies(root, add_check)

    project_name = root.name
    engine = IndexEngine(project_name, root)
    index_path = root / ".flyto-index" / "index.json"
    if full_scan or not index_path.exists():
        scan_result = engine.scan(incremental=not full_scan)
        _invalidate_index_store_caches()
        add_check(
            "scan",
            "pass" if scan_result.get("errors", 0) == 0 else "warn",
            "Index scan completed",
            metrics=scan_result,
        )
        engine = IndexEngine(project_name, root)
    else:
        add_check("scan", "pass", "Existing .flyto-index loaded; scan not requested")

    _check_index_integrity(engine, add_check)
    _check_quality_health(root, add_check)
    _check_single_project_islands(engine, add_check)
    _check_context_loop(engine, query, add_check)
    _check_impact_loop(engine, symbol, add_check)
    _check_weak_scanners(root, add_check)
    _check_rules_policy(root, add_check)
    _check_no_external_runtime(root, add_check)
    _check_package_integrity(root, add_check)
    _check_ci_closed_loop(root, add_check)
    _check_change_hygiene(root, add_check)
    _check_mcp_registry(root, add_check)
    _check_mcp_runtime_smoke(root, add_check)
    _check_suppression_drift(root, add_check)
    _check_env_contract(root, add_check)
    _check_agent_hygiene(root, add_check)
    _check_policy_budget(root, checks, policy_path)

    if baseline_path is not None:
        pass_override = _check_regression_gate(root, checks, Path(baseline_path), regression_only)

    return _finalize(root, checks, pass_override=pass_override)


def run_workspace_verification(
    workspace_path: str | Path = ".",
    *,
    project_paths: list[str | Path] | None = None,
    full_scan: bool = False,
    strict: bool = False,
    baseline_dir: str | Path | None = None,
    regression_only: bool = False,
    changed_only: bool = False,
    base: str = "",
    policy_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run verification across multiple projects and aggregate the result."""
    root = Path(workspace_path).resolve()
    projects = [
        Path(path).resolve()
        for path in (project_paths or _discover_workspace_projects(root))
    ]

    baseline_root = Path(baseline_dir).resolve() if baseline_dir else None
    results: list[dict[str, Any]] = []
    skipped_projects: list[str] = []
    verified_projects: list[Path] = []
    for project in projects:
        if changed_only and not _project_has_changes(project, base):
            skipped_projects.append(str(project))
            continue
        verified_projects.append(project)
        baseline_path = baseline_root / f"{project.name}.json" if baseline_root else None
        results.append(run_verification(
            project,
            full_scan=full_scan,
            strict=strict,
            baseline_path=baseline_path,
            regression_only=regression_only,
            policy_path=policy_path,
        ))

    summary = {
        "projects": len(results),
        "skipped": len(skipped_projects),
        "pass": sum(1 for result in results if result["pass"] and result["summary"].get("warn", 0) == 0),
        "warn": sum(1 for result in results if result["pass"] and result["summary"].get("warn", 0) > 0),
        "fail": sum(1 for result in results if not result["pass"]),
    }
    workspace_checks: list[dict[str, Any]] = []
    workspace_projects = verified_projects if changed_only else projects
    _check_cross_project_contract(workspace_projects, workspace_checks)
    _check_product_loop_closure(workspace_projects, workspace_checks)
    _check_dynamic_validation_plan(workspace_projects, workspace_checks)
    _check_dependency_drift(workspace_projects, workspace_checks)
    workspace_summary = _summarize_checks(workspace_checks)
    summary["workspace_checks"] = len(workspace_checks)
    summary["workspace_warn"] = workspace_summary.get("warn", 0)
    summary["workspace_fail"] = workspace_summary.get("fail", 0)
    return {
        "workspace": root.name,
        "path": str(root),
        "pass": summary["fail"] == 0 and summary["workspace_fail"] == 0,
        "summary": summary,
        "workspace_checks": workspace_checks,
        "skipped_projects": skipped_projects,
        "projects": results,
    }










def _check_index_integrity(engine: IndexEngine, add_check) -> None:
    index = engine.index
    files = index.files
    symbols = index.symbols
    dependencies = index.dependencies
    reverse_index = index.reverse_index or {}

    if files and not symbols:
        add_check("index_integrity", "fail", "Files exist but no symbols were indexed")
        return

    file_symbol_missing = []
    for path in files:
        expected_id = f"{index.project}:{path}:file:{Path(path).stem}"
        if expected_id not in symbols:
            file_symbol_missing.append(path)

    reverse_targets_missing = [sid for sid in reverse_index if sid not in symbols]
    reverse_callers_missing = []
    for callers in reverse_index.values():
        for caller in callers:
            if caller not in symbols:
                reverse_callers_missing.append(caller)

    status = "pass"
    summary = "Index graph is internally connected"
    if file_symbol_missing:
        status = "fail"
        summary = "Some indexed files do not have file-level symbols"
    elif reverse_targets_missing or reverse_callers_missing:
        status = "warn"
        summary = "Reverse index has unresolved IDs"

    add_check(
        "index_integrity",
        status,
        summary,
        metrics={
            "files": len(files),
            "symbols": len(symbols),
            "dependencies": len(dependencies),
            "reverse_targets": len(reverse_index),
            "missing_file_symbols": len(file_symbol_missing),
            "missing_reverse_targets": len(reverse_targets_missing),
            "missing_reverse_callers": len(reverse_callers_missing),
        },
    )
























def _check_context_loop(engine: IndexEngine, query: str | None, add_check) -> None:
    chosen_query = query or _pick_context_query(engine)
    if not chosen_query:
        if not (getattr(getattr(engine, "index", None), "symbols", {}) or {}):
            add_check(
                "context_loop",
                "pass",
                "No indexed symbols; context lookup not applicable",
                metrics={"symbols": 0},
            )
            return
        add_check("context_loop", "warn", "No queryable symbol found")
        return

    result = engine.context(query=chosen_query, level="auto")
    symbols = result.get("symbols") or []
    add_check(
        "context_loop",
        "pass" if symbols else "fail",
        "Context query returned symbols" if symbols else "Context query returned no symbols",
        metrics={"query": chosen_query, "symbols": len(symbols), "level": result.get("level")},
    )


def _check_impact_loop(engine: IndexEngine, symbol: str | None, add_check) -> None:
    chosen_symbol = symbol or _pick_impact_symbol(engine)
    if not chosen_symbol:
        if not (getattr(getattr(engine, "index", None), "symbols", {}) or {}):
            add_check(
                "impact_loop",
                "pass",
                "No indexed symbols; impact lookup not applicable",
                metrics={"symbols": 0},
            )
            return
        add_check("impact_loop", "warn", "No impactable symbol found")
        return

    result = engine.impact(chosen_symbol, max_depth=2)
    if result.get("error"):
        add_check("impact_loop", "fail", result["error"], metrics={"symbol": chosen_symbol})
        return

    direct = result.get("direct_references") or []
    unresolved = [ref for ref in direct if not ref.get("resolved")]
    ref_count = (result.get("symbol_info") or {}).get("ref_count", 0)
    status = "pass"
    summary = "Impact graph returned direct references"
    if ref_count and not direct:
        status = "warn"
        summary = "Symbol has ref_count but no direct references; index may have indirect or stale reference counts"
    elif unresolved:
        status = "warn"
        summary = "Impact graph has unresolved direct references"

    add_check(
        "impact_loop",
        status,
        summary,
        metrics={
            "symbol": result.get("symbol"),
            "ref_count": ref_count,
            "direct_references": len(direct),
            "unresolved_direct_references": len(unresolved),
        },
    )














































# A local checkout, a workspace link or a VCS ref is not a published version,
# so comparing them across repositories says nothing.




















































































# A file-level pragma removes the whole file from a tool's reach. A targeted
# one -- suppressing a single code on a single line -- is ordinary engineering,
# so only the blanket forms are counted.
#
# The pragmas are assembled rather than written out: a linter reading this file
# treats its own directive spelled literally in a string as a directive, and
# warns about the one it cannot parse.

# Read far enough to clear a licence header; a blanket pragma has to precede
# the code it silences, so it cannot hide further down than this.

# Five files is where a reviewer stops noticing them one at a time, and two
# percent is an order of magnitude above the incidental use measured across
# this workspace (3 of 1,673). A fifth of the files is material on its own,
# however few they are.








# Reads with no fallback: absent, the process fails rather than degrades. A
# read that supplies a default is a preference, and an operator who never sets
# it still gets a working system.








































































def _pick_context_query(engine: IndexEngine) -> str:
    candidates = [
        symbol for symbol in engine.index.symbols.values()
        if symbol.symbol_type != SymbolType.FILE and symbol.name
    ]
    if not candidates:
        return ""
    top = max(candidates, key=lambda symbol: symbol.reference_count)
    return top.name


def _pick_impact_symbol(engine: IndexEngine) -> str:
    candidates = [
        symbol for symbol in engine.index.symbols.values()
        if symbol.symbol_type != SymbolType.FILE and symbol.name
    ]
    if not candidates:
        return ""
    top = max(candidates, key=lambda symbol: symbol.reference_count)
    return top.id
