"""CLI integration tests for continuity and usage evidence."""

import json
from pathlib import Path

from src.cli import build_parser
from src.task_cli import (
    execute_task_status,
    execute_usage_record,
    execute_usage_report,
)
from src.task_runs import TaskRunStore, default_task_db
from src.task_usage import TokenUsage


def _planned_store(root: Path):
    store = TaskRunStore(default_task_db(root))
    run = store.start_task(
        "task-cli",
        project=root.name,
        objective="Verify CLI evidence",
        project_root=root,
        base_commit="abc123",
    )
    return store, run


def test_task_status_on_new_project_does_not_create_files(tmp_path):
    args = build_parser().parse_args(["task-status", str(tmp_path), "--json"])

    payload = json.loads(execute_task_status(args))

    assert payload["continuity"]["status"] == "closed"
    assert not (tmp_path / ".flyto-index").exists()


def test_usage_record_accepts_provider_metadata_and_is_idempotent(tmp_path):
    store, run = _planned_store(tmp_path)
    argv = [
        "usage-record",
        run["run_id"],
        str(tmp_path),
        "--provider",
        "openai",
        "--model",
        "gpt-5",
        "--usage",
        '{"input_tokens":120,"output_tokens":30}',
        "--event-id",
        "event-cli",
    ]
    args = build_parser().parse_args(argv)

    assert execute_usage_record(args)["recorded"] is True
    assert execute_usage_record(args)["recorded"] is False
    assert store.aggregate_usage(run["run_id"])["total_tokens"] == 150


def test_usage_record_character_estimate_and_json_report(tmp_path):
    store, run = _planned_store(tmp_path)
    record_args = build_parser().parse_args(
        [
            "usage-record",
            run["run_id"],
            str(tmp_path),
            "--provider",
            "local",
            "--model",
            "unknown",
            "--estimated-input-chars",
            "400",
            "--estimated-output-chars",
            "80",
        ]
    )
    execute_usage_record(record_args)
    store.finish_task(run["run_id"], success=True, verification={"pass": True})
    report_args = build_parser().parse_args(
        ["usage-report", str(tmp_path), "--task", run["run_id"], "--format", "json"]
    )

    report = json.loads(execute_usage_report(report_args))

    assert report["usage"]["total_tokens"] == 120
    assert report["usage"]["sources"] == ["estimated"]
    assert report["comparison"] == {"available": False, "reason": "not_requested"}


def test_usage_report_writes_portable_static_html(tmp_path):
    store, run = _planned_store(tmp_path)
    store.record_usage(
        run["run_id"],
        TokenUsage(10, 2),
        provider="openai",
        model="gpt-5",
    )
    output = tmp_path / "reports" / "evidence.html"
    args = build_parser().parse_args(
        [
            "usage-report",
            str(tmp_path),
            "--task",
            run["run_id"],
            "--format",
            "html",
            "--output",
            str(output),
        ]
    )

    message = execute_usage_report(args)

    assert output.is_file()
    assert "<!doctype html>" in output.read_text(encoding="utf-8")
    assert str(output) in message


def test_parser_exposes_only_the_three_compact_evidence_commands():
    parser = build_parser()

    assert parser.parse_args(["task-status"]).command == "task-status"
    assert (
        parser.parse_args(
            [
                "usage-record",
                "task-1",
                "--provider",
                "openai",
                "--model",
                "gpt-5",
                "--estimated-input-chars",
                "1",
            ]
        ).command
        == "usage-record"
    )
    assert parser.parse_args(["usage-report"]).command == "usage-report"
