"""Argument adapter for the consolidated task MCP dispatch entry."""

from __future__ import annotations

from typing import Any

from .lazy_imports import _smart


def build_task_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """Map wire arguments to smart_task without hiding defaults in a lambda."""
    return {
        "action": args.get("action", "plan"),
        "description": args.get("description", ""),
        "targets": args.get("targets"),
        "intent": args.get("intent", "refactor"),
        "task_contract": args.get("task_contract"),
        "recovery_context": args.get("recovery_context"),
        "next_phase": args.get("next_phase"),
        "current_state": args.get("current_state"),
        "project": args.get("project"),
        "run_tests": args.get("run_tests", True),
        "test_path": args.get("test_path"),
        "grill_action": args.get("grill_action", "start"),
        "grill_session_id": args.get("grill_session_id"),
        "decisions": args.get("decisions"),
        "decision_id": args.get("decision_id"),
        "answer": args.get("answer"),
        "selected_option": args.get("selected_option"),
        "accept_recommendation": args.get("accept_recommendation", False),
        "mode": args.get("mode", "interactive"),
        "locale": args.get("locale", "und"),
        "max_questions": args.get("max_questions", 8),
        "request_id": args.get("request_id"),
        "proof_receipts": args.get("proof_receipts"),
        "required_proof_kinds": args.get("required_proof_kinds"),
        "feedback_action": args.get("feedback_action", "record"),
        "feedback_category": args.get("feedback_category", "other"),
        "feedback_summary": args.get("feedback_summary", ""),
        "feedback_severity": args.get("feedback_severity", "medium"),
        "feedback_tool": args.get("feedback_tool", ""),
        "finding_id": args.get("finding_id", ""),
        "rule_id": args.get("rule_id", ""),
        "framework": args.get("framework", ""),
        "duration_ms": args.get("duration_ms"),
        "expected": args.get("expected", ""),
        "actual": args.get("actual", ""),
        "feedback_id": args.get("feedback_id", ""),
        "resolution": args.get("resolution", ""),
        "resolved_by": args.get("resolved_by", ""),
        "since_days": args.get("since_days", 90),
        "limit": args.get("limit", 10),
    }


def dispatch_task(args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch the single public task tool through its stable argument map."""
    return _smart().smart_task(**build_task_arguments(args))
