"""Local development-feedback lifecycle and privacy tests."""

import json

from src.tools.development_feedback import (
    FeedbackStore,
    record_feedback,
    record_validation_feedback,
    resolve_feedback,
    sanitize_feedback_text,
    summarize_feedback,
)


def test_feedback_is_private_idempotent_and_actionable(tmp_path):
    store = FeedbackStore(tmp_path / "feedback")
    first = record_feedback(
        project="/private/work/demo",
        category="false_positive",
        summary="```python\npassword='actual-secret-value'\n``` Demo password was flagged",
        severity="high",
        tool_name="scan_secrets",
        rule_id="secret/password",
        request_id="request-1",
        store=store,
    )
    replay = record_feedback(
        project="/private/work/demo",
        category="false_positive",
        summary="```python\npassword='actual-secret-value'\n``` Demo password was flagged",
        severity="high",
        tool_name="scan_secrets",
        rule_id="secret/password",
        request_id="request-1",
        store=store,
    )

    assert first["status"] == "recorded"
    assert replay["status"] == "already_recorded"
    raw = store.path.read_text(encoding="utf-8")
    assert "actual-secret-value" not in raw
    assert "[code omitted]" in raw
    assert store.path.stat().st_mode & 0o777 == 0o600

    summary = summarize_feedback("demo", store=store)
    candidate = summary["improvement_candidates"][0]
    assert candidate["category"] == "false_positive"
    assert candidate["priority_score"] == 7
    assert summary["governance"]["automatic_policy_changes"] is False


def test_repeated_feedback_aggregates_and_resolution_preserves_history(tmp_path):
    store = FeedbackStore(tmp_path / "feedback")
    records = []
    for request_id in ("one", "two"):
        records.append(record_feedback(
            project="demo",
            category="framework_gap",
            summary="React lazy route target was missing",
            severity="medium",
            framework="react",
            request_id=request_id,
            store=store,
        ))

    before = summarize_feedback("demo", store=store)
    assert before["improvement_candidates"][0]["occurrences"] == 2

    resolved = resolve_feedback(
        records[0]["feedback_id"],
        resolution="Added an on-demand relationship adapter",
        resolved_by="maintainer",
        request_id="resolution-1",
        store=store,
    )
    assert resolved["status"] == "resolved"
    assert summarize_feedback("demo", store=store)["total_issue_groups"] == 0
    assert summarize_feedback(
        "demo", include_resolved=True, store=store
    )["improvement_candidates"][0]["status"] == "resolved"
    assert len(store.read()) == 3


def test_finding_identity_groups_wording_variants_and_counts_occurrences(tmp_path):
    store = FeedbackStore(tmp_path / "feedback")
    for request_id, summary in (
        ("one", "The demo credential is not a real secret"),
        ("two", "This fixture value should not be reported"),
    ):
        record_feedback(
            project="demo",
            category="false_positive",
            summary=summary,
            severity="medium",
            tool_name="scan_secrets",
            finding_id="finding-stable-1",
            request_id=request_id,
            store=store,
        )

    summary = summarize_feedback("demo", store=store)

    assert summary["total_issue_groups"] == 1
    assert summary["total_occurrences"] == 2
    assert summary["by_category"] == {"false_positive": 2}
    assert summary["by_tool"] == {"scan_secrets": 2}


def test_failed_validation_becomes_compact_automatic_feedback(tmp_path):
    store = FeedbackStore(tmp_path / "feedback")
    result = record_validation_feedback(
        {
            "overall": "fail",
            "reason_codes": ["INTENT_LEDGER_NONCONFORMANT"],
        },
        project="demo",
        task_id="task-1",
        store=store,
    )

    assert result["status"] == "recorded"
    event = json.loads(store.path.read_text(encoding="utf-8"))
    assert event["category"] == "missing_context"
    assert event["source"] == "automatic_validation"


def test_feedback_text_redacts_home_paths_and_tokens():
    sanitized = sanitize_feedback_text(
        "/Users/alice/private/app.ts used ghp_abcdefghijklmnopqrstuvwxyz1234567890ABCDE"
    )

    assert "/Users/alice" not in sanitized
    assert "ghp_" not in sanitized
    assert "$HOME/" in sanitized
