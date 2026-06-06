"""
Self-contained verification gate for flyto-indexer.

This module intentionally uses only stdlib + flyto-indexer internals. It is the
CLI/CI entry point an AI agent can run after code edits to prove the index,
impact graph, context lookup, and lightweight security scans still close.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .doc_scanner import scan_documentation
from .engine import IndexEngine
from .models import SymbolType
from .secret_scanner import scan_secrets


def run_verification(
    project_path: str | Path,
    *,
    full_scan: bool = False,
    query: str | None = None,
    symbol: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Run the no-external-dependency verification suite."""
    root = Path(project_path).resolve()
    checks: list[dict[str, Any]] = []

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
    _check_agent_hygiene(root, add_check)

    return _finalize(root, checks)


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


def _check_agent_hygiene(root: Path, add_check) -> None:
    instruction_files = [root / "AGENTS.md", root / "CLAUDE.md"]
    present = [path for path in instruction_files if path.exists()]
    if not present:
        add_check("agent_hygiene", "warn", "No AGENTS.md or CLAUDE.md found")
    else:
        combined = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in present)
        mentions_indexer = "flyto-indexer" in combined or "flyto-index" in combined
        mentions_verify = "verify" in combined or "scan" in combined
        status = "pass" if mentions_indexer and mentions_verify else "warn"
        add_check(
            "agent_hygiene",
            status,
            "Agent instructions reference indexer gates" if status == "pass" else "Agent instructions exist but do not clearly require indexer gates",
            metrics={"files": [path.name for path in present]},
        )

    ignored = _generated_index_is_ignored(root)
    add_check(
        "generated_index_ignore",
        "pass" if ignored else "warn",
        ".flyto-index is ignored" if ignored else ".flyto-index is not ignored",
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


def _finalize(root: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks:
        summary[check["status"]] = summary.get(check["status"], 0) + 1
    return {
        "project": root.name,
        "path": str(root),
        "pass": summary.get("fail", 0) == 0,
        "summary": summary,
        "checks": checks,
    }
