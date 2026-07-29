"""Focused CLI adapter for the task grill/plan/gate/validate workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def configure_task_parser(subparsers) -> None:
    """Register the backward-compatible ``flyto-index task`` arguments."""
    parser = subparsers.add_parser(
        "task",
        help="Run local task grill/plan/gate/validate workflow",
        description=(
            "Run the same grill, plan, gate, and validate workflow exposed by the MCP "
            "task tool. Useful when a long-running MCP server has stale source."
        ),
    )
    parser.add_argument(
        "action",
        choices=["grill", "plan", "gate", "validate"],
        help="Task workflow action",
    )
    parser.add_argument("--description", default="", help="Task description for plan")
    parser.add_argument(
        "--target", action="append", default=[], help="Target file or symbol. Repeatable"
    )
    parser.add_argument(
        "--targets",
        action="append",
        default=[],
        help="Comma-separated target files or symbols",
    )
    parser.add_argument(
        "--intent",
        choices=["refactor", "bugfix", "feature", "cleanup", "migration"],
        default="refactor",
        help="Plan intent",
    )
    parser.add_argument("--project", help="Project name for scoped analysis")
    parser.add_argument(
        "--task-contract",
        help="Gate/validate contract JSON object or path to a JSON file",
    )
    parser.add_argument(
        "--current-state",
        help="Gate current-state JSON object or path to a JSON file",
    )
    parser.add_argument(
        "--next-phase",
        help="Gate phase to enter, e.g. inspect, assess, implement, verify",
    )
    parser.add_argument("--test-path", help="Test file or directory for validate")
    parser.add_argument(
        "--no-tests", action="store_true", help="Skip pytest during validate"
    )
    parser.add_argument(
        "--grill-action",
        choices=["start", "answer", "status", "freeze", "discard"],
        default="start",
        help="Grill session operation (default: start)",
    )
    parser.add_argument(
        "--grill-session-id", help="Grill session to resume or attach to plan"
    )
    parser.add_argument(
        "--decisions", help="Decision array as inline JSON or a JSON file"
    )
    parser.add_argument("--decision-id", help="Decision to answer")
    parser.add_argument("--answer", help="Decision answer in any language")
    parser.add_argument("--selected-option", help="Stable selected option ID")
    parser.add_argument(
        "--accept-recommendation",
        action="store_true",
        help="Use the recommended answer",
    )
    parser.add_argument(
        "--mode",
        choices=["interactive", "batch"],
        default="interactive",
        help="Grill question mode",
    )
    parser.add_argument(
        "--locale", default="und", help="BCP-47 language metadata (default: und)"
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=8,
        help="Batch/frontier limit (1-20)",
    )
    parser.add_argument("--request-id", help="Idempotency key for a grill answer")


def _load_json_arg(
    value: str | None,
    arg_name: str,
    expected_type: type,
) -> dict | list | None:
    if not value:
        return None
    raw = value.strip()
    opening = "{" if expected_type is dict else "["
    if not raw.startswith(opening):
        candidate = Path(value)
        if candidate.exists():
            raw = candidate.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, expected_type):
        label = "object" if expected_type is dict else "array"
        raise ValueError(f"{arg_name} must be a JSON {label}")
    return data


def _collect_targets(
    targets: list[str] | None,
    target_groups: list[str] | None,
) -> list[str]:
    collected = [value.strip() for value in targets or [] if value.strip()]
    for group in target_groups or []:
        collected.extend(
            value.strip() for value in group.split(",") if value.strip()
        )
    return collected


def _validate_args(args, task_contract: dict | None, current_state: dict | None) -> None:
    if args.action == "gate":
        if task_contract is None:
            raise ValueError("task gate requires --task-contract")
        if current_state is None:
            raise ValueError("task gate requires --current-state")
        if not args.next_phase:
            raise ValueError("task gate requires --next-phase")
    if args.action != "grill":
        return
    if args.grill_action == "start" and not args.description.strip():
        raise ValueError("task grill start requires --description")
    if args.grill_action != "start" and not args.grill_session_id:
        raise ValueError(
            f"task grill {args.grill_action} requires --grill-session-id"
        )
    if args.grill_action == "answer":
        if not args.decision_id:
            raise ValueError("task grill answer requires --decision-id")
        if not args.accept_recommendation and not args.answer:
            raise ValueError(
                "task grill answer requires --answer or --accept-recommendation"
            )


def build_task_arguments(args) -> dict[str, Any]:
    """Translate argparse state to the single smart_task keyword contract."""
    task_contract = _load_json_arg(args.task_contract, "--task-contract", dict)
    current_state = _load_json_arg(args.current_state, "--current-state", dict)
    decisions = _load_json_arg(args.decisions, "--decisions", list)
    _validate_args(args, task_contract, current_state)
    return {
        "action": args.action,
        "description": args.description,
        "targets": _collect_targets(args.target, args.targets),
        "intent": args.intent,
        "task_contract": task_contract,
        "next_phase": args.next_phase,
        "current_state": current_state,
        "project": args.project,
        "run_tests": not args.no_tests,
        "test_path": args.test_path,
        "grill_action": args.grill_action,
        "grill_session_id": args.grill_session_id,
        "decisions": decisions,
        "decision_id": args.decision_id,
        "answer": args.answer,
        "selected_option": args.selected_option,
        "accept_recommendation": args.accept_recommendation,
        "mode": args.mode,
        "locale": args.locale,
        "max_questions": args.max_questions,
        "request_id": args.request_id,
    }


def task_result_should_fail(args, result: dict) -> bool:
    """Map a task result to the established CLI exit-code policy."""
    if result.get("error"):
        return True
    if args.action == "gate" and result.get("pass") is False:
        return True
    if args.action == "validate":
        return any(
            result.get(key) is False
            for key in ("lint_passed", "tests_passed", "pass")
        )
    return (
        args.action == "grill"
        and args.grill_action == "freeze"
        and result.get("pass") is False
    )


def execute_task_command(
    args,
    smart_task: Callable[..., dict] | None = None,
) -> tuple[dict, bool]:
    """Run one CLI task call and return the result plus exit-code decision."""
    if smart_task is None:
        from .tools.smart import smart_task
    result = smart_task(**build_task_arguments(args))
    return result, task_result_should_fail(args, result)
