"""
Health scoring dimensions — security, complexity, dead code, coverage, docs.
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.profile")


def _is_test_file_fallback(path: str) -> bool:
    try:
        try:
            from ..analyzer.complexity import _is_test_file
        except ImportError:
            from analyzer.complexity import _is_test_file
        return _is_test_file(path)
    except ImportError:
        lower = path.lower()
        return any(pat in lower for pat in ("test_", "_test.", ".test.", ".spec.", "/test/", "/tests/"))


def _project_root_from_index_dir(index_dir: "Path") -> Optional["Path"]:
    if index_dir.exists():
        return index_dir.parent
    return None


def _status_from_score(score: int) -> str:
    if score >= 20:
        return "PASS"
    if score >= 10:
        return "WARN"
    return "FAIL"


def _security_dim(index_dir: "Path") -> tuple[int, int]:
    """Return (security_score, finding_count)."""
    try:
        try:
            from ..analyzer.security import SecurityScanner
        except ImportError:
            from analyzer.security import SecurityScanner

        project_root = _project_root_from_index_dir(index_dir)
        if not (project_root and project_root.exists()):
            return 25, 0

        scanner = SecurityScanner(project_root)
        report = scanner.analyze()
        finding_count = len(report.issues)
        penalty = 0.0
        sev_weights = {"critical": 3, "high": 1.5, "medium": 0.5}
        for issue in report.issues:
            penalty += sev_weights.get(issue.severity, 0)
        scaled = int(25 * penalty / (penalty + 50)) if penalty > 0 else 0
        return max(0, 25 - scaled), finding_count
    except Exception:
        return 25, 0


def _complexity_dim(complexity_summary: dict) -> tuple[int, int]:
    """Return (complexity_score, complex_count)."""
    func_count = complexity_summary.get("total_functions", 0)
    complex_count = complexity_summary.get("complex_functions", 0)
    if func_count <= 0:
        return 25, complex_count
    pct = complex_count / func_count
    score = max(0, int(25 * (1 - min(pct * 2, 1))))
    return score, complex_count


def _is_dead_symbol(sym: dict, sym_id: str, reverse_index: dict) -> bool:
    if sym.get("ref_count", sym.get("reference_count", 0)) != 0:
        return False
    if reverse_index.get(sym_id, []):
        return False
    name = sym.get("name", "")
    path = sym.get("path", "")
    sym_type = sym.get("type", "")
    exports = sym.get("exports") or []
    language = (sym.get("language") or "").lower()
    path_lower = path.replace("\\", "/").lower()
    ignored_markers = (
        "/__tests__/", "__tests__/", "/tests/", "tests/", "/test/", "test/",
        ".test.", ".spec.", "_test.", "/fixtures/", "fixtures/",
        "/testdata/", "testdata/", ".semgrep/fixtures/", "/examples/", "examples/",
    )
    if any(marker in path_lower for marker in ignored_markers):
        return False
    if name.startswith("_"):
        return False
    if exports and language == "go":
        return False
    if path.endswith(".go") and name and name[0].isupper():
        return False
    if exports and path_lower.startswith(("scripts/", "bin/", "cmd/", "tools/")):
        return False
    if exports and sym_type in ("class", "interface", "type"):
        parts = set(re.split(r"[/_.-]+", path_lower))
        contract_markers = {
            "model", "models", "dto", "schema", "schemas", "type", "types",
            "contract", "contracts", "interface", "interfaces", "entity",
            "entities", "row", "rows", "event", "events",
        }
        if parts & contract_markers:
            return False
    if sym_type in ("interface", "type"):
        return False
    return True


def _dead_code_dim(symbols: dict, reverse_index: dict) -> tuple[int, int, list]:
    """Return (dead_score, dead_count, dead_symbols_list)."""
    non_test_symbols = {
        k: v for k, v in symbols.items()
        if not _is_test_file_fallback(v.get("path", ""))
        and v.get("type", "") in ("function", "method", "class", "component", "composable")
    }
    dead_list = []
    for sym_id, sym in non_test_symbols.items():
        if _is_dead_symbol(sym, sym_id, reverse_index):
            dead_list.append({
                "name": sym.get("name", ""),
                "path": sym.get("path", ""),
                "line": sym.get("line", 0),
                "type": sym.get("type", ""),
            })
    dead_count = len(dead_list)
    dead_pct = dead_count / max(len(non_test_symbols), 1)
    score = max(0, int(25 * (1 - min(dead_pct * 2, 1))))
    return score, dead_count, dead_list


def _coverage_dim(index_dir: "Path") -> tuple[int, int]:
    """Return (coverage_score, coverage_pct)."""
    try:
        project_root = _project_root_from_index_dir(index_dir)
        if not project_root:
            return 0, 0
        if not (project_root / ".coverage").exists():
            return 0, 0
        try:
            proc = subprocess.run(
                ["python", "-m", "coverage", "report", "--format=total"],
                capture_output=True, text=True, timeout=30,
                cwd=str(project_root),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 0, 0
        if proc.returncode != 0 or not proc.stdout.strip():
            return 0, 0
        try:
            pct = int(float(proc.stdout.strip()))
        except ValueError:
            return 0, 0
        return min(25, round(pct / 4)), pct
    except Exception:
        return 0, 0


def _doc_score(index_dir: "Path") -> int:
    try:
        try:
            from ..doc_scanner import scan_documentation
        except ImportError:
            from doc_scanner import scan_documentation
        project_root = _project_root_from_index_dir(index_dir)
        if not (project_root and project_root.exists()):
            return 0
        return scan_documentation(str(project_root)).overall_score
    except Exception:
        return 0


def _doc_penalty_for_score(doc_score: int) -> int:
    if doc_score < 30:
        return -10
    if doc_score < 50:
        return -5
    if doc_score >= 70:
        return 5
    return 0


def _select_active_dims(
    project_type: str,
    security_score: int,
    complexity_score: int,
    dead_score: int,
    doc_score_val: int,
) -> dict:
    """Pick which dimensions count for the overall score for this project type."""
    if project_type in ("backend", "fullstack"):
        return {"security": security_score, "complexity": complexity_score, "dead_code": dead_score}
    if project_type == "frontend":
        return {
            "complexity": complexity_score,
            "dead_code": dead_score,
            "security": min(25, security_score + 10),
        }
    if project_type == "library":
        return {"dead_code": dead_score, "complexity": complexity_score}
    if project_type == "mobile":
        return {"complexity": complexity_score, "dead_code": dead_score}
    if project_type in ("static", "unknown", ""):
        return {"documentation": min(25, round(doc_score_val / 4))}
    return {"security": security_score, "complexity": complexity_score, "dead_code": dead_score}


def grade_for_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def empty_health_dimensions() -> dict:
    return {
        "security": {"score": 25, "max": 25, "status": "PASS", "finding_count": 0},
        "complexity": {"score": 25, "max": 25, "status": "PASS", "complex_count": 0},
        "dead_code": {"score": 25, "max": 25, "status": "PASS", "dead_count": 0},
        "overall": {"score": 100, "max": 100, "grade": "A"},
    }


def compute_health_dimensions(
    symbols: dict,
    reverse_index: dict,
    index_dir: "Path",
    complexity_summary: dict,
    project_type: str = "",
) -> dict:
    """Compute health score dimensions based on project type."""
    if not symbols:
        return empty_health_dimensions()

    security_score, finding_count = _security_dim(index_dir)
    complexity_score, complex_count = _complexity_dim(complexity_summary)
    dead_score, dead_count, dead_list = _dead_code_dim(symbols, reverse_index)
    coverage_score, coverage_pct = _coverage_dim(index_dir)
    doc_score_val = _doc_score(index_dir)
    doc_penalty = _doc_penalty_for_score(doc_score_val)

    has_coverage = coverage_pct > 0 or coverage_score > 0
    active_dims = _select_active_dims(
        project_type, security_score, complexity_score, dead_score, doc_score_val,
    )
    if has_coverage:
        active_dims["coverage"] = coverage_score

    max_possible = len(active_dims) * 25
    raw_score = sum(active_dims.values())
    overall_score = round(raw_score / max_possible * 100) if max_possible > 0 else 50
    if project_type not in ("static", "unknown", ""):
        overall_score += doc_penalty
    overall_score = max(0, min(100, overall_score))

    result: dict = {}
    if "security" in active_dims:
        result["security"] = {
            "score": security_score, "max": 25,
            "status": _status_from_score(security_score),
            "finding_count": finding_count,
        }
    if "complexity" in active_dims:
        result["complexity"] = {
            "score": complexity_score, "max": 25,
            "status": _status_from_score(complexity_score),
            "complex_count": complex_count,
        }
    if "dead_code" in active_dims:
        result["dead_code"] = {
            "score": dead_score, "max": 25,
            "status": _status_from_score(dead_score),
            "dead_count": dead_count,
            "dead_symbols": dead_list[:50],
        }
    if "documentation" in active_dims:
        doc_score_dim = active_dims["documentation"]
        result["documentation"] = {
            "score": doc_score_dim, "max": 25,
            "status": _status_from_score(doc_score_dim),
        }
    if has_coverage and "coverage" in active_dims:
        coverage_status = _status_from_score(coverage_score) if has_coverage else "N/A"
        result["coverage"] = {
            "score": coverage_score, "max": 25,
            "status": coverage_status,
            "coverage_pct": coverage_pct,
        }

    result["overall"] = {"score": int(overall_score), "max": 100, "grade": grade_for_score(overall_score)}
    return result


def build_health_dims(idx: dict, project_type: str) -> dict:
    health_inputs = idx.get("_health_inputs")
    if not health_inputs:
        return {"overall": {"score": 0, "max": 100, "grade": "?"}}
    return compute_health_dimensions(
        health_inputs["symbols"],
        health_inputs["reverse_index"],
        health_inputs["index_dir"],
        health_inputs["complexity_summary"],
        project_type,
    )


def _error_handling_penalty(data: dict) -> int:
    """Penalty for poor error handling: bare except, empty except, low coverage."""
    if not isinstance(data, dict):
        return 0
    coverage = data.get("coverage_pct", 100)
    issue_count = data.get("issue_count", 0)
    if coverage >= 20 or issue_count <= 5:
        return 0
    by_cat = data.get("by_category", {})
    raw = by_cat.get("bare_except", 0) * 3 + by_cat.get("empty_except", 0) * 2
    return min(raw, 10)


def _tech_debt_penalty(data: dict) -> int:
    """Penalty for excessive high-severity tech debt markers (FIXME/HACK/BUG)."""
    if not isinstance(data, dict):
        return 0
    high = data.get("high_count", 0)
    if high <= 10:
        return 0
    return min(high // 5, 5)


def _perf_patterns_penalty(data: dict) -> int:
    """Penalty for performance anti-patterns: N+1, sync-in-async, missing timeout."""
    if not isinstance(data, dict):
        return 0
    by_cat = data.get("by_category", {})
    raw = (by_cat.get("n_plus_1", 0) * 4
           + by_cat.get("sync_in_async", 0) * 3
           + by_cat.get("missing_timeout", 0))
    return min(raw, 10)


def _import_health_penalty(data: dict) -> int:
    """Penalty for architectural issues: god modules, circular dependencies."""
    if not isinstance(data, dict):
        return 0
    god = data.get("god_module_count", 0)
    circular = data.get("circular_dep_count", 0)
    if god == 0 and circular == 0:
        return 0
    return min(god * 2 + circular * 3, 5)


def adjust_overall_health(
    overall: dict,
    secrets_data: dict, taint_data: dict, iac_data: dict,
    license_policy_issues: list, documentation_data: dict,
    project_type: str,
    error_handling_data: dict = None,
    tech_debt_data: dict = None,
    perf_patterns_data: dict = None,
    import_health_data: dict = None,
) -> dict:
    """Apply penalties on top of dimension-derived score.

    Penalty sources (all capped individually, cumulative):
      - Secrets: logistic, max -20
      - Taint: linear, max -15
      - IaC: logistic, max -15
      - License: per-violation
      - Documentation: -5 if score < 30
      - Error handling: max -10 (bare/empty except when coverage < 20%)
      - Tech debt: max -5 (>10 high-severity markers)
      - Perf patterns: max -10 (N+1, sync-in-async)
      - Import health: max -5 (god modules, circular deps)
    """
    score = overall.get("score", 0)

    if isinstance(secrets_data, dict):
        raw = (secrets_data.get("critical", 0) * 5 +
               secrets_data.get("high", 0) * 3 +
               secrets_data.get("medium", 0))
        if raw > 0:
            score -= int(20 * raw / (raw + 30))

    if isinstance(taint_data, dict):
        high = taint_data.get("high_risk_count", 0)
        if high > 0:
            score -= min(high * 3, 15)

    if isinstance(iac_data, dict):
        raw = iac_data.get("critical", 0) * 5 + iac_data.get("high", 0) * 3
        if raw > 0:
            score -= int(15 * raw / (raw + 30))

    for issue in license_policy_issues:
        risk = issue.get("risk_level")
        if risk == "critical":
            score -= 5
        elif risk == "high":
            score -= 2

    if project_type not in ("static", "unknown", "") and isinstance(documentation_data, dict):
        if documentation_data.get("overall_score", 0) < 30:
            score -= 5

    # Engineering intelligence penalties (v2.11+)
    score -= _error_handling_penalty(error_handling_data)
    score -= _tech_debt_penalty(tech_debt_data)
    score -= _perf_patterns_penalty(perf_patterns_data)
    score -= _import_health_penalty(import_health_data)

    score = max(0, min(100, score))
    return {"score": score, "max": 100, "grade": grade_for_score(score)}
