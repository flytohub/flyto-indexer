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
    return {
        "workspace": root.name,
        "path": str(root),
        "pass": summary["fail"] == 0,
        "summary": summary,
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


def _finalize(
    root: Path,
    checks: list[dict[str, Any]],
    *,
    pass_override: bool | None = None,
) -> dict[str, Any]:
    summary = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks:
        summary[check["status"]] = summary.get(check["status"], 0) + 1
    return {
        "project": root.name,
        "path": str(root),
        "pass": pass_override if pass_override is not None else summary.get("fail", 0) == 0,
        "summary": summary,
        "metadata": _verification_metadata(root, checks),
        "checks": checks,
    }
