"""
Self-contained verification gate for flyto-indexer.

This module intentionally uses only stdlib + flyto-indexer internals. It is the
CLI/CI entry point an AI agent can run after code edits to prove the index,
impact graph, context lookup, and lightweight security scans still close.
"""

from __future__ import annotations

import json
import html
import fnmatch
import hashlib
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .doc_scanner import scan_documentation
from .engine import IndexEngine
from .models import SymbolType
from .secret_scanner import scan_secrets

_STATUS_RANK = {"pass": 0, "warn": 1, "fail": 2}
_VERIFY_RESULT_SCHEMA_VERSION = "1"
_PROJECT_MARKERS = (
    ".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
    "composer.json", "Gemfile", "src", "src-next",
)
_SKIP_WORKSPACE_DIRS = {
    ".git", ".flyto-index", ".venv", "venv", "node_modules", "dist",
    "build", "coverage", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache",
}
_CI_CANDIDATES = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".gitlab-ci.yml",
    "cloudbuild.yaml",
    "cloudbuild.yml",
    "Makefile",
)
_GENERATED_CHANGE_PATTERNS = (
    ".flyto-index/*",
    ".flyto/*",
    "dist/*",
    "build/*",
    "node_modules/*",
    "__pycache__/*",
)
_HIGH_RISK_CHANGE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*secret*",
    "*credential*",
    ".claude/settings.local.json",
)
_CONTRACT_SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".go", ".py"}
_CONTRACT_SKIP_PARTS = {"__tests__", "__mocks__", "tests", "test", "fixtures"}
_CONTRACT_SKIP_SUFFIXES = (".test", ".spec")
_CONTRACT_SURFACE_TERMS = (
    "asset-map", "asset", "compliance", "ctem", "darkweb", "domain", "domains",
    "footprint", "pentest", "report", "reports", "scan", "scoring",
)
_SINGLE_PROJECT_ISLAND_TYPES = {
    "api", "route", "component", "composable", "store",
}
_SINGLE_PROJECT_PRODUCT_PARTS = {
    "app", "apps", "components", "compounds", "domains", "features", "hooks",
    "lib", "pages", "routes", "router", "routers", "services", "stores",
    "views",
}
_SINGLE_PROJECT_FEATURE_PARTS = {
    "app", "apps", "compounds", "domains", "features", "pages", "routes",
    "router", "routers", "stores", "views",
}
_SINGLE_PROJECT_ENTRY_PARTS = {
    "api", "app", "apps", "bin", "cli", "cmd", "commands", "pages", "routes",
    "router", "routers", "scripts", "server", "views",
}
_SINGLE_PROJECT_ENTRY_NAMES = {
    "__init__", "__main__", "app", "cli", "configure", "handler", "index",
    "main", "page", "route", "server", "setup",
}
_PRODUCT_LOOP_SURFACES: dict[str, tuple[str, ...]] = {
    "overview": (
        "dashboard", "overview", "posture", "workspace",
    ),
    "assets": (
        "asset", "asset-map", "inventory", "domain", "domains", "repo", "repos",
        "repository",
    ),
    "code_redteam": (
        "attack", "attack-path", "autofix", "pentest", "redteam", "red-team",
        "runner", "scan",
    ),
    "exposure": (
        "brand", "exposure", "external-report", "footprint", "issue", "issues",
        "sla",
    ),
    "runtime_cloud_identity": (
        "cloud", "container", "identity", "iam", "kubernetes", "runtime",
        "workload", "workloads",
    ),
    "darkweb": (
        "botshield", "breach", "credential", "darkweb", "dark-web",
        "data-leaks", "data_leaks", "ioc", "ioc-lookup", "ioc_lookup",
        "leak", "leaks", "malware", "malware-families",
        "malware_families", "ransomware", "ransomware-incidents",
        "ransomware_incidents", "sensor-map", "sensor_map", "threat",
        "threat-actors", "threat_actors", "threat-intel", "threat_intel",
    ),
    "scoring_compliance": (
        "audit", "compliance", "control", "evidence", "policy", "score",
        "scoring",
    ),
    "operations_admin": (
        "admin", "approval", "business-unit", "business-units", "fusion",
        "integration", "integrations", "organization", "settings",
    ),
}
_PRODUCT_LOOP_EVIDENCE_PARTS = {
    "docs", "evidence", "platform-loops", "recipes", "workflows",
}
_PRODUCT_LOOP_EVIDENCE_SUFFIXES = {
    ".json", ".md", ".yaml", ".yml",
}
_DYNAMIC_VALIDATION_GUARDS = {
    "audit_loops": "audit:loops",
    "audit_navbar_smoke": "audit:navbar-smoke",
    "branch_guard": "guard:branch",
    "compliance_evidence": "compliance:ci",
}
_RECIPE_ASSERTION_KINDS = {
    "event_invalidates_query",
    "query_key_present",
    "api_path_present",
    "event_routed",
    "route_renders_without_error",
    "dom_contains",
    "http_status",
    "command_succeeds",
}


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
    _check_single_project_islands(engine, add_check)
    _check_context_loop(engine, query, add_check)
    _check_impact_loop(engine, symbol, add_check)
    _check_weak_scanners(root, add_check)
    _check_no_external_runtime(root, add_check)
    _check_package_integrity(root, add_check)
    _check_ci_closed_loop(root, add_check)
    _check_change_hygiene(root, add_check)
    _check_mcp_registry(root, add_check)
    _check_mcp_runtime_smoke(root, add_check)
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
    for project in projects:
        if changed_only and not _project_has_changes(project, base):
            skipped_projects.append(str(project))
            continue
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
    _check_cross_project_contract(projects, workspace_checks)
    _check_product_loop_closure(projects, workspace_checks)
    _check_dynamic_validation_plan(projects, workspace_checks)
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


def format_verification(result: dict[str, Any]) -> str:
    """Human-readable verification report."""
    lines = [
        f"Flyto Verify: {result['project']}",
        f"  Path:   {result['path']}",
        f"  Status: {'PASS' if result['pass'] else 'FAIL'}",
        f"  Checks: {result['summary']['pass']} pass, {result['summary']['warn']} warn, {result['summary']['fail']} fail",
        "",
    ]
    for check in result["checks"]:
        label = check["status"].upper()
        lines.append(f"[{label}] {check['name']}: {check['summary']}")
        metrics = check.get("metrics") or {}
        if metrics:
            compact = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
            if len(compact) > 280:
                compact = compact[:277] + "..."
            lines.append(f"  {compact}")
    return "\n".join(lines)


def render_report(result: dict[str, Any], report_format: str) -> str:
    """Render project or workspace verification result as a report artifact."""
    fmt = report_format.lower()
    if fmt == "json":
        return json.dumps(result, ensure_ascii=False, indent=2)
    if fmt == "markdown":
        return _render_markdown_report(result)
    if fmt == "junit":
        return _render_junit_report(result)
    if fmt == "sarif":
        return _render_sarif_report(result)
    raise ValueError(f"Unsupported report format: {report_format}")


def format_workspace_verification(result: dict[str, Any]) -> str:
    """Human-readable workspace verification report."""
    lines = [
        f"Flyto Workspace Verify: {result['workspace']}",
        f"  Path:     {result['path']}",
        f"  Status:   {'PASS' if result['pass'] else 'FAIL'}",
        f"  Projects: {result['summary']['pass']} pass, {result['summary']['warn']} warn, "
        f"{result['summary']['fail']} fail, {result['summary'].get('skipped', 0)} skipped",
        f"  Workspace checks: {result['summary'].get('workspace_warn', 0)} warn, "
        f"{result['summary'].get('workspace_fail', 0)} fail",
        "",
    ]
    for project in result["projects"]:
        summary = project["summary"]
        status = "PASS" if project["pass"] else "FAIL"
        lines.append(
            f"[{status}] {project['project']}: "
            f"{summary.get('pass', 0)} pass, {summary.get('warn', 0)} warn, {summary.get('fail', 0)} fail"
        )
    return "\n".join(lines)


def _check_runtime_dependencies(root: Path, add_check) -> None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        add_check("runtime_dependencies", "pass", "No pyproject.toml found; Python runtime dependency contract not applicable")
        return

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        add_check("runtime_dependencies", "fail", f"Cannot parse pyproject.toml: {exc}")
        return

    deps = data.get("project", {}).get("dependencies", [])
    project_name = data.get("project", {}).get("name", root.name)
    requires_python = data.get("project", {}).get("requires-python", "")
    if project_name == "flyto-indexer" and deps:
        add_check(
            "runtime_dependencies",
            "fail",
            "Runtime dependencies must stay empty for the no-external-deps contract",
            metrics={"project": project_name, "dependencies": deps, "requires_python": requires_python},
        )
        return

    add_check(
        "runtime_dependencies",
        "pass",
        "Runtime dependencies are empty" if not deps else "Runtime dependencies recorded",
        metrics={"project": project_name, "dependency_count": len(deps), "requires_python": requires_python},
    )


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


