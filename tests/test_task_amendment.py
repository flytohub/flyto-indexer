"""Hostile tests for the cumulative plan-amendment contract."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from src.tools import smart
from src.tools.task_amendment import (
    MAX_AMENDMENT_CHAIN,
    MAX_AMENDMENT_TARGETS,
    build_amendment_request,
    parent_contract_digest,
    validate_amendment_state,
)
from src.tools.task_context import validate_intent_ledger


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A minimal, provider-neutral project with no specification files."""
    _write(tmp_path, "AGENTS.md", "- Always keep changes scoped.\n")
    for relative in ("alpha.py", "beta.py", "gamma.py", "delta.py"):
        _write(tmp_path, relative, "value = 1\n")
    _write(tmp_path, "pkg/inner.py", "value = 1\n")
    return tmp_path


def _plan(repo: Path, description: str, targets: list[str],
          parent: dict | None = None) -> dict:
    return smart._task_plan(
        description,
        targets,
        "refactor",
        str(repo),
        None,
        parent,
    )


def _changed(contract: dict) -> dict:
    return {
        "status": "captured",
        "changed_paths": list(contract["task_amendment"]["cumulative_paths"]),
    }


def _validate(contract: dict, changed_paths: list[str], repo: Path) -> dict:
    return validate_intent_ledger(
        contract,
        project=str(repo),
        validation={
            "ruff": {"status": "pass"},
            "pytest": {"status": "pass"},
        },
        change_set={"status": "captured", "changed_paths": changed_paths},
    )


# ---------------------------------------------------------------------------
# legacy compatibility
# ---------------------------------------------------------------------------

def test_plan_without_parent_keeps_legacy_shape(repo: Path) -> None:
    contract = _plan(repo, "Refactor alpha", ["alpha.py"])

    assert "task_amendment" not in contract
    assert "amendment_requirements" not in contract["intent_ledger"]
    assert contract["intent_ledger"]["allowed_paths"] == ["alpha.py"]
    assert "root_task_id" not in contract["task_profile"]


def test_plan_without_parent_is_fingerprint_stable(repo: Path) -> None:
    first = _plan(repo, "Refactor alpha", ["alpha.py"])
    second = _plan(repo, "Refactor alpha", ["alpha.py"])

    assert (
        first["intent_ledger"]["fingerprint"]
        == second["intent_ledger"]["fingerprint"]
    )
    assert first["task_profile"]["task_id"] != second["task_profile"]["task_id"]


def test_empty_parent_dict_is_treated_as_no_parent(repo: Path) -> None:
    contract = _plan(repo, "Refactor alpha", ["alpha.py"], {})

    assert "task_amendment" not in contract
    assert contract["intent_ledger"]["allowed_paths"] == ["alpha.py"]


# ---------------------------------------------------------------------------
# one and two amendments
# ---------------------------------------------------------------------------

def test_first_amendment_unions_scope_on_the_same_root(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)

    state = amended["task_amendment"]
    assert state["amendment_index"] == 1
    assert state["cumulative_paths"] == ["alpha.py", "beta.py"]
    assert state["added_paths"] == ["beta.py"]
    assert amended["task_profile"]["task_id"] == root["task_profile"]["task_id"]
    assert amended["task_profile"]["description"] == "Refactor alpha"
    assert amended["intent_ledger"]["allowed_paths"] == ["alpha.py", "beta.py"]
    # Recomputed, not appended: the fingerprint is a fresh value.
    assert (
        amended["intent_ledger"]["fingerprint"]
        != root["intent_ledger"]["fingerprint"]
    )
    assert (
        amended["task_profile"]["intent_fingerprint"]
        == amended["intent_ledger"]["fingerprint"]
    )


