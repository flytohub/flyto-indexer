"""Tests for lean target instructions and intent traceability."""

from pathlib import Path

import pytest

from src.tools.task_context import (
    _content_only_drift,
    _inline_contract,
    _match_allowed,
    _symbol_path,
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


CONTROL_CHARACTERS = ("\x00", "\x01", "\t", "\n", "\r", "\x1f", "\x7f")


def test_inline_contract_keeps_dotted_module_ids_as_symbols():
    paths, symbols, proofs = _inline_contract(
        "`human.approval` MUST NOT be declared as produced."
    )

    assert paths == []
    assert symbols == ["human.approval"]
    assert proofs == []


def test_inline_contract_still_recognizes_real_relative_files():
    paths, symbols, proofs = _inline_contract(
        "Update `README.md`, `src/app.py`, and `Makefile`; keep `module.identifier`."
    )

    assert paths == ["README.md", "src/app.py", "Makefile"]
    assert symbols == ["module.identifier"]
    assert proofs == []


def test_dotted_spec_identifier_does_not_invent_a_required_diff_path(tmp_path):
    _write(tmp_path, "scripts/verify.sh", "#!/bin/sh\n")
    _write(
        tmp_path,
        "docs/specs/plugin.md",
        "`human.approval` MUST NOT be declared as produced.\n",
    )

    ledger = build_intent_ledger(
        str(tmp_path),
        "Fix scripts/verify.sh",
        ["scripts/verify.sh"],
        _plan(),
    )
    requirement = next(
        item for item in ledger["requirements"] if item["source"] == "docs/specs/plugin.md"
    )
    assert requirement["expected_paths"] == []
    assert requirement["expected_symbols"] == ["human.approval"]

    result = validate_intent_ledger(
        {
            "task_profile": {"project": str(tmp_path)},
            "intent_ledger": ledger,
        },
        project=str(tmp_path),
        validation={"pytest": {"status": "pass"}},
        change_set={"status": "captured", "changed_paths": ["scripts/verify.sh"]},
    )

    assert result["pass"] is True
    assert result["violations"] == []


@pytest.mark.parametrize(
    "target,expected",
    [
        ("repo:smoke.py:file:smoke", "smoke.py"),
        ("repo:README.md:file:README", "README.md"),
        ("repo:Makefile:file:Makefile", "Makefile"),
        ("repo:Dockerfile:file:Dockerfile", "Dockerfile"),
        ("flyto-ai:src/app.py:file:app", "src/app.py"),
        ("repo:src/tools/task_context.py:file:task_context", "src/tools/task_context.py"),
        ("repo:src/app.py:function:main", "src/app.py"),
        ("repo:src/pkg/mod.go:method:Server.Handle", "src/pkg/mod.go"),
        ("repo:src/rs/lib.rs:function:module::helper", "src/rs/lib.rs"),
        # Scanners build ids from the real relative path, so ordinary spaces
        # and Unicode reach _symbol_path() unnormalized.
        ("repo:my dir/smoke.py:file:smoke", "my dir/smoke.py"),
        ("repo:my file.py:file:my file", "my file.py"),
        ("repo:資料/程式.py:file:程式", "資料/程式.py"),
        ("repo:src/données.ts:function:charger", "src/données.ts"),
        ("my project:src/app.py:file:app", "src/app.py"),
        ("repo:Makefile légal:file:Makefile légal", "Makefile légal"),
    ],
)
def test_symbol_path_accepts_root_and_nested_symbol_ids(target, expected):
    assert _symbol_path(target) == expected


@pytest.mark.parametrize(
    "target",
    [
        "",
        "smoke.py",
        "repo:smoke.py",
        "repo:smoke.py:file",
        ":smoke.py:file:smoke",
        "repo::file:smoke",
        "repo:smoke.py:file:",
        "repo:smoke.py::smoke",
        "repo:smoke.py:File:smoke",
        "repo:smoke.py:9file:smoke",
        "repo:Makefile:function:build",
        "repo:draft:file:notes",
        "repo:/etc/passwd:file:passwd",
        "repo:~/secrets.env:file:secrets",
        "repo:~backup/secrets.env:file:secrets",
        "repo:../../etc/passwd:file:passwd",
        "repo:src/../../etc/passwd:file:passwd",
        "repo:./smoke.py:file:smoke",
        "repo:.:file:smoke",
        "repo:src\\app.py:file:app",
        "repo:src//app.py:file:app",
        "repo:src/app.py/:file:app",
        "repo:my dir\\smoke.py:file:smoke",
        "   :smoke.py:file:smoke",
        "repo:smoke.py:file:   ",
        "note: fix the bug: in src: today",
        "note:rewrite the parser:file:notes",
        "C:\\repo\\smoke.py:file:smoke",
        "repo:" + "a/" * 24 + "app.py:file:app",
        "repo:" + "a" * 513 + ".py:file:app",
        "repo:" + "a" * 256 + "/app.py:file:app",
        "repo:src/app.py:file:" + "a" * 257,
        "a" * 129 + ":src/app.py:file:app",
    ],
)
def test_symbol_path_rejects_unsafe_or_malformed_ids(target):
    assert _symbol_path(target) is None


@pytest.mark.parametrize(
    "template",
    [
        "re{control}po:smoke.py:file:smoke",
        "repo:smo{control}ke.py:file:smoke",
        "repo:src/smo{control}ke.py:file:smoke",
        "repo:smoke.py:fi{control}le:smoke",
        "repo:smoke.py:file:smo{control}ke",
        "repo:Makefile{control}:file:Makefile{control}",
    ],
)
@pytest.mark.parametrize("control", CONTROL_CHARACTERS)
def test_symbol_path_rejects_control_characters(template, control):
    assert _symbol_path(template.format(control=control)) is None


def test_intent_ledger_allows_root_file_symbol_target(tmp_path):
    _write(tmp_path, "smoke.py", "print('hi')\n")

    ledger = build_intent_ledger(
        str(tmp_path),
        "Fix the root smoke script",
        ["repo:smoke.py:file:smoke"],
        _plan(),
    )

    assert ledger["allowed_paths"] == ["smoke.py"]
    contract = {
        "task_profile": {"project": str(tmp_path)},
        "intent_ledger": ledger,
    }

    result = validate_intent_ledger(
        contract,
        project=str(tmp_path),
        validation={"pytest": {"status": "pass"}},
        change_set={"status": "captured", "changed_paths": ["smoke.py"]},
    )

    assert result["pass"] is True
    assert result["violations"] == []


def test_intent_ledger_allows_extensionless_root_file_symbol_target(tmp_path):
    _write(tmp_path, "Makefile", "build:\n\techo hi\n")

    ledger = build_intent_ledger(
        str(tmp_path),
        "Fix the root Makefile",
        ["repo:Makefile:file:Makefile"],
        _plan(),
    )

    assert ledger["allowed_paths"] == ["Makefile"]
    contract = {
        "task_profile": {"project": str(tmp_path)},
        "intent_ledger": ledger,
    }

    result = validate_intent_ledger(
        contract,
        project=str(tmp_path),
        validation={"pytest": {"status": "pass"}},
        change_set={"status": "captured", "changed_paths": ["Makefile"]},
    )

    assert result["pass"] is True
    assert result["violations"] == []


def test_intent_ledger_allows_space_and_unicode_symbol_targets(tmp_path):
    _write(tmp_path, "my dir/smoke.py", "print('hi')\n")
    _write(tmp_path, "資料/程式.py", "print('hi')\n")

    ledger = build_intent_ledger(
        str(tmp_path),
        "Fix the scanner-produced targets",
        ["repo:my dir/smoke.py:file:smoke", "repo:資料/程式.py:file:程式"],
        _plan(),
    )

    assert ledger["allowed_paths"] == ["my dir/smoke.py", "資料/程式.py"]
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
            "changed_paths": ["my dir/smoke.py", "資料/程式.py"],
        },
    )

    assert result["pass"] is True
    assert result["violations"] == []


