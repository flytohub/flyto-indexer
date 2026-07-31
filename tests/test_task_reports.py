"""Portable task evidence rendering tests."""

import csv
import io
import json

from src.task_reports import render_task_status, render_usage_report


def _report() -> dict:
    return {
        "run": {
            "task_id": "task-1",
            "run_id": "run-1",
            "project": "demo",
            "status": "passed",
            "variant": "indexer",
        },
        "usage": {
            "input_tokens": 500,
            "output_tokens": 100,
            "total_tokens": 600,
            "sources": ["reported"],
            "tool_calls": 8,
            "duration_ms": 1200,
        },
        "efficiency": {"verified_successes_per_1000_tokens": 1.666667},
        "comparison": {
            "available": True,
            "claim": "measured_reduction",
            "before_tokens": 1000,
            "after_tokens": 600,
            "saved_tokens": 400,
            "reduction_percent": 40.0,
            "quality_regression": False,
        },
    }


def test_table_report_labels_measurement_and_proof():
    rendered = render_usage_report(_report(), "table")

    assert "measured_reduction" in rendered
    assert "Quality regression   no" in rendered
    assert "ROI" not in rendered


def test_json_report_round_trips():
    assert json.loads(render_usage_report(_report(), "json"))["run"]["status"] == "passed"


def test_csv_report_is_machine_readable():
    rows = list(csv.DictReader(io.StringIO(render_usage_report(_report(), "csv"))))

    assert rows[0]["task_id"] == "task-1"
    assert rows[0]["reduction_percent"] == "40.0"


def test_csv_report_prevents_spreadsheet_formula_execution():
    report = _report()
    report["run"]["task_id"] = '=HYPERLINK("https://invalid")'

    rows = list(csv.DictReader(io.StringIO(render_usage_report(report, "csv"))))

    assert rows[0]["task_id"].startswith("'=")


def test_html_report_is_static_and_escapes_values():
    report = _report()
    report["run"]["task_id"] = "<script>alert(1)</script>"
    rendered = render_usage_report(report, "html")

    assert "<!doctype html>" in rendered
    assert "&lt;script&gt;" in rendered
    assert "<script>" not in rendered
    assert "No prompts, responses, or source code" in rendered


def test_status_only_reminds_when_handoff_is_required():
    active = render_task_status({"continuity": {"status": "active", "handoff_required": False}})
    actionable = render_task_status(
        {
            "continuity": {
                "status": "needs_handoff",
                "handoff_required": True,
                "task_id": "task-1",
                "remaining": ["verify"],
            }
        }
    )

    assert "not needed" in active
    assert "required before switching AI tools" in actionable
