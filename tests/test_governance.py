"""Tests for optional atomicity and documentation governance."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.governance import (
    evaluate_task_governance,
    load_governance_policy,
    validate_governance_diff,
)
from tools.task_analysis import task_gate_check
from tools.task_context import build_intent_ledger, validate_intent_ledger


def _contract(mode="advisory", waivers=None):
    return {
        "version": "governance.v1",
        "mode": mode,
        "waivers": waivers or {"valid": [], "invalid": []},
    }


def test_internal_fix_needs_no_docs_or_forced_split():
    result = evaluate_task_governance(
        description="Fix an internal cache invalidation bug",
        targets=["src/index_store.py"],
        resolved_targets=[{"path": "src/index_store.py"}],
        project=None,
        options={"governance": {"mode": "advisory"}},
    )

    assert result["mode"] == "advisory"
    assert result["atomicity"]["recommend_split"] is False
    assert result["documentation"]["required"] is False


def test_public_tool_schema_does_not_require_database_migration_docs():
    tool_schema = evaluate_task_governance(
        description="Preserve the public MCP tool schema",
        targets=["src/tools/smart.py"],
        resolved_targets=[{"path": "src/tools/smart.py"}],
        project=None,
        options={"governance": {"mode": "advisory"}},
    )
    database_schema = evaluate_task_governance(
        description="Change the database schema for accounts",
        targets=["src/storage.py"],
        resolved_targets=[{"path": "src/storage.py"}],
        project=None,
        options={"governance": {"mode": "advisory"}},
    )

    assert "schema" not in tool_schema["documentation"]["signals"]
    assert "migration" not in tool_schema["documentation"]["required_kinds"]
    assert "schema" in database_schema["documentation"]["signals"]
    assert "migration" in database_schema["documentation"]["required_kinds"]


def test_atomicity_uses_responsibilities_instead_of_line_count():
    result = evaluate_task_governance(
        description="Change task analysis and runtime rule loading",
        targets=["src/tools/task_analysis.py", "src/rule_loader.py"],
        resolved_targets=[
            {"path": "src/tools/task_analysis.py"},
            {"path": "src/rule_loader.py"},
        ],
        project=None,
        options={"governance": {"mode": "advisory"}},
    )

    assert result["atomicity"]["basis"] == "responsibility_dependency"
    assert result["atomicity"]["recommend_split"] is True
    assert len(result["atomicity"]["responsibilities"]) == 2


def test_advisory_reports_but_never_blocks():
    result = validate_governance_diff(
        _contract(),
        changed_paths=["src/service.py"],
        state={
            "governance_findings": [{
                "code": "forbidden_layer_edge",
                "severity": "high",
                "paths": ["src/service.py"],
            }],
        },
    )

    assert result["pass"] is True
    assert result["findings"][0]["code"] == "forbidden_layer_edge"
    assert result["blocking"] == []


def test_guarded_blocks_deterministic_architecture_violation():
    result = task_gate_check(
        {
            "constraints": {},
            "governance": _contract("guarded"),
        },
        next_phase="apply_changes",
        current_state={
            "forbidden_layer_edges": ["scanner -> mcp"],
            "changed_paths": ["src/scanner/python.py"],
        },
    )

    assert result["pass"] is False
    assert "GOVERNANCE_FORBIDDEN_LAYER_EDGE" in result["reason_codes"]


def test_guarded_requires_tests_and_docs_for_public_contract():
    result = validate_governance_diff(
        _contract("guarded"),
        changed_paths=["src/api/users.py"],
    )

    assert result["pass"] is False
    assert {
        item["code"] for item in result["blocking"]
    } == {
        "public_contract_missing_docs",
        "public_contract_missing_tests",
    }


def test_strict_requires_change_aware_behavior_docs():
    guarded = validate_governance_diff(
        _contract("guarded"),
        changed_paths=["src/cli.py", "tests/test_cli.py"],
    )
    strict = validate_governance_diff(
        _contract("strict"),
        changed_paths=["src/cli.py", "tests/test_cli.py"],
    )

    assert guarded["pass"] is True
    assert strict["pass"] is False
    assert strict["blocking"][0]["code"] == "behavior_docs_missing"


def test_strict_requires_the_corresponding_security_document():
    wrong_doc = validate_governance_diff(
        _contract("strict"),
        changed_paths=["src/security/policy.py", "README.md"],
    )
    matching_doc = validate_governance_diff(
        _contract("strict"),
        changed_paths=["src/security/policy.py", "SECURITY.md"],
    )

    assert wrong_doc["pass"] is False
    assert wrong_doc["blocking"][0]["code"] == "security_docs_missing"
    assert matching_doc["pass"] is True


def test_valid_narrow_waiver_suppresses_one_matching_check():
    waivers = {
        "valid": [{
            "id": "legacy-edge",
            "checks": ["forbidden_layer_edge"],
            "paths": ["src/legacy/**"],
            "rationale": "Remove after legacy adapter migration.",
            "expires": "2099-01-01",
        }],
        "invalid": [],
    }
    result = validate_governance_diff(
        _contract("guarded", waivers),
        changed_paths=["src/legacy/service.py"],
        state={
            "governance_findings": [{
                "code": "forbidden_layer_edge",
                "paths": ["src/legacy/service.py"],
            }],
        },
    )

    assert result["pass"] is True
    assert result["findings"] == []
    assert result["waived"][0]["waiver_id"] == "legacy-edge"


def test_expired_waiver_is_invalid():
    policy = load_governance_policy(
        None,
        {
            "governance": {
                "mode": "guarded",
                "waivers": [{
                    "id": "expired",
                    "checks": ["forbidden_layer_edge"],
                    "paths": ["src/legacy/**"],
                    "rationale": "Temporary migration.",
                    "expires": "2000-01-01",
                }],
            },
        },
    )

    assert policy["waivers"]["valid"] == []
    assert "expired" in policy["waivers"]["invalid"][0]["reasons"]


def test_validate_intent_ledger_applies_strict_diff_governance():
    targets = ["src/cli.py", "tests/test_cli.py", "README.md"]
    ledger = build_intent_ledger(
        "flyto-indexer",
        "Change CLI output behavior",
        targets,
        [{
            "id": "step_01_apply",
            "purpose": "gate_before_apply",
            "tool": "task_gate_check",
        }],
    )
    contract = {
        "task_profile": {"project": "flyto-indexer"},
        "intent_ledger": ledger,
        "governance": _contract("strict"),
    }

    blocked = validate_intent_ledger(
        contract,
        project="flyto-indexer",
        change_set={
            "status": "captured",
            "changed_paths": ["src/cli.py", "tests/test_cli.py"],
        },
    )
    passing = validate_intent_ledger(
        contract,
        project="flyto-indexer",
        change_set={
            "status": "captured",
            "changed_paths": ["src/cli.py", "tests/test_cli.py", "README.md"],
        },
    )

    assert blocked["pass"] is False
    assert "resolve_governance:behavior_docs_missing" in blocked["required_actions"]
    assert passing["pass"] is True
