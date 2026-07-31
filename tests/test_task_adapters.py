"""Focused compatibility tests for extracted CLI and MCP task adapters."""

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.task_cli import (
    build_task_arguments as build_cli_arguments,
    configure_task_parser,
    task_result_should_fail,
)
from src.tool_registry.task_dispatch import (
    build_task_arguments as build_dispatch_arguments,
    dispatch_task,
)


def _parse(*arguments):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    configure_task_parser(subparsers)
    return parser.parse_args(["task", *arguments])


def test_cli_adapter_preserves_inline_contract_and_all_task_fields(tmp_path):
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps([{"id": "scope", "question": "Scope?", "recommendation": "Bound"}]),
        encoding="utf-8",
    )
    args = _parse(
        "validate",
        "--project",
        "flyto-indexer",
        "--target",
        "src/a.py",
        "--targets",
        "src/b.py, src/c.py",
        "--task-contract",
        '{"decision_contract":{"status":"frozen"}}',
        "--decisions",
        str(decisions_path),
        "--test-path",
        "tests/test_grill.py",
        "--locale",
        "zh-TW",
    )

    mapped = build_cli_arguments(args)

    assert mapped["targets"] == ["src/a.py", "src/b.py", "src/c.py"]
    assert mapped["task_contract"]["decision_contract"]["status"] == "frozen"
    assert mapped["decisions"][0]["id"] == "scope"
    assert mapped["test_path"] == "tests/test_grill.py"
    assert mapped["locale"] == "zh-TW"


def test_cli_exit_policy_includes_closed_loop_validate_failure():
    args = _parse("validate")

    assert task_result_should_fail(args, {"pass": False}) is True
    assert task_result_should_fail(args, {"pass": True}) is False
    assert task_result_should_fail(args, {"tests_passed": False}) is True


def test_dispatch_adapter_preserves_wire_defaults_and_contract():
    contract = {"decision_contract": {"status": "frozen"}}

    mapped = build_dispatch_arguments(
        {
            "action": "validate",
            "task_contract": contract,
            "project": "flyto-indexer",
        }
    )

    assert mapped["action"] == "validate"
    assert mapped["task_contract"] is contract
    assert mapped["run_tests"] is True
    assert mapped["grill_action"] == "start"
    assert mapped["mode"] == "interactive"
    assert mapped["max_questions"] == 8


def test_dispatch_task_calls_smart_task_once_with_canonical_mapping():
    smart = MagicMock()
    smart.smart_task.return_value = {"pass": True}

    with patch("src.tool_registry.task_dispatch._smart", return_value=smart):
        result = dispatch_task({"action": "gate", "next_phase": "apply_changes"})

    assert result == {"pass": True}
    smart.smart_task.assert_called_once()
    kwargs = smart.smart_task.call_args.kwargs
    assert kwargs["action"] == "gate"
    assert kwargs["next_phase"] == "apply_changes"
    assert kwargs["run_tests"] is True


def test_cli_and_dispatch_adapters_expose_feedback_and_external_proof():
    feedback_args = _parse(
        "feedback",
        "--project",
        "demo",
        "--feedback-category",
        "framework_gap",
        "--feedback-summary",
        "Lazy route edge was missing",
        "--framework",
        "react",
    )
    mapped_feedback = build_cli_arguments(feedback_args)
    mapped_validate = build_dispatch_arguments({
        "action": "validate",
        "required_proof_kinds": ["browser"],
        "proof_receipts": [{"schema": "flyto-proof-receipt.v1"}],
    })

    assert mapped_feedback["action"] == "feedback"
    assert mapped_feedback["feedback_category"] == "framework_gap"
    assert mapped_feedback["framework"] == "react"
    assert mapped_validate["required_proof_kinds"] == ["browser"]
    assert mapped_validate["proof_receipts"][0]["schema"] == "flyto-proof-receipt.v1"


def test_generated_cli_reference_keeps_extracted_task_surface():
    reference = (
        Path(__file__).parents[1] / "docs" / "reference" / "cli.md"
    ).read_text(encoding="utf-8")

    assert "## `flyto-index task`" in reference
    assert "`--task-contract`" in reference
    assert "`src/task_cli.py:" in reference