def test_intent_ledger_keeps_root_symbol_scope_bounded(tmp_path):
    _write(tmp_path, "smoke.py", "print('hi')\n")
    ledger = build_intent_ledger(
        str(tmp_path),
        "Fix the root smoke script",
        ["repo:smoke.py:file:smoke"],
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
            "changed_paths": ["smoke.py", "ops/deploy.sh"],
        },
    )

    assert result["pass"] is False
    violation = next(
        item for item in result["violations"] if item["type"] == "unplanned_diff"
    )
    assert violation["changed_paths"] == ["ops/deploy.sh"]


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
    assert result["violations"] == [
        {"type": "instruction_context_stale", "instruction_files": ["AGENTS.md"]}
    ]


def test_allowed_paths_preserve_repository_dotfiles(tmp_path):
    _write(tmp_path, ".gitignore", "dist/\n")
    _write(tmp_path, ".github/workflows/ci.yml", "name: CI\n")

    ledger = build_intent_ledger(
        str(tmp_path),
        "Document the offline CI loop",
        [".gitignore", "./.github/workflows/ci.yml"],
        _plan(),
    )

    assert ledger["allowed_paths"] == [".gitignore", ".github/workflows/ci.yml"]

    result = validate_intent_ledger(
        {"task_profile": {"project": str(tmp_path)}, "intent_ledger": ledger},
        project=str(tmp_path),
        change_set={
            "status": "captured",
            "changed_paths": [".gitignore", ".github/workflows/ci.yml"],
        },
    )

    assert result["pass"] is True
    assert result["violations"] == []


