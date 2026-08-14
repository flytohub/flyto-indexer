"""Proof-bound recovery of one historical task parent's edit authority.

The public task tool normally amends a fresh parent contract without any
normalization.  This module handles the narrower recovery case: a host can
bind an audited prior implementation scope to the exact parent digest and ask
for one deterministic successor.  Historical authority is retained unless a
current exact resolver, the repository filesystem, and the parent's own
resolution evidence jointly prove that an old non-path label was resolved to
an unrelated symbol.

There are deliberately no job, session, provider, or retry identifiers here.
Those belong to the orchestration host; this producer only proves repository
authority and successor identity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import task_context
from .task_recovery_evidence import (
    LEGACY_MISMATCH_REASON,
    LEGACY_RESOLUTION_TAG,
    NORMALIZED_PARENT_TAG,
    RECOVERY_REQUEST_VERSION,
    authority_digest,
    source_contract_digest,
    strict_canonical,
    tagged_digest,
)

_REQUEST_KEYS = frozenset(
    {
        "version",
        "source_parent_contract_digest",
        "prior_scope",
        "requested_targets",
    }
)


def _fail(code: str) -> dict[str, Any]:
    return {"pass": False, "reason_codes": [code]}


def _bounded_source_paths(value: Any, *, max_count: int) -> list[str] | None:
    """Read historical ledger paths without applying today's suffix grammar."""
    if not isinstance(value, list) or not value or len(value) > max_count:
        return None
    paths: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            return None
        path = raw
        if (
            not path
            or path != path.strip()
            or len(path) > 512
            or task_context._CONTROL_CHAR_RE.search(path)
            or "\\" in path
            or path.startswith(("/", "~", "./"))
            or path.endswith("/")
            or "//" in path
            or (len(path) >= 2 and path[0].isalpha() and path[1] == ":")
            or any(character in path for character in "*?[]")
        ):
            return None
        segments = path.split("/")
        if not segments or any(segment in {"", ".", ".."} for segment in segments):
            return None
        normalized = "/".join(segments)
        if normalized in paths:
            return None
        paths.append(normalized)
    return paths


def _exact_resolution(raw: str, project: str | None) -> dict[str, Any] | None:
    """Resolve under the public exact-identity rule, never fuzzy authority."""
    from . import task_analysis

    resolved = task_analysis._resolve_targets([raw], project=project)
    if len(resolved) != 1:
        return None
    candidate = resolved[0]
    if not candidate.get("symbol_id"):
        return None
    if not task_analysis._is_exact_task_target_match(raw, candidate):
        return None
    return candidate


def _legacy_record(value: Any) -> dict[str, Any] | None:
    """Return the exact bounded legacy resolution record used by its digest."""
    if not isinstance(value, dict):
        return None
    record = {key: value.get(key) for key in ("input", "symbol_id", "name", "type", "path")}
    if not all(isinstance(record[key], str) for key in ("input", "name", "type", "path")):
        return None
    if record["symbol_id"] is not None and not isinstance(record["symbol_id"], str):
        return None
    return record


def _resolution_owner(parent: dict[str, Any], raw: str) -> tuple[Any, Any] | None:
    """Find one root or compound target/resolution owner, never a multiset guess."""
    profile = parent.get("task_profile")
    if not isinstance(profile, dict):
        return None
    if profile.get("compound") or parent.get("sub_tasks"):
        if profile.get("targets") or profile.get("resolved_targets"):
            return None
        owners = _compound_owners(parent.get("sub_tasks"), raw)
        if owners is None:
            return None
    else:
        owners = [(profile.get("targets"), profile.get("resolved_targets"))]
    matches = [owner for owner in owners if _owner_matches(owner, raw)]
    return matches[0] if len(matches) == 1 else None


def _compound_owners(value: Any, raw: str) -> list[tuple[Any, Any]] | None:
    if not isinstance(value, list):
        return None
    owners: list[tuple[Any, Any]] = []
    for subtask in value:
        if not isinstance(subtask, dict):
            return None
        targets = subtask.get("targets")
        if isinstance(targets, list) and raw in targets:
            owners.append((targets, subtask.get("resolved_targets")))
    return owners


