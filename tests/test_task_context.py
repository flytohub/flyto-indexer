"""Tests for lean target instructions and intent traceability."""

from pathlib import Path

from src.tools.task_context import (
    attach_task_context,
    build_intent_ledger,
    resolve_instruction_context,
    validate_instruction_context,
    validate_intent_ledger,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _plan() -> list[dict]:
    return [
        {
            "id": "step_01_apply_changes",
            "tool": "task_gate_check",
            "purpose": "apply_changes",
        },
        {
            "id": "step_02_validate",
            "tool": "validate_changes",
            "purpose": "run_validation",
        },
    ]


def test_instruction_context_resolves_nested_precedence(tmp_path):
    _write(tmp_path, "AGENTS.md", "- Do not use SQLite.\n- Always run pytest.\n")
    _write(tmp_path, "src/payments/AGENTS.md", "- Use SQLite.\n")
    _write(tmp_path, "src/payments/service.py", "def pay():\n    pass\n")

    context = resolve_instruction_context(
        str(tmp_path),
        ["src/payments/service.py"],
    )

    assert context["status"] == "ready"
    assert [item["path"] for item in context["files"]] == [
        "AGENTS.md",
        "src/payments/AGENTS.md",
    ]
    assert context["conflicts"][0]["status"] == "resolved_by_scope"
    assert context["conflicts"][0]["winner"] == "src/payments/AGENTS.md"
    assert all(len(item["text"]) <= 240 for item in context["clauses"])


def test_instruction_context_blocks_same_scope_conflict(tmp_path):
    _write(tmp_path, "AGENTS.md", "- Do not use SQLite.\n")
    _write(tmp_path, "CLAUDE.md", "- Use SQLite.\n")
    _write(tmp_path, "src/app.py", "")

    context = resolve_instruction_context(str(tmp_path), ["src/app.py"])

    assert context["status"] == "blocked"
    assert context["summary"]["unresolved_conflicts"] == 1


def test_instruction_context_fingerprint_detects_drift(tmp_path):
    _write(tmp_path, "AGENTS.md", "- Always run pytest.\n")
    _write(tmp_path, "src/app.py", "")
    context = resolve_instruction_context(str(tmp_path), ["src/app.py"])
    contract = {
        "task_profile": {"project": str(tmp_path)},
        "instruction_context": context,
    }
    _write(tmp_path, "AGENTS.md", "- Always run pytest.\n- Never use eval.\n")

    result = validate_instruction_context(contract, project=str(tmp_path))

    assert result["pass"] is False
    assert result["violations"] == [{"type": "instruction_context_stale"}]


def test_intent_ledger_parses_openspec_requirements_and_proofs(tmp_path):
    _write(tmp_path, "src/theme.py", "")
    _write(tmp_path, "tests/test_theme.py", "")
    _write(
        tmp_path,
        "openspec/changes/dark-mode/specs/theme.md",
        """
## ADDED Requirements

### Requirement: Theme selector in `src/theme.py`
The app SHALL persist the selected theme.

#### Scenario: User selects dark mode

### Acceptance: `pytest tests/test_theme.py`
""".strip(),
    )

    ledger = build_intent_ledger(
        str(tmp_path),
        "Add dark mode",
        ["src/theme.py", "tests/test_theme.py"],
        _plan(),
    )

    assert ledger["status"] == "ready"
    assert ledger["summary"]["source_count"] == 1
    assert ledger["summary"]["requirement_count"] >= 4
    requirement = next(
        item
        for item in ledger["requirements"]
        if item["text"].startswith("Theme selector")
    )
    assert requirement["expected_paths"] == ["src/theme.py"]
    proof = next(
        item
        for item in ledger["requirements"]
        if item["kind"] == "acceptance"
    )
    assert proof["proof_commands"] == ["pytest tests/test_theme.py"]

    contract = {
        "task_profile": {"project": str(tmp_path)},
        "intent_ledger": ledger,
    }
    result = validate_intent_ledger(
        contract,
        project=str(tmp_path),
        validation={"pytest": {"status": "pass"}},
        change_set={
            "status": "captured",
            "changed_paths": ["src/theme.py", "tests/test_theme.py"],
        },
    )

    assert result["pass"] is True
    assert result["summary"]["violations"] == 0


def test_intent_ledger_rejects_unplanned_diff(tmp_path):
    _write(tmp_path, "src/app.py", "")
    ledger = build_intent_ledger(
        str(tmp_path),
        "Change the app",
        ["src/app.py"],
        _plan(),
    )
    contract = {
        "task_profile": {"project": str(tmp_path)},
        "intent_ledger": ledger,
    }

    result = validate_intent_ledger(
        contract,
        project=str(tmp_path),
        validation={"pytest": {"status": "pass"}},
        change_set={
            "status": "captured",
            "changed_paths": ["src/app.py", "ops/deploy.sh"],
        },
    )

    assert result["pass"] is False
    violation = next(
        item for item in result["violations"] if item["type"] == "unplanned_diff"
    )
    assert violation["changed_paths"] == ["ops/deploy.sh"]


def test_attach_task_context_keeps_existing_tool_surface(tmp_path):
    _write(tmp_path, "AGENTS.md", "- Always run pytest.\n")
    _write(tmp_path, "src/app.py", "")
    contract = {
        "task_profile": {"project": str(tmp_path)},
        "execution_plan": _plan(),
    }

    attached = attach_task_context(
        contract,
        project=str(tmp_path),
        description="Change the app",
        targets=["src/app.py"],
    )

    assert attached["instruction_context"]["status"] == "ready"
    assert attached["intent_ledger"]["status"] == "ready"
    assert attached["task_profile"]["instruction_fingerprint"]
    assert attached["task_profile"]["intent_fingerprint"]
