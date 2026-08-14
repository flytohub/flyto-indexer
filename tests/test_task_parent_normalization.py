"""Proof-bound generation-2 task-parent normalization regressions."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from src.tools import smart, task_context
from src.tools.task_amendment import build_amendment_request
from src.tools.task_recovery_evidence import (
    NORMALIZED_PARENT_TAG,
    SUCCESSOR_AUTHORITY_TAG,
    _resolved_coordinate_matches,
    authority_digest,
    evidence_digest,
    source_contract_digest,
    validate_recovery_evidence,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _write(tmp_path, "AGENTS.md", "- Always keep changes scoped.\n")
    for relative in ("alpha.py", "beta.py", "gamma.py", "delta.py"):
        _write(tmp_path, relative, "value = 1\n")
    _write(tmp_path, "pkg/inner.py", "value = 1\n")
    return tmp_path


def _plan(
    repo: Path,
    description: str,
    targets: list[str],
    parent: dict | None = None,
    recovery_context: dict | None = None,
) -> dict:
    return smart._task_plan(
        description,
        targets,
        "refactor",
        str(repo),
        None,
        parent,
        recovery_context,
    )


def _codes(result: dict) -> list[str]:
    return result["reason_codes"]


def _legacy_poisoned_parent(
    repo: Path,
    base_targets: list[str] | None = None,
    legacy_target: str = "M1.1",
) -> dict:
    """Build an internally valid pre-exact-authority parent contract."""
    base = list(base_targets or ["alpha.py"])
    parent = _plan(repo, "Refactor alpha without widening scope", base)
    targets = [*base, legacy_target]
    execution_plan = list(parent["execution_plan"])
    parent["task_profile"]["targets"] = targets
    parent["task_profile"]["resolved_targets"] = [
        *parent["task_profile"]["resolved_targets"],
        {
            "input": legacy_target,
            "symbol_id": f"{repo.name}:ui/GitLabLogo.tsx:component:GitLabLogo",
            "name": "GitLabLogo",
            "type": "component",
            "path": "ui/GitLabLogo.tsx",
        },
    ]
    parent["intent_ledger"] = task_context.build_intent_ledger(
        str(repo), parent["task_profile"]["description"], targets, execution_plan
    )
    # This fixture models the historical path grammar under which a dotted
    # milestone label was incorrectly minted as edit authority.
    if legacy_target not in parent["intent_ledger"]["allowed_paths"]:
        parent["intent_ledger"]["allowed_paths"].append(legacy_target)
    if legacy_target not in parent["intent_ledger"]["requirements"][0]["expected_paths"]:
        parent["intent_ledger"]["requirements"][0]["expected_paths"].append(legacy_target)
    ledger_payload = {
        key: parent["intent_ledger"].get(key)
        for key in (
            "description",
            "targets",
            "execution_plan",
            "sources",
            "requirements",
            "allowed_paths",
        )
    }
    parent["intent_ledger"]["fingerprint"] = task_context._fingerprint(ledger_payload)
    parent["instruction_context"] = task_context.resolve_instruction_context(str(repo), targets)
    parent["task_profile"]["intent_fingerprint"] = parent["intent_ledger"]["fingerprint"]
    parent["task_profile"]["instruction_fingerprint"] = parent["instruction_context"]["fingerprint"]
    return parent


def _legacy_compound_parent(repo: Path) -> dict:
    parent = _legacy_poisoned_parent(repo)
    profile = parent["task_profile"]
    resolved = profile.pop("resolved_targets")
    targets = profile.pop("targets")
    profile["compound"] = True
    profile["sub_task_count"] = 1
    parent["sub_tasks"] = [
        {
            "intent": "refactor",
            "targets": targets,
            "resolved_targets": resolved,
            "execution_plan": list(parent["execution_plan"]),
        }
    ]
    return parent


def _rehash_ledger(parent: dict) -> None:
    ledger = parent["intent_ledger"]
    payload = {
        key: ledger.get(key)
        for key in (
            "description",
            "targets",
            "execution_plan",
            "sources",
            "requirements",
            "allowed_paths",
        )
    }
    ledger["fingerprint"] = task_context._fingerprint(payload)
    parent["task_profile"]["intent_fingerprint"] = ledger["fingerprint"]


def _append_conflicting_legacy_resolution(
    repo: Path,
    parent: dict,
    *,
    compound: bool,
    variant: str,
) -> None:
    owner = parent["sub_tasks"][0] if compound else parent["task_profile"]
    source = copy.deepcopy(owner["resolved_targets"][-1])
    source["input"] = "M2.2"
    if variant == "same_symbol":
        source["path"] = "ui/OtherLogo.tsx"
    elif variant == "same_path":
        source["symbol_id"] = f"{repo.name}:ui/OtherLogo.tsx:component:OtherLogo"
    owner["targets"].append("M2.2")
    owner["resolved_targets"].append(source)
    ledger = parent["intent_ledger"]
    ledger["targets"].append("M2.2")
    ledger["allowed_paths"].append("M2.2")
    ledger["requirements"][0]["expected_paths"].append("M2.2")
    _rehash_ledger(parent)
    instruction = task_context.resolve_instruction_context(str(repo), ledger["targets"])
    parent["instruction_context"] = instruction
    parent["task_profile"]["instruction_fingerprint"] = instruction["fingerprint"]


def _recovery(parent: dict, prior_scope: Any, targets: Any) -> dict:
    return {
        "version": "task-rework-recovery.request.v1",
        "source_parent_contract_digest": source_contract_digest(parent),
        "prior_scope": prior_scope,
        "requested_targets": targets,
    }


def _bound_evidence(
    repo: Path,
    parent: dict,
    *,
    prior_scope: list[str] | None = None,
    targets: list[str] | None = None,
) -> tuple[dict, dict, dict[str, dict]]:
    prior = prior_scope or ["beta.py"]
    requested = targets or ["gamma.py"]
    context = _recovery(parent, prior, requested)
    request = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=requested,
        recovery_context=context,
    )
    amended = _plan(repo, "Continue", requested, parent, context)
    recovery = request["recovery"]
    bindings = {
        "contract": amended,
        "source_parent": recovery["_source_contract"],
        "normalized_parent": recovery["_normalized_contract"],
        "expected_source_parent": recovery["source_parent"],
        "expected_normalized_parent": recovery["normalized_parent"],
        "expected_request": {
            "prior_scope": recovery["prior_scope"],
            "requested_targets": recovery["requested_targets"],
            "plan_targets": request["plan_targets"],
        },
        "expected_dropped_targets": recovery["dropped_targets"],
    }
    return amended["recovery_evidence"], amended, bindings


def _rehash_evidence(proof: dict) -> None:
    payload = {key: value for key, value in proof.items() if key != "evidence_digest"}
    proof["evidence_digest"] = evidence_digest(payload)


def _set_path(value: dict, path: tuple[str | int, ...], replacement: Any) -> None:
    target: Any = value
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = replacement


# ---------------------------------------------------------------------------
# proof-bound generation-2 parent recovery
# ---------------------------------------------------------------------------


def test_generation2_recovery_excludes_only_exact_legacy_poison(
    repo: Path,
) -> None:
    _write(repo, "ui/GitLabLogo.tsx", "export const GitLabLogo = 1;\n")
    parent = _legacy_poisoned_parent(repo)
    evidence = _recovery(parent, ["beta.py"], ["gamma.py"])

    amended = _plan(
        repo,
        "Continue the exact audited work",
        ["gamma.py"],
        parent,
        evidence,
    )

    assert "reason_codes" not in amended, amended
    state = amended["task_amendment"]
    assert state["objective"] == "Refactor alpha without widening scope"
    assert state["cumulative_paths"] == ["alpha.py", "beta.py", "gamma.py"]
    assert state["cumulative_targets"] == ["alpha.py", "beta.py", "gamma.py"]
    assert amended["intent_ledger"]["allowed_paths"] == [
        "alpha.py",
        "beta.py",
        "gamma.py",
    ]
    proof = amended["recovery_evidence"]
    assert list(proof) == [
        "version",
        "generation",
        "normalization_kind",
        "source_parent",
        "normalized_parent",
        "request",
        "dropped_targets",
        "authority_union",
        "successor",
        "evidence_digest",
    ]
    assert proof["version"] == "task-rework-recovery.v1"
    assert proof["generation"] == 2
    assert proof["normalization_kind"] == "legacy_exact_target_authority.v1"
    assert proof["source_parent"]["sha256"] == source_contract_digest(parent)
    assert proof["normalized_parent"]["paths"] == ["alpha.py"]
    assert proof["request"] == {
        "prior_scope": ["beta.py"],
        "requested_targets": ["gamma.py"],
        "plan_targets": ["beta.py", "gamma.py"],
    }
    assert proof["dropped_targets"] == [
        {
            "target": "M1.1",
            "reason_code": "legacy_nonexact_unresolved_target",
            "legacy_resolution_sha256": proof["dropped_targets"][0]["legacy_resolution_sha256"],
            "current_exact_resolution": False,
            "existing_literal": False,
        }
    ]
    assert proof["authority_union"] == ["alpha.py", "beta.py", "gamma.py"]
    assert proof["successor"]["cumulative_paths"] == proof["authority_union"]
    assert (
        proof["successor"]["parent_contract_digest"]
        == proof["normalized_parent"]["amendment_digest"]
    )


def test_generation2_recovery_union_is_first_seen_and_deterministic(
    repo: Path,
) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py", "beta.py"])
    requested = ["gamma.py", "pkg/inner.py", "delta.py"]
    evidence = _recovery(
        parent,
        ["beta.py", "pkg/inner.py", "alpha.py"],
        requested,
    )

    first = _plan(
        repo,
        "Continue",
        requested,
        parent,
        evidence,
    )
    second = _plan(
        repo,
        "Continue",
        requested,
        parent,
        evidence,
    )

    expected = ["alpha.py", "beta.py", "pkg/inner.py", "gamma.py", "delta.py"]
    assert first["task_amendment"]["cumulative_paths"] == expected
    assert second["task_amendment"]["cumulative_paths"] == expected
    assert first["task_amendment"]["contract_id"] == second["task_amendment"]["contract_id"]
    assert (
        first["recovery_evidence"]["evidence_digest"]
        == second["recovery_evidence"]["evidence_digest"]
    )


def test_generation2_code_shape_drops_only_poison_and_keeps_four_files(
    repo: Path,
) -> None:
    paths = [
        "src/hooks/useEffectivePageAccess.test.tsx",
        "src/components/WorkspaceSidebar.tsx",
        "src/hooks/useEffectivePageAccess.ts",
        "src/components/WorkspaceLayout.tsx",
    ]
    for path in paths:
        _write(repo, path, "export const value = 1;\n")
    parent = _legacy_poisoned_parent(repo, paths[:2])

    amended = _plan(
        repo,
        "Continue Code recovery",
        [paths[-1]],
        parent,
        _recovery(parent, paths, [paths[-1]]),
    )

    proof = amended["recovery_evidence"]
    assert proof["normalization_kind"] == "legacy_exact_target_authority.v1"
    assert proof["normalized_parent"]["paths"] == paths[:2]
    assert [item["target"] for item in proof["dropped_targets"]] == ["M1.1"]
    assert proof["request"]["plan_targets"] == paths
    assert proof["authority_union"] == paths


def test_generation2_engine_shape_unions_six_parent_and_twenty_one_audited(
    repo: Path,
) -> None:
    paths = [f".flyto-project/evidence/engine-{index:02d}.json" for index in range(21)]
    for path in paths:
        _write(repo, path, "{}\n")
    parent = _plan(repo, "Harden Engine image evidence", paths[:6])
    context = _recovery(parent, paths, [paths[-1]])

    first = _plan(repo, "Continue Engine recovery", [paths[-1]], parent, context)
    second = _plan(repo, "Continue Engine recovery", [paths[-1]], parent, context)

    proof = first["recovery_evidence"]
    assert proof["normalization_kind"] == "identity.v1"
    assert proof["normalized_parent"]["paths"] == paths[:6]
    assert proof["request"]["plan_targets"] == paths
    assert len(proof["request"]["plan_targets"]) <= 32
    assert proof["authority_union"] == paths
    assert first["task_amendment"]["cumulative_paths"] == paths
    assert proof["evidence_digest"] == second["recovery_evidence"]["evidence_digest"]


def _force_compound_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def classify(resolved: list[dict], _intent: str) -> dict[str, str]:
        return {
            item.get("symbol_id") or item["input"]: ("cleanup" if index % 2 == 0 else "refactor")
            for index, item in enumerate(resolved)
        }

    monkeypatch.setattr(
        "src.tools.task_analysis._classify_target_intent",
        classify,
    )


def test_generation2_compound_successor_binds_each_plan_target_once(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py"])
    _force_compound_successor(monkeypatch)

    proof, amended, bindings = _bound_evidence(
        repo,
        parent,
        prior_scope=["beta.py", "gamma.py"],
        targets=["delta.py"],
    )

    assert amended["task_profile"]["compound"] is True
    assert len(amended["sub_tasks"]) == 2
    assert validate_recovery_evidence(proof, **bindings) is True


@pytest.mark.parametrize("tamper", ["omit", "duplicate", "invent"])
def test_generation2_compound_successor_rejects_plan_attribution_drift(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py"])
    _force_compound_successor(monkeypatch)
    proof, amended, bindings = _bound_evidence(
        repo,
        parent,
        prior_scope=["beta.py", "gamma.py"],
        targets=["delta.py"],
    )
    drifted_contract = copy.deepcopy(amended)
    subtask = drifted_contract["sub_tasks"][0]
    if tamper == "omit":
        subtask["targets"].pop()
        subtask["resolved_targets"].pop()
    elif tamper == "duplicate":
        subtask["targets"].append(subtask["targets"][0])
        subtask["resolved_targets"].append(copy.deepcopy(subtask["resolved_targets"][0]))
    else:
        subtask["targets"][0] = "invented.py"
        subtask["resolved_targets"][0]["input"] = "invented.py"
    proof["successor"]["sha256"] = authority_digest(SUCCESSOR_AUTHORITY_TAG, drifted_contract)
    _rehash_evidence(proof)
    bindings["contract"] = drifted_contract

    assert validate_recovery_evidence(proof, **bindings) is False


def test_generation2_live_shape_is_deterministic_across_hash_seeds(
    repo: Path,
) -> None:
    paths = [f".flyto-project/evidence/seed-{index:02d}.json" for index in range(21)]
    for path in paths:
        _write(repo, path, "{}\n")
    parent = _plan(repo, "Harden Engine image evidence", paths[:6])
    recovery = _recovery(parent, paths, [paths[-1]])
    parent_path = repo / "parent.json"
    recovery_path = repo / "recovery.json"
    parent_path.write_text(json.dumps(parent), encoding="utf-8")
    recovery_path.write_text(json.dumps(recovery), encoding="utf-8")
    script = """