def test_version_label_does_not_become_allowed_path(tmp_path):
    ledger = build_intent_ledger(
        str(tmp_path),
        "Implement M1.1 module-state coherence",
        ["M1.1", "src/app.py"],
        _plan(),
    )

    assert ledger["allowed_paths"] == ["src/app.py"]
    task_requirement = next(
        requirement
        for requirement in ledger["requirements"]
        if requirement["kind"] == "task"
    )
    assert task_requirement["expected_paths"] == ["src/app.py"]


def test_hostile_nonpath_target_stays_bounded_unknown(tmp_path, monkeypatch):
    original_exists = Path.exists

    def fail_on_hostile_probe(path):
        if "\x00" in str(path):
            raise OSError("invalid filesystem probe")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", fail_on_hostile_probe)
    ledger = build_intent_ledger(
        str(tmp_path),
        "Ignore an invalid target label",
        ["hostile\x00label", "src/app.py"],
        _plan(),
    )

    assert ledger["allowed_paths"] == ["src/app.py"]


def test_match_allowed_keeps_leading_dot_and_refuses_escapes():
    assert _match_allowed("./.gitignore", [".gitignore"]) is True
    assert _match_allowed(".github/workflows/ci.yml", [".github"]) is True
    assert _match_allowed(".github/workflows/ci.yml", [".github/workflows"]) is True
    # A dot-stripped impostor must never satisfy the dotted plan.
    assert _match_allowed("gitignore", [".gitignore"]) is False
    assert _match_allowed("github/workflows/ci.yml", [".github"]) is False
    # Absolute and traversal paths stay refused on both sides.
    assert _match_allowed("/etc/passwd", ["/etc/passwd"]) is False
    assert _match_allowed("../outside/app.py", ["../outside/app.py"]) is False
    assert _match_allowed("outside/app.py", ["../outside/app.py"]) is False


def test_allowed_paths_refuse_absolute_and_traversal_targets(tmp_path):
    _write(tmp_path, ".gitignore", "dist/\n")

    ledger = build_intent_ledger(
        str(tmp_path),
        "Escape the repository",
        ["/etc/passwd", "../outside/app.py", ".gitignore"],
        _plan(),
    )

    assert ledger["allowed_paths"] == [".gitignore"]