def _check_single_project_islands(engine: IndexEngine, add_check) -> None:
    index = engine.index
    symbols = index.symbols or {}
    dependencies = index.dependencies or {}
    reverse_index = index.reverse_index or {}
    inbound: dict[str, set[str]] = {}
    outbound: dict[str, set[str]] = {}
    path_outbound: dict[str, set[str]] = {}
    project_root = getattr(engine, "project_root", None)
    source_refs = _collect_source_name_references(project_root) if isinstance(project_root, Path) else {}

    for target, callers in reverse_index.items():
        for caller in callers:
            inbound.setdefault(str(target), set()).add(str(caller))

    for dep in dependencies.values():
        source = _dep_value(dep, "source_id", "source")
        target = _dep_value(dep, "target_id", "target")
        metadata = _dep_metadata(dep)
        resolved = str(metadata.get("resolved_target") or "")
        targets = [value for value in (target, resolved) if value]
        for dep_target in targets:
            outbound.setdefault(source, set()).add(dep_target)
            inbound.setdefault(dep_target, set()).add(source)
        source_path = _symbol_path(symbols.get(source))
        if source_path:
            for dep_target in targets:
                path_outbound.setdefault(source_path, set()).add(dep_target)

    islands: list[dict[str, Any]] = []
    for sid, symbol in symbols.items():
        path = _symbol_path(symbol)
        sym_type = _symbol_type(symbol)
        name = _symbol_name(symbol)
        if not _is_single_project_candidate(path, sym_type, name):
            continue
        is_entry = _is_single_project_entry(path, sym_type, name)
        inbound_count = len(inbound.get(sid, set()))
        outbound_count = len(outbound.get(sid, set()) | path_outbound.get(path, set()))
        ref_count = _symbol_ref_count(symbol)
        if ref_count:
            inbound_count = max(inbound_count, ref_count)
        source_ref_count = len(source_refs.get(name, set()) - {path})
        if source_ref_count:
            inbound_count = max(inbound_count, source_ref_count)

        reason = ""
        if not is_entry and inbound_count == 0 and outbound_count == 0:
            reason = "no_inbound_or_outbound_edges"
        elif not is_entry and inbound_count == 0:
            reason = "no_inbound_edges"
        elif is_entry and sym_type in {"component", "route"} and outbound_count == 0 and _has_product_surface_signal(path, name):
            reason = "entry_without_data_or_call_edges"

        if reason:
            islands.append({
                "symbol": sid,
                "type": sym_type,
                "name": name,
                "path": path,
                "reason": reason,
                "inbound": inbound_count,
                "outbound": outbound_count,
            })

    api_defs, api_calls = _extract_single_project_api_contract(symbols, dependencies)
    unmatched_api_calls = _match_single_project_api_calls(api_defs, api_calls)
    status = "pass"
    summary = "No high-confidence single-project islands found"
    if islands or unmatched_api_calls:
        status = "warn"
        summary = "Single-project island signals found"

    add_check(
        "single_project_islands",
        status,
        summary,
        metrics={
            "candidate_symbols": sum(
                1 for symbol in symbols.values()
                if _is_single_project_candidate(
                    _symbol_path(symbol), _symbol_type(symbol), _symbol_name(symbol)
                )
            ),
            "island_count": len(islands),
            "island_samples": islands[:10],
            "api_definitions": len(api_defs),
            "api_calls": len(api_calls),
            "unmatched_api_calls": len(unmatched_api_calls),
            "unmatched_api_call_samples": _contract_samples(unmatched_api_calls),
        },
    )


