"""Strict JSON digests and schema validation for task recovery evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import task_context

RECOVERY_REQUEST_VERSION = "task-rework-recovery.request.v1"
RECOVERY_EVIDENCE_VERSION = "task-rework-recovery.v1"
SOURCE_CONTRACT_TAG = "task-rework-recovery.source-parent.v1"
NORMALIZED_PARENT_TAG = "task-rework-recovery.normalized-parent.v1"
SUCCESSOR_AUTHORITY_TAG = "task-rework-recovery.successor-authority.v1"
LEGACY_RESOLUTION_TAG = "task-rework-recovery.legacy-resolution.v1"
RECOVERY_EVIDENCE_TAG = "task-rework-recovery.evidence.v1"
LEGACY_MISMATCH_REASON = "legacy_nonexact_unresolved_target"
MAX_SOURCE_CONTRACT_BYTES = 256 * 1024

EVIDENCE_KEYS = frozenset(
    {
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
    }
)
PARENT_KEYS = frozenset(
    {
        "sha256",
        "amendment_digest",
        "task_id",
        "project",
        "objective",
        "amendment_index",
        "contract_id",
        "parent_contract_id",
    }
)
DROP_KEYS = frozenset(
    {
        "target",
        "reason_code",
        "legacy_resolution_sha256",
        "current_exact_resolution",
        "existing_literal",
    }
)
SUCCESSOR_KEYS = frozenset(
    {
        "sha256",
        "task_id",
        "project",
        "amendment_index",
        "contract_id",
        "parent_contract_id",
        "parent_contract_digest",
        "intent_fingerprint",
        "instruction_fingerprint",
        "cumulative_paths",
    }
)
REQUEST_KEYS = frozenset({"prior_scope", "requested_targets", "plan_targets"})


def _has_only_json_values(value: Any, seen: set[int] | None = None) -> bool:
    if not isinstance(value, (dict, list)):
        return value is None or isinstance(value, (str, int, float, bool))
    active = seen if seen is not None else set()
    identity = id(value)
    if identity in active:
        return False
    active.add(identity)
    try:
        if isinstance(value, dict):
            return all(
                isinstance(key, str) and _has_only_json_values(item, active)
                for key, item in value.items()
            )
        return all(_has_only_json_values(item, active) for item in value)
    finally:
        active.remove(identity)


def strict_canonical(value: Any) -> str | None:
    """Serialize exact JSON without coercion, non-finite numbers, or surrogates."""
    if not _has_only_json_values(value):
        return None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return None


def tagged_digest(tag: str, payload: Any) -> str | None:
    """Hash one canonical payload under an explicit domain-separation tag."""
    encoded = strict_canonical([tag, payload])
    if encoded is None:
        return None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def source_contract_digest(parent: dict[str, Any]) -> str | None:
    """Bind the complete raw parent while enforcing the source-size ceiling."""
    encoded = strict_canonical(parent)
    if encoded is None or len(encoded.encode("utf-8")) > MAX_SOURCE_CONTRACT_BYTES:
        return None
    return tagged_digest(SOURCE_CONTRACT_TAG, parent)


def authority_projection(contract: dict[str, Any]) -> dict[str, Any] | None:
    """Remove only declared volatile top-level fields from an exact deep copy."""
    encoded = strict_canonical(contract)
    if encoded is None:
        return None
    projected = json.loads(encoded)
    projected.pop("recovery_evidence", None)
    projected.pop("continuity", None)
    profile = projected.get("task_profile")
    if isinstance(profile, dict):
        profile.pop("generated_at", None)
    return projected


def authority_digest(tag: str, contract: dict[str, Any]) -> str | None:
    """Hash the non-volatile authority projection under the requested domain."""
    projection = authority_projection(contract)
    return tagged_digest(tag, projection) if projection is not None else None


def evidence_digest(payload: dict[str, Any]) -> str:
    """Return the mandatory recovery-evidence digest or reject non-JSON input."""
    digest = tagged_digest(RECOVERY_EVIDENCE_TAG, payload)
    if digest is None:
        raise ValueError("recovery evidence is not canonical JSON")
    return digest


def _has_exact_keys(value: Any, expected: frozenset[str]) -> bool:
    return isinstance(value, dict) and set(value) == expected


def _is_hex_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_contract_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if value.startswith("amd_root_"):
        suffix = value.removeprefix("amd_root_")
        return len(suffix) == 20 and all(char in "0123456789abcdef" for char in suffix)
    if value.startswith("amd_"):
        suffix = value.removeprefix("amd_")
        return len(suffix) == 24 and all(char in "0123456789abcdef" for char in suffix)
    return False


def _is_canonical_path(value: Any) -> bool:
    """Recognize one already-canonical bounded repository-relative path."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if len(value) > 512 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if (
        "\\" in value
        or value.startswith(("/", "~", "./"))
        or value.endswith("/")
        or "//" in value
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
        or any(character in value for character in "*?[]")
    ):
        return False
    segments = value.split("/")
    return bool(segments) and all(segment not in {"", ".", ".."} for segment in segments)


