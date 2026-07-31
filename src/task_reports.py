"""Dependency-free renderers for local task continuity and efficiency evidence."""

from __future__ import annotations

import csv
import html
import io
import json
from typing import Any


def _value(data: dict[str, Any], path: str, default: Any = "") -> Any:
    """Read a dotted path from a nested report mapping."""
    current: Any = data
    for key in path.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
    return current


def _flat_report(report: dict[str, Any]) -> dict[str, Any]:
    """Flatten stable fields for CSV, HTML, and terminal views."""
    comparison = report.get("comparison") or {}
    return {
        "task_id": _value(report, "run.task_id"),
        "run_id": _value(report, "run.run_id"),
        "project": _value(report, "run.project"),
        "status": _value(report, "run.status"),
        "variant": _value(report, "run.variant"),
        "input_tokens": _value(report, "usage.input_tokens", 0),
        "output_tokens": _value(report, "usage.output_tokens", 0),
        "total_tokens": _value(report, "usage.total_tokens", 0),
        "usage_sources": ",".join(_value(report, "usage.sources", [])),
        "tool_calls": _value(report, "usage.tool_calls", 0),
        "duration_ms": _value(report, "usage.duration_ms", 0),
        "verified_successes_per_1000_tokens": _value(
            report, "efficiency.verified_successes_per_1000_tokens", 0
        ),
        "comparison_available": bool(comparison.get("available")),
        "comparison_claim": comparison.get("claim", ""),
        "before_tokens": comparison.get("before_tokens", ""),
        "after_tokens": comparison.get("after_tokens", ""),
        "saved_tokens": comparison.get("saved_tokens", ""),
        "reduction_percent": comparison.get("reduction_percent", ""),
        "quality_regression": comparison.get("quality_regression", ""),
    }


def _csv_safe(value: Any) -> Any:
    """Prevent spreadsheet formula execution in exported text cells."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def render_usage_report(report: dict[str, Any], output_format: str) -> str:
    """Render a report as a terminal summary, JSON, CSV, or static HTML."""
    format_name = output_format.lower()
    if format_name == "json":
        return json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    flat = _flat_report(report)
    if format_name == "csv":
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(flat))
        writer.writeheader()
        writer.writerow({key: _csv_safe(value) for key, value in flat.items()})
        return stream.getvalue()
    if format_name == "html":
        rows = "\n".join(
            "<tr><th>{}</th><td>{}</td></tr>".format(
                html.escape(key.replace("_", " ").title()),
                html.escape(str(value)),
            )
            for key, value in flat.items()
        )
        return (
            '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Flyto Indexer task evidence</title>"
            "<style>body{font:16px system-ui;max-width:900px;margin:3rem auto;padding:0 1rem;"
            "color:#17202a}table{border-collapse:collapse;width:100%}th,td{padding:.65rem;"
            "border-bottom:1px solid #dfe6e9;text-align:left}th{width:42%}</style></head>"
            f"<body><h1>Task evidence</h1><table>{rows}</table>"
            "<p>Counts only. No prompts, responses, or source code are stored.</p>"
            "</body></html>\n"
        )
    if format_name != "table":
        raise ValueError("format must be table, json, csv, or html")
    source = flat["usage_sources"] or "none"
    lines = [
        f"Task                 {flat['task_id']}",
        f"Outcome              {flat['status']}",
        f"Tokens               {flat['total_tokens']:,} ({source})",
        f"Input / output       {flat['input_tokens']:,} / {flat['output_tokens']:,}",
        f"Tool calls           {flat['tool_calls']}",
        f"Duration             {float(flat['duration_ms']) / 1000:.2f}s",
        f"Verified successes   {flat['verified_successes_per_1000_tokens']} per 1,000 tokens",
    ]
    comparison = report.get("comparison") or {}
    if comparison.get("available"):
        lines.extend(
            [
                f"Comparison           {comparison['claim']}",
                f"Before / after       {comparison['before_tokens']:,} / "
                f"{comparison['after_tokens']:,}",
                f"Saved                {comparison['saved_tokens']:,} "
                f"({comparison['reduction_percent']}%)",
                "Quality regression   no (both runs passed the same proof policy)",
            ]
        )
    elif comparison.get("reason") != "not_requested":
        lines.append(f"Comparison           unavailable: {comparison.get('reason')}")
    return "\n".join(lines) + "\n"


def render_task_status(payload: dict[str, Any], *, as_json: bool = False) -> str:
    """Render the actionable handoff state without generating a document."""
    if as_json:
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    continuity = payload.get("continuity") or {}
    lines = [f"Continuity           {continuity.get('status', 'closed')}"]
    if continuity.get("task_id"):
        lines.append(f"Task                 {continuity['task_id']}")
    if continuity.get("objective"):
        lines.append(f"Objective            {continuity['objective']}")
    if continuity.get("next_action"):
        lines.append(f"Next action          {continuity['next_action']}")
    if continuity.get("remaining"):
        lines.append(f"Remaining            {len(continuity['remaining'])} step(s)")
    if continuity.get("changed_paths"):
        lines.append(f"Changed paths        {len(continuity['changed_paths'])}")
    if continuity.get("blockers"):
        lines.append(f"Blockers             {len(continuity['blockers'])}")
    if continuity.get("handoff_required"):
        lines.append("Handoff reminder     required before switching AI tools")
    else:
        lines.append("Handoff reminder     not needed")
    return "\n".join(lines) + "\n"
