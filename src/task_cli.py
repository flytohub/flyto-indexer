"""Focused CLI adapter for the task grill/plan/gate/validate/feedback workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TypeVar

from .task_reports import render_task_status, render_usage_report
from .task_runs import TaskRunStore, default_task_db, read_task_continuity
from .task_usage import estimate_usage_from_char_counts, normalize_provider_usage

JsonContainer = TypeVar("JsonContainer", dict, list)


def configure_task_parser(subparsers) -> None:
    """Register the backward-compatible ``flyto-index task`` arguments."""
    parser = subparsers.add_parser(
        "task",
        help="Run local task grill/plan/gate/validate/feedback workflow",
        description=(
            "Run the same grill, plan, gate, validate, and feedback workflow exposed by the MCP "
            "task tool. Useful when a long-running MCP server has stale source."
        ),
    )
    parser.add_argument(
        "action",
        choices=["grill", "plan", "gate", "validate", "feedback"],
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
    parser.add_argument("--no-tests", action="store_true", help="Skip pytest during validate")
    parser.add_argument(
        "--grill-action",
        choices=["start", "answer", "status", "freeze", "discard"],
        default="start",
        help="Grill session operation (default: start)",
    )
    parser.add_argument("--grill-session-id", help="Grill session to resume or attach to plan")
    parser.add_argument("--decisions", help="Decision array as inline JSON or a JSON file")
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
    parser.add_argument("--locale", default="und", help="BCP-47 language metadata (default: und)")
    parser.add_argument(
        "--max-questions",
        type=int,
        default=8,
        help="Batch/frontier limit (1-20)",
    )
    parser.add_argument("--request-id", help="Idempotency key for a state-changing action")
    parser.add_argument(
        "--proof-receipts",
        help="External proof receipt array as inline JSON or a JSON file",
    )
    parser.add_argument(
        "--require-proof",
        action="append",
        default=[],
        help="Required external proof kind. Repeatable",
    )
    parser.add_argument(
        "--feedback-action",
        choices=["record", "summary", "resolve"],
        default="record",
        help="Feedback lifecycle operation",
    )
    parser.add_argument("--feedback-category", default="other")
    parser.add_argument("--feedback-summary")
    parser.add_argument(
        "--feedback-severity",
        choices=["low", "medium", "high", "critical"],
        default="medium",
    )
    parser.add_argument("--feedback-tool")
    parser.add_argument("--finding-id")
    parser.add_argument("--rule-id")
    parser.add_argument("--framework")
    parser.add_argument("--duration-ms", type=float)
    parser.add_argument("--expected")
    parser.add_argument("--actual")
    parser.add_argument("--feedback-id")
    parser.add_argument("--resolution")
    parser.add_argument("--resolved-by")
    parser.add_argument("--since-days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=10)


def configure_task_evidence_parsers(subparsers) -> None:
    """Register local continuity and normalized usage commands."""
    status = subparsers.add_parser(
        "task-status",
        help="Show resumable task state and an actionable handoff reminder",
    )
    status.add_argument("path", nargs="?", default=".", help="Project root path")
    status.add_argument("--task", help="Specific task or run ID")
    status.add_argument("--all", action="store_true", help="Include recent task runs")
    status.add_argument("--json", action="store_true", dest="as_json")

    usage = subparsers.add_parser(
        "usage-record",
        help="Record provider-reported or count-estimated task usage",
    )
    usage.add_argument("task_id", help="Task or run ID created by task plan")
    usage.add_argument("path", nargs="?", default=".", help="Project root path")
    usage.add_argument("--provider", required=True, help="Usage metadata provider")
    usage.add_argument("--model", required=True, help="Model identity")
    source = usage.add_mutually_exclusive_group(required=True)
    source.add_argument("--usage", help="Usage JSON object or JSON file")
    source.add_argument(
        "--estimated-input-chars",
        type=int,
        help="Raw-text-free input character count fallback",
    )
    usage.add_argument("--estimated-output-chars", type=int, default=0)
    usage.add_argument("--event-id", default="", help="Optional idempotency key")
    usage.add_argument("--tool-calls", type=int, default=0)
    usage.add_argument("--duration-ms", type=float, default=0)
    usage.add_argument(
        "--comparison-context",
        help="Paired experiment identity as a JSON object or file",
    )
    usage.add_argument("--variant", default="", help="Experiment variant name")

    report = subparsers.add_parser(
        "usage-report",
        help="Report verified task efficiency without claiming unpaired savings",
    )
    report.add_argument("path", nargs="?", default=".", help="Project root path")
    report.add_argument("--task", help="Task or run ID; defaults to the latest")
    report.add_argument("--compare", default="", help="Baseline task or run ID")
    report.add_argument(
        "--format",
        choices=["table", "json", "csv", "html"],
        default="table",
    )
    report.add_argument("--output", help="Write the rendered report to a file")


def task_evidence_tool_descriptions() -> list[dict[str, Any]]:
    """Describe the compact evidence CLI commands for ``flyto-index tools``."""
    common_path = {
        "name": "path",
        "type": "string",
        "required": False,
        "default": ".",
        "description": "Project root path",
    }
    return [
        {
            "name": "task-status",
            "summary": "Show resumable state and actionable handoff reminders",
            "args": [
                common_path,
                {
                    "name": "--task",
                    "type": "string",
                    "required": False,
                    "description": "Task or run ID",
                },
                {
                    "name": "--all",
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Include recent task runs",
                },
                {
                    "name": "--json",
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Output JSON",
                },
            ],
            "outputs": [],
            "side_effects": [],
            "examples": ["flyto-index task-status . --json"],
            "exit_codes": {"0": "success", "1": "invalid task"},
        },
        {
            "name": "usage-record",
            "summary": "Record normalized provider usage or a count estimate",
            "args": [
                {
                    "name": "task_id",
                    "type": "string",
                    "required": True,
                    "description": "Task or run ID",
                },
                common_path,
                {
                    "name": "--provider",
                    "type": "string",
                    "required": True,
                    "description": "Usage provider",
                },
                {
                    "name": "--model",
                    "type": "string",
                    "required": True,
                    "description": "Model identity",
                },
                {
                    "name": "--usage",
                    "type": "json|string",
                    "required": False,
                    "description": "Provider usage object or JSON file",
                },
                {
                    "name": "--estimated-input-chars",
                    "type": "integer",
                    "required": False,
                    "description": "Input character count fallback",
                },
                {
                    "name": "--estimated-output-chars",
                    "type": "integer",
                    "required": False,
                    "default": 0,
                    "description": "Output character count fallback",
                },
                {
                    "name": "--event-id",
                    "type": "string",
                    "required": False,
                    "description": "Idempotency key",
                },
                {
                    "name": "--tool-calls",
                    "type": "integer",
                    "required": False,
                    "default": 0,
                    "description": "Tool call count",
                },
                {
                    "name": "--duration-ms",
                    "type": "number",
                    "required": False,
                    "default": 0,
                    "description": "Duration in milliseconds",
                },
                {
                    "name": "--comparison-context",
                    "type": "json|string",
                    "required": False,
                    "description": "Paired experiment identity",
                },
                {
                    "name": "--variant",
                    "type": "string",
                    "required": False,
                    "description": "Experiment variant",
                },
            ],
            "outputs": [".flyto-index/task-runs.sqlite"],
            "side_effects": ["stores normalized counts in the ignored local index"],
            "examples": ["flyto-index usage-record task-1 --help"],
            "exit_codes": {"0": "success", "1": "invalid usage or task"},
        },
        {
            "name": "usage-report",
            "summary": "Report task efficiency without unpaired savings claims",
            "args": [
                common_path,
                {
                    "name": "--task",
                    "type": "string",
                    "required": False,
                    "description": "Task or run ID; latest by default",
                },
                {
                    "name": "--compare",
                    "type": "string",
                    "required": False,
                    "description": "Paired baseline run",
                },
                {
                    "name": "--format",
                    "type": "string",
                    "required": False,
                    "default": "table",
                    "description": "table, json, csv, or html",
                },
                {
                    "name": "--output",
                    "type": "string",
                    "required": False,
                    "description": "Write a portable report file",
                },
            ],
            "outputs": ["terminal, JSON, CSV, or static HTML evidence"],
            "side_effects": ["writes a report only when --output is set"],
            "examples": ["flyto-index usage-report . --format json"],
            "exit_codes": {"0": "success", "1": "missing evidence"},
        },
    ]


def _load_json_arg(
    value: str | None,
    arg_name: str,
    expected_type: type[JsonContainer],
) -> JsonContainer | None:
    """Load inline JSON or a JSON file with container-type validation."""
    if not value:
        return None
    raw = value.strip()
    opening = "{" if expected_type is dict else "["
    if not raw.startswith(opening):
        candidate = Path(value)
        if candidate.exists():
            raw = candidate.read_text(encoding="utf-8")
    data: Any = json.loads(raw)
    if not isinstance(data, expected_type):
        label = "object" if expected_type is dict else "array"
        raise ValueError(f"{arg_name} must be a JSON {label}")
    return data


def _collect_targets(
    targets: list[str] | None,
    target_groups: list[str] | None,
) -> list[str]:
    """Merge repeatable and comma-separated target arguments."""
    collected = [value.strip() for value in targets or [] if value.strip()]
    for group in target_groups or []:
        collected.extend(value.strip() for value in group.split(",") if value.strip())
    return collected


def _validate_args(args, task_contract: dict | None, current_state: dict | None) -> None:
    """Fail early when an action is missing required CLI evidence."""
    if args.action == "gate":
        if task_contract is None:
            raise ValueError("task gate requires --task-contract")
        if current_state is None:
            raise ValueError("task gate requires --current-state")
        if not args.next_phase:
            raise ValueError("task gate requires --next-phase")
    if args.action == "feedback":
        if args.feedback_action == "record" and not args.feedback_summary:
            raise ValueError("task feedback record requires --feedback-summary")
        if args.feedback_action == "resolve":
            if not args.feedback_id or not args.resolution:
                raise ValueError("task feedback resolve requires --feedback-id and --resolution")
        return
    if args.action != "grill":
        return
    if args.grill_action == "start" and not args.description.strip():
        raise ValueError("task grill start requires --description")
    if args.grill_action != "start" and not args.grill_session_id:
        raise ValueError(f"task grill {args.grill_action} requires --grill-session-id")
    if args.grill_action == "answer":
        if not args.decision_id:
            raise ValueError("task grill answer requires --decision-id")
        if not args.accept_recommendation and not args.answer:
            raise ValueError("task grill answer requires --answer or --accept-recommendation")


def build_task_arguments(args) -> dict[str, Any]:
    """Translate argparse state to the single smart_task keyword contract."""
    task_contract = _load_json_arg(args.task_contract, "--task-contract", dict)
    current_state = _load_json_arg(args.current_state, "--current-state", dict)
    decisions = _load_json_arg(args.decisions, "--decisions", list)
    proof_receipts = _load_json_arg(args.proof_receipts, "--proof-receipts", list)
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
        "proof_receipts": proof_receipts,
        "required_proof_kinds": args.require_proof,
        "feedback_action": args.feedback_action,
        "feedback_category": args.feedback_category,
        "feedback_summary": args.feedback_summary or "",
        "feedback_severity": args.feedback_severity,
        "feedback_tool": args.feedback_tool or "",
        "finding_id": args.finding_id or "",
        "rule_id": args.rule_id or "",
        "framework": args.framework or "",
        "duration_ms": args.duration_ms,
        "expected": args.expected or "",
        "actual": args.actual or "",
        "feedback_id": args.feedback_id or "",
        "resolution": args.resolution or "",
        "resolved_by": args.resolved_by or "",
        "since_days": args.since_days,
        "limit": args.limit,
    }


def task_result_should_fail(args, result: dict) -> bool:
    """Map a task result to the established CLI exit-code policy."""
    if result.get("error"):
        return True
    if args.action == "gate" and result.get("pass") is False:
        return True
    if args.action == "validate":
        return any(result.get(key) is False for key in ("lint_passed", "tests_passed", "pass"))
    return args.action == "grill" and args.grill_action == "freeze" and result.get("pass") is False


def execute_task_command(
    args,
    smart_task: Callable[..., dict] | None = None,
) -> tuple[dict, bool]:
    """Run one CLI task call and return the result plus exit-code decision."""
    if smart_task is None:
        from .tools.smart import smart_task as default_smart_task

        smart_task = default_smart_task
    result = smart_task(**build_task_arguments(args))
    return result, task_result_should_fail(args, result)


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    """Remove internal raw JSON helper fields from CLI output."""
    return {key: value for key, value in run.items() if not key.endswith("_json")}


def execute_task_status(args) -> str:
    """Return current local continuity; only warn when actionable state remains."""
    root = Path(args.path).resolve()
    db_path = default_task_db(root)
    payload: dict[str, Any] = {"continuity": read_task_continuity(root, project=root.name)}
    if not db_path.is_file():
        if args.task or args.all:
            raise ValueError("no task evidence found; run task plan first")
        return render_task_status(payload, as_json=args.as_json)
    store = TaskRunStore(db_path, readonly=True)
    if args.task:
        run = store.get_run(args.task)
        if not run:
            raise ValueError(f"unknown task run: {args.task}")
        payload["run"] = _public_run(run)
    if args.all:
        payload["runs"] = [_public_run(run) for run in store.list_runs(project=root.name, limit=50)]
    return render_task_status(payload, as_json=args.as_json)


def execute_usage_record(args) -> dict[str, Any]:
    """Normalize one usage event without accepting prompt or response text."""
    root = Path(args.path).resolve()
    db_path = default_task_db(root)
    if not db_path.is_file():
        raise ValueError("no task evidence found; run task plan first")
    store = TaskRunStore(db_path)
    if args.usage:
        metadata = _load_json_arg(args.usage, "--usage", dict)
        usage = normalize_provider_usage(args.provider, metadata or {})
    else:
        usage = estimate_usage_from_char_counts(
            args.estimated_input_chars,
            args.estimated_output_chars,
        )
    context = _load_json_arg(args.comparison_context, "--comparison-context", dict)
    return store.record_usage(
        args.task_id,
        usage,
        provider=args.provider,
        model=args.model,
        event_id=args.event_id,
        tool_calls=args.tool_calls,
        duration_ms=args.duration_ms,
        comparison_context=context,
        variant=args.variant,
    )


def execute_usage_report(args) -> str:
    """Render the latest or selected task evidence in one portable format."""
    root = Path(args.path).resolve()
    db_path = default_task_db(root)
    if not db_path.is_file():
        raise ValueError("no task evidence found; run task plan first")
    store = TaskRunStore(db_path, readonly=True)
    identifier = args.task
    if not identifier:
        runs = store.list_runs(project=root.name, limit=1)
        if not runs:
            raise ValueError("no task evidence found; run task plan first")
        identifier = str(runs[0]["run_id"])
    rendered = render_usage_report(store.report(identifier, compare_to=args.compare), args.format)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        return f"Wrote {args.format} report to {output_path}\n"
    return rendered
