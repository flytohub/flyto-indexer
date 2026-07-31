"""Local continuity, privacy, retention, and honest comparison tests."""

import sqlite3
from pathlib import Path

import pytest

from src.task_runs import TaskRunStore, read_task_continuity
from src.task_usage import TokenUsage


def _context() -> dict:
    return {
        "experiment_id": "exp-1",
        "task_fingerprint": "task-fingerprint",
        "repo_commit": "abc123",
        "provider": "openai",
        "model": "gpt-5",
        "tool_policy": "same-tools-v1",
        "verification_policy": "tests-and-lint-v1",
        "sample_count": 100,
    }


def _start(store: TaskRunStore, task_id: str, root: Path, **kwargs):
    return store.start_task(
        task_id,
        project=root.name,
        objective="Finish auth migration",
        project_root=root,
        base_commit="abc123",
        **kwargs,
    )


def test_read_continuity_is_read_only_when_database_is_absent(tmp_path):
    result = read_task_continuity(tmp_path, project=tmp_path.name)

    assert result == {"status": "closed", "handoff_required": False}
    assert not (tmp_path / ".flyto-index").exists()


def test_continuity_warns_only_for_actionable_state(tmp_path):
    store = TaskRunStore(tmp_path / ".flyto-index" / "task-runs.sqlite")
    run = _start(store, "task-1", tmp_path)

    assert store.continuity(project=tmp_path.name)["handoff_required"] is False

    store.update_continuity(
        run["run_id"],
        completed_steps=["Mapped dependencies"],
        remaining_steps=["Run browser proof"],
        changed_paths=["src/auth.py"],
        next_action="Run browser proof",
    )
    continuity = store.continuity(project=tmp_path.name)

    assert continuity["status"] == "needs_handoff"
    assert continuity["handoff_required"] is True
    assert continuity["remaining"] == ["Run browser proof"]

    store.finish_task(run["run_id"], success=True, verification={"pass": True})
    assert store.continuity(project=tmp_path.name) == {
        "status": "closed",
        "handoff_required": False,
    }


def test_storage_schema_and_sanitization_exclude_raw_development_content(tmp_path):
    store = TaskRunStore(tmp_path / ".flyto-index" / "task-runs.sqlite")
    run = store.start_task(
        "task-secret",
        project=tmp_path.name,
        objective="password=unsafe /Users/alice/private.py ```source code```",
        project_root=tmp_path,
        base_commit="abc123",
    )
    columns = store.schema_columns()
    stored = store.get_run(run["run_id"])

    forbidden = {"prompt", "response", "source_code", "provider_metadata"}
    assert forbidden.isdisjoint(columns["task_runs"])
    assert forbidden.isdisjoint(columns["usage_events"])
    assert "unsafe" not in stored["objective"]
    assert "/Users/alice" not in stored["objective"]
    assert "source code" not in stored["objective"]


def test_usage_events_are_idempotent(tmp_path):
    store = TaskRunStore(tmp_path / "runs.sqlite")
    run = _start(store, "task-usage", tmp_path)
    usage = TokenUsage(100, 20)

    first = store.record_usage(
        run["run_id"], usage, provider="openai", model="gpt-5", event_id="event-1"
    )
    second = store.record_usage(
        run["run_id"], usage, provider="openai", model="gpt-5", event_id="event-1"
    )

    assert first["recorded"] is True
    assert second["recorded"] is False
    assert store.aggregate_usage(run["run_id"])["events"] == 1

    other = _start(store, "task-usage-other", tmp_path)
    cross_run = store.record_usage(
        other["run_id"], usage, provider="openai", model="gpt-5", event_id="event-1"
    )
    assert cross_run["recorded"] is True


@pytest.mark.parametrize(
    ("usage", "duration_ms"),
    [
        (TokenUsage(-1, 2), 0),
        (TokenUsage(1, 2, source="invented"), 0),
        (TokenUsage(1, 2), float("nan")),
    ],
)
def test_record_usage_rejects_invalid_programmatic_evidence(tmp_path, usage, duration_ms):
    store = TaskRunStore(tmp_path / "runs.sqlite")
    run = _start(store, "invalid-usage", tmp_path)

    with pytest.raises(ValueError):
        store.record_usage(
            run["run_id"],
            usage,
            provider="openai",
            model="gpt-5",
            duration_ms=duration_ms,
        )