def _collect_source_name_references(root: Path) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in _iter_contract_source_files(root):
        if path.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name in set(re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", text)):
            refs.setdefault(name, set()).add(rel)
    return refs


def _extract_single_project_api_contract(symbols: dict[str, Any], dependencies: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    api_defs: list[dict[str, Any]] = []
    for sid, symbol in symbols.items():
        if _symbol_type(symbol) != "api":
            continue
        metadata = _symbol_metadata(symbol)
        raw_name = _symbol_name(symbol)
        method = str(metadata.get("method") or "").upper()
        route_path = str(metadata.get("path") or metadata.get("url") or "")
        if not route_path and " " in raw_name:
            method_part, route_path = raw_name.split(" ", 1)
            method = method or method_part.upper()
        if not route_path.startswith("/api/"):
            continue
        api_defs.append({
            "method": method,
            "path": route_path,
            "raw": raw_name,
            "normalized": _normalize_api_path(route_path),
            "source": _symbol_path(symbol),
            "symbol": sid,
        })

    api_calls: list[dict[str, Any]] = []
    for dep in dependencies.values():
        if _dep_type(dep) != "api_calls":
            continue
        metadata = _dep_metadata(dep)
        raw = str(metadata.get("url") or _dep_value(dep, "target_id", "target") or "")
        if not raw.startswith("/api/"):
            continue
        source = _dep_value(dep, "source_id", "source")
        api_calls.append({
            "method": str(metadata.get("method") or "").upper(),
            "path": _strip_url_to_api_path(raw),
            "raw": raw,
            "normalized": _normalize_api_path(raw),
            "source": _symbol_id_path(source),
        })

    return _dedupe_contract_items(api_defs), _dedupe_contract_items(api_calls)


def _match_single_project_api_calls(api_defs: list[dict[str, Any]], api_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not api_defs or not api_calls:
        return []
    api_keys = {
        (route.get("method", ""), route.get("normalized", ""))
        for route in api_defs
    }
    api_paths = {route.get("normalized", "") for route in api_defs}
    unmatched = []
    for call in api_calls:
        method = call.get("method", "")
        normalized = call.get("normalized", "")
        if (method and (method, normalized) in api_keys) or normalized in api_paths:
            continue
        unmatched.append(call)
    return unmatched


def _check_context_loop(engine: IndexEngine, query: str | None, add_check) -> None:
    chosen_query = query or _pick_context_query(engine)
    if not chosen_query:
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
        status = "fail"
        summary = "Symbol has ref_count but no direct references"
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


def _check_weak_scanners(root: Path, add_check) -> None:
    secrets = scan_secrets(root)
    secret_samples = [
        {
            "file": finding.file,
            "line": finding.line,
            "pattern": finding.pattern,
            "severity": finding.severity,
            "masked_value": finding.masked_value,
        }
        for finding in secrets.findings[:10]
    ]
    secret_status = "pass"
    if secrets.critical or secrets.high:
        secret_status = "fail"
    elif secrets.medium:
        secret_status = "warn"
    add_check(
        "weak_scan_secrets",
        secret_status,
        "Secret scan completed",
        metrics={
            "files_scanned": secrets.total_files_scanned,
            "findings": secrets.total_findings,
            "critical": secrets.critical,
            "high": secrets.high,
            "medium": secrets.medium,
            "samples": secret_samples,
        },
    )

    try:
        from .analyzer.taint import TaintAnalyzer

        analyzer = TaintAnalyzer(root, index=_load_index_json(root))
        taint = analyzer.analyze_full()
        unsanitized = [flow for flow in taint.taint_flows if not flow.sanitized]
        high_risk = [
            flow for flow in unsanitized
            if flow.severity in {"critical", "high"}
        ]
        taint_samples = []
        for flow in high_risk[:10]:
            item = flow.to_dict()
            taint_samples.append({
                "source_file": item.get("source_file"),
                "source_line": item.get("source_line"),
                "sink_file": item.get("sink_file"),
                "sink_line": item.get("sink_line"),
                "severity": item.get("severity"),
                "category": item.get("category"),
                "recommendation": item.get("recommendation"),
            })
        add_check(
            "weak_scan_taint",
            "fail" if high_risk else "pass",
            "Taint scan completed; no high-risk flows" if not high_risk else "Taint scan found high-risk flows",
            metrics={
                "sources": taint.total_sources,
                "sinks": taint.total_sinks,
                "unsanitized": len(unsanitized),
                "high_risk": len(high_risk),
                "sanitized": taint.sanitized_flows,
                "samples": taint_samples,
            },
        )
    except (OSError, ValueError, RuntimeError) as exc:
        add_check("weak_scan_taint", "warn", f"Taint scan could not complete: {exc}")

    docs = scan_documentation(root)
    add_check(
        "docs_coverage",
        "pass" if docs.overall_score >= 70 else "warn",
        "Documentation scan completed",
        metrics={
            "overall_score": docs.overall_score,
            "readme_score": docs.readme_score,
            "inline_doc_coverage": round(docs.inline_doc_coverage, 3),
            "suggestions": len(docs.suggestions),
        },
    )


def _check_no_external_runtime(root: Path, add_check) -> None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        add_check("no_external_runtime", "pass", "No Python package runtime contract to enforce")
        return

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        add_check("no_external_runtime", "fail", f"Cannot parse pyproject.toml: {exc}")
        return

    project = data.get("project", {})
    project_name = project.get("name", root.name)
    dependencies = project.get("dependencies", [])
    optional = project.get("optional-dependencies", {})
    if project_name != "flyto-indexer":
        add_check(
            "no_external_runtime",
            "pass",
            "No-external-runtime contract is scoped to flyto-indexer",
            metrics={"project": project_name, "dependency_count": len(dependencies)},
        )
        return

    _ci_files, ci_text = _read_ci_files(root)
    lowered_ci = ci_text.lower()
    has_no_deps_smoke = "--no-deps" in lowered_ci and "flyto-index --help" in lowered_ci
    has_metadata_assertion = "requires-dist" in lowered_ci and "runtime_requires" in lowered_ci

    problems = []
    if dependencies:
        problems.append("runtime dependencies are not empty")
    if not has_no_deps_smoke:
        problems.append("CI does not run a no-deps wheel smoke")
    if not has_metadata_assertion:
        problems.append("CI does not assert wheel runtime metadata")

    status = "pass"
    if dependencies:
        status = "fail"
    elif problems:
        status = "warn"

    add_check(
        "no_external_runtime",
        status,
        "flyto-indexer keeps zero runtime dependencies" if not problems else "No-external-runtime guard is incomplete",
        metrics={
            "project": project_name,
            "dependency_count": len(dependencies),
            "optional_dependency_groups": sorted(optional.keys()) if isinstance(optional, dict) else [],
            "ci_no_deps_smoke": has_no_deps_smoke,
            "ci_metadata_assertion": has_metadata_assertion,
            "problems": problems,
        },
    )


def _check_package_integrity(root: Path, add_check) -> None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        add_check("package_integrity", "pass", "No Python package manifest to inspect")
        return

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        add_check("package_integrity", "fail", f"Cannot parse pyproject.toml: {exc}")
        return

    project = data.get("project", {})
    project_name = project.get("name", root.name)
    if project_name != "flyto-indexer":
        add_check("package_integrity", "pass", "Package integrity contract is scoped to flyto-indexer")
        return

    tool = data.get("tool", {})
    hatch = tool.get("hatch", {}) if isinstance(tool, dict) else {}
    build = hatch.get("build", {}) if isinstance(hatch, dict) else {}
    targets = build.get("targets", {}) if isinstance(build, dict) else {}
    wheel = targets.get("wheel", {}) if isinstance(targets, dict) else {}
    sdist = targets.get("sdist", {}) if isinstance(targets, dict) else {}

    wheel_packages = wheel.get("packages", []) if isinstance(wheel, dict) else []
    wheel_sources = wheel.get("sources", {}) if isinstance(wheel, dict) else {}
    force_include = wheel.get("force-include", {}) if isinstance(wheel, dict) else {}
    sdist_include = sdist.get("include", []) if isinstance(sdist, dict) else []
    scripts = project.get("scripts", {}) if isinstance(project, dict) else {}
    license_files = project.get("license-files", []) if isinstance(project, dict) else []

    required = {
        "hatchling_backend": data.get("build-system", {}).get("build-backend") == "hatchling.build",
        "wheel_src_package": "src" in wheel_packages,
        "wheel_src_remap": isinstance(wheel_sources, dict) and wheel_sources.get("src") == "flyto_indexer",
        "rule_corpus_force_include": isinstance(force_include, dict)
        and force_include.get("config/rules") == "flyto_indexer/config/rules",
        "sdist_src": "/src" in sdist_include,
        "sdist_config": "/config" in sdist_include,
        "cli_entrypoint": isinstance(scripts, dict)
        and scripts.get("flyto-index") == "flyto_indexer.cli:main",
        "license_files_exist": all((root / str(path)).is_file() for path in license_files)
        and {"LICENSE", "NOTICE"}.issubset({str(path) for path in license_files}),
    }
    package_entries = _package_manifest_entries(wheel_packages, wheel_sources, force_include, sdist_include)
    forbidden_entries = [
        entry for entry in package_entries
        if _matches_any(entry, _GENERATED_CHANGE_PATTERNS + _HIGH_RISK_CHANGE_PATTERNS)
    ]
    missing = sorted(name for name, present in required.items() if not present)
    status = "pass"
    if forbidden_entries or missing:
        status = "fail"
    add_check(
        "package_integrity",
        status,
        "Package manifest preserves the install/runtime contract" if status == "pass" else "Package manifest can leak or break runtime artifacts",
        metrics={
            "required": required,
            "missing": missing,
            "forbidden_entries": forbidden_entries,
            "entries_checked": len(package_entries),
        },
    )


def _check_ci_closed_loop(root: Path, add_check) -> None:
    ci_files, ci_text = _read_ci_files(root)
    if not ci_files:
        add_check("ci_closed_loop", "warn", "No CI workflow files found")
        return

    lowered = ci_text.lower()
    project_name = _pyproject_name(root) or root.name
    required = {
        "verify": "flyto-index verify" in lowered or "verify-workspace" in lowered,
        "tests": any(token in lowered for token in (
            "pytest", "vitest", "npm test", "npm run test", "pnpm test", "yarn test", "go test",
        )),
        "lint": any(token in lowered for token in ("ruff", "mypy", "eslint", "npm run lint", "golangci-lint")),
        "build": any(token in lowered for token in ("python -m build", "npm run build", "go build", "cargo build")),
    }
    if project_name == "flyto-indexer":
        required.update({
            "sarif_report": "--report-format sarif" in lowered,
            "no_deps_wheel": "--no-deps" in lowered and "flyto-index --help" in lowered,
        })

    missing = sorted(name for name, present in required.items() if not present)
    add_check(
        "ci_closed_loop",
        "pass" if not missing else "warn",
        "CI runs the verify/test/build loop" if not missing else "CI does not fully close the verify loop",
        metrics={
            "files": [str(path.relative_to(root)) for path in ci_files],
            "required": required,
            "missing": missing,
        },
    )


def _check_change_hygiene(root: Path, add_check) -> None:
    if not (root / ".git").exists():
        add_check("change_hygiene", "pass", "No git repository; change hygiene not applicable")
        return

    changed = _git_changed_paths(root)
    generated = [path for path in changed if _matches_any(path, _GENERATED_CHANGE_PATTERNS)]
    high_risk = [path for path in changed if _matches_any(path, _HIGH_RISK_CHANGE_PATTERNS)]
    status = "pass"
    summary = "No high-risk working tree changes"
    if generated:
        status = "fail"
        summary = "Generated artifacts are tracked in the working tree"
    elif high_risk:
        status = "warn"
        summary = "Working tree includes high-risk config or secret-shaped paths"

    add_check(
        "change_hygiene",
        status,
        summary,
        metrics={
            "changed": len(changed),
            "generated": generated,
            "high_risk": high_risk,
        },
    )


def _check_mcp_runtime_smoke(root: Path, add_check) -> None:
    if not (root / "src" / "mcp_server.py").exists():
        add_check("mcp_runtime_smoke", "pass", "No MCP server module to smoke")
        return

    try:
        from . import mcp_server
        from .tool_registry import SMART_TOOLS, SMART_TOOL_NAMES, has_tool
    except ImportError:
        try:
            import mcp_server
            from tool_registry import SMART_TOOLS, SMART_TOOL_NAMES, has_tool
        except ImportError as exc:
            add_check("mcp_runtime_smoke", "fail", f"Cannot import MCP runtime: {exc}")
            return

    problems = []
    server_names = {tool.get("name", "") for tool in getattr(mcp_server, "TOOLS", [])}
    smart_names = {tool.get("name", "") for tool in SMART_TOOLS}
    if server_names != smart_names:
        problems.append("server tool list does not match smart tool registry")
    if set(SMART_TOOL_NAMES) != smart_names:
        problems.append("SMART_TOOL_NAMES does not match SMART_TOOLS")
    missing_dispatch = sorted(name for name in smart_names if name and not has_tool(name))
    if missing_dispatch:
        problems.append("some smart tools are missing dispatch")
    protocols = getattr(mcp_server, "SUPPORTED_PROTOCOL_VERSIONS", ())
    if not protocols:
        problems.append("no supported protocol versions declared")
    elif mcp_server.negotiate_protocol_version(protocols[-1]) != protocols[-1]:
        problems.append("protocol negotiation does not echo supported clients")
    if not getattr(mcp_server, "RESOURCES", []):
        problems.append("no MCP resources declared")
    if not getattr(mcp_server, "PROMPTS", []):
        problems.append("no MCP prompts declared")
    impact_prompt = mcp_server._get_prompt("impact-check")
    if not impact_prompt.get("messages"):
        problems.append("impact-check prompt cannot be rendered")

    add_check(
        "mcp_runtime_smoke",
        "fail" if problems else "pass",
        "MCP runtime imports and protocol helpers smoke cleanly" if not problems else "MCP runtime smoke found drift",
        metrics={
            "tools": len(server_names),
            "protocol_versions": list(protocols),
            "resources": len(getattr(mcp_server, "RESOURCES", [])),
            "prompts": len(getattr(mcp_server, "PROMPTS", [])),
            "missing_dispatch": missing_dispatch,
            "problems": problems,
        },
    )


def _check_cross_project_contract(projects: list[Path], checks: list[dict[str, Any]]) -> None:
    frontends = [project for project in projects if _looks_like_frontend(project)]
    backends = [project for project in projects if _looks_like_backend(project)]
    if not frontends or not backends:
        checks.append({
            "name": "cross_project_contract",
            "status": "pass",
            "summary": "No frontend/backend project pair in this workspace verification",
            "metrics": {
                "frontends": [project.name for project in frontends],
                "backends": [project.name for project in backends],
            },
        })
        return

    frontend_calls: list[dict[str, Any]] = []
    frontend_terms: dict[str, int] = {}
    for project in frontends:
        calls, terms = _extract_frontend_contract_signals(project)
        frontend_calls.extend(calls)
        _merge_term_counts(frontend_terms, terms)

    backend_routes: list[dict[str, Any]] = []
    backend_terms: dict[str, int] = {}
    for project in backends:
        routes, terms = _extract_backend_contract_signals(project)
        backend_routes.extend(routes)
        _merge_term_counts(backend_terms, terms)

    backend_keys = {
        (route.get("method", ""), route.get("normalized", ""))
        for route in backend_routes
    }
    backend_path_keys = {route.get("normalized", "") for route in backend_routes}
    unmatched = []
    matched = 0
    for call in frontend_calls:
        method = call.get("method", "")
        normalized = call.get("normalized", "")
        if (method and (method, normalized) in backend_keys) or (not method and normalized in backend_path_keys):
            matched += 1
            continue
        if method and normalized in backend_path_keys:
            matched += 1
            continue
        unmatched.append(call)

    shared_terms = sorted(set(frontend_terms) & set(backend_terms))
    frontend_only_terms = sorted(set(frontend_terms) - set(backend_terms))
    backend_only_terms = sorted(set(backend_terms) - set(frontend_terms))
    status = "pass"
    summary = "Frontend API calls are backed by backend routes"
    if not frontend_calls or not backend_routes:
        status = "warn"
        summary = "Frontend/backend contract has no extractable endpoints"
    elif unmatched:
        status = "warn"
        summary = "Some frontend API calls do not match indexed backend routes"
    elif not shared_terms:
        status = "warn"
        summary = "Frontend/backend endpoints match, but no shared product surface terms were found"

    checks.append({
        "name": "cross_project_contract",
        "status": status,
        "summary": summary,
        "metrics": {
            "frontends": [project.name for project in frontends],
            "backends": [project.name for project in backends],
            "frontend_calls": len(frontend_calls),
            "backend_routes": len(backend_routes),
            "matched_calls": matched,
            "unmatched_calls": len(unmatched),
            "unmatched_samples": _contract_samples(unmatched),
            "shared_terms": shared_terms,
            "frontend_only_terms": frontend_only_terms,
            "backend_only_terms": backend_only_terms,
        },
    })


def _check_product_loop_closure(projects: list[Path], checks: list[dict[str, Any]]) -> None:
    loops = _empty_product_loops()
    project_roles: dict[str, list[str]] = {}
    for project in projects:
        roles = []
        if _looks_like_frontend(project):
            roles.append("frontend")
        if _looks_like_backend(project):
            roles.append("backend")
        project_roles[project.name] = roles or ["project"]
        if roles:
            _merge_product_loops(loops, _collect_project_product_loop_signals(project, roles))

    active = {
        surface: metrics for surface, metrics in loops.items()
        if _product_loop_is_active(metrics)
    }
    unmatched_by_surface = _workspace_unmatched_api_calls_by_surface(projects)
    gaps: list[dict[str, Any]] = []
    for surface, metrics in active.items():
        reasons = []
        if metrics["ui"] and not metrics["api_calls"] and not metrics["backend_routes"]:
            reasons.append("ui_without_data_contract")
        if metrics["api_calls"] and not metrics["backend_routes"]:
            reasons.append("frontend_calls_without_backend_route")
        if unmatched_by_surface.get(surface):
            reasons.append("unmatched_frontend_api_calls")
        if metrics["backend_routes"] and not metrics["ui"]:
            reasons.append("backend_route_without_ui_surface")
        if metrics["ui"] and not metrics["tests"]:
            reasons.append("ui_without_surface_tests")
        if (metrics["ui"] or metrics["backend_routes"] or metrics["api_calls"]) and not (metrics["evidence"] or metrics["workflows"]):
            reasons.append("missing_evidence_or_recipe")
        if reasons:
            gaps.append({
                "surface": surface,
                "reasons": reasons,
                "counts": _product_loop_counts(metrics),
                "unmatched_api_call_samples": _contract_samples(unmatched_by_surface.get(surface, []), limit=6),
                "samples": metrics["samples"][:6],
            })

    status = "pass"
    summary = "Product surfaces have closed-loop signals"
    if gaps:
        status = "warn"
        summary = "Product surfaces have missing loop signals"

    checks.append({
        "name": "product_loop_closure",
        "status": status,
        "summary": summary,
        "metrics": {
            "projects": project_roles,
            "surfaces": {surface: _product_loop_counts(metrics) for surface, metrics in active.items()},
            "active_surfaces": sorted(active),
            "unmatched_api_calls_by_surface": {
                surface: len(calls)
                for surface, calls in sorted(unmatched_by_surface.items())
            },
            "gap_count": len(gaps),
            "gaps": gaps[:12],
        },
    })


def _collect_project_product_loop_signals(project: Path, roles: list[str]) -> dict[str, dict[str, Any]]:
    loops = _empty_product_loops()
    index = _load_index_json(project)
    symbols = index.get("symbols") or {}
    dependencies = index.get("dependencies") or {}
    deps_iter = dependencies.values() if isinstance(dependencies, dict) else dependencies

    for sid, symbol in symbols.items():
        path = _symbol_path(symbol)
        name = _symbol_name(symbol)
        sym_type = _symbol_type(symbol)
        text = f"{path}\n{name}\n{json.dumps(_symbol_metadata(symbol), ensure_ascii=False, sort_keys=True)}"
        for surface in _classify_product_surfaces(text):
            if sym_type in {"component", "route", "store", "composable"} and _looks_like_frontend(project):
                _add_product_loop_signal(loops, surface, "ui", project.name, path, name)
            elif sym_type == "api":
                _add_product_loop_signal(loops, surface, "backend_routes", project.name, path, name)

    for dep in deps_iter:
        if _dep_type(dep) != "api_calls":
            continue
        metadata = _dep_metadata(dep)
        raw = str(metadata.get("url") or _dep_value(dep, "target_id", "target") or "")
        source = _dep_value(dep, "source_id", "source")
        text = f"{raw}\n{source}"
        for surface in _classify_product_surfaces(text):
            _add_product_loop_signal(
                loops,
                surface,
                "api_calls",
                project.name,
                _symbol_id_path(source),
                raw,
            )

    for path in _iter_product_loop_files(project):
        rel = str(path.relative_to(project))
        text = rel
        try:
            if path.suffix in _PRODUCT_LOOP_EVIDENCE_SUFFIXES:
                text += "\n" + path.read_text(encoding="utf-8", errors="ignore")[:20000]
        except OSError:
            pass
        surfaces = _classify_product_surfaces(text)
        if not surfaces:
            continue
        kind = _product_loop_file_kind(rel, path)
        for surface in surfaces:
            _add_product_loop_signal(loops, surface, kind, project.name, rel, Path(rel).name)

    _ci_files, ci_text = _read_ci_files(project)
    for surface in _classify_product_surfaces(ci_text):
        _add_product_loop_signal(loops, surface, "ci", project.name, "ci", surface)

    return loops


def _workspace_unmatched_api_calls_by_surface(projects: list[Path]) -> dict[str, list[dict[str, Any]]]:
    frontends = [project for project in projects if _looks_like_frontend(project)]
    backends = [project for project in projects if _looks_like_backend(project)]
    if not frontends or not backends:
        return {}

    frontend_calls: list[dict[str, Any]] = []
    for project in frontends:
        calls, _terms = _extract_frontend_contract_signals(project)
        frontend_calls.extend(calls)
    backend_routes: list[dict[str, Any]] = []
    for project in backends:
        routes, _terms = _extract_backend_contract_signals(project)
        backend_routes.extend(routes)

    backend_keys = {
        (route.get("method", ""), route.get("normalized", ""))
        for route in backend_routes
    }
    backend_path_keys = {route.get("normalized", "") for route in backend_routes}
    unmatched_by_surface: dict[str, list[dict[str, Any]]] = {}
    for call in frontend_calls:
        method = call.get("method", "")
        normalized = call.get("normalized", "")
        if (method and (method, normalized) in backend_keys) or normalized in backend_path_keys:
            continue
        surfaces = _classify_product_surfaces(
            f"{call.get('path', '')}\n{call.get('raw', '')}\n{call.get('source', '')}"
        )
        for surface in surfaces:
            unmatched_by_surface.setdefault(surface, []).append(call)
    return unmatched_by_surface


def _iter_product_loop_files(project: Path):
    for path in project.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(project))
        parts = set(Path(rel).parts)
        if parts & _SKIP_WORKSPACE_DIRS:
            continue
        suffix = path.suffix.lower()
        if parts & {"docs", "evidence", "recipes", "workflows", ".github"}:
            yield path
            continue
        if any(Path(rel).stem.endswith(test_suffix) for test_suffix in _CONTRACT_SKIP_SUFFIXES):
            yield path
            continue
        if suffix in _PRODUCT_LOOP_EVIDENCE_SUFFIXES and parts & _PRODUCT_LOOP_EVIDENCE_PARTS:
            yield path


def _product_loop_file_kind(rel: str, path: Path) -> str:
    parts = set(Path(rel).parts)
    stem = Path(rel).stem
    if ".github" in parts:
        return "ci"
    if "recipes" in parts or "workflows" in parts or path.suffix.lower() in {".yaml", ".yml"}:
        return "workflows"
    if any(stem.endswith(test_suffix) for test_suffix in _CONTRACT_SKIP_SUFFIXES) or parts & {"__tests__", "tests", "test"}:
        return "tests"
    if parts & _PRODUCT_LOOP_EVIDENCE_PARTS:
        return "evidence"
    return "evidence"


def _empty_product_loops() -> dict[str, dict[str, Any]]:
    return {
        surface: {
            "ui": set(),
            "api_calls": set(),
            "backend_routes": set(),
            "tests": set(),
            "evidence": set(),
            "workflows": set(),
            "ci": set(),
            "projects": set(),
            "samples": [],
        }
        for surface in _PRODUCT_LOOP_SURFACES
    }


def _merge_product_loops(target: dict[str, dict[str, Any]], incoming: dict[str, dict[str, Any]]) -> None:
    for surface, metrics in incoming.items():
        if surface not in target:
            target[surface] = metrics
            continue
        for key, value in metrics.items():
            if key == "samples":
                existing = target[surface]["samples"]
                for sample in value:
                    if sample not in existing:
                        existing.append(sample)
                continue
            target[surface].setdefault(key, set()).update(value)


def _add_product_loop_signal(
    loops: dict[str, dict[str, Any]],
    surface: str,
    kind: str,
    project: str,
    source: str,
    name: str,
) -> None:
    if surface not in loops:
        return
    loops[surface].setdefault(kind, set()).add(f"{project}:{source}:{name}")
    loops[surface]["projects"].add(project)
    sample = {"project": project, "kind": kind, "source": source, "name": name}
    if len(loops[surface]["samples"]) < 20 and sample not in loops[surface]["samples"]:
        loops[surface]["samples"].append(sample)


def _product_loop_is_active(metrics: dict[str, Any]) -> bool:
    return any(metrics[key] for key in ("ui", "api_calls", "backend_routes", "tests", "evidence", "workflows"))


def _product_loop_counts(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "ui": len(metrics["ui"]),
        "api_calls": len(metrics["api_calls"]),
        "backend_routes": len(metrics["backend_routes"]),
        "tests": len(metrics["tests"]),
        "evidence": len(metrics["evidence"]),
        "workflows": len(metrics["workflows"]),
        "ci": len(metrics["ci"]),
        "projects": sorted(metrics["projects"]),
    }


def _classify_product_surfaces(text: str) -> list[str]:
    lowered = text.lower()
    tokens = set(re.split(r"[^a-z0-9]+", lowered))
    matches = []
    for surface, terms in _PRODUCT_LOOP_SURFACES.items():
        if any(
            (term in lowered if "-" in term or "_" in term else term in tokens)
            for term in terms
        ):
            matches.append(surface)
    return matches


def _check_dynamic_validation_plan(projects: list[Path], checks: list[dict[str, Any]]) -> None:
    frontend_projects = [project for project in projects if _looks_like_frontend(project)]
    if not frontend_projects:
        checks.append({
            "name": "dynamic_validation_plan",
            "status": "pass",
            "summary": "No frontend project requiring browser/YAML validation plan",
            "metrics": {"frontend_projects": []},
        })
        return

    project_reports = []
    gaps: list[dict[str, Any]] = []
    for project in frontend_projects:
        report = _collect_dynamic_validation_project(project)
        project_reports.append(report)
        missing_registries = [
            name for name, present in report["registries"].items()
            if not present
        ]
        if missing_registries:
            gaps.append({
                "project": project.name,
                "surface": "workspace",
                "reasons": ["missing_dynamic_validation_registries"],
                "missing_registries": missing_registries,
            })
        for surface, surface_report in report["surfaces"].items():
            reasons = []
            if surface_report["registry_modules"] and not surface_report["browser_routes"]:
                reasons.append("missing_navbar_browser_smoke_route")
            if surface_report["registry_recipes"] and surface_report["missing_recipe_files"]:
                reasons.append("missing_recipe_files")
            if surface_report["registry_recipes"] and not surface_report["browser_recipe_files"]:
                reasons.append("recipes_without_browser_or_flyto_core_steps")
            if surface_report["prose_assertion_recipes"]:
                reasons.append("recipes_without_machine_checkable_assertions")
            if surface_report["invalid_routes"]:
                reasons.append("invalid_smoke_route_contract")
            if reasons:
                gaps.append({
                    "project": project.name,
                    "surface": surface,
                    "reasons": reasons,
                    "counts": {
                        "registry_modules": len(surface_report["registry_modules"]),
                        "browser_routes": len(surface_report["browser_routes"]),
                        "registry_recipes": len(surface_report["registry_recipes"]),
                        "existing_recipe_files": len(surface_report["existing_recipe_files"]),
                        "browser_recipe_files": len(surface_report["browser_recipe_files"]),
                    },
                    "missing_recipe_files": surface_report["missing_recipe_files"][:8],
                    "prose_assertion_recipes": surface_report["prose_assertion_recipes"][:8],
                    "invalid_routes": surface_report["invalid_routes"][:8],
                })
        missing_guards = [
            name for name, present in report["guards"].items()
            if not present
        ]
        if missing_guards:
            gaps.append({
                "project": project.name,
                "surface": "workspace",
                "reasons": ["missing_dynamic_validation_ci_guards"],
                "missing_guards": missing_guards,
            })

    status = "pass"
    summary = "Browser smoke and YAML recipe validation plans are closed"
    if gaps:
        status = "warn"
        summary = "Dynamic validation plan has missing smoke/recipe/CI coverage"

    checks.append({
        "name": "dynamic_validation_plan",
        "status": status,
        "summary": summary,
        "metrics": {
            "frontend_projects": [project.name for project in frontend_projects],
            "projects": project_reports,
            "gap_count": len(gaps),
            "gaps": gaps[:12],
        },
    })


def _collect_dynamic_validation_project(project: Path) -> dict[str, Any]:
    navbar_registry = _load_json_file(project / "docs" / "platform-loops" / "navbar-smoke-registry.json")
    loop_registry = _load_json_file(project / "docs" / "platform-loops" / "platform-loop-registry.json")
    surfaces = _empty_dynamic_surface_report()

    routes = navbar_registry.get("routes", [])
    if not isinstance(routes, list):
        routes = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        surface = str(route.get("surface") or "")
        if surface not in surfaces:
            continue
        route_id = str(route.get("id") or route.get("moduleId") or route.get("pathTemplate") or "")
        surfaces[surface]["browser_routes"].append(route_id)
        invalid = _invalid_navbar_smoke_route(route)
        if invalid:
            surfaces[surface]["invalid_routes"].append({"route": route_id, "missing": invalid})

    surface_entries = loop_registry.get("surfaces", [])
    if not isinstance(surface_entries, list):
        surface_entries = []
    for surface_entry in surface_entries:
        if not isinstance(surface_entry, dict):
            continue
        surface = str(surface_entry.get("id") or "")
        if surface not in surfaces:
            continue
        modules = _string_list(surface_entry.get("modules"))
        recipes = _string_list(surface_entry.get("recipes"))
        surfaces[surface]["registry_modules"].extend(modules)
        surfaces[surface]["registry_recipes"].extend(recipes)
        for recipe in recipes:
            recipe_path = project / "docs" / "platform-loops" / "recipes" / recipe
            if not recipe_path.is_file():
                surfaces[surface]["missing_recipe_files"].append(recipe)
                continue
            surfaces[surface]["existing_recipe_files"].append(recipe)
            text = recipe_path.read_text(encoding="utf-8", errors="ignore")
            if _recipe_has_dynamic_steps(text):
                surfaces[surface]["browser_recipe_files"].append(recipe)
            if not _recipe_assertions_machine_checkable(text):
                surfaces[surface]["prose_assertion_recipes"].append(recipe)

    _ci_files, ci_text = _read_ci_files(project)
    package_text = ""
    package_json = project / "package.json"
    if package_json.is_file():
        package_text = package_json.read_text(encoding="utf-8", errors="ignore")
    guard_text = f"{ci_text}\n{package_text}"
    guards = {
        name: token in guard_text
        for name, token in _DYNAMIC_VALIDATION_GUARDS.items()
    }

    return {
        "project": project.name,
        "registries": {
            "navbar_smoke": bool(navbar_registry),
            "platform_loops": bool(loop_registry),
        },
        "guards": guards,
        "surfaces": surfaces,
    }


def _empty_dynamic_surface_report() -> dict[str, dict[str, Any]]:
    return {
        surface: {
            "registry_modules": [],
            "browser_routes": [],
            "registry_recipes": [],
            "existing_recipe_files": [],
            "browser_recipe_files": [],
            "missing_recipe_files": [],
            "prose_assertion_recipes": [],
            "invalid_routes": [],
        }
        for surface in _PRODUCT_LOOP_SURFACES
    }


def _invalid_navbar_smoke_route(route: dict[str, Any]) -> list[str]:
    missing = []
    if not route.get("pathTemplate"):
        missing.append("pathTemplate")
    if route.get("mode") not in {"both", "engineer", "exec"}:
        missing.append("mode")
    if route.get("scrollPolicy") not in {"host", "self", "page", "document"}:
        missing.append("scrollPolicy")
    expected = route.get("expectedText")
    if not isinstance(expected, list) or not [item for item in expected if str(item).strip()]:
        missing.append("expectedText")
    return missing


def _recipe_has_dynamic_steps(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in (
        "flyto-core",
        "module: browser.",
        "browser.goto",
        "browser.click",
        "browser.extract",
        "browser.evaluate",
        "browser.wait",
    ))


def _recipe_assertions_machine_checkable(text: str) -> bool:
    """True only when a recipe carries a non-empty, structured assertions block.

    A machine-checkable assertion is a block-sequence item that opens a mapping
    keyed on a known ``assert:`` kind (e.g.
    ``- assert: event_invalidates_query``). Prose assertions
    (``- pipeline.progress invalidates ...``), unknown kinds, or a missing block
    fail this check, so a recipe of "browser.goto + browser.extract + paragraph"
    can no longer count as a closed validation plan.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("#") or not line.strip():
            continue
        if line.lstrip() == "assertions:" and not line.startswith(" "):
            start = i
            break
    if start is None:
        return False

    items: list[str] = []
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break  # dedent to the next top-level key ends the block
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    if not items:
        return False
    for item in items:
        if not item.startswith("assert:"):
            return False
        kind = item.split(":", 1)[1].strip()
        if kind not in _RECIPE_ASSERTION_KINDS:
            return False
    return True


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _looks_like_frontend(project: Path) -> bool:
    package_json = project / "package.json"
    return package_json.exists() and ((project / "src-next").exists() or (project / "src").exists())


def _looks_like_backend(project: Path) -> bool:
    return (project / "go.mod").exists() or (project / "api").exists() or (project / "internal").exists()


def _extract_frontend_contract_signals(project: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    calls: list[dict[str, Any]] = []
    terms: dict[str, int] = {}
    roots = [path for path in (project / "src-next", project / "src") if path.exists()]
    for root in roots:
        for path in _iter_contract_source_files(root):
            rel = str(path.relative_to(project))
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            _count_surface_terms(rel + "\n" + text, terms)
            for call in _extract_api_calls_from_text(text):
                call["project"] = project.name
                call["source"] = rel
                calls.append(call)
    return _dedupe_contract_items(calls), terms


def _extract_backend_contract_signals(project: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    routes = _extract_backend_routes_from_index(project)
    terms: dict[str, int] = {}
    for route in routes:
        _count_surface_terms(f"{route.get('path', '')}\n{route.get('raw', '')}", terms)
    for root_name in ("api", "internal"):
        root = project / root_name
        if not root.exists():
            continue
        for path in _iter_contract_source_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            _count_surface_terms(str(path.relative_to(project)) + "\n" + text, terms)
    return routes, terms


def _extract_backend_routes_from_index(project: Path) -> list[dict[str, Any]]:
    index = _load_index_json(project)
    routes: list[dict[str, Any]] = []
    for symbol in (index.get("symbols") or {}).values():
        if not isinstance(symbol, dict) or symbol.get("type") != "api":
            continue
        metadata = symbol.get("metadata") if isinstance(symbol.get("metadata"), dict) else {}
        method = str(metadata.get("method") or "").upper()
        route_path = str(metadata.get("path") or "")
        raw = str(symbol.get("name") or "")
        if not route_path and " " in raw:
            method_part, route_path = raw.split(" ", 1)
            method = method or method_part.upper()
        if not route_path.startswith("/api/"):
            continue
        routes.append({
            "project": project.name,
            "method": method,
            "path": route_path,
            "raw": raw,
            "normalized": _normalize_api_path(route_path),
            "source": symbol.get("path", ""),
        })
    return _dedupe_contract_items(routes)


def _extract_api_calls_from_text(text: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    string_pattern = re.compile(r"(?P<quote>[`'\"])(?P<value>[^`'\"\n\r]*/api/v1[^`'\"\n\r]*)(?P=quote)")
    comment_pattern = re.compile(r"\b(?P<method>GET|POST|PUT|PATCH|DELETE)\s+(?P<path>/api/v1/[^\s,;`'\")]+)")
    for match in string_pattern.finditer(text):
        raw = match.group("value")
        method = _infer_http_method(text[max(0, match.start() - 100):match.start()])
        calls.append({
            "method": method,
            "path": _strip_url_to_api_path(raw),
            "raw": raw,
            "normalized": _normalize_api_path(raw),
        })
    for match in comment_pattern.finditer(text):
        raw = match.group("path")
        calls.append({
            "method": match.group("method").upper(),
            "path": _strip_url_to_api_path(raw),
            "raw": raw,
            "normalized": _normalize_api_path(raw),
        })
    return _dedupe_contract_items(calls)


def _iter_contract_source_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in _CONTRACT_SOURCE_EXTENSIONS:
            continue
        if _is_contract_skipped_path(str(path)):
            continue
        yield path


def _infer_http_method(prefix: str) -> str:
    matches = re.findall(r"\b(GET|POST|PUT|PATCH|DELETE)\b", prefix, flags=re.IGNORECASE)
    return matches[-1].upper() if matches else ""


def _strip_url_to_api_path(raw: str) -> str:
    raw = raw.strip()
    idx = raw.find("/api/v1")
    path = raw[idx:] if idx >= 0 else raw
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = re.sub(r"(?<!/)\$\{[^}/]*(?:\}|$)", "", path)
    return path.rstrip(".,:;")


def _normalize_api_path(raw: str) -> str:
    path = _strip_url_to_api_path(raw)
    path = re.sub(r"\$\{[^}/]*$", "{param}", path)
    path = re.sub(r"\$\{[^}]+\}", "{param}", path)
    path = re.sub(r"\{[^}/]+\}", "{param}", path)
    path = re.sub(r"\[[^]/]+\]", "{param}", path)
    path = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "{param}", path)
    path = re.sub(r"(?<=/)\*(?=/|$)", "{param}", path)
    path = re.sub(r"/+", "/", path).rstrip("/")
    return path or "/"


def _dedupe_contract_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen = set()
    for item in items:
        key = (item.get("method", ""), item.get("normalized", ""), item.get("path", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _count_surface_terms(text: str, counts: dict[str, int]) -> None:
    lowered = text.lower()
    for term in _CONTRACT_SURFACE_TERMS:
        count = lowered.count(term)
        if count:
            counts[term] = counts.get(term, 0) + count


def _merge_term_counts(target: dict[str, int], incoming: dict[str, int]) -> None:
    for key, value in incoming.items():
        target[key] = target.get(key, 0) + value


def _contract_samples(items: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "method": item.get("method", ""),
            "path": item.get("path", ""),
            "normalized": item.get("normalized", ""),
            "source": item.get("source", ""),
        }
        for item in items[:limit]
    ]


def _symbol_value(symbol: Any, attr: str, key: str | None = None, default: Any = "") -> Any:
    if symbol is None:
        return default
    if isinstance(symbol, dict):
        return symbol.get(key or attr, default)
    return getattr(symbol, attr, default)


def _symbol_path(symbol: Any) -> str:
    return str(_symbol_value(symbol, "path", default=""))


def _symbol_type(symbol: Any) -> str:
    value = _symbol_value(symbol, "symbol_type", "type", "")
    return str(getattr(value, "value", value))


def _symbol_name(symbol: Any) -> str:
    return str(_symbol_value(symbol, "name", default=""))


def _symbol_metadata(symbol: Any) -> dict[str, Any]:
    metadata = _symbol_value(symbol, "metadata", default={})
    return metadata if isinstance(metadata, dict) else {}


def _symbol_ref_count(symbol: Any) -> int:
    value = _symbol_value(symbol, "reference_count", "ref_count", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _dep_value(dep: Any, attr: str, key: str | None = None) -> str:
    if isinstance(dep, dict):
        return str(dep.get(key or attr, "") or "")
    return str(getattr(dep, attr, "") or "")


def _dep_type(dep: Any) -> str:
    value = dep.get("type", "") if isinstance(dep, dict) else getattr(dep, "dep_type", "")
    return str(getattr(value, "value", value))


def _dep_metadata(dep: Any) -> dict[str, Any]:
    metadata = dep.get("metadata", {}) if isinstance(dep, dict) else getattr(dep, "metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _symbol_id_path(symbol_id: str) -> str:
    parts = symbol_id.split(":")
    return parts[1] if len(parts) >= 4 else ""


def _is_single_project_candidate(path: str, sym_type: str, name: str) -> bool:
    if not path or _is_contract_skipped_path(path):
        return False
    if sym_type in {"api", "route"}:
        return True
    if sym_type in {"component", "composable", "store"}:
        return _has_single_project_feature_signal(path, name)
    return False


def _has_single_project_feature_signal(path: str, name: str = "") -> bool:
    lowered = f"{path}\n{name}".lower()
    if any(term in lowered for term in _CONTRACT_SURFACE_TERMS):
        return True
    parts = set(Path(path).parts)
    return bool(parts & _SINGLE_PROJECT_FEATURE_PARTS)


def _has_product_surface_signal(path: str, name: str = "") -> bool:
    lowered = f"{path}\n{name}".lower()
    if any(term in lowered for term in _CONTRACT_SURFACE_TERMS):
        return True
    parts = set(Path(path).parts)
    return bool(parts & _SINGLE_PROJECT_PRODUCT_PARTS)


def _is_single_project_entry(path: str, sym_type: str, name: str) -> bool:
    parts = set(Path(path).parts)
    stem = Path(path).stem.lower()
    bare_name = name.split(".")[-1].lower()
    if sym_type in {"api", "route"}:
        return True
    if bare_name in _SINGLE_PROJECT_ENTRY_NAMES or stem in _SINGLE_PROJECT_ENTRY_NAMES:
        return True
    return bool(parts & _SINGLE_PROJECT_ENTRY_PARTS)


def _is_contract_skipped_path(path: str) -> bool:
    parts = set(Path(path).parts)
    if parts & _SKIP_WORKSPACE_DIRS or parts & _CONTRACT_SKIP_PARTS:
        return True
    stem = Path(path).stem
    return any(stem.endswith(suffix) for suffix in _CONTRACT_SKIP_SUFFIXES)


def _check_agent_hygiene(root: Path, add_check) -> None:
    instruction_files = [root / "AGENTS.md", root / "CLAUDE.md"]
    present = [path for path in instruction_files if path.exists()]
    if not present:
        add_check("agent_hygiene", "warn", "No AGENTS.md or CLAUDE.md found")
    else:
        combined = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in present)
        lowered = combined.lower()
        mentions_indexer = "flyto-indexer" in lowered or "flyto-index" in lowered
        mentions_pre_change = (
            "search" in lowered
            and ("impact" in lowered or "task(action='plan')" in lowered or 'task(action="plan")' in lowered)
        )
        mentions_post_verify = "verify" in lowered
        status = "pass" if mentions_indexer and mentions_pre_change and mentions_post_verify else "warn"
        add_check(
            "agent_hygiene",
            status,
            "Agent instructions require indexer exploration and verification" if status == "pass" else "Agent instructions exist but do not clearly require pre-change exploration and post-change verification",
            metrics={
                "files": [path.name for path in present],
                "mentions_indexer": mentions_indexer,
                "mentions_pre_change": mentions_pre_change,
                "mentions_post_verify": mentions_post_verify,
            },
        )

    ignored = _generated_index_is_ignored(root)
    add_check(
        "generated_index_ignore",
        "pass" if ignored else "warn",
        ".flyto-index is ignored" if ignored else ".flyto-index is not ignored",
    )


def _check_policy_budget(
    root: Path,
    checks: list[dict[str, Any]],
    policy_path: str | Path | None = None,
) -> None:
    policy, source = _load_verify_policy(root, policy_path)
    if not policy:
        return

    warn_as_fail = set(_as_list(policy.get("warn_as_fail") or policy.get("fail_on_warn")))
    allow_warn = set(_as_list(policy.get("allow_warn") or policy.get("allow_warnings")))
    min_docs_score = _as_int(policy.get("min_docs_score"))

    violations: list[dict[str, Any]] = []
    for check in checks:
        name = check.get("name", "")
        status = check.get("status", "fail")
        if status == "warn" and ("*" in warn_as_fail or name in warn_as_fail) and name not in allow_warn:
            violations.append({
                "check": name,
                "rule": "warn_as_fail",
                "status": status,
            })
        if name == "docs_coverage" and min_docs_score is not None:
            score = (check.get("metrics") or {}).get("overall_score", 0)
            if isinstance(score, (int, float)) and score < min_docs_score:
                violations.append({
                    "check": name,
                    "rule": "min_docs_score",
                    "score": score,
                    "minimum": min_docs_score,
                })

    checks.append({
        "name": "policy_budget",
        "status": "fail" if violations else "pass",
        "summary": "Verify policy budget passed" if not violations else "Verify policy budget failed",
        "metrics": {
            "policy": str(source) if source else "",
            "warn_as_fail": sorted(warn_as_fail),
            "allow_warn": sorted(allow_warn),
            "min_docs_score": min_docs_score,
            "violations": violations,
        },
    })


def _check_mcp_registry(root: Path, add_check) -> None:
    """Verify MCP smart tool schemas and dispatch stay in sync."""
    if not (root / "src" / "tool_registry").exists():
        return

    try:
        from .tool_registry import SMART_TOOLS, SMART_TOOL_NAMES, has_tool
    except ImportError:
        try:
            from tool_registry import SMART_TOOLS, SMART_TOOL_NAMES, has_tool
        except ImportError as exc:
            add_check("mcp_registry", "fail", f"Cannot import tool registry: {exc}")
            return

    names = [tool.get("name", "") for tool in SMART_TOOLS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    missing_dispatch = sorted(name for name in names if name and not has_tool(name))
    derived_mismatch = sorted(set(names) ^ set(SMART_TOOL_NAMES))
    missing_schema = sorted(
        name for name, tool in zip(names, SMART_TOOLS)
        if not tool.get("inputSchema") or not tool.get("description")
    )

    problems = duplicates or missing_dispatch or derived_mismatch or missing_schema
    add_check(
        "mcp_registry",
        "fail" if problems else "pass",
        "MCP smart tools and dispatch are in sync" if not problems else "MCP smart tool registry has drift",
        metrics={
            "smart_tools": len(names),
            "duplicates": duplicates,
            "missing_dispatch": missing_dispatch,
            "derived_mismatch": derived_mismatch,
            "missing_schema": missing_schema,
        },
    )


def _generated_index_is_ignored(root: Path) -> bool:
    """Check .flyto-index ignore status using git when available."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", ".flyto-index"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return False
    content = gitignore.read_text(encoding="utf-8", errors="ignore")
    return ".flyto-index/" in content or ".flyto-index" in content


def _read_ci_files(root: Path) -> tuple[list[Path], str]:
    files: list[Path] = []
    for pattern in _CI_CANDIDATES:
        files.extend(sorted(root.glob(pattern)))
    readable = []
    chunks = []
    for path in files:
        if not path.is_file():
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
            readable.append(path)
        except OSError:
            continue
    return readable, "\n".join(chunks)


def _pyproject_name(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return ""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return ""
    name = data.get("project", {}).get("name", "")
    return str(name) if name else ""


def _package_manifest_entries(*values: Any) -> list[str]:
    entries: list[str] = []
    for value in values:
        if isinstance(value, dict):
            for key, item in value.items():
                entries.append(str(key).lstrip("/"))
                entries.append(str(item).lstrip("/"))
        elif isinstance(value, list):
            entries.extend(str(item).lstrip("/") for item in value)
        elif value:
            entries.append(str(value).lstrip("/"))
    return sorted({entry.replace("\\", "/") for entry in entries if entry})


def _git_changed_paths(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []

    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            paths.extend(part.strip().replace("\\", "/") for part in raw_path.split(" -> ") if part.strip())
        elif raw_path:
            paths.append(raw_path.replace("\\", "/"))
    return sorted(set(paths))


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def _load_verify_policy(root: Path, policy_path: str | Path | None = None) -> tuple[dict[str, Any], Path | None]:
    candidates = [Path(policy_path).resolve()] if policy_path else [
        root / ".flyto-rules.yaml",
        root / ".flyto-rules.yml",
        root / ".flyto-rules.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        if path.suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}, path
            verify = data.get("verify") if isinstance(data, dict) else None
            return (verify if isinstance(verify, dict) else {}), path
        return _parse_verify_yaml_block(path), path
    return {}, None


def _parse_verify_yaml_block(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    block_indent: int | None = None
    current_key = ""
    policy: dict[str, Any] = {}
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if block_indent is None:
            if stripped == "verify:":
                block_indent = indent
            continue
        if indent <= block_indent:
            break
        if stripped.startswith("- ") and current_key:
            items = policy.setdefault(current_key, [])
            if isinstance(items, list):
                items.append(_parse_policy_scalar(stripped[2:].strip()))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        policy[current_key] = [] if value == "" else _parse_policy_scalar(value)
    return policy


def _parse_policy_scalar(value: str) -> Any:
    value = value.strip().strip("'\"")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_policy_scalar(part.strip()) for part in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        return value


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _check_regression_gate(
    root: Path,
    checks: list[dict[str, Any]],
    baseline_path: Path,
    regression_only: bool,
) -> bool | None:
    """Add a regression gate check comparing current checks to a baseline result."""
    baseline = _load_baseline(baseline_path)
    if baseline is None:
        checks.append({
            "name": "regression_gate",
            "status": "fail",
            "summary": f"Baseline file not found or invalid: {baseline_path}",
            "metrics": {"baseline": str(baseline_path), "regressions": []},
        })
        return False if regression_only else None

    integrity_status, integrity_metrics = _baseline_integrity(root, baseline)
    regressions = _find_status_regressions(checks, baseline)
    checks.append({
        "name": "baseline_integrity",
        "status": integrity_status,
        "summary": "Baseline metadata matches this project" if integrity_status == "pass" else "Baseline metadata is incomplete or mismatched",
        "metrics": {"baseline": str(baseline_path), **integrity_metrics},
    })
    checks.append({
        "name": "regression_gate",
        "status": "fail" if regressions else "pass",
        "summary": "No new verification regressions" if not regressions else "New verification regressions detected",
        "metrics": {
            "baseline": str(baseline_path),
            "regressions": regressions,
            "regression_only": regression_only,
        },
    })
    return not regressions and integrity_status != "fail" if regression_only else None


def _load_baseline(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _baseline_integrity(root: Path, baseline: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    metadata = baseline.get("metadata") if isinstance(baseline.get("metadata"), dict) else {}
    problems: list[str] = []
    warnings: list[str] = []

    baseline_project = str(baseline.get("project") or "")
    metadata_project = str(metadata.get("project") or "")
    if baseline_project and baseline_project != root.name:
        problems.append("baseline project does not match current project")
    if metadata_project and metadata_project != root.name:
        problems.append("baseline metadata project does not match current project")
    if not metadata:
        warnings.append("baseline has no metadata")
    elif metadata.get("schema_version") != _VERIFY_RESULT_SCHEMA_VERSION:
        warnings.append("baseline schema version is different")
    if metadata.get("git_dirty") is True:
        warnings.append("baseline was created from a dirty working tree")

    status = "pass"
    if problems:
        status = "fail"
    elif warnings:
        status = "warn"
    return status, {
        "project": root.name,
        "baseline_project": baseline_project,
        "metadata_project": metadata_project,
        "schema_version": metadata.get("schema_version", ""),
        "git_head": metadata.get("git_head", ""),
        "git_dirty": metadata.get("git_dirty"),
        "problems": problems,
        "warnings": warnings,
    }


def _find_status_regressions(
    current_checks: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_checks = {
        check.get("name"): check
        for check in baseline.get("checks", [])
        if isinstance(check, dict) and check.get("name")
    }
    regressions: list[dict[str, Any]] = []
    for check in current_checks:
        name = check.get("name", "")
        if name in {"regression_gate", "baseline_integrity"}:
            continue
        current_status = check.get("status", "fail")
        baseline_status = (baseline_checks.get(name) or {}).get("status")
        if baseline_status is None:
            if current_status != "pass":
                regressions.append({
                    "check": name,
                    "baseline": "missing",
                    "current": current_status,
                    "reason": "new non-pass check",
                })
            continue
        if _STATUS_RANK.get(current_status, 3) > _STATUS_RANK.get(baseline_status, 3):
            regressions.append({
                "check": name,
                "baseline": baseline_status,
                "current": current_status,
                "reason": "status worsened",
            })
    return regressions


def _discover_workspace_projects(root: Path) -> list[Path]:
    if _looks_like_project(root):
        return [root]
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        return []
    projects = []
    for child in children:
        if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP_WORKSPACE_DIRS:
            continue
        if _looks_like_project(child):
            projects.append(child)
    return projects


def _looks_like_project(path: Path) -> bool:
    return any((path / marker).exists() for marker in _PROJECT_MARKERS)


def _project_has_changes(project: Path, base: str = "") -> bool:
    if not (project / ".git").exists():
        return True
    commands: list[list[str]]
    if base:
        commands = [
            ["git", "-C", str(project), "diff", "--name-only", f"{base}...HEAD"],
            ["git", "-C", str(project), "diff", "--name-only", f"{base}..HEAD"],
            ["git", "-C", str(project), "diff", "--name-only"],
            ["git", "-C", str(project), "diff", "--cached", "--name-only"],
            ["git", "-C", str(project), "ls-files", "--others", "--exclude-standard"],
        ]
    else:
        commands = [
            ["git", "-C", str(project), "diff", "--name-only"],
            ["git", "-C", str(project), "diff", "--cached", "--name-only"],
            ["git", "-C", str(project), "ls-files", "--others", "--exclude-standard"],
        ]
    saw_valid_git = False
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return True
        if result.returncode != 0:
            continue
        saw_valid_git = True
        if result.stdout.strip():
            return True
    return not saw_valid_git


def _flatten_report_checks(result: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    if "projects" not in result:
        return [(result.get("project", "project"), check, result.get("path", "")) for check in result.get("checks", [])]
    flattened = []
    for check in result.get("workspace_checks", []):
        flattened.append((result.get("workspace", "workspace"), check, result.get("path", "")))
    for project in result.get("projects", []):
        for check in project.get("checks", []):
            flattened.append((project.get("project", "project"), check, project.get("path", "")))
    return flattened


def _render_markdown_report(result: dict[str, Any]) -> str:
    title = "Flyto Workspace Verify" if "projects" in result else "Flyto Verify"
    name = result.get("workspace") or result.get("project") or "project"
    lines = [
        f"# {title}: {name}",
        "",
        f"- Status: {'PASS' if result.get('pass') else 'FAIL'}",
        f"- Path: `{result.get('path', '')}`",
        "",
        "| Project | Check | Status | Summary |",
        "|---|---|---|---|",
    ]
    for project, check, _path in _flatten_report_checks(result):
        lines.append(
            f"| {project} | {check.get('name', '')} | {check.get('status', '')} | "
            f"{str(check.get('summary', '')).replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


def _render_junit_report(result: dict[str, Any]) -> str:
    checks = _flatten_report_checks(result)
    failures = [item for item in checks if item[1].get("status") == "fail"]
    skipped = [item for item in checks if item[1].get("status") == "warn"]
    suite_name = html.escape(result.get("workspace") or result.get("project") or "flyto-verify")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuite name="{suite_name}" tests="{len(checks)}" failures="{len(failures)}" skipped="{len(skipped)}">',
    ]
    for project, check, _path in checks:
        case_name = html.escape(f"{project}.{check.get('name', '')}")
        lines.append(f'  <testcase classname="flyto.verify" name="{case_name}">')
        summary = html.escape(str(check.get("summary", "")))
        if check.get("status") == "fail":
            lines.append(f'    <failure message="{summary}">{summary}</failure>')
        elif check.get("status") == "warn":
            lines.append(f'    <skipped message="{summary}" />')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines) + "\n"


def _render_sarif_report(result: dict[str, Any]) -> str:
    sarif_results = []
    rules: dict[str, dict[str, Any]] = {}
    for project, check, path in _flatten_report_checks(result):
        status = check.get("status")
        rule_id = str(check.get("name", "verify"))
        rules.setdefault(rule_id, {
            "id": rule_id,
            "name": rule_id,
            "shortDescription": {"text": rule_id},
        })
        if status not in {"warn", "fail"}:
            continue
        sarif_results.append({
            "ruleId": rule_id,
            "level": "error" if status == "fail" else "warning",
            "message": {"text": f"{project}: {check.get('summary', '')}"},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": path or project},
                },
            }],
        })
    return json.dumps({
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "flyto-indexer",
                    "informationUri": "https://github.com/flytohub/flyto-indexer",
                    "rules": list(rules.values()),
                },
            },
            "results": sarif_results,
        }],
    }, ensure_ascii=False, indent=2)


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


def _load_index_json(root: Path) -> dict[str, Any]:
    index_path = root / ".flyto-index" / "index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _verification_metadata(root: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": _VERIFY_RESULT_SCHEMA_VERSION,
        "project": root.name,
        "git_head": _git_head(root),
        "git_dirty": bool(_git_changed_paths(root)) if (root / ".git").exists() else None,
        "check_count": len(checks),
        "check_fingerprint": _checks_fingerprint(checks),
    }


def _git_head(root: Path) -> str:
    if not (root / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _checks_fingerprint(checks: list[dict[str, Any]]) -> str:
    payload = [
        {
            "name": check.get("name", ""),
            "status": check.get("status", ""),
            "summary": check.get("summary", ""),
        }
        for check in checks
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _summarize_checks(checks: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks:
        status = str(check.get("status", "fail"))
        summary[status] = summary.get(status, 0) + 1
    return summary


def _finalize(
    root: Path,
    checks: list[dict[str, Any]],
    *,
    pass_override: bool | None = None,
) -> dict[str, Any]:
    summary = _summarize_checks(checks)
    return {
        "project": root.name,
        "path": str(root),
        "pass": pass_override if pass_override is not None else summary.get("fail", 0) == 0,
        "summary": summary,
        "metadata": _verification_metadata(root, checks),
        "checks": checks,
    }
