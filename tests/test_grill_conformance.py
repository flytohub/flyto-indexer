"""Decision-to-diff conformance and smart validate integration tests."""

import subprocess
from unittest.mock import MagicMock, patch

from src.tools.grill import GrillSessionStore, run_grill
from src.tools.grill_conformance import (
    collect_change_set,
    validate_decision_conformance,
)
from src.tools.smart import smart_task


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _contract(tmp_path, acceptance):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "conformance@example.test")
    _git(repo, "config", "user.name", "Conformance Test")
    (repo / "base.py").write_text("BASE = True\n", encoding="utf-8")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-m", "baseline")
    store = GrillSessionStore(tmp_path / "state")
    started = run_grill(
        "start",
        description="Implement a conformant change",
        project=str(repo),
        decisions=[
            {
                "id": "implementation",
                "severity": "critical",
                "question": "What change is allowed?",
                "recommendation": "Add the bounded adapter.",
                "acceptance": acceptance,
            }
        ],
        store=store,
    )
    answered = run_grill(
        "answer",
        session_id=started["session_id"],
        decision_id="implementation",
        accept_recommendation=True,
        store=store,
    )
    frozen = run_grill(
        "freeze",
        session_id=answered["session_id"],
        store=store,
    )
    return repo, {"decision_contract": frozen["contract"]}


def test_expected_path_symbol_and_validation_proof_close_the_loop(tmp_path):
    repo, task_contract = _contract(
        tmp_path,
        {
            "expected_paths": ["src/adapter.py"],
            "expected_symbols": ["BoundedAdapter"],
            "forbidden_paths": ["src/legacy/**"],
            "proof_commands": ["pytest -q tests/test_adapter.py", "ruff check src"],
        },
    )
    source = repo / "src" / "adapter.py"
    source.parent.mkdir()
    source.write_text("class BoundedAdapter:\n    pass\n", encoding="utf-8")

    result = validate_decision_conformance(
        task_contract,
        validation={
            "pytest": {"status": "pass"},
            "ruff": {"status": "pass"},
        },
    )

    assert result["pass"] is True
    assert result["change_set"]["changed_paths"] == ["src/adapter.py"]
    assert {item["status"] for item in result["proof_results"]} == {"satisfied"}


def test_forbidden_diff_and_missing_expected_symbol_fail_closed(tmp_path):
    repo, task_contract = _contract(
        tmp_path,
        {
            "expected_paths": ["src/adapter.py"],
            "expected_symbols": ["BoundedAdapter"],
            "forbidden_paths": ["legacy/**"],
        },
    )
    forbidden = repo / "legacy" / "unsafe.py"
    forbidden.parent.mkdir()
    forbidden.write_text("UNSAFE = True\n", encoding="utf-8")

    result = validate_decision_conformance(task_contract)

    assert result["pass"] is False
    assert {item["type"] for item in result["violations"]} == {
        "expected_path_missing",
        "expected_symbol_missing",
        "forbidden_path_changed",
    }


def test_unsupported_proof_command_is_never_executed(tmp_path):
    repo, task_contract = _contract(
        tmp_path,
        {"proof_commands": ["python -c \"raise SystemExit('executed')\""]},
    )
    (repo / "safe.py").write_text("SAFE = True\n", encoding="utf-8")

    result = validate_decision_conformance(task_contract)

    assert result["pass"] is False
    assert result["proof_results"][0]["status"] == "unsupported"


def test_spoofed_python_module_proof_cannot_borrow_pytest_success(tmp_path):
    _, task_contract = _contract(
        tmp_path,
        {"proof_commands": ["not-python -m pytest tests/test_adapter.py"]},
    )

    result = validate_decision_conformance(
        task_contract,
        validation={"pytest": {"status": "pass"}},
    )

    assert result["pass"] is False
    assert result["proof_results"][0]["status"] == "unsupported"


def test_nested_project_change_set_excludes_parent_repo_changes(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "fixtures" / "demo"
    nested.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "conformance@example.test")
    _git(repo, "config", "user.name", "Conformance Test")
    (nested / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "outside.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    (repo / "outside.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = collect_change_set(str(nested), None)

    assert result["status"] == "captured"
    assert result["changed_paths"] == []


def test_smart_validate_combines_code_contract_and_diff_gates():
    validation = MagicMock()
    validation.validate_changes.return_value = {
        "overall": "pass",
        "ruff": {"status": "pass"},
        "pytest": {"status": "pass"},
    }
    grill = MagicMock()
    grill.validate_decision_contract.return_value = {
        "pass": True,
        "artifacts": {"adr_markdown": "# ADR\n"},
    }
    conformance = MagicMock()
    conformance.validate_decision_conformance.return_value = {
        "pass": False,
        "status": "blocked",
        "required_actions": ["fix_conformance:scope:expected_path_missing"],
    }
    outcomes = MagicMock()
    outcomes.record_outcome.return_value = {
        "status": "recorded",
        "outcome_id": "outcome-1",
    }
    contract = {"decision_contract": {"status": "frozen"}}

    with (
        patch("src.tools.smart._validation_mod", return_value=validation),
        patch("src.tools.smart._grill_mod", return_value=grill),
        patch("src.tools.smart._conformance_mod", return_value=conformance),
        patch("src.tools.smart._outcomes_mod", return_value=outcomes),
        patch("src.tools.smart._coverage_mod", return_value=MagicMock()),
    ):
        result = smart_task(
            action="validate",
            project="demo",
            task_contract=contract,
        )

    assert result["pass"] is False
    assert result["overall"] == "fail"
    assert result["reason_codes"] == ["DECISION_DIFF_NONCONFORMANT"]
    assert result["required_actions"] == [
        "fix_conformance:scope:expected_path_missing"
    ]
    assert result["artifacts"]["adr_markdown"] == "# ADR\n"
    assert result["outcome_learning"]["status"] == "recorded"
    outcomes.record_outcome.assert_called_once_with(
        contract,
        success=False,
        validation=result,
        conformance=conformance.validate_decision_conformance.return_value,
    )