def _owner_matches(owner: tuple[Any, Any], raw: str) -> bool:
    targets, resolved = owner
    if not isinstance(targets, list) or not targets:
        return False
    if not all(isinstance(target, str) for target in targets):
        return False
    if len(targets) != len(set(targets)) or targets.count(raw) != 1:
        return False
    if not isinstance(resolved, list) or not all(
        isinstance(item, dict) and isinstance(item.get("input"), str) for item in resolved
    ):
        return False
    inputs = [item["input"] for item in resolved]
    return len(inputs) == len(set(inputs)) and set(inputs) == set(targets)


def _resolution_owners(parent: dict[str, Any]) -> list[tuple[Any, Any]] | None:
    profile = parent.get("task_profile")
    if not isinstance(profile, dict):
        return None
    if profile.get("compound") or parent.get("sub_tasks"):
        if profile.get("targets") or profile.get("resolved_targets"):
            return None
        subtasks = parent.get("sub_tasks")
        if not isinstance(subtasks, list) or not subtasks:
            return None
        owners = [
            (subtask.get("targets"), subtask.get("resolved_targets"))
            for subtask in subtasks
            if isinstance(subtask, dict)
        ]
        if len(owners) != len(subtasks):
            return None
        return owners
    return [(profile.get("targets"), profile.get("resolved_targets"))]


def _bounded_owner_records(owner: tuple[Any, Any]) -> list[dict[str, Any]] | None:
    targets, resolved = owner
    if not isinstance(targets, list) or not targets:
        return None
    if not all(isinstance(target, str) for target in targets):
        return None
    if len(targets) != len(set(targets)) or not isinstance(resolved, list):
        return None
    bounded = [_legacy_record(item) for item in resolved]
    if any(item is None for item in bounded):
        return None
    rows = [item for item in bounded if item is not None]
    inputs = [item["input"] for item in rows]
    if len(inputs) != len(set(inputs)) or set(inputs) != set(targets):
        return None
    by_input = {item["input"]: item for item in rows}
    return [by_input[target] for target in targets]


