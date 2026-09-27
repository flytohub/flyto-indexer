"""Baseline and regression comparison for verification results.

Keeping comparison policy separate prevents the verification runner from becoming
a mixed execution, presentation, and historical-policy authority.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .reporting import _finding_ids_from_check


_STATUS_RANK = {"pass": 0, "warn": 1, "fail": 2}
_VERIFY_RESULT_SCHEMA_VERSION = "2"


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
            continue
        if name == "quality_health":
            metric_regressions = _find_quality_metric_regressions(
                check,
                baseline_checks.get(name) or {},
            )
            if metric_regressions:
                regressions.append({
                    "check": name,
                    "baseline": baseline_status,
                    "current": current_status,
                    "reason": "quality metric worsened",
                    "metrics": metric_regressions,
                })
        if current_status not in {"warn", "fail"}:
            continue
        baseline_ids = _finding_ids_from_check(baseline_checks.get(name) or {})
        current_ids = _finding_ids_from_check(check)
        if not baseline_ids or not current_ids:
            continue
        new_ids = sorted(current_ids - baseline_ids)
        if new_ids:
            regressions.append({
                "check": name,
                "baseline": baseline_status,
                "current": current_status,
                "reason": "new finding",
                "new_finding_ids": new_ids,
            })
    return regressions

def _find_quality_metric_regressions(
    current: dict[str, Any],
    baseline: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare accepted quality debt without imposing an absolute threshold."""
    current_metrics = current.get("metrics") or {}
    baseline_metrics = baseline.get("metrics") or {}
    comparisons = (
        ("health_score", ("health_score",), "min"),
        (
            "complex_functions",
            ("complexity", "complex_functions"),
            "max",
        ),
        (
            "complexity_burden",
            ("complexity", "complexity_burden"),
            "max",
        ),
        ("dead_code", ("dead_code", "dead_count"), "max"),
        (
            "documentation_score",
            ("documentation", "score"),
            "min",
        ),
    )
    regressions = []
    for name, path, direction in comparisons:
        current_value = _nested_metric(current_metrics, path)
        baseline_value = _nested_metric(baseline_metrics, path)
        if not isinstance(current_value, (int, float)) or not isinstance(
            baseline_value,
            (int, float),
        ):
            continue
        worsened = (
            current_value < baseline_value
            if direction == "min"
            else current_value > baseline_value
        )
        if worsened:
            regressions.append({
                "metric": name,
                "baseline": baseline_value,
                "current": current_value,
            })
    return regressions

def _nested_metric(metrics: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = metrics
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value