def _is_path_list(value: Any, *, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, list)
        and minimum <= len(value) <= maximum
        and all(_is_canonical_path(item) for item in value)
        and len(value) == len(set(value))
    )


def _ordered_union(*groups: list[str]) -> list[str]:
    union: list[str] = []
    for group in groups:
        for item in group:
            if item not in union:
                union.append(item)
    return union


def _parent_semantics(source: dict[str, Any], normalized: dict[str, Any]) -> bool:
    """Validate shared parent identity, digest shape, and root linkage rules."""
    shared = ("task_id", "project", "objective", "amendment_index")
    if any(source.get(key) != normalized.get(key) for key in shared):
        return False
    digests = tuple(
        item.get(key) for item in (source, normalized) for key in ("sha256", "amendment_digest")
    )
    if not all(_is_hex_digest(digest) for digest in digests):
        return False
    index = source.get("amendment_index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        return False
    if not all(isinstance(source.get(key), str) and source[key] for key in shared[:3]):
        return False
    if not all(_is_contract_id(item.get("contract_id")) for item in (source, normalized)):
        return False
    parents = (source.get("parent_contract_id"), normalized.get("parent_contract_id"))
    if index == 0:
        return all(parent is None for parent in parents)
    return all(_is_contract_id(parent) for parent in parents)


def _drop_semantics(kind: str, dropped: Any, source: dict[str, Any]) -> bool:
    """Validate the closed normalization enum and bounded drop records."""
    if not isinstance(dropped, list):
        return False
    if kind == "identity.v1":
        return not dropped
    if kind != "legacy_exact_target_authority.v1" or not dropped:
        return False
    if source.get("amendment_index") != 0:
        return False
    targets = [item.get("target") for item in dropped if isinstance(item, dict)]
    if not 1 <= len(dropped) <= 64 or len(targets) != len(set(targets)):
        return False
    return all(
        item.get("reason_code") == LEGACY_MISMATCH_REASON
        and item.get("current_exact_resolution") is False
        and item.get("existing_literal") is False
        and _is_canonical_path(item.get("target"))
        and _is_hex_digest(item.get("legacy_resolution_sha256"))
        for item in dropped
    )


def _authority_semantics(value: dict[str, Any]) -> bool:
    """Reproduce ordered plan and cumulative authority unions exactly."""
    request = value["request"]
    normalized = value["normalized_parent"]
    successor = value["successor"]
    if not _is_path_list(request.get("prior_scope"), minimum=1, maximum=32):
        return False
    if not _is_path_list(request.get("requested_targets"), minimum=1, maximum=32):
        return False
    if not _is_path_list(request.get("plan_targets"), minimum=1, maximum=32):
        return False
    if not _is_path_list(normalized.get("paths"), minimum=1, maximum=64):
        return False
    if not _is_path_list(value.get("authority_union"), minimum=1, maximum=64):
        return False
    if not _is_path_list(successor.get("cumulative_paths"), minimum=1, maximum=64):
        return False
    plan = _ordered_union(request["prior_scope"], request["requested_targets"])
    union = _ordered_union(normalized["paths"], plan)
    return (
        request["plan_targets"] == plan
        and value["authority_union"] == union
        and successor["cumulative_paths"] == union
    )


def _drop_authority_is_disjoint(value: dict[str, Any]) -> bool:
    dropped = [item["target"] for item in value["dropped_targets"]]
    if not dropped:
        return True
    request = value["request"]
    retained = _ordered_union(
        value["normalized_parent"]["paths"],
        request["prior_scope"],
        request["requested_targets"],
        request["plan_targets"],
        value["authority_union"],
        value["successor"]["cumulative_paths"],
    )
    return all(target not in retained for target in dropped)


def _successor_semantics(value: dict[str, Any]) -> bool:
    """Validate successor generation, parent linkage, and content identities."""
    source = value["source_parent"]
    normalized = value["normalized_parent"]
    successor = value["successor"]
    identities = (
        successor.get("task_id") == source.get("task_id"),
        successor.get("project") == source.get("project"),
        successor.get("amendment_index") == source.get("amendment_index") + 1,
        successor.get("parent_contract_id") == normalized.get("contract_id"),
        successor.get("parent_contract_digest") == normalized.get("amendment_digest"),
    )
    digests = (
        successor.get("sha256"),
        successor.get("parent_contract_digest"),
        successor.get("intent_fingerprint"),
        successor.get("instruction_fingerprint"),
    )
    return (
        all(identities)
        and all(_is_hex_digest(digest) for digest in digests)
        and _is_contract_id(successor.get("contract_id"))
        and _is_contract_id(successor.get("parent_contract_id"))
    )


def _identity_semantics(value: dict[str, Any]) -> bool:
    if value.get("normalization_kind") != "identity.v1":
        return True
    source = value["source_parent"]
    normalized = value["normalized_parent"]
    return all(
        source.get(key) == normalized.get(key)
        for key in ("amendment_digest", "contract_id", "parent_contract_id")
    )


def _resolved_coordinate_matches(
    row: dict[str, Any],
    target: str,
    project: str,
) -> bool:
    """Confine every executable resolution coordinate to its plan path."""
    path = row.get("path")
    symbol_id = row.get("symbol_id")
    if not isinstance(path, str) or (path and path != target):
        return False
    if symbol_id is None or symbol_id == "":
        return True
    if not isinstance(symbol_id, str):
        return False
    symbol_project = symbol_id.split(":", 1)[0]
    return (
        symbol_project == project
        and task_context._symbol_path(symbol_id) == target
    )


def _resolved_rows_match_targets(
    value: Any,
    targets: list[str],
    project: str,
) -> bool:
    if not isinstance(value, list) or len(value) != len(targets):
        return False
    return all(
        isinstance(row, dict)
        and row.get("input") == target
        and _resolved_coordinate_matches(row, target, project)
        for row, target in zip(value, targets, strict=True)
    )


def _compound_plan_semantics(
    contract: dict[str, Any],
    plan_targets: list[str],
    project: str,
) -> bool:
    sub_tasks = contract.get("sub_tasks")
    if not isinstance(sub_tasks, list) or not sub_tasks:
        return False
    flattened: list[str] = []
    for subtask in sub_tasks:
        if not isinstance(subtask, dict):
            return False
        targets = subtask.get("targets")
        resolved = subtask.get("resolved_targets")
        if not isinstance(targets, list) or not targets:
            return False
        if not all(isinstance(item, str) for item in targets):
            return False
        if not _resolved_rows_match_targets(resolved, targets, project):
            return False
        flattened.extend(targets)
    return (
        len(flattened) == len(set(flattened))
        and len(flattened) == len(plan_targets)
        and set(flattened) == set(plan_targets)
    )


def _profile_plan_semantics(
    contract: dict[str, Any],
    profile: dict[str, Any],
    plan_targets: list[str],
) -> bool:
    """Bind executable root or compound attribution to every plan target."""
    project = profile.get("project")
    if not isinstance(project, str) or not project:
        return False
    if profile.get("compound"):
        if profile.get("targets") or profile.get("resolved_targets"):
            return False
        return _compound_plan_semantics(contract, plan_targets, project)
    return (
        profile.get("targets") == plan_targets
        and _resolved_rows_match_targets(
            profile.get("resolved_targets"), plan_targets, project
        )
    )


def _contract_semantics(value: dict[str, Any], contract: dict[str, Any]) -> bool:
    """Match evidence to the emitted executable successor contract."""
    profile = contract.get("task_profile")
    amendment = contract.get("task_amendment")
    ledger = contract.get("intent_ledger")
    instruction = contract.get("instruction_context")
    if not isinstance(profile, dict) or not isinstance(amendment, dict):
        return False
    if not isinstance(ledger, dict) or not isinstance(instruction, dict):
        return False
    plan_targets = value["request"]["plan_targets"]
    if not _profile_plan_semantics(contract, profile, plan_targets):
        return False
    successor = value["successor"]
    checks = (
        successor["sha256"] == authority_digest(SUCCESSOR_AUTHORITY_TAG, contract),
        successor["task_id"] == profile.get("task_id"),
        successor["project"] == profile.get("project"),
        successor["amendment_index"] == amendment.get("amendment_index"),
        successor["contract_id"] == amendment.get("contract_id"),
        successor["parent_contract_id"] == amendment.get("parent_contract_id"),
        successor["parent_contract_digest"] == amendment.get("parent_contract_digest"),
        successor["intent_fingerprint"] == profile.get("intent_fingerprint"),
        successor["instruction_fingerprint"] == profile.get("instruction_fingerprint"),
        successor["cumulative_paths"] == amendment.get("cumulative_paths"),
        value["authority_union"] == ledger.get("allowed_paths"),
        value["authority_union"] == instruction.get("targets"),
    )
    return all(checks)


def _bound_parent_semantics(
    value: dict[str, Any],
    *,
    source_parent: dict[str, Any] | None,
    normalized_parent: dict[str, Any] | None,
    expected_source_parent: dict[str, Any] | None,
    expected_normalized_parent: dict[str, Any] | None,
) -> bool:
    """Match producer snapshots and raw contracts without reverse imports."""
    if expected_source_parent is not None and value["source_parent"] != (expected_source_parent):
        return False
    if expected_normalized_parent is not None and value["normalized_parent"] != (
        expected_normalized_parent
    ):
        return False
    if source_parent is not None and value["source_parent"]["sha256"] != (
        source_contract_digest(source_parent)
    ):
        return False
    if normalized_parent is not None and value["normalized_parent"]["sha256"] != (
        authority_digest(NORMALIZED_PARENT_TAG, normalized_parent)
    ):
        return False
    if value["normalization_kind"] == "identity.v1" and source_parent is not None:
        if normalized_parent is None:
            return False
        source_projection = authority_projection(source_parent)
        normalized_projection = authority_projection(normalized_parent)
        if source_projection is None or source_projection != normalized_projection:
            return False
    return True


def _shape_is_valid(value: Any) -> bool:
    """Require the exact closed evidence and nested-object key sets."""
    if not _has_exact_keys(value, EVIDENCE_KEYS):
        return False
    if not _has_exact_keys(value.get("source_parent"), PARENT_KEYS):
        return False
    if not _has_exact_keys(value.get("normalized_parent"), PARENT_KEYS | {"paths"}):
        return False
    if not _has_exact_keys(value.get("request"), REQUEST_KEYS):
        return False
    if not _has_exact_keys(value.get("successor"), SUCCESSOR_KEYS):
        return False
    dropped = value.get("dropped_targets")
    return isinstance(dropped, list) and all(_has_exact_keys(item, DROP_KEYS) for item in dropped)


def _bound_evidence_semantics(
    value: dict[str, Any],
    *,
    contract: dict[str, Any],
    source_parent: dict[str, Any],
    normalized_parent: dict[str, Any],
    expected_source_parent: dict[str, Any],
    expected_normalized_parent: dict[str, Any],
    expected_request: dict[str, Any],
    expected_dropped_targets: list[dict[str, Any]],
) -> bool:
    if not _contract_semantics(value, contract):
        return False
    if not _bound_parent_semantics(
        value,
        source_parent=source_parent,
        normalized_parent=normalized_parent,
        expected_source_parent=expected_source_parent,
        expected_normalized_parent=expected_normalized_parent,
    ):
        return False
    return (
        value["request"] == expected_request
        and value["dropped_targets"] == expected_dropped_targets
    )


def validate_recovery_evidence(
    value: Any,
    *,
    contract: dict[str, Any],
    source_parent: dict[str, Any],
    normalized_parent: dict[str, Any],
    expected_source_parent: dict[str, Any],
    expected_normalized_parent: dict[str, Any],
    expected_request: dict[str, Any],
    expected_dropped_targets: list[dict[str, Any]],
) -> bool:
    """Validate exact shape, semantic equalities, and non-circular digests."""
    if not _shape_is_valid(value):
        return False
    if value.get("version") != RECOVERY_EVIDENCE_VERSION or value.get("generation") != 2:
        return False
    if not _parent_semantics(value["source_parent"], value["normalized_parent"]):
        return False
    if not _drop_semantics(
        value.get("normalization_kind"), value["dropped_targets"], value["source_parent"]
    ):
        return False
    if not _authority_semantics(value) or not _drop_authority_is_disjoint(value):
        return False
    if not _successor_semantics(value):
        return False
    if not _identity_semantics(value):
        return False
    if not _bound_evidence_semantics(
        value,
        contract=contract,
        source_parent=source_parent,
        normalized_parent=normalized_parent,
        expected_source_parent=expected_source_parent,
        expected_normalized_parent=expected_normalized_parent,
        expected_request=expected_request,
        expected_dropped_targets=expected_dropped_targets,
    ):
        return False
    claimed = value.get("evidence_digest")
    payload = {key: item for key, item in value.items() if key != "evidence_digest"}
    expected = tagged_digest(RECOVERY_EVIDENCE_TAG, payload)
    return isinstance(claimed, str) and expected is not None and claimed == expected


def build_recovery_evidence(
    contract: dict[str, Any],
    request: dict[str, Any],
    *,
    source_parent: dict[str, Any],
    normalized_parent: dict[str, Any],
    expected_source_parent: dict[str, Any],
    expected_normalized_parent: dict[str, Any],
    expected_request: dict[str, Any],
    expected_dropped_targets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build and immediately validate one content-bound successor receipt."""
    recovery = request.get("recovery")
    profile = contract.get("task_profile")
    if not isinstance(recovery, dict) or not isinstance(profile, dict):
        return None
    payload = {
        "version": RECOVERY_EVIDENCE_VERSION,
        "generation": 2,
        "normalization_kind": recovery["normalization_kind"],
        "source_parent": recovery["source_parent"],
        "normalized_parent": recovery["normalized_parent"],
        "request": {
            "prior_scope": recovery["prior_scope"],
            "requested_targets": recovery["requested_targets"],
            "plan_targets": request["plan_targets"],
        },
        "dropped_targets": recovery["dropped_targets"],
        "authority_union": request["cumulative_paths"],
        "successor": {
            "sha256": "",
            "task_id": request["root_task_id"],
            "project": request["project"],
            "amendment_index": request["amendment_index"],
            "contract_id": request["contract_id"],
            "parent_contract_id": request["parent_contract_id"],
            "parent_contract_digest": request["parent_contract_digest"],
            "intent_fingerprint": profile.get("intent_fingerprint"),
            "instruction_fingerprint": profile.get("instruction_fingerprint"),
            "cumulative_paths": request["cumulative_paths"],
        },
    }
    successor_sha = authority_digest(SUCCESSOR_AUTHORITY_TAG, contract)
    if successor_sha is None:
        return None
    payload["successor"]["sha256"] = successor_sha
    payload["evidence_digest"] = evidence_digest(payload)
    valid = validate_recovery_evidence(
        payload,
        contract=contract,
        source_parent=source_parent,
        normalized_parent=normalized_parent,
        expected_source_parent=expected_source_parent,
        expected_normalized_parent=expected_normalized_parent,
        expected_request=expected_request,
        expected_dropped_targets=expected_dropped_targets,
    )
    return payload if valid else None
