"""Verification report formatting and finding identity helpers.

This module owns presentation only; verification policy and check execution stay
in :mod:`src.verify`.
"""

from __future__ import annotations

import html
import json
from typing import Any

from ..finding_identity import stable_finding_id


def format_verification(result: dict[str, Any]) -> str:
    """Human-readable verification report."""
    lines = [
        f"Flyto2 Verify: {result['project']}",
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
        f"Flyto2 Workspace Verify: {result['workspace']}",
        f"  Path:     {result['path']}",
        f"  Status:   {'PASS' if result['pass'] else 'FAIL'}",
        f"  Projects: {result['summary']['pass']} pass, {result['summary']['warn']} warn, "
        f"{result['summary']['fail']} fail, {result['summary'].get('skipped', 0)} skipped",
        f"  Workspace checks: {result['summary'].get('workspace_warn', 0)} warn, "
        f"{result['summary'].get('workspace_fail', 0)} fail",
        "",
    ]
    # Only the counts used to be printed here, so a workspace-level finding was
    # visible as the digit in "1 warn" and nowhere else -- the reader could not
    # tell which check spoke or what it said. The single-project report has
    # always named every check; this one now does too.
    for check in result.get("workspace_checks") or []:
        lines.append(f"[{check['status'].upper()}] {check['name']}: {check['summary']}")
        if check["status"] != "pass":
            metrics = check.get("metrics") or {}
            if metrics:
                compact = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
                if len(compact) > 280:
                    compact = compact[:277] + "..."
                lines.append(f"  {compact}")
    if result.get("workspace_checks"):
        lines.append("")
    for project in result["projects"]:
        summary = project["summary"]
        status = "PASS" if project["pass"] else "FAIL"
        lines.append(
            f"[{status}] {project['project']}: "
            f"{summary.get('pass', 0)} pass, {summary.get('warn', 0)} warn, {summary.get('fail', 0)} fail"
        )
    return "\n".join(lines)

def _finding_ids_from_check(check: dict[str, Any]) -> set[str]:
    """Collect nested finding IDs without treating arbitrary metric strings as findings."""
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            finding_id = value.get("finding_id")
            if isinstance(finding_id, str) and finding_id:
                found.add(finding_id)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(check)
    return found

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
    title = "Flyto2 Workspace Verify" if "projects" in result else "Flyto2 Verify"
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
        metrics = check.get("metrics") if isinstance(check.get("metrics"), dict) else {}
        samples = [
            sample
            for sample in metrics.get("samples", [])
            if isinstance(sample, dict) and sample.get("finding_id")
        ]
        candidates = samples or [{
            "finding_id": check.get("finding_id"),
            "file": path or project,
        }]
        for candidate in candidates:
            location_path = (
                candidate.get("sink_file")
                or candidate.get("file")
                or path
                or project
            )
            line = candidate.get("sink_line") or candidate.get("line")
            physical_location: dict[str, Any] = {
                "artifactLocation": {"uri": str(location_path)},
            }
            if isinstance(line, int) and line > 0:
                physical_location["region"] = {"startLine": line}
            finding_id = str(candidate.get("finding_id") or stable_finding_id(
                f"verify/{rule_id}",
                location_path,
                anchor=project,
            ))
            detail = candidate.get("category") or candidate.get("pattern")
            message = f"{project}: {check.get('summary', '')}"
            if detail:
                message = f"{message} ({detail})"
            sarif_results.append({
                "ruleId": rule_id,
                "level": "error" if status == "fail" else "warning",
                "message": {"text": message},
                "locations": [{"physicalLocation": physical_location}],
                "partialFingerprints": {
                    "flytoFindingId/v1": finding_id,
                    "flytoFindingFingerprint/v1": str(
                        candidate.get("fingerprint") or finding_id
                    ),
                },
                "properties": {
                    "findingId": finding_id,
                    "findingSchema": candidate.get("schema"),
                    "confidence": candidate.get("confidence"),
                    "trace": candidate.get("trace"),
                    "suppression": candidate.get("suppression"),
                    "project": project,
                },
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