def test_second_amendment_stays_on_the_same_root_and_objective(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    second = _plan(repo, "Also touch gamma", ["gamma.py"], first)

    state = second["task_amendment"]
    assert state["amendment_index"] == 2
    assert state["cumulative_paths"] == ["alpha.py", "beta.py", "gamma.py"]
    assert state["objective"] == "Refactor alpha"
    assert second["task_profile"]["task_id"] == root["task_profile"]["task_id"]
    assert second["task_profile"]["title"] == "Refactor alpha"
    assert [item["index"] for item in state["amendments"]] == [1, 2]
    assert len(state["chain"]) == 2
    assert state["chain"][0]["amendment_index"] == 0
    assert state["chain"][1]["amendment_index"] == 1
    assert state["chain"][1]["parent_contract_id"] == state["chain"][0]["contract_id"]
    assert state["parent_contract_id"] == state["chain"][1]["contract_id"]


def test_amendment_never_widens_to_the_whole_repository(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)

    assert "**" not in amended["intent_ledger"]["allowed_paths"]
    assert amended["intent_ledger"]["allowed_paths"] == ["alpha.py", "beta.py"]


# ---------------------------------------------------------------------------
# deterministic union
# ---------------------------------------------------------------------------

def test_union_is_deterministic_and_order_stable(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Add more", ["gamma.py", "beta.py"], root)
    second = _plan(repo, "Add more", ["gamma.py", "beta.py"], root)

    assert first["task_amendment"]["cumulative_paths"] == [
        "alpha.py",
        "gamma.py",
        "beta.py",
    ]
    assert (
        first["task_amendment"]["cumulative_paths"]
        == second["task_amendment"]["cumulative_paths"]
    )
    assert (
        first["task_amendment"]["contract_id"]
        == second["task_amendment"]["contract_id"]
    )


def test_duplicate_targets_collapse_without_double_authority(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(
        repo, "Repeat", ["beta.py", "./beta.py", "beta.py", "alpha.py"], root
    )

    state = amended["task_amendment"]
    assert state["added_paths"] == ["beta.py"]
    assert state["cumulative_paths"] == ["alpha.py", "beta.py"]
    assert amended["intent_ledger"]["allowed_paths"] == ["alpha.py", "beta.py"]


# ---------------------------------------------------------------------------
# validate: cumulative authority, current amendment coverage
# ---------------------------------------------------------------------------

def test_alpha_root_beta_only_amendment_passes_with_retained_authority(
    repo: Path,
) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)

    gate = _validate(amended, ["beta.py"], repo)

    assert gate["pass"] is True, gate["violations"]
    assert amended["intent_ledger"]["allowed_paths"] == ["alpha.py", "beta.py"]


def test_validate_rejects_dropped_alpha_authority(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)
    tampered = copy.deepcopy(amended)
    tampered["intent_ledger"]["allowed_paths"] = ["beta.py"]

    gate = _validate(tampered, ["beta.py"], repo)

    assert gate["pass"] is False
    assert any(
        item["type"] == "amendment_scope_shrunk"
        and item["paths"] == ["alpha.py"]
        for item in gate["violations"]
    )


def test_validate_rejects_missing_beta_from_current_amendment(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)

    gate = _validate(amended, ["alpha.py"], repo)

    assert gate["pass"] is False
    assert any(
        item["type"] == "requirement_path_uncovered"
        and item["expected_paths"] == ["beta.py"]
        for item in gate["violations"]
    )


def test_validate_rejects_unplanned_gamma(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)

    gate = _validate(amended, ["alpha.py", "beta.py", "gamma.py"], repo)

    assert gate["pass"] is False
    assert any(
        item["type"] == "unplanned_diff" and item["changed_paths"] == ["gamma.py"]
        for item in gate["violations"]
    )


def test_second_amendment_gamma_only_passes_with_cumulative_authority(
    repo: Path,
) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    second = _plan(repo, "Also touch gamma", ["gamma.py"], first)

    gate = _validate(second, ["gamma.py"], repo)

    assert gate["pass"] is True, gate["violations"]
    assert second["intent_ledger"]["allowed_paths"] == [
        "alpha.py",
        "beta.py",
        "gamma.py",
    ]


def test_validate_rejects_scope_erased_after_the_fact(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)
    tampered = copy.deepcopy(amended)
    # Strip prior scope from the ledger while leaving the amendment claiming it.
    tampered["intent_ledger"]["allowed_paths"] = ["beta.py"]

    gate = _validate(tampered, ["beta.py"], repo)

    assert gate["pass"] is False
    assert any(item["type"] == "amendment_scope_shrunk" for item in gate["violations"])


def test_validate_rejects_tampered_chain(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    second = _plan(repo, "Also touch gamma", ["gamma.py"], first)
    tampered = copy.deepcopy(second)
    tampered["task_amendment"]["chain"][0]["target_count"] = 9

    gate = _validate(tampered, ["alpha.py", "beta.py", "gamma.py"], repo)

    assert gate["pass"] is False
    assert any(item["type"] == "amendment_chain_invalid" for item in gate["violations"])


def test_validate_rejects_unbounded_amendment_scope(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)
    tampered = copy.deepcopy(amended)
    tampered["intent_ledger"]["allowed_paths"] = ["**"]

    violations = validate_amendment_state(tampered, allowed_paths=["**"])

    assert any(item["type"] == "amendment_scope_unbounded" for item in violations)


# ---------------------------------------------------------------------------
# hostile parents
# ---------------------------------------------------------------------------

_UNSET = object()


def _request(repo: Path, parent: dict, targets: Any = _UNSET) -> dict:
    """Default the targets without swallowing an explicitly supplied ``None``."""
    return build_amendment_request(
        parent,
        project=str(repo),
        description="Also touch beta",
        targets=["beta.py"] if targets is _UNSET else targets,
    )


def _codes(result: dict) -> list[str]:
    return result["reason_codes"]


def test_unknown_parent_version_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    root["task_profile"]["version"] = "task-contract.v99"

    result = _request(repo, root)

    assert result["pass"] is False
    assert "AMENDMENT_PARENT_VERSION_UNSUPPORTED" in _codes(result)
    assert result["required_actions"] == ["rerun_task_plan_without_parent"]


def test_unknown_amendment_version_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    first["task_amendment"]["version"] = "task-amendment.v9"

    assert "AMENDMENT_PARENT_VERSION_UNSUPPORTED" in _codes(_request(repo, first))


def test_wrong_project_is_refused(repo: Path, tmp_path_factory) -> None:
    other = tmp_path_factory.mktemp("other")
    _write(other, "AGENTS.md", "- Always keep changes scoped.\n")
    _write(other, "beta.py", "value = 1\n")
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    result = build_amendment_request(
        root, project=str(other), description="Also touch beta", targets=["beta.py"]
    )

    assert result["pass"] is False
    assert "AMENDMENT_PARENT_PROJECT_MISMATCH" in _codes(result)
    assert result["required_actions"] == ["amend_within_the_same_project"]


def test_tampered_ledger_payload_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    # Widen recorded authority without recomputing the fingerprint.
    root["intent_ledger"]["allowed_paths"] = ["**"]

    assert "AMENDMENT_PARENT_LEDGER_TAMPERED" in _codes(_request(repo, root))


def test_tampered_ledger_fingerprint_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    root["intent_ledger"]["fingerprint"] = "0" * 64

    assert "AMENDMENT_PARENT_LEDGER_TAMPERED" in _codes(_request(repo, root))


def test_tampered_profile_fingerprint_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    root["task_profile"]["intent_fingerprint"] = "0" * 64

    assert "AMENDMENT_PARENT_LEDGER_TAMPERED" in _codes(_request(repo, root))


def test_stale_instruction_context_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    _write(repo, "AGENTS.md", "- Always keep changes scoped.\n- Never skip review.\n")

    result = _request(repo, root)

    assert result["pass"] is False
    assert "AMENDMENT_PARENT_INSTRUCTION_STALE" in _codes(result)
    assert result["required_actions"] == ["refresh_parent_task_contract"]


def test_stale_intent_ledger_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    _write(repo, "SPEC.md", "## Requirement: MUST keep `alpha.py` stable\n")

    assert "AMENDMENT_PARENT_LEDGER_STALE" in _codes(_request(repo, root))


def test_rewritten_objective_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    root["task_profile"]["description"] = "Do something else entirely"

    assert "AMENDMENT_PARENT_OBJECTIVE_MISMATCH" in _codes(_request(repo, root))


def test_long_objective_round_trips_without_false_staleness(repo: Path) -> None:
    objective = "Refactor alpha " + ("detail " * 80)
    root = _plan(repo, objective, ["alpha.py"])

    amended = _plan(repo, "Also touch beta", ["beta.py"], root)

    assert "task_amendment" in amended, amended.get("reason_codes")
    assert amended["task_amendment"]["objective"] == objective
    assert amended["task_profile"]["description"] == objective


def test_oversized_objective_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    root["intent_ledger"]["description"] = "x" * 5000

    result = _request(repo, root)

    assert result["pass"] is False
    assert "AMENDMENT_PARENT_OBJECTIVE_OVERSIZED" in _codes(result)


def test_missing_root_identity_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    root["task_profile"]["task_id"] = ""

    assert "AMENDMENT_PARENT_MISSING_ROOT_IDENTITY" in _codes(_request(repo, root))


def test_non_contract_parent_is_refused(repo: Path) -> None:
    for parent in ({"task_profile": "nope"}, {"error": "boom"}, {"unrelated": 1}):
        result = _request(repo, parent)
        assert result["pass"] is False
        assert result["reason_codes"]
        assert result["required_actions"]


# ---------------------------------------------------------------------------
# hostile targets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "target",
    [
        "../escape.py",
        "/etc/passwd",
        "~/secret.py",
        "pkg/../../escape.py",
        "pkg\\inner.py",
        "alpha\x00.py",
    ],
)
def test_path_escape_targets_are_refused(repo: Path, target: str) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    result = _request(repo, root, [target])

    assert result["pass"] is False
    assert "AMENDMENT_TARGET_NOT_RELATIVE" in _codes(result)
    assert result["required_actions"] == [
        "declare_bounded_relative_amendment_targets"
    ]


@pytest.mark.parametrize(
    "target",
    [
        ".", "**", "*", "src/**", "*.py", "alpha*.py", "pkg/*", "pkg/*.py",
        "alpha?.py", "alpha[ab].py", "[a-z].py", "pkg/**/inner.py",
    ],
)
def test_unbounded_targets_are_refused(repo: Path, target: str) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    result = _request(repo, root, [target])

    assert result["pass"] is False
    assert "AMENDMENT_TARGET_UNBOUNDED" in _codes(result)


def test_symlink_target_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    (repo / "link.py").symlink_to(repo / "beta.py")

    result = _request(repo, root, ["link.py"])

    assert result["pass"] is False
    assert "AMENDMENT_TARGET_SYMLINK" in _codes(result)


def test_symlinked_parent_directory_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    (repo / "linkdir").symlink_to(repo / "pkg", target_is_directory=True)

    result = _request(repo, root, ["linkdir/inner.py"])

    assert result["pass"] is False
    assert "AMENDMENT_TARGET_SYMLINK" in _codes(result)


@pytest.mark.parametrize(
    "target",
    [
        "check.generated_reference",
        "human.approval",
        "module.identifier",
        "pkg/check.some_capability",
        "some_bare_token",
    ],
)
def test_unresolvable_identifiers_never_become_edit_authority(
    repo: Path, target: str
) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    result = _request(repo, root, [target])

    assert result["pass"] is False
    assert "AMENDMENT_TARGET_UNRESOLVED" in _codes(result)
    assert result["required_actions"] == ["declare_resolvable_amendment_targets"]


def test_new_typed_file_under_existing_parent_is_allowed(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    amended = _plan(repo, "Add a module", ["pkg/created.py"], root)

    assert amended["task_amendment"]["added_paths"] == ["pkg/created.py"]


def test_new_file_under_missing_parent_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    result = _request(repo, root, ["absent/created.py"])

    assert "AMENDMENT_TARGET_UNRESOLVED" in _codes(result)


def test_existing_directory_and_symbol_targets_are_allowed(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    amended = _plan(
        repo, "Extend", ["pkg", f"{repo.name}:beta.py:function:handler"], root
    )

    assert amended["task_amendment"]["added_paths"] == ["pkg", "beta.py"]


def test_symbol_target_in_missing_file_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    result = _request(repo, root, [f"{repo.name}:absent.py:function:handler"])

    assert "AMENDMENT_TARGET_UNRESOLVED" in _codes(result)


@pytest.mark.parametrize(
    "targets",
    [[], "alpha.py", None, [123], [None], [""], ["   "], {}, 0],
    ids=[
        "empty-list", "scalar-string", "none", "non-string-item", "none-item",
        "empty-string-item", "blank-item", "empty-mapping", "zero",
    ],
)
def test_empty_scalar_none_and_non_string_targets_fail_closed(
    repo: Path, targets: Any
) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    result = _request(repo, root, targets)

    assert result["pass"] is False
    assert result["reason_codes"]
    assert result["required_actions"]
    assert all(code.startswith("AMENDMENT_") for code in result["reason_codes"])


def test_only_invalid_identifiers_keeps_the_specific_remediation(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    result = _request(repo, root, ["check.generated_reference"])

    assert result["reason_codes"] == ["AMENDMENT_TARGET_UNRESOLVED"]
    assert result["required_actions"] == ["declare_resolvable_amendment_targets"]
    assert "AMENDMENT_TARGETS_MISSING" not in result["reason_codes"]


def test_genuinely_empty_declaration_reports_missing(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])

    result = _request(repo, root, [])

    assert result["reason_codes"] == ["AMENDMENT_TARGETS_MISSING"]
    assert result["required_actions"] == [
        "declare_bounded_relative_amendment_targets"
    ]


def test_oversized_amendment_target_list_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    targets = []
    for index in range(MAX_AMENDMENT_TARGETS + 4):
        relative = f"pkg/mod_{index:03d}.py"
        _write(repo, relative, "value = 1\n")
        targets.append(relative)

    result = _request(repo, root, targets)

    assert result["pass"] is False
    assert "AMENDMENT_TARGETS_OVERSIZED" in _codes(result)
    assert result["required_actions"] == ["reduce_amendment_target_count"]


# ---------------------------------------------------------------------------
# hostile chains
# ---------------------------------------------------------------------------

def test_cyclic_chain_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    state = first["task_amendment"]
    state["chain"] = state["chain"] + [dict(state["chain"][0])]

    result = _request(repo, first, ["gamma.py"])

    assert result["pass"] is False
    assert _codes(result) == ["AMENDMENT_CHAIN_CYCLIC"]


def test_self_referential_chain_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    state = first["task_amendment"]
    state["chain"][0]["contract_id"] = state["contract_id"]

    result = _request(repo, first, ["gamma.py"])

    assert result["pass"] is False
    assert _codes(result)[0].startswith("AMENDMENT_CHAIN_")


def test_broken_chain_linkage_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    second = _plan(repo, "Also touch gamma", ["gamma.py"], first)
    second["task_amendment"]["chain"][1]["parent_contract_id"] = "amd_elsewhere"

    result = _request(repo, second, ["delta.py"])

    assert result["pass"] is False
    assert "AMENDMENT_CHAIN_TAMPERED" in _codes(result)


def test_chain_entry_digest_tamper_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    first["task_amendment"]["chain"][0]["contract_digest"] = "f" * 64

    result = _request(repo, first, ["gamma.py"])

    assert result["pass"] is False
    assert "AMENDMENT_CHAIN_TAMPERED" in _codes(result)


def test_chain_index_tamper_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    first["task_amendment"]["amendment_index"] = 5

    result = _request(repo, first, ["gamma.py"])

    assert result["pass"] is False
    assert _codes(result)[0].startswith("AMENDMENT_CHAIN_")


def test_chain_root_mismatch_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    first["task_amendment"]["chain"][0]["root_task_id"] = "task_refactor_other"

    result = _request(repo, first, ["gamma.py"])

    assert result["pass"] is False
    assert "AMENDMENT_CHAIN_ROOT_MISMATCH" in _codes(result)


def test_chain_project_mismatch_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    first["task_amendment"]["chain"][0]["project"] = "/somewhere/else"

    result = _request(repo, first, ["gamma.py"])

    assert result["pass"] is False
    assert "AMENDMENT_CHAIN_PROJECT_MISMATCH" in _codes(result)


def test_declared_cumulative_scope_tamper_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    first["task_amendment"]["cumulative_paths"] = ["alpha.py"]

    result = _request(repo, first, ["gamma.py"])

    assert result["pass"] is False
    assert "AMENDMENT_PARENT_SCOPE_TAMPERED" in _codes(result)


def test_malformed_chain_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    for chain in ("nope", [42], [{}], [{"contract_id": "x"}]):
        candidate = copy.deepcopy(first)
        candidate["task_amendment"]["chain"] = chain
        result = _request(repo, candidate, ["gamma.py"])
        assert result["pass"] is False
        assert result["reason_codes"]


def test_oversized_chain_is_refused(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    entry = first["task_amendment"]["chain"][0]
    first["task_amendment"]["chain"] = [
        dict(entry) for _index in range(MAX_AMENDMENT_CHAIN)
    ]

    result = _request(repo, first, ["gamma.py"])

    assert result["pass"] is False
    assert "AMENDMENT_CHAIN_OVERSIZED" in _codes(result)
    assert result["required_actions"] == ["close_this_task_before_amending_again"]


def test_parent_digest_changes_with_parent_content(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    before = parent_contract_digest(root)
    mutated = copy.deepcopy(root)
    mutated["task_profile"]["project"] = "/elsewhere"

    assert parent_contract_digest(mutated) != before
    assert parent_contract_digest(copy.deepcopy(root)) == before


# ---------------------------------------------------------------------------
# typed bounds and stable codes
# ---------------------------------------------------------------------------

def test_every_refusal_uses_bounded_stable_codes(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    root["task_profile"]["version"] = "nope"

    blocked = _plan(repo, "Also touch beta", ["beta.py"], root)

    assert blocked["pass"] is False
    assert blocked["decision"] == "blocked"
    assert blocked["task_amendment"]["status"] == "blocked"
    for code in blocked["reason_codes"]:
        assert code.startswith("AMENDMENT_")
        assert code == code.upper()
        assert len(code) <= 64
    for action in blocked["required_actions"]:
        assert action == action.lower()
        assert " " not in action


def test_amendment_requirements_are_typed_and_bounded(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)

    added = [
        item for item in amended["intent_ledger"]["requirements"]
        if item["source"] == "task.amendment"
    ]
    kinds = {item["kind"] for item in added}
    assert kinds == {"amendment", "amendment_target"}
    for item in added:
        assert item["id"].startswith("AMD-")
        assert len(item["text"]) <= 360
        assert item["expected_symbols"] == []
        assert item["proof_commands"] == []
        assert all("**" not in path for path in item["expected_paths"])
        assert item["planned_steps"]


def test_only_current_amendment_targets_are_coverage_requirements(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    first = _plan(repo, "Also touch beta", ["beta.py"], root)
    second = _plan(repo, "Also touch gamma", ["gamma.py"], first)

    scoped = {
        tuple(item["expected_paths"])
        for item in second["intent_ledger"]["requirements"]
        if item["kind"] in {"amendment_scope", "amendment_target"}
    }
    assert scoped == {("gamma.py",)}


def test_task_requirement_still_describes_the_original_objective(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    amended = _plan(repo, "Also touch beta", ["beta.py"], root)

    task_requirement = amended["intent_ledger"]["requirements"][0]
    assert task_requirement["kind"] == "task"
    assert task_requirement["text"] == "Refactor alpha"
    assert task_requirement["expected_paths"] == ["alpha.py", "beta.py"]


# ---------------------------------------------------------------------------
# continuity
# ---------------------------------------------------------------------------

def test_continuity_stays_on_the_root_task(repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLYTO_INDEXER_TASK_TRACKING", "1")
    monkeypatch.setattr(smart, "_structure_scan_path", lambda _mod, _project: str(repo))

    root = smart.smart_task(
        action="plan",
        description="Refactor alpha",
        targets=["alpha.py"],
        project=str(repo),
    )
    amended = smart.smart_task(
        action="plan",
        description="Also touch beta",
        targets=["beta.py"],
        project=str(repo),
        task_contract=root,
    )

    root_id = root["task_profile"]["task_id"]
    assert amended["task_profile"]["task_id"] == root_id
    assert amended["task_amendment"]["objective"] == "Refactor alpha"

    # Read continuity back through the same project identity the store wrote,
    # never through a locally re-derived name: _safe_project truncates its
    # input to 120 characters before taking the basename, so a re-derived key
    # silently stops matching once the temporary path grows.
    continuity = amended["continuity"]
    assert continuity["run_id"], "task tracking did not record this plan"
    assert continuity["task_id"] == root_id
    assert continuity["objective"] == "Refactor alpha"
    assert continuity["status"] in {"active", "needs_handoff"}
    # Same run, so the amendment continued the root task instead of
    # superseding it and opening a second active run.
    assert continuity["run_id"] == root["continuity"]["run_id"]

    from src.task_runs import read_task_continuity

    stored = read_task_continuity(repo, project=continuity["project"])
    assert stored["task_id"] == root_id
    assert stored["objective"] == "Refactor alpha"
    assert stored["run_id"] == continuity["run_id"]


def test_amendment_does_not_inherit_the_parent_run_id(repo: Path) -> None:
    root = _plan(repo, "Refactor alpha", ["alpha.py"])
    root["task_profile"]["run_id"] = "run_previous"

    amended = _plan(repo, "Also touch beta", ["beta.py"], root)

    assert "run_id" not in amended["task_profile"]