import json
import sys
from src.tools import smart, task_analysis

task_analysis.load_index = lambda project=None: {"project": project, "symbols": {}}
with open(sys.argv[1], encoding="utf-8") as handle:
    parent = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    recovery = json.load(handle)
result = smart._task_plan(
    "Continue Engine recovery",
    recovery["requested_targets"],
    "refactor",
    sys.argv[3],
    None,
    parent,
    recovery,
)
proof = result["recovery_evidence"]
print(json.dumps({
    "contract_id": result["task_amendment"]["contract_id"],
    "parent_contract_digest": result["task_amendment"]["parent_contract_digest"],
    "evidence": proof,
    "cumulative_paths": result["task_amendment"]["cumulative_paths"],
}, sort_keys=True, separators=(",", ":")))
"""
    outputs: list[dict[str, Any]] = []
    project_root = str(Path(__file__).parents[1])
    for seed in ("0", "3"):
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = project_root
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(parent_path),
                str(recovery_path),
                str(repo),
            ],
            cwd=project_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        outputs.append(json.loads(process.stdout.strip().splitlines()[-1]))

    assert outputs[0] == outputs[1]


def test_generation2_recovery_requires_exact_parent_digest(repo: Path) -> None:
    parent = _legacy_poisoned_parent(repo)
    evidence = _recovery(parent, ["beta.py"], ["gamma.py"])
    evidence["source_parent_contract_digest"] = "0" * 64

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=evidence,
    )

    assert result["pass"] is False
    assert "AMENDMENT_RECOVERY_PARENT_MISMATCH" in _codes(result)


def test_generation2_recovery_never_excludes_existing_literal_path(
    repo: Path,
) -> None:
    _write(repo, "M1.1", "literal path\n")
    parent = _legacy_poisoned_parent(repo)

    amended = _plan(
        repo,
        "Continue",
        ["gamma.py"],
        parent,
        _recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    assert amended["task_amendment"]["cumulative_paths"] == [
        "alpha.py",
        "M1.1",
        "beta.py",
        "gamma.py",
    ]
    assert amended["recovery_evidence"]["dropped_targets"] == []


def test_generation2_recovery_never_drops_absent_typed_archive(repo: Path) -> None:
    parent = _legacy_poisoned_parent(repo, legacy_target="archive.7z")

    amended = _plan(
        repo,
        "Continue",
        ["gamma.py"],
        parent,
        _recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    assert amended["recovery_evidence"]["normalization_kind"] == "identity.v1"
    assert amended["recovery_evidence"]["dropped_targets"] == []
    assert "archive.7z" in amended["task_amendment"]["cumulative_paths"]


def test_generation2_recovery_never_excludes_current_exact_symbol(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(repo, "milestones.ts", "export const milestone = 1;\n")
    parent = _legacy_poisoned_parent(repo)

    monkeypatch.setattr(
        "src.tools.task_analysis.load_index",
        lambda project=None: {"project": str(repo), "symbols": {}},
    )
    monkeypatch.setattr(
        "src.tools.task_analysis.search_by_keyword",
        lambda *_args, **_kwargs: {
            "results": [
                {
                    "input": "M1.1",
                    "symbol_id": f"{repo.name}:milestones.ts:constant:M1.1",
                    "name": "M1.1",
                    "type": "constant",
                    "path": "milestones.ts",
                }
            ]
        },
    )
    result = _plan(
        repo,
        "Continue",
        ["gamma.py"],
        parent,
        _recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    # Exact current authority is never classified as legacy poison. The
    # ordinary amendment validator still rejects the historical label as an
    # unresolved path instead of silently dropping or rewriting it.
    assert result["pass"] is False
    assert result["reason_codes"] == ["AMENDMENT_TARGET_UNRESOLVED"]


def test_generation2_recovery_refuses_unaudited_or_unresolved_scope(
    repo: Path,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    for audited in (["absent/missing.py"], ["ui/GitLabLogo.tsx"]):
        result = build_amendment_request(
            parent,
            project=str(repo),
            description="Continue",
            targets=["gamma.py"],
            recovery_context=_recovery(parent, audited, ["gamma.py"]),
        )
        assert result["pass"] is False
        assert result["reason_codes"]


def test_generation2_recovery_source_digest_binds_compound_resolutions(
    repo: Path,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    parent["sub_tasks"] = [
        {
            "targets": ["M1.1"],
            "resolved_targets": [
                {
                    "input": "M1.1",
                    "symbol_id": f"{repo.name}:ui/GitLabLogo.tsx:component:GitLabLogo",
                    "name": "GitLabLogo",
                    "type": "component",
                    "path": "ui/GitLabLogo.tsx",
                }
            ],
        }
    ]
    context = _recovery(parent, ["beta.py"], ["gamma.py"])
    parent["sub_tasks"][0]["resolved_targets"][0]["name"] = "DifferentLogo"

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=context,
    )

    assert "AMENDMENT_RECOVERY_PARENT_MISMATCH" in _codes(result)


def test_generation2_recovery_normalizes_one_compound_owner(repo: Path) -> None:
    parent = _legacy_compound_parent(repo)

    amended = _plan(
        repo,
        "Continue",
        ["gamma.py"],
        parent,
        _recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    assert amended["recovery_evidence"]["normalization_kind"] == (
        "legacy_exact_target_authority.v1"
    )
    assert amended["recovery_evidence"]["dropped_targets"][0]["target"] == "M1.1"
    assert amended["intent_ledger"]["allowed_paths"] == [
        "alpha.py",
        "beta.py",
        "gamma.py",
    ]


def test_generation2_recovery_refuses_duplicate_resolution_authority(
    repo: Path,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    parent["sub_tasks"] = [
        {"resolved_targets": [copy.deepcopy(parent["task_profile"]["resolved_targets"][1])]}
    ]

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=_recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    assert "AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN" in _codes(result)


@pytest.mark.parametrize("compound", [False, True])
@pytest.mark.parametrize("variant", ["duplicate", "same_symbol", "same_path"])
def test_generation2_recovery_requires_global_legacy_resolution_ownership(
    repo: Path,
    compound: bool,
    variant: str,
) -> None:
    parent = _legacy_compound_parent(repo) if compound else _legacy_poisoned_parent(repo)
    _append_conflicting_legacy_resolution(
        repo,
        parent,
        compound=compound,
        variant=variant,
    )

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=_recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    assert result["pass"] is False
    assert result["reason_codes"] == ["AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN"]


@pytest.mark.parametrize("tamper", ["duplicate_target", "duplicate_record", "missing_record"])
def test_generation2_recovery_requires_one_to_one_resolution_matrix(
    repo: Path,
    tamper: str,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    if tamper == "duplicate_target":
        parent["task_profile"]["targets"].append("M1.1")
    elif tamper == "duplicate_record":
        parent["task_profile"]["resolved_targets"].append(
            copy.deepcopy(parent["task_profile"]["resolved_targets"][-1])
        )
    else:
        parent["task_profile"]["targets"].append("unresolved-extra")

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=_recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    assert "AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN" in _codes(result)


def test_generation2_recovery_request_has_no_caller_selected_drop_mode(
    repo: Path,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    context = _recovery(parent, ["beta.py"], ["gamma.py"])
    context["dropped_parent_paths"] = ["alpha.py"]

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=context,
    )

    assert "AMENDMENT_RECOVERY_CONTEXT_INVALID" in _codes(result)


def test_generation2_recovery_request_targets_are_content_bound(
    repo: Path,
) -> None:
    parent = _legacy_poisoned_parent(repo)

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=_recovery(parent, ["beta.py"], ["delta.py"]),
    )

    assert "AMENDMENT_RECOVERY_REQUEST_MISMATCH" in _codes(result)


def test_generation2_identity_recovery_binds_plan_and_authority_separately(
    repo: Path,
) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py", "beta.py"])
    requested = ["gamma.py", "pkg/inner.py", "delta.py"]
    prior_scope = ["beta.py", "pkg/inner.py", "alpha.py"]

    amended = _plan(
        repo,
        "Continue",
        requested,
        parent,
        _recovery(parent, prior_scope, requested),
    )

    proof = amended["recovery_evidence"]
    assert proof["normalization_kind"] == "identity.v1"
    assert amended["task_profile"]["targets"] == [
        "beta.py",
        "pkg/inner.py",
        "alpha.py",
        "gamma.py",
        "delta.py",
    ]
    assert proof["authority_union"] == [
        "alpha.py",
        "beta.py",
        "pkg/inner.py",
        "gamma.py",
        "delta.py",
    ]
    assert amended["intent_ledger"]["allowed_paths"] == proof["authority_union"]
    assert amended["instruction_context"]["targets"] == proof["authority_union"]
    assert proof["normalized_parent"]["sha256"] == authority_digest(NORMALIZED_PARENT_TAG, parent)
    assert (
        proof["source_parent"]["amendment_digest"] == proof["normalized_parent"]["amendment_digest"]
    )


def test_generation2_identity_preserves_parent_raw_symbol_history(
    repo: Path,
) -> None:
    symbol = f"{repo.name}:alpha.py:function:foo"
    parent = _plan(repo, "Refactor alpha symbol", [symbol])

    amended = _plan(
        repo,
        "Continue",
        ["gamma.py"],
        parent,
        _recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    state = amended["task_amendment"]
    assert state["original_targets"] == [symbol]
    assert state["original_paths"] == ["alpha.py"]
    assert state["cumulative_targets"] == [symbol, "beta.py", "gamma.py"]
    assert state["cumulative_paths"] == ["alpha.py", "beta.py", "gamma.py"]
    assert amended["recovery_evidence"]["normalization_kind"] == "identity.v1"


def test_generation2_evidence_digests_are_exact_and_non_circular(
    repo: Path,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    proof, amended, bindings = _bound_evidence(repo, parent)
    without_digest = {key: value for key, value in proof.items() if key != "evidence_digest"}

    assert proof["successor"]["sha256"] == authority_digest(SUCCESSOR_AUTHORITY_TAG, amended)
    assert proof["evidence_digest"] == evidence_digest(without_digest)
    assert list(proof["source_parent"]) == [
        "sha256",
        "amendment_digest",
        "task_id",
        "project",
        "objective",
        "amendment_index",
        "contract_id",
        "parent_contract_id",
    ]
    assert list(proof["normalized_parent"]) == [
        *list(proof["source_parent"]),
        "paths",
    ]
    assert validate_recovery_evidence(proof, **bindings) is True
    for path in (
        ("top",),
        ("source_parent",),
        ("normalized_parent",),
        ("request",),
        ("successor",),
    ):
        tampered = copy.deepcopy(proof)
        target = tampered if path == ("top",) else tampered[path[0]]
        target["unexpected"] = True
        assert validate_recovery_evidence(tampered, **bindings) is False


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("version",), "task-rework-recovery.v9"),
        (("generation",), 3),
        (("normalization_kind",), "identity.v1"),
        (("source_parent", "sha256"), "0" * 64),
        (("source_parent", "amendment_digest"), "0" * 64),
        (("source_parent", "task_id"), "different-task"),
        (("source_parent", "project"), "/different/project"),
        (("source_parent", "objective"), "different objective"),
        (("source_parent", "amendment_index"), 1),
        (("source_parent", "contract_id"), "amd_root_" + "0" * 20),
        (("source_parent", "parent_contract_id"), "amd_" + "0" * 24),
        (("normalized_parent", "sha256"), "0" * 64),
        (("normalized_parent", "amendment_digest"), "0" * 64),
        (("normalized_parent", "task_id"), "different-task"),
        (("normalized_parent", "project"), "/different/project"),
        (("normalized_parent", "objective"), "different objective"),
        (("normalized_parent", "amendment_index"), 1),
        (("normalized_parent", "contract_id"), "amd_root_" + "0" * 20),
        (("normalized_parent", "parent_contract_id"), "amd_" + "0" * 24),
        (("normalized_parent", "paths"), ["beta.py"]),
        (("request", "prior_scope"), ["alpha.py"]),
        (("request", "requested_targets"), ["delta.py"]),
        (("request", "plan_targets"), ["beta.py"]),
        (("dropped_targets", 0, "target"), "alpha.py"),
        (("dropped_targets", 0, "reason_code"), "different"),
        (("dropped_targets", 0, "legacy_resolution_sha256"), "0" * 64),
        (("dropped_targets", 0, "current_exact_resolution"), True),
        (("dropped_targets", 0, "existing_literal"), True),
        (("authority_union",), ["alpha.py", "beta.py"]),
        (("successor", "sha256"), "0" * 64),
        (("successor", "task_id"), "different-task"),
        (("successor", "project"), "/different/project"),
        (("successor", "amendment_index"), 3),
        (("successor", "contract_id"), "amd_" + "0" * 24),
        (("successor", "parent_contract_id"), "amd_root_" + "0" * 20),
        (("successor", "parent_contract_digest"), "0" * 64),
        (("successor", "intent_fingerprint"), "0" * 64),
        (("successor", "instruction_fingerprint"), "0" * 64),
        (("successor", "cumulative_paths"), ["alpha.py", "beta.py"]),
    ],
)
def test_generation2_bound_validator_rejects_authority_tamper_after_rehash(
    repo: Path,
    path: tuple[str | int, ...],
    replacement: Any,
) -> None:
    proof, _amended, bindings = _bound_evidence(repo, _legacy_poisoned_parent(repo))
    tampered = copy.deepcopy(proof)
    _set_path(tampered, path, replacement)
    _rehash_evidence(tampered)

    assert validate_recovery_evidence(tampered, **bindings) is False


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("request", "prior_scope"), []),
        (("request", "requested_targets"), []),
        (("request", "plan_targets"), []),
        (("normalized_parent", "paths"), []),
        (("authority_union",), []),
        (("successor", "cumulative_paths"), []),
        (("request", "prior_scope"), ["./beta.py"]),
        (("request", "prior_scope"), [f"prior-{index}.py" for index in range(33)]),
    ],
)
def test_generation2_evidence_path_bounds_are_semantic(
    repo: Path,
    path: tuple[str | int, ...],
    replacement: Any,
) -> None:
    proof, _amended, bindings = _bound_evidence(repo, _legacy_poisoned_parent(repo))
    tampered = copy.deepcopy(proof)
    _set_path(tampered, path, replacement)
    _rehash_evidence(tampered)

    assert validate_recovery_evidence(tampered, **bindings) is False


def test_generation2_evidence_rejects_thirty_three_plan_targets_after_rehash(
    repo: Path,
) -> None:
    proof, _amended, bindings = _bound_evidence(repo, _legacy_poisoned_parent(repo))
    proof["request"]["plan_targets"] = [f"oversized/plan-{index}.py" for index in range(33)]
    _rehash_evidence(proof)

    assert validate_recovery_evidence(proof, **bindings) is False


@pytest.mark.parametrize("field", ["targets", "resolved_targets"])
def test_generation2_evidence_binds_executable_profile_plan(
    repo: Path,
    field: str,
) -> None:
    proof, amended, bindings = _bound_evidence(repo, _legacy_poisoned_parent(repo))
    drifted_contract = copy.deepcopy(amended)
    if field == "targets":
        drifted_contract["task_profile"]["targets"] = ["delta.py"]
    else:
        drifted_contract["task_profile"]["resolved_targets"][0]["input"] = "delta.py"
    proof["successor"]["sha256"] = authority_digest(SUCCESSOR_AUTHORITY_TAG, drifted_contract)
    _rehash_evidence(proof)
    bindings["contract"] = drifted_contract

    assert validate_recovery_evidence(proof, **bindings) is False


@pytest.mark.parametrize("field", ["path", "symbol_id"])
def test_generation2_evidence_rejects_noncompound_resolved_coordinate_drift(
    repo: Path,
    field: str,
) -> None:
    proof, amended, bindings = _bound_evidence(repo, _legacy_poisoned_parent(repo))
    drifted_contract = copy.deepcopy(amended)
    row = drifted_contract["task_profile"]["resolved_targets"][0]
    row[field] = (
        "evil.py"
        if field == "path"
        else f"{repo.name}:evil.py:function:outside_scope"
    )
    proof["successor"]["sha256"] = authority_digest(
        SUCCESSOR_AUTHORITY_TAG, drifted_contract
    )
    _rehash_evidence(proof)
    bindings["contract"] = drifted_contract

    assert validate_recovery_evidence(proof, **bindings) is False


@pytest.mark.parametrize("field", ["path", "symbol_id"])
def test_generation2_evidence_rejects_compound_resolved_coordinate_drift(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py"])
    _force_compound_successor(monkeypatch)
    proof, amended, bindings = _bound_evidence(
        repo,
        parent,
        prior_scope=["beta.py", "gamma.py"],
        targets=["delta.py"],
    )
    drifted_contract = copy.deepcopy(amended)
    row = drifted_contract["sub_tasks"][0]["resolved_targets"][0]
    row[field] = (
        "evil.py"
        if field == "path"
        else f"{repo.name}:evil.py:function:outside_scope"
    )
    proof["successor"]["sha256"] = authority_digest(
        SUCCESSOR_AUTHORITY_TAG, drifted_contract
    )
    _rehash_evidence(proof)
    bindings["contract"] = drifted_contract

    assert validate_recovery_evidence(proof, **bindings) is False


def test_generation2_resolved_coordinate_accepts_exact_project_symbol() -> None:
    assert _resolved_coordinate_matches(
        {
            "input": "beta.py",
            "path": "beta.py",
            "symbol_id": "flyto-code:beta.py:function:inside_scope",
        },
        "beta.py",
        "flyto-code",
    ) is True


@pytest.mark.parametrize(
    "symbol_id",
    [
        "foreign-project:beta.py:function:outside_scope",
        "flyto-code:../beta.py:function:traversal",
        "flyto-code:beta.py:malformed",
    ],
)
def test_generation2_resolved_coordinate_rejects_foreign_or_malformed_symbol(
    symbol_id: str,
) -> None:
    assert _resolved_coordinate_matches(
        {"input": "beta.py", "path": "beta.py", "symbol_id": symbol_id},
        "beta.py",
        "flyto-code",
    ) is False


@pytest.mark.parametrize("compound", [False, True])
def test_generation2_evidence_rejects_foreign_project_symbol_after_rehash(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    compound: bool,
) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py"])
    if compound:
        _force_compound_successor(monkeypatch)
        proof, amended, bindings = _bound_evidence(
            repo,
            parent,
            prior_scope=["beta.py", "gamma.py"],
            targets=["delta.py"],
        )
        row = amended["sub_tasks"][0]["resolved_targets"][0]
    else:
        proof, amended, bindings = _bound_evidence(repo, parent)
        row = amended["task_profile"]["resolved_targets"][0]
    row["path"] = row["input"]
    row["symbol_id"] = f"foreign-project:{row['input']}:function:outside_scope"
    proof["successor"]["sha256"] = authority_digest(SUCCESSOR_AUTHORITY_TAG, amended)
    _rehash_evidence(proof)
    bindings["contract"] = amended

    assert validate_recovery_evidence(proof, **bindings) is False


def test_generation2_evidence_drop_set_is_bounded_unique_and_disjoint(
    repo: Path,
) -> None:
    proof, _amended, bindings = _bound_evidence(repo, _legacy_poisoned_parent(repo))
    duplicate = copy.deepcopy(proof)
    duplicate["dropped_targets"].append(copy.deepcopy(duplicate["dropped_targets"][0]))
    _rehash_evidence(duplicate)
    retained = copy.deepcopy(proof)
    retained["dropped_targets"][0]["target"] = "alpha.py"
    _rehash_evidence(retained)

    assert validate_recovery_evidence(duplicate, **bindings) is False
    assert validate_recovery_evidence(retained, **bindings) is False


@pytest.mark.parametrize("hostile", ["\ud800", float("nan"), ("not", "json")])
def test_generation2_evidence_validator_is_total_for_noncanonical_values(
    repo: Path,
    hostile: Any,
) -> None:
    proof, _amended, bindings = _bound_evidence(repo, _legacy_poisoned_parent(repo))
    proof["request"]["requested_targets"] = [hostile]
    proof["evidence_digest"] = "0" * 64

    assert validate_recovery_evidence(proof, **bindings) is False


@pytest.mark.parametrize(
    "hostile",
    [float("nan"), float("inf"), {1: "non-string key"}],
)
def test_generation2_source_digest_rejects_noncanonical_json(
    repo: Path,
    hostile: Any,
) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py"])
    parent["hostile"] = hostile

    assert source_contract_digest(parent) is None


def test_generation2_source_digest_rejects_tuple_and_lone_surrogate(
    repo: Path,
) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py"])
    with_tuple = copy.deepcopy(parent)
    with_tuple["hostile"] = ("not", "json")
    with_surrogate = copy.deepcopy(parent)
    with_surrogate["hostile"] = "\ud800"

    assert source_contract_digest(with_tuple) is None
    assert source_contract_digest(with_surrogate) is None


def test_generation2_source_digest_rejects_recursive_contract(repo: Path) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py"])
    parent["recursive"] = parent

    assert source_contract_digest(parent) is None


def test_generation2_source_digest_rejects_contract_over_256k(repo: Path) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py"])
    parent["oversized"] = "x" * (256 * 1024)

    assert source_contract_digest(parent) is None


def test_generation2_authority_projection_excludes_only_permitted_display(
    repo: Path,
) -> None:
    parent = _plan(repo, "Refactor alpha", ["alpha.py"])
    baseline = authority_digest(NORMALIZED_PARENT_TAG, parent)
    permitted = copy.deepcopy(parent)
    permitted["continuity"] = {"status": "recorded"}
    permitted["recovery_evidence"] = {"ignored": True}
    permitted["task_profile"]["generated_at"] = "different"
    bound = copy.deepcopy(parent)
    bound["human_summary"]["summary"] = "tampered display"

    assert authority_digest(NORMALIZED_PARENT_TAG, permitted) == baseline
    assert authority_digest(NORMALIZED_PARENT_TAG, bound) != baseline


@pytest.mark.parametrize(
    "path",
    ["./alpha.py", "pkg/", "pkg//inner.py", "C:/alpha.py", " alpha.py"],
)
def test_generation2_recovery_rejects_noncanonical_source_paths(
    repo: Path,
    path: str,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    parent["intent_ledger"]["allowed_paths"] = [path, "M1.1"]
    _rehash_ledger(parent)
    context = _recovery(parent, ["beta.py"], ["gamma.py"])

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=context,
    )

    assert "AMENDMENT_RECOVERY_SOURCE_SCOPE_INVALID" in _codes(result)


@pytest.mark.parametrize("broken", [False, True])
def test_generation2_recovery_never_drops_live_or_broken_symlink(
    repo: Path,
    broken: bool,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    target = repo / ("absent-target" if broken else "alpha.py")
    (repo / "M1.1").symlink_to(target)

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=_recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    assert "AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN" in _codes(result)


def test_generation2_recovery_retains_existing_directory(repo: Path) -> None:
    (repo / "M1.1").mkdir()
    parent = _legacy_poisoned_parent(repo)

    amended = _plan(
        repo,
        "Continue",
        ["gamma.py"],
        parent,
        _recovery(parent, ["beta.py"], ["gamma.py"]),
    )

    assert amended["recovery_evidence"]["dropped_targets"] == []
    assert "M1.1" in amended["task_amendment"]["cumulative_paths"]


def test_generation2_recovery_rejects_duplicate_or_oversized_requested_targets(
    repo: Path,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    duplicates = ["gamma.py", "gamma.py"]
    oversized = [f"new-{index}.py" for index in range(33)]
    for targets in (duplicates, oversized):
        result = build_amendment_request(
            parent,
            project=str(repo),
            description="Continue",
            targets=targets,
            recovery_context=_recovery(parent, ["beta.py"], targets),
        )
        assert result["pass"] is False


def test_generation2_recovery_rejects_oversized_cross_list_plan_union(
    repo: Path,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    prior_scope = [f"prior-{index:02d}.py" for index in range(32)]

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=_recovery(parent, prior_scope, ["gamma.py"]),
    )

    assert result["pass"] is False
    assert result["reason_codes"] == ["AMENDMENT_TARGETS_OVERSIZED"]


@pytest.mark.parametrize(
    "prior_scope",
    [(), [], [f"prior-{index}.py" for index in range(33)], [{}], [[]]],
)
def test_generation2_recovery_prior_scope_is_exact_bounded_json_list(
    repo: Path,
    prior_scope: Any,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    context = _recovery(parent, prior_scope, ["gamma.py"])

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=["gamma.py"],
        recovery_context=context,
    )

    assert result["pass"] is False
    assert "AMENDMENT_RECOVERY_PRIOR_SCOPE_INVALID" in _codes(result)


@pytest.mark.parametrize(
    "requested",
    [(), [], ["gamma.py", "gamma.py"], [{}], [[]]],
)
def test_generation2_recovery_requested_targets_are_exact_json_list(
    repo: Path,
    requested: Any,
) -> None:
    parent = _legacy_poisoned_parent(repo)
    context = _recovery(parent, ["beta.py"], requested)

    result = build_amendment_request(
        parent,
        project=str(repo),
        description="Continue",
        targets=requested,
        recovery_context=context,
    )

    assert result["pass"] is False


def test_generation2_recovery_without_parent_never_falls_back_to_fresh_plan(
    repo: Path,
) -> None:
    result = _plan(
        repo,
        "Continue",
        ["gamma.py"],
        None,
        {
            "version": "task-rework-recovery.request.v1",
            "source_parent_contract_digest": "0" * 64,
            "prior_scope": ["beta.py"],
            "requested_targets": ["gamma.py"],
        },
    )

    assert result["pass"] is False
    assert result["reason_codes"] == ["AMENDMENT_PARENT_NOT_A_CONTRACT"]