OUT_OF_REPOSITORY_TARGETS = (
    "C:/repo/app.py",
    "c:/repo/app.py",
    "C:\\repo\\app.py",
    "C:app.py",
    "\\\\server\\share\\app.py",
    "//server/share/app.py",
    "/etc/passwd",
    "../outside/app.py",
)


@pytest.mark.parametrize("target", OUT_OF_REPOSITORY_TARGETS)
def test_allowed_paths_refuse_out_of_repository_targets(tmp_path, target):
    """A drive, UNC, absolute, or traversal target never becomes a scope."""
    _write(tmp_path, ".gitignore", "dist/\n")

    ledger = build_intent_ledger(
        str(tmp_path),
        "Escape the repository",
        [target, ".gitignore"],
        _plan(),
    )

    assert ledger["allowed_paths"] == [".gitignore"]
    # The same target is refused as a pattern, so a plan cannot be tricked
    # into licensing itself.
    assert _match_allowed(target, [target]) is False


@pytest.mark.parametrize("target", OUT_OF_REPOSITORY_TARGETS)
def test_match_allowed_refuses_out_of_repository_paths_under_full_scope(target):
    """Even the whole-repository scope cannot admit a path outside the repo."""
    assert _match_allowed(target, ["**"]) is False


def test_match_allowed_keeps_relative_forms_while_refusing_drive_paths():
    # Literal `./` stripping and dotted repository names stay intact.
    assert _match_allowed("./src/app.py", ["src/app.py"]) is True
    assert _match_allowed(".gitignore", ["./.gitignore"]) is True
    assert _match_allowed(".github/workflows/ci.yml", [".github"]) is True
    # A drive-qualified path is neither a usable target nor a usable pattern.
    assert _match_allowed("src/app.py", ["C:/repo/src/app.py"]) is False
    assert _match_allowed("C:/repo/src/app.py", ["src/app.py"]) is False


def test_intent_ledger_reports_drive_qualified_change_as_unplanned(tmp_path):
    _write(tmp_path, "src/app.py", "")
    ledger = build_intent_ledger(
        str(tmp_path),
        "Change the app",
        ["src/app.py"],
        _plan(),
    )

    result = validate_intent_ledger(
        {"task_profile": {"project": str(tmp_path)}, "intent_ledger": ledger},
        project=str(tmp_path),
        change_set={
            "status": "captured",
            "changed_paths": ["src/app.py", "C:/repo/src/app.py"],
        },
    )

    assert result["pass"] is False
    violation = next(
        item for item in result["violations"] if item["type"] == "unplanned_diff"
    )
    assert violation["changed_paths"] == ["C:/repo/src/app.py"]


def test_inline_contract_preserves_dot_directory_paths():
    paths, symbols, proofs = _inline_contract(
        "Update `./src/app.py` and `.github/workflows/ci.yml`."
    )

    assert paths == ["src/app.py", ".github/workflows/ci.yml"]
    assert symbols == []
    assert proofs == []


def _instruction_contract(tmp_path, targets: list[str]) -> dict:
    return attach_task_context(
        {
            "task_profile": {"project": str(tmp_path)},
            "execution_plan": _plan(),
        },
        project=str(tmp_path),
        description="Document the offline verification loop",
        targets=targets,
    )


def test_instruction_context_allows_planned_instruction_file_edit(tmp_path):
    _write(tmp_path, "AGENTS.md", "- Always run pytest.\n")
    _write(tmp_path, "docs/README.md", "# Docs\n")
    contract = _instruction_contract(tmp_path, ["AGENTS.md", "docs/README.md"])
    _write(
        tmp_path,
        "AGENTS.md",
        "- Always run pytest.\n- Always run python -m unittest offline.\n",
    )

    result = validate_instruction_context(
        contract,
        project=str(tmp_path),
        changed_paths=["AGENTS.md", "docs/README.md"],
    )

    assert result["pass"] is True
    assert result["violations"] == []