def _source_resolution_records(parent: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Read one complete root or compound resolution matrix."""
    owners = _resolution_owners(parent)
    if owners is None:
        return None
    records: list[dict[str, Any]] = []
    for owner in owners:
        rows = _bounded_owner_records(owner)
        if rows is None:
            return None
        records.extend(rows)
    return records


def _legacy_coordinate_is_unique(
    parent: dict[str, Any],
    record: dict[str, Any],
) -> bool:
    """Require exclusive symbol and path ownership across the source matrix."""
    records = _source_resolution_records(parent)
    if records is None or not record.get("symbol_id") or not record.get("path"):
        return False
    overlaps = [
        item
        for item in records
        if item.get("symbol_id") == record["symbol_id"]
        or item.get("path") == record["path"]
    ]
    return len(overlaps) == 1 and overlaps[0] == record


def _legacy_drop_proof(
    raw: str,
    parent: dict[str, Any],
    *,
    prior_paths: set[str],
) -> dict[str, Any] | None:
    """Prove one ledger target maps one-to-one to a legacy non-exact hit."""
    from . import task_analysis

    ledger = parent.get("intent_ledger")
    instruction = parent.get("instruction_context")
    ledger_targets = ledger.get("targets") if isinstance(ledger, dict) else None
    instruction_targets = instruction.get("targets") if isinstance(instruction, dict) else None
    if not isinstance(ledger_targets, list) or ledger_targets.count(raw) != 1:
        return None
    if not isinstance(instruction_targets, list) or instruction_targets.count(raw) != 1:
        return None
    owner = _resolution_owner(parent, raw)
    if owner is None:
        return None
    records = [item for item in owner[1] if isinstance(item, dict) and item.get("input") == raw]
    record = _legacy_record(records[0])
    if record is None or not record.get("symbol_id"):
        return None
    if task_analysis._is_exact_task_target_match(raw, record):
        return None
    if record["path"] in prior_paths:
        return None
    if not _legacy_coordinate_is_unique(parent, record):
        return None
    return record


def _literal_exists_or_is_symlink(root: Path, raw: str) -> bool | None:
    """Probe literal authority without following or ignoring broken symlinks."""
    try:
        candidate = root / raw
        return candidate.exists() or candidate.is_symlink()
    except (OSError, ValueError):
        return None


def _derive_normalization(
    source_paths: list[str],
    *,
    parent: dict[str, Any],
    root: Path,
    project: str | None,
    audited_paths: list[str],
    validate_target: Callable[..., tuple[str | None, str | None, str | None]],
) -> tuple[
    list[tuple[str, str]],
    list[dict[str, Any]],
    str | None,
]:
    """Retain current authority and derive only fully proved legacy drops."""
    retained: list[tuple[str, str]] = []
    dropped: list[dict[str, Any]] = []
    audited = set(audited_paths)
    for raw in source_paths:
        normalized, _kind, reason = validate_target(root, raw)
        if reason is None and normalized is not None:
            retained.append((normalized, raw))
            continue
        exact = _exact_resolution(raw, project)
        if exact is not None:
            exact_path, _kind, exact_reason = validate_target(root, exact.get("path"))
            if exact_reason is None and exact_path is not None:
                retained.append((exact_path, str(exact["symbol_id"])))
                continue
        literal = _literal_exists_or_is_symlink(root, raw)
        if literal is not False:
            return [], [], "AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN"
        proof = _legacy_drop_proof(raw, parent, prior_paths=audited)
        if proof is None:
            return [], [], "AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN"
        legacy_digest = tagged_digest(LEGACY_RESOLUTION_TAG, proof)
        if legacy_digest is None:
            return [], [], "AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN"
        dropped.append(
            {
                "target": raw,
                "reason_code": LEGACY_MISMATCH_REASON,
                "legacy_resolution_sha256": legacy_digest,
                "current_exact_resolution": False,
                "existing_literal": False,
            }
        )
    return retained, dropped, None


def _filtered_parent(
    parent: dict[str, Any],
    *,
    dropped: list[dict[str, Any]],
    project: str | None,
    objective: str,
    sanitize_amendments: Callable[[Any], list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Remove proved labels and recompute only instruction and ledger state."""
    encoded = strict_canonical(parent)
    if encoded is None:
        return None
    normalized = json.loads(encoded)
    dropped_names = [item["target"] for item in dropped]
    ledger = normalized["intent_ledger"]
    normalized_targets = [target for target in ledger["targets"] if target not in dropped_names]
    if not normalized_targets:
        return None
    profile = normalized["task_profile"]
    if profile.get("compound") or normalized.get("sub_tasks"):
        for subtask in normalized.get("sub_tasks") or []:
            affected = any(target in dropped_names for target in subtask.get("targets") or [])
            subtask["targets"] = [
                target for target in subtask.get("targets") or [] if target not in dropped_names
            ]
            subtask["resolved_targets"] = [
                item
                for item in subtask.get("resolved_targets") or []
                if item.get("input") not in dropped_names
            ]
            if affected and (not subtask["targets"] or not subtask["resolved_targets"]):
                return None
    else:
        profile["targets"] = [
            target for target in profile.get("targets") or [] if target not in dropped_names
        ]
        profile["resolved_targets"] = [
            item
            for item in profile.get("resolved_targets") or []
            if item.get("input") not in dropped_names
        ]
        if not profile["targets"] or not profile["resolved_targets"]:
            return None
    fresh_instruction = task_context.resolve_instruction_context(project, normalized_targets)
    fresh_ledger = task_context.build_intent_ledger(
        project,
        objective,
        normalized_targets,
        list(ledger.get("execution_plan") or []),
        amendment_requirements=sanitize_amendments(ledger.get("amendment_requirements")),
    )
    normalized["instruction_context"] = fresh_instruction
    normalized["intent_ledger"] = fresh_ledger
    profile["instruction_fingerprint"] = fresh_instruction.get("fingerprint")
    profile["intent_fingerprint"] = fresh_ledger.get("fingerprint")
    return normalized


def _validate_recovery_request(
    parent: dict[str, Any],
    requested_targets: Any,
    recovery_context: Any,
) -> dict[str, Any]:
    if not isinstance(recovery_context, dict) or set(recovery_context) != _REQUEST_KEYS:
        return _fail("AMENDMENT_RECOVERY_CONTEXT_INVALID")
    if recovery_context.get("version") != RECOVERY_REQUEST_VERSION:
        return _fail("AMENDMENT_RECOVERY_CONTEXT_INVALID")
    source_parent_digest = source_contract_digest(parent)
    if source_parent_digest is None:
        return _fail("AMENDMENT_RECOVERY_SOURCE_CONTRACT_OVERSIZED")
    if recovery_context.get("source_parent_contract_digest") != source_parent_digest:
        return _fail("AMENDMENT_RECOVERY_PARENT_MISMATCH")
    if (
        not isinstance(requested_targets, list)
        or recovery_context.get("requested_targets") != requested_targets
    ):
        return _fail("AMENDMENT_RECOVERY_REQUEST_MISMATCH")
    return {"pass": True, "source_parent_contract_digest": source_parent_digest}


def _validate_recovery_scopes(
    *,
    state: dict[str, Any],
    root: Path,
    requested_targets: Any,
    recovery_context: dict[str, Any],
    validate_targets: Callable[..., tuple[list[tuple[str, str]], list[str]]],
    max_cumulative_targets: int,
    max_requested_targets: int,
) -> dict[str, Any]:
    """Bound and resolve source, audited, and explicit request path lists."""
    prior_scope = recovery_context.get("prior_scope")
    if (
        not isinstance(prior_scope, list)
        or not prior_scope
        or len(prior_scope) > max_requested_targets
        or not all(isinstance(item, str) for item in prior_scope)
        or len(prior_scope) != len(set(prior_scope))
    ):
        return _fail("AMENDMENT_RECOVERY_PRIOR_SCOPE_INVALID")
    if (
        not isinstance(requested_targets, list)
        or not requested_targets
        or len(requested_targets) > max_requested_targets
        or not all(isinstance(item, str) for item in requested_targets)
        or len(requested_targets) != len(set(requested_targets))
    ):
        code = (
            "AMENDMENT_TARGETS_OVERSIZED"
            if isinstance(requested_targets, list)
            and len(requested_targets) > max_requested_targets
            else "AMENDMENT_RECOVERY_REQUEST_MISMATCH"
        )
        return _fail(code)
    source_paths = _bounded_source_paths(
        state["ledger"].get("allowed_paths"), max_count=max_cumulative_targets
    )
    if source_paths is None:
        return _fail("AMENDMENT_RECOVERY_SOURCE_SCOPE_INVALID")
    prior_pairs, prior_codes = validate_targets(root, prior_scope)
    if prior_codes or len(prior_pairs) != len(prior_scope):
        return _fail("AMENDMENT_RECOVERY_PRIOR_SCOPE_INVALID")
    requested_pairs, requested_codes = validate_targets(root, requested_targets)
    requested_count = len(requested_targets)
    if (
        requested_codes
        or not requested_pairs
        or requested_count != len(requested_pairs)
        or requested_count > max_requested_targets
    ):
        code = requested_codes[0] if requested_codes else "AMENDMENT_TARGETS_MISSING"
        if requested_count > max_requested_targets:
            code = "AMENDMENT_TARGETS_OVERSIZED"
        elif requested_count != len(requested_pairs):
            code = "AMENDMENT_RECOVERY_REQUEST_MISMATCH"
        return _fail(code)
    plan_pairs = _plan_pairs(prior_pairs, requested_pairs)
    if len(plan_pairs) > max_requested_targets:
        return _fail("AMENDMENT_TARGETS_OVERSIZED")
    return {
        "pass": True,
        "source_paths": source_paths,
        "prior_pairs": prior_pairs,
        "requested_pairs": requested_pairs,
        "plan_pairs": plan_pairs,
    }


def _plan_pairs(
    prior_pairs: list[tuple[str, str]],
    requested_pairs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Deduplicate canonical plan paths in audited-then-explicit order."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pair in prior_pairs + requested_pairs:
        if pair[0] not in seen:
            seen.add(pair[0])
            pairs.append(pair)
    return pairs


def prepare_recovery_parent(
    *,
    parent: dict[str, Any],
    state: dict[str, Any],
    root: Path,
    project: str | None,
    requested_targets: Any,
    recovery_context: Any,
    validate_target: Callable[..., tuple[str | None, str | None, str | None]],
    validate_targets: Callable[..., tuple[list[tuple[str, str]], list[str]]],
    sanitize_amendments: Callable[[Any], list[dict[str, Any]]],
    max_cumulative_targets: int,
    max_requested_targets: int,
) -> dict[str, Any]:
    """Validate a content-bound request and return a normalized parent."""
    request = _validate_recovery_request(parent, requested_targets, recovery_context)
    if not request.get("pass"):
        return request
    scopes = _validate_recovery_scopes(
        state=state,
        root=root,
        requested_targets=requested_targets,
        recovery_context=recovery_context,
        validate_targets=validate_targets,
        max_cumulative_targets=max_cumulative_targets,
        max_requested_targets=max_requested_targets,
    )
    if not scopes.get("pass"):
        return scopes

    audited_paths = [path for path, _raw in scopes["prior_pairs"]]
    retained, dropped, normalize_code = _derive_normalization(
        scopes["source_paths"],
        parent=parent,
        root=root,
        project=project,
        audited_paths=audited_paths,
        validate_target=validate_target,
    )
    if normalize_code is not None:
        return _fail(normalize_code)
    if dropped and (state["amendment_index"] != 0 or parent.get("task_amendment")):
        return _fail("AMENDMENT_RECOVERY_LEGACY_PARENT_REQUIRED")
    normalized_parent: dict[str, Any] | None = parent
    if dropped:
        normalized_parent = _filtered_parent(
            parent,
            dropped=dropped,
            project=project,
            objective=state["objective"],
            sanitize_amendments=sanitize_amendments,
        )
    if normalized_parent is None:
        return _fail("AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN")
    return {
        "pass": True,
        "source_paths": scopes["source_paths"],
        "source_parent_contract_digest": request["source_parent_contract_digest"],
        "normalized_parent": normalized_parent,
        "normalized_paths": [path for path, _raw in retained],
        "prior_pairs": scopes["prior_pairs"],
        "requested_pairs": scopes["requested_pairs"],
        "plan_pairs": scopes["plan_pairs"],
        "dropped_targets": dropped,
    }


def _contract_identity(
    state: dict[str, Any],
    paths: list[str],
    root_contract_id: Callable[[str, str, list[str]], str],
) -> tuple[str, str | None]:
    """Reuse existing root or amendment identity without a new ID formula."""
    if state["amendment_index"] == 0:
        return root_contract_id(state["root_task_id"], state["objective"], paths), None
    return state["declared_contract_id"], state["declared_parent_contract_id"]


def _recovery_metadata(
    *,
    state: dict[str, Any],
    normalized_state: dict[str, Any],
    prepared: dict[str, Any],
    request: dict[str, Any],
    parent: dict[str, Any],
    parent_project: str,
    parent_digest: Callable[[dict[str, Any]], str],
    root_contract_id: Callable[[str, str, list[str]], str],
) -> dict[str, Any] | None:
    """Assemble producer-owned parent snapshots for the pure evidence layer."""
    normalized_sha = authority_digest(NORMALIZED_PARENT_TAG, prepared["normalized_parent"])
    if normalized_sha is None:
        return None
    source_id, source_parent_id = _contract_identity(
        state, prepared["source_paths"], root_contract_id
    )
    normalized_parent_id = (
        None
        if normalized_state["amendment_index"] == 0
        else normalized_state["declared_parent_contract_id"]
    )
    return {
        "_source_contract": parent,
        "_normalized_contract": prepared["normalized_parent"],
        "normalization_kind": (
            "legacy_exact_target_authority.v1" if prepared["dropped_targets"] else "identity.v1"
        ),
        "source_parent": {
            "sha256": prepared["source_parent_contract_digest"],
            "amendment_digest": parent_digest(parent),
            "task_id": state["root_task_id"],
            "project": parent_project,
            "objective": state["objective"],
            "amendment_index": state["amendment_index"],
            "contract_id": source_id,
            "parent_contract_id": source_parent_id,
        },
        "normalized_parent": {
            "sha256": normalized_sha,
            "amendment_digest": request["parent_contract_digest"],
            "task_id": normalized_state["root_task_id"],
            "project": parent_project,
            "objective": normalized_state["objective"],
            "amendment_index": normalized_state["amendment_index"],
            "contract_id": request["parent_contract_id"],
            "parent_contract_id": normalized_parent_id,
            "paths": prepared["normalized_paths"],
        },
        "prior_scope": [path for path, _raw in prepared["prior_pairs"]],
        "requested_targets": [path for path, _raw in prepared["requested_pairs"]],
        "dropped_targets": prepared["dropped_targets"],
    }


def build_recovery_request(
    *,
    parent: dict[str, Any],
    project: str | None,
    description: str,
    targets: Any,
    recovery_context: Any,
    read_parent_state: Callable[..., tuple[dict[str, Any], list[str]]],
    refuse: Callable[..., dict[str, Any]],
    resolve_root: Callable[[str | None], Path | None],
    validate_target: Callable[..., tuple[str | None, str | None, str | None]],
    validate_targets: Callable[..., tuple[list[tuple[str, str]], list[str]]],
    sanitize_amendments: Callable[[Any], list[dict[str, Any]]],
    ordinary_builder: Callable[..., dict[str, Any]],
    parent_digest: Callable[[dict[str, Any]], str],
    root_contract_id: Callable[[str, str, list[str]], str],
    max_cumulative_targets: int,
    max_requested_targets: int,
) -> dict[str, Any]:
    """Adapt a recovery request onto the existing ordinary amendment builder."""
    if not isinstance(parent, dict) or not parent or parent.get("error"):
        return refuse(["AMENDMENT_PARENT_NOT_A_CONTRACT"])
    state, codes = read_parent_state(parent)
    if not state or codes:
        return refuse(codes or ["AMENDMENT_PARENT_NOT_A_CONTRACT"])
    parent_project = state["project"] if isinstance(state["project"], str) else ""
    if parent_project and project and parent_project != project:
        return refuse(["AMENDMENT_PARENT_PROJECT_MISMATCH"])
    effective_project = project or parent_project or None
    root = resolve_root(effective_project)
    if root is None:
        return refuse(["AMENDMENT_PROJECT_UNRESOLVED"])
    prepared = prepare_recovery_parent(
        parent=parent,
        state=state,
        root=root,
        project=effective_project,
        requested_targets=targets,
        recovery_context=recovery_context,
        validate_target=validate_target,
        validate_targets=validate_targets,
        sanitize_amendments=sanitize_amendments,
        max_cumulative_targets=max_cumulative_targets,
        max_requested_targets=max_requested_targets,
    )
    if not prepared.get("pass"):
        return refuse(list(prepared.get("reason_codes") or []))
    plan_targets = [path for path, _raw in prepared["plan_pairs"]]
    request = ordinary_builder(
        prepared["normalized_parent"],
        project=project,
        description=description,
        targets=plan_targets,
    )
    if not request.get("pass"):
        return request
    normalized_state, normalized_codes = read_parent_state(prepared["normalized_parent"])
    metadata = _recovery_metadata(
        state=state,
        normalized_state=normalized_state,
        prepared=prepared,
        request=request,
        parent=parent,
        parent_project=parent_project,
        parent_digest=parent_digest,
        root_contract_id=root_contract_id,
    )
    if normalized_codes or metadata is None:
        return refuse(["AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN"])
    request["plan_targets"] = plan_targets
    request["recovery"] = metadata
    return request


def build_successor_recovery_evidence(
    contract: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any] | None:
    """Inject producer-owned parent snapshots into the pure evidence builder."""
    recovery = request.get("recovery")
    if not isinstance(recovery, dict):
        return None
    source_contract = recovery.get("_source_contract")
    normalized_contract = recovery.get("_normalized_contract")
    source_snapshot = recovery.get("source_parent")
    normalized_snapshot = recovery.get("normalized_parent")
    dropped_targets = recovery.get("dropped_targets")
    if not isinstance(source_contract, dict):
        return None
    if not isinstance(normalized_contract, dict):
        return None
    if not isinstance(source_snapshot, dict):
        return None
    if not isinstance(normalized_snapshot, dict):
        return None
    if not isinstance(dropped_targets, list) or not all(
        isinstance(item, dict) for item in dropped_targets
    ):
        return None
    from .task_recovery_evidence import build_recovery_evidence

    return build_recovery_evidence(
        contract,
        request,
        source_parent=source_contract,
        normalized_parent=normalized_contract,
        expected_source_parent=source_snapshot,
        expected_normalized_parent=normalized_snapshot,
        expected_request={
            "prior_scope": recovery.get("prior_scope"),
            "requested_targets": recovery.get("requested_targets"),
            "plan_targets": request.get("plan_targets"),
        },
        expected_dropped_targets=dropped_targets,
    )