def test_honest_paired_comparison_requires_equal_proof_and_measurement(tmp_path):
    store = TaskRunStore(tmp_path / "runs.sqlite")
    baseline = _start(
        store,
        "paired-task",
        tmp_path,
        comparison_context=_context(),
        variant="control",
    )
    store.record_usage(
        baseline["run_id"],
        TokenUsage(900, 100),
        provider="openai",
        model="gpt-5",
    )
    store.finish_task(baseline["run_id"], success=True, verification={"pass": True})
    current = _start(
        store,
        "paired-task",
        tmp_path,
        comparison_context=_context(),
        variant="indexer",
    )
    store.record_usage(
        current["run_id"],
        TokenUsage(500, 100),
        provider="openai",
        model="gpt-5",
    )
    store.finish_task(current["run_id"], success=True, verification={"pass": True})

    comparison = store.compare_runs(baseline["run_id"], current["run_id"])

    assert baseline["run_id"] != current["run_id"]
    assert comparison["available"] is True
    assert comparison["claim"] == "measured_reduction"
    assert comparison["reduction_percent"] == 40.0
    assert comparison["quality_regression"] is False


def test_comparison_refuses_unverified_or_mismatched_runs(tmp_path):
    store = TaskRunStore(tmp_path / "runs.sqlite")
    baseline = _start(
        store,
        "paired-task",
        tmp_path,
        comparison_context=_context(),
        variant="control",
    )
    store.record_usage(baseline["run_id"], TokenUsage(100, 10), provider="openai", model="gpt-5")
    current = _start(
        store,
        "paired-task",
        tmp_path,
        comparison_context={**_context(), "verification_policy": "weaker"},
        variant="indexer",
    )
    store.record_usage(current["run_id"], TokenUsage(50, 10), provider="openai", model="gpt-5")

    result = store.compare_runs(baseline["run_id"], current["run_id"])

    assert result == {"available": False, "reason": "comparison_context_mismatch"}


def test_task_cannot_be_marked_passed_without_positive_verification(tmp_path):
    store = TaskRunStore(tmp_path / "runs.sqlite")
    run = _start(store, "task-unverified", tmp_path)

    with pytest.raises(ValueError, match="positive verification"):
        store.finish_task(run["run_id"], success=True, verification={})


def test_comparison_refuses_usage_that_disagrees_with_declared_provider(tmp_path):
    store = TaskRunStore(tmp_path / "runs.sqlite")
    baseline = _start(
        store,
        "provider-task",
        tmp_path,
        comparison_context=_context(),
        variant="control",
    )
    store.record_usage(baseline["run_id"], TokenUsage(100, 10), provider="custom", model="gpt-5")
    store.finish_task(baseline["run_id"], success=True, verification={"pass": True})
    current = _start(
        store,
        "provider-task",
        tmp_path,
        comparison_context=_context(),
        variant="indexer",
    )
    store.record_usage(current["run_id"], TokenUsage(50, 10), provider="custom", model="gpt-5")
    store.finish_task(current["run_id"], success=True, verification={"pass": True})

    assert store.compare_runs(baseline["run_id"], current["run_id"]) == {
        "available": False,
        "reason": "declared_measurement_mismatch",
    }


def test_comparison_context_drops_arbitrary_provider_payload(tmp_path):
    store = TaskRunStore(tmp_path / "runs.sqlite")
    run = _start(
        store,
        "task-context",
        tmp_path,
        comparison_context={**_context(), "prompt": "do not store me"},
        variant="control",
    )

    assert "prompt" not in run["comparison_context"]
    assert "do not store me" not in str(run)


def test_prune_bounds_terminal_history_and_keeps_active_task(tmp_path):
    store = TaskRunStore(tmp_path / "runs.sqlite")
    for number in range(4):
        run = _start(store, f"task-{number}", tmp_path)
        store.finish_task(run["run_id"], success=True, verification={"pass": True})
    active = _start(store, "active-task", tmp_path)

    deleted = store.prune(max_runs=2)
    runs = store.list_runs(limit=20)

    assert deleted["overflow_runs_deleted"] == 2
    assert len(runs) == 3
    assert any(run["run_id"] == active["run_id"] for run in runs)


def test_readonly_store_rejects_writes(tmp_path):
    path = tmp_path / "runs.sqlite"
    store = TaskRunStore(path)
    _start(store, "task-ro", tmp_path)
    readonly = TaskRunStore(path, readonly=True)

    with pytest.raises(sqlite3.OperationalError):
        _start(readonly, "task-write", tmp_path)