def test_instruction_context_blocks_mixed_unrelated_instruction_drift(tmp_path):
    _write(tmp_path, "AGENTS.md", "- Always run pytest.\n")
    _write(tmp_path, "CLAUDE.md", "- Always write a handoff.\n")
    contract = _instruction_contract(tmp_path, ["AGENTS.md"])
    _write(
        tmp_path,
        "AGENTS.md",
        "- Always run pytest.\n- Always run python -m unittest offline.\n",
    )
    _write(
        tmp_path,
        "CLAUDE.md",
        "- Always write a handoff.\n- Always update STATE.md.\n",
    )

    result = validate_instruction_context(
        contract,
        project=str(tmp_path),
        changed_paths=["AGENTS.md", "CLAUDE.md"],
    )

    assert result["pass"] is False
    assert result["violations"] == [
        {"type": "instruction_context_stale", "instruction_files": ["CLAUDE.md"]}
    ]
    assert result["required_actions"] == [
        "refresh_task_context:instruction_context_stale"
    ]


def test_instruction_context_blocks_unplanned_edit_of_scoped_instructions(tmp_path):
    _write(tmp_path, "AGENTS.md", "- Always run pytest.\n")
    _write(tmp_path, "docs/README.md", "# Docs\n")
    contract = _instruction_contract(tmp_path, ["docs/README.md"])
    _write(
        tmp_path,
        "AGENTS.md",
        "- Always run pytest.\n- Always run python -m unittest offline.\n",
    )

    result = validate_instruction_context(
        contract,
        project=str(tmp_path),
        changed_paths=["AGENTS.md", "docs/README.md"],
    )

    assert result["pass"] is False
    assert result["violations"] == [
        {"type": "instruction_context_stale", "instruction_files": ["AGENTS.md"]}
    ]


def test_content_only_drift_requires_identical_scope_metadata():
    """Only a digest change is a planned edit; any scope change stays stale."""
    expected = {
        "path": "AGENTS.md",
        "provider": "agents",
        "scope": ".",
        "depth": 0,
        "sha256": "a",
        "applies_to": ["."],
    }

    assert _content_only_drift(expected, {**expected, "sha256": "b"}) is True
    assert _content_only_drift(expected, dict(expected)) is False
    assert _content_only_drift(
        expected, {**expected, "sha256": "b", "scope": "src"}
    ) is False
    assert _content_only_drift(
        expected, {**expected, "sha256": "b", "applies_to": ["src"]}
    ) is False


def test_instruction_context_blocks_added_instruction_scope(tmp_path):
    _write(tmp_path, "AGENTS.md", "- Always run pytest.\n")
    _write(tmp_path, "src/app.py", "")
    contract = _instruction_contract(tmp_path, ["AGENTS.md", "src"])
    _write(tmp_path, "src/AGENTS.md", "- Always run pytest.\n")

    result = validate_instruction_context(
        contract,
        project=str(tmp_path),
        changed_paths=["src/AGENTS.md"],
    )

    assert result["pass"] is False
    assert {item["type"] for item in result["violations"]} == {
        "instruction_context_stale",
        "unplanned_instruction_scope",
    }


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


def test_attach_task_context_flattens_compound_execution_plans(tmp_path):
    _write(tmp_path, "AGENTS.md", "- Always run pytest.\n")
    _write(tmp_path, "src/app.py", "")
    contract = {
        "task_profile": {
            "project": str(tmp_path),
            "compound": True,
        },
        "sub_tasks": [
            {
                "intent": "feature",
                "execution_plan": _plan(),
            },
            {
                "intent": "cleanup",
                "execution_plan": _plan(),
            },
        ],
    }

    attached = attach_task_context(
        contract,
        project=str(tmp_path),
        description="Change the app",
        targets=["src/app.py"],
    )

    ledger = attached["intent_ledger"]
    step_ids = [step["id"] for step in ledger["execution_plan"]]
    assert ledger["status"] == "ready"
    assert ledger["orphan_requirements"] == []
    assert len(step_ids) == 4
    assert len(set(step_ids)) == 4
    assert step_ids[0] == "subtask_01:step_01_apply_changes"
    assert step_ids[-1] == "subtask_02:step_02_validate"
