"""Cumulative plan amendments on one immutable root task identity.

``task(action='plan')`` normally builds a fresh contract from a description and
target list.  When the caller passes the contract it just received back as
``task_contract``, the same action instead builds *one cumulative successor*:
the original objective and root task id are preserved, the declared scope
becomes the deterministic bounded union of the original and amendment targets,
and every derived artifact (intent ledger, instruction context, fingerprints)
is recomputed from scratch.

Three properties are load bearing.

* **Fail closed.**  A parent is only amendable when its own recorded state is
  internally consistent (its stored canonical payload hashes to its stored
  fingerprint), still recomputable from the repository, and cryptographically
  linked to every predecessor.  Anything else is refused.
* **No new authority.**  An amendment may only declare bounded, resolvable,
  repository-relative coordinates.  Scope is always recomputed from the
  cumulative target union, never appended to, and can never widen to the whole
  repository or shrink below what the parent already declared.
* **Provider neutral.**  Nothing here knows about a specific vendor, agent,
  language, documentation layout, or generator.

Every refusal is a stable ``reason_codes`` / ``required_actions`` pair so
callers remediate mechanically instead of parsing prose.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import task_context
from .grill_evidence import resolve_project_root

# Stable aliases for the shared context primitives. Binding them once keeps
# this module readable while leaving ``task_context`` the single owner of the
# path grammar and of payload canonicalization. ``task_context`` only imports
# this module lazily inside functions, so there is no import cycle.
_canonical_json = task_context._canonical_json
_fingerprint = task_context._fingerprint
_normalize_allowed_paths = task_context._normalize_allowed_paths
_symbol_path = task_context._symbol_path
_CONTROL_CHAR_RE = task_context._CONTROL_CHAR_RE
_PATHLIKE_BASENAMES = task_context._PATHLIKE_BASENAMES
_PATHLIKE_SUFFIXES = task_context._PATHLIKE_SUFFIXES
MAX_SYMBOL_PATH_SEGMENTS = task_context.MAX_SYMBOL_PATH_SEGMENTS

AMENDMENT_VERSION = "task-amendment.v1"

# Only versions this module knows how to read may be amended.  An unknown
# version is a hard refusal, never a best-effort parse.
SUPPORTED_PARENT_CONTRACT_VERSIONS = frozenset({"task-contract.v2"})
SUPPORTED_AMENDMENT_VERSIONS = frozenset({AMENDMENT_VERSION})
SUPPORTED_LEDGER_VERSIONS = frozenset(
    {"intent-ledger.v1", "task-context.v1"}
)
SUPPORTED_INSTRUCTION_VERSIONS = frozenset({"task-context.v1"})

# Domain separation tags keep the three digest families from ever colliding.
_PARENT_DIGEST_TAG = "task-amendment.parent.v1"
_ENTRY_DIGEST_TAG = "task-amendment.entry.v1"
_CONTRACT_DIGEST_TAG = "task-amendment.contract.v1"
_ROOT_DIGEST_TAG = "task-amendment.root.v1"

MAX_AMENDMENT_CHAIN = 8
MAX_CUMULATIVE_TARGETS = 64
MAX_AMENDMENT_TARGETS = 32
MAX_AMENDMENT_REQUIREMENTS = 72
MAX_TARGET_LENGTH = 512
MAX_OBJECTIVE_LENGTH = 4096
MAX_AMENDMENT_TEXT = 360
MAX_IDENTIFIER_LENGTH = 160

AMENDMENT_REQUIREMENT_KINDS = frozenset(
    {"amendment", "amendment_scope", "amendment_target"}
)

# Every fnmatch metacharacter. Declared targets are literal coordinates.
_GLOB_METACHARACTERS = frozenset("*?[]")

_LEDGER_PAYLOAD_KEYS = (
    "description",
    "targets",
    "execution_plan",
    "sources",
    "requirements",
    "allowed_paths",
)
_INSTRUCTION_PAYLOAD_KEYS = ("targets", "files", "clauses")

_BLOCKED_ERROR = (
    "Amendment refused: the supplied parent contract did not satisfy the "
    "cumulative amendment contract."
)

_REMEDIATION: dict[str, str] = {
    "AMENDMENT_PARENT_NOT_A_CONTRACT": "supply_immediately_preceding_task_contract",
    "AMENDMENT_PARENT_VERSION_UNSUPPORTED": "rerun_task_plan_without_parent",
    "AMENDMENT_PARENT_PROJECT_MISMATCH": "amend_within_the_same_project",
    "AMENDMENT_PARENT_MISSING_ROOT_IDENTITY": "rerun_task_plan_without_parent",
    "AMENDMENT_PARENT_OBJECTIVE_MISSING": "rerun_task_plan_without_parent",
    "AMENDMENT_PARENT_OBJECTIVE_OVERSIZED": "rerun_task_plan_without_parent",
    "AMENDMENT_PARENT_OBJECTIVE_MISMATCH": "refresh_parent_task_contract",
    "AMENDMENT_PARENT_LEDGER_MISSING": "rerun_task_plan_without_parent",
    "AMENDMENT_PARENT_LEDGER_TAMPERED": "rerun_task_plan_without_parent",
    "AMENDMENT_PARENT_LEDGER_STALE": "refresh_parent_task_contract",
    "AMENDMENT_PARENT_INSTRUCTION_MISSING": "rerun_task_plan_without_parent",
    "AMENDMENT_PARENT_INSTRUCTION_TAMPERED": "rerun_task_plan_without_parent",
    "AMENDMENT_PARENT_INSTRUCTION_STALE": "refresh_parent_task_contract",
    "AMENDMENT_PARENT_SCOPE_TAMPERED": "rerun_task_plan_without_parent",
    "AMENDMENT_PROJECT_UNRESOLVED": "supply_an_indexed_project",
    "AMENDMENT_TARGETS_MISSING": "declare_bounded_relative_amendment_targets",
    "AMENDMENT_TARGETS_OVERSIZED": "reduce_amendment_target_count",
    "AMENDMENT_TARGET_NOT_RELATIVE": "declare_bounded_relative_amendment_targets",
    "AMENDMENT_TARGET_SYMLINK": "declare_bounded_relative_amendment_targets",
    "AMENDMENT_TARGET_UNBOUNDED": "declare_bounded_relative_amendment_targets",
    "AMENDMENT_TARGET_UNRESOLVED": "declare_resolvable_amendment_targets",
    "AMENDMENT_RECOVERY_CONTEXT_INVALID": "supply_bound_recovery_context",
    "AMENDMENT_RECOVERY_PARENT_MISMATCH": "refresh_recovery_parent_digest",
    "AMENDMENT_RECOVERY_REQUEST_MISMATCH": "bind_recovery_to_requested_targets",
    "AMENDMENT_RECOVERY_SOURCE_SCOPE_INVALID": "refresh_parent_task_contract",
    "AMENDMENT_RECOVERY_SOURCE_CONTRACT_OVERSIZED": "reduce_parent_contract_size",
    "AMENDMENT_RECOVERY_PRIOR_SCOPE_INVALID": "declare_resolvable_prior_scope",
    "AMENDMENT_RECOVERY_NORMALIZATION_UNPROVEN": "refresh_parent_task_contract",
    "AMENDMENT_RECOVERY_LEGACY_PARENT_REQUIRED": "recover_from_root_parent_contract",
    "AMENDMENT_RECOVERY_SUCCESSOR_NONCANONICAL": "refresh_parent_task_contract",
    "AMENDMENT_CHAIN_MALFORMED": "rerun_task_plan_without_parent",
    "AMENDMENT_CHAIN_CYCLIC": "rerun_task_plan_without_parent",
    "AMENDMENT_CHAIN_OVERSIZED": "close_this_task_before_amending_again",
    "AMENDMENT_CHAIN_ROOT_MISMATCH": "rerun_task_plan_without_parent",
    "AMENDMENT_CHAIN_PROJECT_MISMATCH": "rerun_task_plan_without_parent",
    "AMENDMENT_CHAIN_TAMPERED": "rerun_task_plan_without_parent",
    "AMENDMENT_CHAIN_LINKAGE_INVALID": "rerun_task_plan_without_parent",
    "AMENDMENT_CHAIN_INDEX_INVALID": "rerun_task_plan_without_parent",
    "AMENDMENT_CHAIN_COUNT_INVALID": "rerun_task_plan_without_parent",
}


# ---------------------------------------------------------------------------
# digests and small helpers
# ---------------------------------------------------------------------------

def _digest(*parts: Any) -> str:
    return hashlib.sha256(_canonical_json(list(parts)).encode("utf-8")).hexdigest()


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _index(value: Any) -> int:
    return value if isinstance(value, bool) is False and isinstance(value, int) else -1


def parent_contract_digest(parent: dict[str, Any]) -> str:
    """Digest the bounded identity-bearing fields of one concrete contract.

    Only fixed-size, already-canonical fields take part, so the digest stays
    bounded regardless of contract size while still changing if the parent's
    identity, project, objective state, or derived fingerprints change.
    """
    profile = parent.get("task_profile") or {}
    ledger = parent.get("intent_ledger") or {}
    instruction = parent.get("instruction_context") or {}
    amendment = parent.get("task_amendment") or {}
    if not isinstance(profile, dict):
        profile = {}
    if not isinstance(ledger, dict):
        ledger = {}
    if not isinstance(instruction, dict):
        instruction = {}
    if not isinstance(amendment, dict):
        amendment = {}
    return _digest(
        _PARENT_DIGEST_TAG,
        _text(profile.get("version"), 64),
        _text(profile.get("task_id"), MAX_IDENTIFIER_LENGTH),
        _text(profile.get("intent"), 32) or _text(profile.get("original_intent"), 32),
        _text(profile.get("project"), MAX_TARGET_LENGTH),
        _text(ledger.get("version"), 64),
        _text(ledger.get("fingerprint"), 64),
        _text(instruction.get("version"), 64),
        _text(instruction.get("fingerprint"), 64),
        _text(amendment.get("version"), 64),
        _text(amendment.get("contract_id"), 64),
        _index(amendment.get("amendment_index")),
    )


def _root_contract_id(root_task_id: str, objective: str, paths: list[str]) -> str:
    return "amd_root_" + _digest(_ROOT_DIGEST_TAG, root_task_id, objective, paths)[:20]


def _contract_id(
    root_task_id: str,
    amendment_index: int,
    objective: str,
    cumulative_paths: list[str],
    parent_contract_id: str,
    parent_digest: str,
) -> str:
    """Content-address a successor to the exact parent contract it amends.

    ``parent_digest`` is what makes the chain transitively verifiable: a
    successor id can only be reproduced by someone holding the same parent
    content, so rewriting any ancestor invalidates every id after it.
    """
    return "amd_" + _digest(
        _CONTRACT_DIGEST_TAG,
        root_task_id,
        amendment_index,
        objective,
        cumulative_paths,
        parent_contract_id,
        parent_digest,
    )[:24]


def _entry_digest(entry: dict[str, Any]) -> str:
    return _digest(
        _ENTRY_DIGEST_TAG,
        entry.get("contract_id"),
        entry.get("parent_contract_id") or "",
        entry.get("root_task_id"),
        entry.get("project") or "",
        entry.get("amendment_index"),
        entry.get("target_count"),
        entry.get("contract_digest"),
    )


def _chain_entry(
    *,
    contract_id: str,
    parent_contract_id: str | None,
    root_task_id: str,
    project: str,
    amendment_index: int,
    target_count: int,
    contract_digest: str,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "contract_id": contract_id,
        "parent_contract_id": parent_contract_id,
        "root_task_id": root_task_id,
        "project": project,
        "amendment_index": amendment_index,
        "target_count": target_count,
        "contract_digest": contract_digest,
    }
    entry["entry_digest"] = _entry_digest(entry)
    return entry


def _stored_fingerprint(section: dict[str, Any], keys: tuple[str, ...]) -> str:
    """Rehash the payload a context section claims to have been built from."""
    payload = {key: section.get(key) for key in keys}
    if "amendment_requirements" in section:
        payload["amendment_requirements"] = section.get("amendment_requirements")
    return _fingerprint(payload)


# ---------------------------------------------------------------------------
# target authority
# ---------------------------------------------------------------------------

def _typed_basename(candidate: str) -> bool:
    """Accept only file kinds the indexer already understands as file kinds.

    This is the closed set the intent ledger uses when reading specifications.
    Reusing it is what stops a dotted capability or check identifier such as
    ``check.some_capability`` from being mistaken for a not-yet-created file
    and thereby becoming edit authority.
    """
    name = Path(candidate).name
    return (
        name.casefold() in _PATHLIKE_BASENAMES
        or Path(name).suffix.casefold() in _PATHLIKE_SUFFIXES
    )


def _validate_target(
    root: Path | None,
    raw: Any,
    *,
    require_resolution: bool = True,
) -> tuple[str | None, str | None, str | None]:
    """Resolve one declared target to a typed coordinate.

    Returns ``(normalized_relative_path, coordinate_kind, reason_code)``.
    Accepted coordinates are exactly: an existing file, an existing directory,
    a bounded canonical ``project:path:kind:name`` symbol whose file exists,
    and an explicitly typed new file under an existing non-symlink parent.
    Anything unresolvable is refused rather than inferred.
    """
    if not isinstance(raw, str):
        return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"
    value = raw.strip()
    if not value or len(value) > MAX_TARGET_LENGTH:
        return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"
    if _CONTROL_CHAR_RE.search(value):
        return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"

    symbol_path = _symbol_path(value)
    candidate = symbol_path if symbol_path is not None else value
    while candidate.startswith("./"):
        candidate = candidate[2:]
    candidate = candidate.rstrip("/")
    if candidate in {"", ".", "*", "**"}:
        return None, None, "AMENDMENT_TARGET_UNBOUNDED"
    # Declared scope is matched with fnmatch, so a single metacharacter
    # anywhere would silently widen authority to every sibling it matches.
    if any(character in _GLOB_METACHARACTERS for character in candidate):
        return None, None, "AMENDMENT_TARGET_UNBOUNDED"
    if "\\" in candidate or candidate.startswith("~"):
        return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"
    if candidate.startswith("/") or Path(candidate).is_absolute():
        return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"
    segments = candidate.split("/")
    if len(segments) > MAX_SYMBOL_PATH_SEGMENTS:
        return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"
    if any(segment in {"", ".", ".."} for segment in segments):
        return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"
    # A bare token that is not a symbol coordinate must still name a file kind
    # the indexer recognizes; otherwise it is an identifier, not a path.
    if symbol_path is None and ":" in candidate:
        return None, None, "AMENDMENT_TARGET_UNRESOLVED"
    normalized = "/".join(segments)

    if root is None or not require_resolution:
        if symbol_path is None and not _typed_basename(normalized):
            return None, None, "AMENDMENT_TARGET_UNRESOLVED"
        return normalized, "symbol" if symbol_path is not None else "path", None

    current = root
    for segment in segments:
        current = current / segment
        try:
            if current.is_symlink():
                return None, None, "AMENDMENT_TARGET_SYMLINK"
        except OSError:
            return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"
    absolute = root / normalized
    try:
        absolute.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"
    try:
        if absolute.is_dir():
            return normalized, "directory", None
        if absolute.is_file():
            return normalized, "symbol" if symbol_path is not None else "file", None
        # Unresolved. A symbol coordinate must live in a real file, and a new
        # path is only credible when its parent exists and is a typed file kind.
        if symbol_path is not None:
            return None, None, "AMENDMENT_TARGET_UNRESOLVED"
        parent = absolute.parent
        if not parent.is_dir() or parent.is_symlink():
            return None, None, "AMENDMENT_TARGET_UNRESOLVED"
        if not _typed_basename(normalized):
            return None, None, "AMENDMENT_TARGET_UNRESOLVED"
    except OSError:
        return None, None, "AMENDMENT_TARGET_NOT_RELATIVE"
    return normalized, "new_path", None


def _validated_targets(
    root: Path | None,
    raw_targets: Any,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Validate a target list, preserving order and collapsing repeats."""
    if not isinstance(raw_targets, (list, tuple)):
        return [], ["AMENDMENT_TARGETS_MISSING"]
    accepted: list[tuple[str, str]] = []
    seen: set[str] = set()
    reason_codes: list[str] = []
    for raw in raw_targets:
        normalized, _kind, reason = _validate_target(root, raw)
        if reason is not None or normalized is None:
            reason_codes.append(reason or "AMENDMENT_TARGET_UNRESOLVED")
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        accepted.append((normalized, raw.strip()))
    return accepted, list(dict.fromkeys(reason_codes))


# ---------------------------------------------------------------------------
# typed amendment requirements
# ---------------------------------------------------------------------------

def _amendment_requirement(
    root_task_id: str,
    amendment_index: int,
    kind: str,
    text: str,
    expected_paths: list[str],
) -> dict[str, Any]:
    identity = _digest(root_task_id, amendment_index, kind, expected_paths)
    return {
        "id": f"AMD-{identity[:8].upper()}",
        "kind": kind,
        "text": text[:MAX_AMENDMENT_TEXT],
        "source": "task.amendment",
        "line": amendment_index,
        "expected_paths": list(expected_paths),
        "expected_symbols": [],
        "proof_commands": [],
    }


def _build_amendment_requirements(
    root_task_id: str,
    amendment_index: int,
    summary: str,
    prior_paths: list[str],
    added_paths: list[str],
) -> list[dict[str, Any]]:
    """Require current-diff coverage only for this amendment's new paths.

    ``prior_paths`` remains an explicit input so callers cannot accidentally
    confuse cumulative authority with newly added targets.  Prior paths stay
    in the cumulative allowed scope and are protected by amendment-state
    validation, but they are not work requirements for the current diff.
    """
    del prior_paths
    requirements: list[dict[str, Any]] = []
    # Authority and coverage deliberately have separate representations:
    # cumulative_paths / allowed_paths retain every authorized predecessor,
    # while these requirements are consumed as current-diff obligations.
    # Reintroducing prior paths here would make each otherwise independent
    # amendment re-edit all earlier targets. Scope shrinkage remains guarded
    # by validate_amendment_state, and extra paths remain unplanned diffs.

    if added_paths:
        requirements.append(
            _amendment_requirement(
                root_task_id,
                amendment_index,
                "amendment",
                (
                    f"Amendment {amendment_index} extends the original objective "
                    f"with {len(added_paths)} additional declared target(s) and "
                    f"must not drop prior scope: {summary}"
                ),
                list(added_paths),
            )
        )
    for path in added_paths:
        requirements.append(
            _amendment_requirement(
                root_task_id,
                amendment_index,
                "amendment_target",
                f"Cumulative scope must change the amendment declared target: {path}",
                [path],
            )
        )
    return requirements[:MAX_AMENDMENT_REQUIREMENTS]


def sanitize_amendment_requirements(value: Any) -> list[dict[str, Any]]:
    """Return a bounded canonical requirement list that is safe to fingerprint.

    ``validate`` feeds the stored list back through the ledger builder, so the
    shape is normalized rather than trusted.  These entries assert coverage
    only; they never widen the authority computed from the target union.
    """
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        identifier = item.get("id")
        if kind not in AMENDMENT_REQUIREMENT_KINDS:
            continue
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        identifier = identifier.strip()[:64]
        if identifier in seen:
            continue
        paths: list[str] = []
        for path in item.get("expected_paths") or []:
            normalized, _kind, reason = _validate_target(
                None, path, require_resolution=False
            )
            if reason is not None or normalized is None:
                continue
            if normalized not in paths:
                paths.append(normalized)
        if not paths:
            continue
        seen.add(identifier)
        cleaned.append(
            {
                "id": identifier,
                "kind": kind,
                "text": _text(item.get("text"), MAX_AMENDMENT_TEXT),
                "source": "task.amendment",
                "line": max(_index(item.get("line")), 0),
                "expected_paths": paths[:MAX_CUMULATIVE_TARGETS],
                "expected_symbols": [],
                "proof_commands": [],
            }
        )
        if len(cleaned) >= MAX_AMENDMENT_REQUIREMENTS:
            break
    return cleaned


# ---------------------------------------------------------------------------
# chain verification
# ---------------------------------------------------------------------------

def verify_chain(
    raw_chain: Any,
    *,
    root_task_id: str,
    project: str,
    own_contract_id: Any = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Verify a declared ancestry chain, returning it or the code that blocks it.

    Checked, in order, for every entry: structure, root identity, project,
    recomputed entry digest, monotonic position-equals-index, parent linkage to
    the preceding entry, and non-decreasing bounded target counts.
    """
    if raw_chain is None:
        raw_chain = []
    if not isinstance(raw_chain, (list, tuple)):
        return [], "AMENDMENT_CHAIN_MALFORMED"
    if len(raw_chain) >= MAX_AMENDMENT_CHAIN:
        return [], "AMENDMENT_CHAIN_OVERSIZED"
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_count = 0
    for position, item in enumerate(raw_chain):
        if not isinstance(item, dict):
            return [], "AMENDMENT_CHAIN_MALFORMED"
        contract_id = item.get("contract_id")
        contract_digest = item.get("contract_digest")
        if not isinstance(contract_id, str) or not contract_id.strip():
            return [], "AMENDMENT_CHAIN_MALFORMED"
        if not isinstance(contract_digest, str) or len(contract_digest) != 64:
            return [], "AMENDMENT_CHAIN_MALFORMED"
        amendment_index = _index(item.get("amendment_index"))
        target_count = _index(item.get("target_count"))
        if amendment_index < 0 or target_count < 0:
            return [], "AMENDMENT_CHAIN_MALFORMED"
        if item.get("root_task_id") != root_task_id:
            return [], "AMENDMENT_CHAIN_ROOT_MISMATCH"
        if _text(item.get("project"), MAX_TARGET_LENGTH) != project:
            return [], "AMENDMENT_CHAIN_PROJECT_MISMATCH"
        parent_contract_id = item.get("parent_contract_id")
        if parent_contract_id is not None and not isinstance(parent_contract_id, str):
            return [], "AMENDMENT_CHAIN_MALFORMED"
        entry = {
            "contract_id": contract_id,
            "parent_contract_id": parent_contract_id,
            "root_task_id": root_task_id,
            "project": project,
            "amendment_index": amendment_index,
            "target_count": target_count,
            "contract_digest": contract_digest,
        }
        if item.get("entry_digest") != _entry_digest(entry):
            return [], "AMENDMENT_CHAIN_TAMPERED"
        if contract_id in seen:
            return [], "AMENDMENT_CHAIN_CYCLIC"
        if amendment_index != position:
            return [], "AMENDMENT_CHAIN_INDEX_INVALID"
        expected_parent = chain[-1]["contract_id"] if chain else None
        if parent_contract_id != expected_parent:
            return [], "AMENDMENT_CHAIN_LINKAGE_INVALID"
        if target_count < previous_count or target_count > MAX_CUMULATIVE_TARGETS:
            return [], "AMENDMENT_CHAIN_COUNT_INVALID"
        previous_count = target_count
        seen.add(contract_id)
        entry["entry_digest"] = item["entry_digest"]
        chain.append(entry)
    if isinstance(own_contract_id, str) and own_contract_id in seen:
        return [], "AMENDMENT_CHAIN_CYCLIC"
    return chain, None


# ---------------------------------------------------------------------------
# parent inspection
# ---------------------------------------------------------------------------

def is_amendment_request(task_contract: Any) -> bool:
    """A non-empty contract dict on ``plan`` means "amend this task"."""
    return isinstance(task_contract, dict) and bool(task_contract)


def _fresh_context(
    project: str | None,
    description: str,
    targets: list[str],
    execution_plan: list[dict[str, Any]],
    amendment_requirements: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    instruction = task_context.resolve_instruction_context(project, targets)
    ledger = task_context.build_intent_ledger(
        project,
        description,
        targets,
        execution_plan,
        amendment_requirements=amendment_requirements,
    )
    return instruction, ledger


def _read_parent_state(parent: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Extract amendment-relevant parent state, or the codes that block it."""
    codes: list[str] = []
    profile = parent.get("task_profile")
    if not isinstance(profile, dict):
        return {}, ["AMENDMENT_PARENT_NOT_A_CONTRACT"]
    if profile.get("version") not in SUPPORTED_PARENT_CONTRACT_VERSIONS:
        codes.append("AMENDMENT_PARENT_VERSION_UNSUPPORTED")
    root_task_id = _text(profile.get("task_id"), MAX_IDENTIFIER_LENGTH).strip()
    if not root_task_id:
        codes.append("AMENDMENT_PARENT_MISSING_ROOT_IDENTITY")

    ledger_raw = parent.get("intent_ledger")
    instruction_raw = parent.get("instruction_context")
    ledger: dict[str, Any] = ledger_raw if isinstance(ledger_raw, dict) else {}
    instruction: dict[str, Any] = (
        instruction_raw if isinstance(instruction_raw, dict) else {}
    )
    if not ledger.get("fingerprint"):
        codes.append("AMENDMENT_PARENT_LEDGER_MISSING")
    elif ledger.get("version") not in SUPPORTED_LEDGER_VERSIONS:
        codes.append("AMENDMENT_PARENT_VERSION_UNSUPPORTED")
    elif _stored_fingerprint(ledger, _LEDGER_PAYLOAD_KEYS) != ledger.get("fingerprint"):
        # The recorded payload does not hash to the recorded fingerprint, so
        # one of the two was edited after the fact.
        codes.append("AMENDMENT_PARENT_LEDGER_TAMPERED")
    if not instruction.get("fingerprint"):
        codes.append("AMENDMENT_PARENT_INSTRUCTION_MISSING")
    elif instruction.get("version") not in SUPPORTED_INSTRUCTION_VERSIONS:
        codes.append("AMENDMENT_PARENT_VERSION_UNSUPPORTED")
    elif _stored_fingerprint(
        instruction, _INSTRUCTION_PAYLOAD_KEYS
    ) != instruction.get("fingerprint"):
        codes.append("AMENDMENT_PARENT_INSTRUCTION_TAMPERED")

    prior_raw = parent.get("task_amendment")
    amendment_index = 0
    prior: dict[str, Any] = {}
    prior_amendments: list[dict[str, Any]] = []
    if prior_raw is not None:
        if not isinstance(prior_raw, dict):
            return {}, ["AMENDMENT_PARENT_NOT_A_CONTRACT"]
        prior = prior_raw
        if prior.get("version") not in SUPPORTED_AMENDMENT_VERSIONS:
            codes.append("AMENDMENT_PARENT_VERSION_UNSUPPORTED")
        if prior.get("root_task_id") != root_task_id:
            codes.append("AMENDMENT_CHAIN_ROOT_MISMATCH")
        index = _index(prior.get("amendment_index"))
        if index < 1:
            codes.append("AMENDMENT_CHAIN_INDEX_INVALID")
        else:
            amendment_index = index
        raw_prior = prior.get("amendments")
        if isinstance(raw_prior, (list, tuple)):
            prior_amendments = [
                {
                    "index": max(_index(item.get("index")), 0),
                    "summary": _text(item.get("summary"), MAX_AMENDMENT_TEXT),
                    "targets": [
                        target
                        for target in (item.get("targets") or [])
                        if isinstance(target, str)
                    ][:MAX_AMENDMENT_TARGETS],
                }
                for item in raw_prior
                if isinstance(item, dict)
            ][:MAX_AMENDMENT_CHAIN]

    # The objective is carried verbatim, never truncated: the parent's ledger
    # was fingerprinted over the exact string, so shortening it here would make
    # a long objective permanently unrecomputable and report a false staleness.
    raw_objective = ledger.get("description")
    objective = raw_objective if isinstance(raw_objective, str) else ""
    if len(objective) > MAX_OBJECTIVE_LENGTH:
        codes.append("AMENDMENT_PARENT_OBJECTIVE_OVERSIZED")
        objective = ""
    elif not objective:
        codes.append("AMENDMENT_PARENT_OBJECTIVE_MISSING")
    profile_description = profile.get("description")
    if (
        objective
        and isinstance(profile_description, str)
        and profile_description != objective
    ):
        codes.append("AMENDMENT_PARENT_OBJECTIVE_MISMATCH")

    state: dict[str, Any] = {
        "root_task_id": root_task_id,
        "objective": objective,
        "intent": _text(profile.get("intent"), 32)
        or _text(profile.get("original_intent"), 32),
        "project": profile.get("project"),
        "amendment_index": amendment_index,
        "prior_targets": ledger.get("targets"),
        "declared_cumulative_paths": prior.get("cumulative_paths"),
        "declared_contract_id": _text(prior.get("contract_id"), 64),
        "declared_parent_contract_id": prior.get("parent_contract_id"),
        "chain": prior.get("chain"),
        "prior_amendments": prior_amendments,
        "ledger": ledger,
        "instruction": instruction,
    }
    return state, list(dict.fromkeys(codes))


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------

def blocked_result(request: dict[str, Any]) -> dict[str, Any]:
    """Render one refusal with bounded, stable machine-readable remediation."""
    return {
        "pass": False,
        "decision": "blocked",
        "error": _BLOCKED_ERROR,
        "reason_codes": list(request.get("reason_codes") or []),
        "required_actions": list(request.get("required_actions") or []),
        "task_amendment": {
            "version": AMENDMENT_VERSION,
            "status": "blocked",
            "root_task_id": request.get("root_task_id") or None,
            "amendment_index": request.get("amendment_index") or None,
        },
    }


def _refuse(
    codes: list[str],
    *,
    root_task_id: str = "",
    amendment_index: int = 0,
) -> dict[str, Any]:
    ordered = list(dict.fromkeys(codes))
    return {
        "pass": False,
        "reason_codes": ordered,
        "required_actions": list(
            dict.fromkeys(
                _REMEDIATION.get(code, "rerun_task_plan_without_parent")
                for code in ordered
            )
        ),
        "root_task_id": root_task_id,
        "amendment_index": amendment_index,
    }


def _build_amendment_request(
    parent: dict[str, Any],
    *,
    project: str | None,
    description: str,
    targets: Any,
) -> dict[str, Any]:
    """Validate the parent fail-closed and derive the cumulative amendment.

    On success the result carries everything ``plan`` needs to build one fresh
    contract: the immutable root identity and objective, the deterministic
    bounded union of targets, the verified ancestry chain, and the typed
    amendment requirements to fold into a freshly computed intent ledger.
    """
    if not isinstance(parent, dict) or not parent or parent.get("error"):
        return _refuse(["AMENDMENT_PARENT_NOT_A_CONTRACT"])

    state, codes = _read_parent_state(parent)
    if not state:
        return _refuse(codes or ["AMENDMENT_PARENT_NOT_A_CONTRACT"])

    root_task_id: str = state["root_task_id"]
    objective: str = state["objective"]
    parent_index: int = state["amendment_index"]
    amendment_index = parent_index + 1

    parent_project = state["project"]
    parent_project_text = _text(parent_project, MAX_TARGET_LENGTH)
    if (
        parent_project_text
        and isinstance(project, str)
        and project
        and parent_project_text != project
    ):
        codes.append("AMENDMENT_PARENT_PROJECT_MISMATCH")
    effective_project: str | None = project or (parent_project_text or None)
    root = resolve_project_root(effective_project)
    if root is None:
        codes.append("AMENDMENT_PROJECT_UNRESOLVED")

    chain, chain_code = verify_chain(
        state["chain"],
        root_task_id=root_task_id,
        project=parent_project_text,
        own_contract_id=state["declared_contract_id"] or None,
    )
    if chain_code is not None:
        codes.append(chain_code)

    prior_pairs, prior_codes = _validated_targets(root, state["prior_targets"])
    codes.extend(prior_codes)
    if not prior_pairs:
        codes.append("AMENDMENT_PARENT_SCOPE_TAMPERED")

    new_pairs, new_codes = _validated_targets(root, targets)
    codes.extend(new_codes)
    if not new_pairs and not new_codes:
        # Only a genuinely empty declaration is "missing". When every supplied
        # target was rejected individually, its specific code and remediation
        # is the actionable answer and must not be diluted.
        codes.append("AMENDMENT_TARGETS_MISSING")
    if len(new_pairs) > MAX_AMENDMENT_TARGETS:
        codes.append("AMENDMENT_TARGETS_OVERSIZED")

    if codes:
        return _refuse(
            codes, root_task_id=root_task_id, amendment_index=amendment_index
        )

    prior_paths = [path for path, _raw in prior_pairs]

    # The parent's own identity must reproduce from its declared ancestry.
    if parent_index >= 1:
        if len(chain) != parent_index:
            return _refuse(
                ["AMENDMENT_CHAIN_INDEX_INVALID"],
                root_task_id=root_task_id,
                amendment_index=amendment_index,
            )
        declared_paths = state["declared_cumulative_paths"]
        if not isinstance(declared_paths, (list, tuple)) or list(
            declared_paths
        ) != prior_paths:
            return _refuse(
                ["AMENDMENT_PARENT_SCOPE_TAMPERED"],
                root_task_id=root_task_id,
                amendment_index=amendment_index,
            )
        predecessor = chain[-1]
        if state["declared_parent_contract_id"] != predecessor["contract_id"]:
            return _refuse(
                ["AMENDMENT_CHAIN_LINKAGE_INVALID"],
                root_task_id=root_task_id,
                amendment_index=amendment_index,
            )
        expected_id = _contract_id(
            root_task_id,
            parent_index,
            objective,
            prior_paths,
            predecessor["contract_id"],
            predecessor["contract_digest"],
        )
        if expected_id != state["declared_contract_id"]:
            return _refuse(
                ["AMENDMENT_CHAIN_TAMPERED"],
                root_task_id=root_task_id,
                amendment_index=amendment_index,
            )
        parent_contract_id = state["declared_contract_id"]
    else:
        if chain:
            return _refuse(
                ["AMENDMENT_CHAIN_INDEX_INVALID"],
                root_task_id=root_task_id,
                amendment_index=amendment_index,
            )
        parent_contract_id = _root_contract_id(root_task_id, objective, prior_paths)

    # Deterministic bounded union: prior order first, then amendment order.
    cumulative_raw: list[str] = [raw for _path, raw in prior_pairs]
    added_paths: list[str] = []
    seen = set(prior_paths)
    for path, raw in new_pairs:
        if path in seen:
            continue
        seen.add(path)
        added_paths.append(path)
        cumulative_raw.append(raw)
    cumulative_paths = prior_paths + added_paths
    if len(cumulative_paths) > MAX_CUMULATIVE_TARGETS:
        return _refuse(
            ["AMENDMENT_TARGETS_OVERSIZED"],
            root_task_id=root_task_id,
            amendment_index=amendment_index,
        )

    # Freshness: the parent's derived state must still recompute from the
    # repository exactly as recorded, or it is stale rather than amendable.
    stored_ledger: dict[str, Any] = state["ledger"]
    stored_instruction: dict[str, Any] = state["instruction"]
    fresh_instruction, fresh_ledger = _fresh_context(
        effective_project,
        objective,
        list(state["prior_targets"]),
        list(stored_ledger.get("execution_plan") or []),
        sanitize_amendment_requirements(
            stored_ledger.get("amendment_requirements")
        ),
    )
    if fresh_ledger.get("fingerprint") != stored_ledger.get("fingerprint"):
        codes.append("AMENDMENT_PARENT_LEDGER_STALE")
    if fresh_instruction.get("fingerprint") != stored_instruction.get("fingerprint"):
        codes.append("AMENDMENT_PARENT_INSTRUCTION_STALE")
    profile = parent.get("task_profile") or {}
    declared_intent = profile.get("intent_fingerprint")
    declared_instruction = profile.get("instruction_fingerprint")
    if declared_intent is not None and declared_intent != stored_ledger.get(
        "fingerprint"
    ):
        codes.append("AMENDMENT_PARENT_LEDGER_TAMPERED")
    if declared_instruction is not None and declared_instruction != (
        stored_instruction.get("fingerprint")
    ):
        codes.append("AMENDMENT_PARENT_INSTRUCTION_TAMPERED")
    if codes:
        return _refuse(
            codes, root_task_id=root_task_id, amendment_index=amendment_index
        )

    parent_digest = parent_contract_digest(parent)
    contract_id = _contract_id(
        root_task_id,
        amendment_index,
        objective,
        cumulative_paths,
        parent_contract_id,
        parent_digest,
    )
    if any(entry["contract_id"] == contract_id for entry in chain):
        return _refuse(
            ["AMENDMENT_CHAIN_CYCLIC"],
            root_task_id=root_task_id,
            amendment_index=amendment_index,
        )
    chain = chain + [
        _chain_entry(
            contract_id=parent_contract_id,
            parent_contract_id=chain[-1]["contract_id"] if chain else None,
            root_task_id=root_task_id,
            project=parent_project_text,
            amendment_index=parent_index,
            target_count=len(prior_paths),
            contract_digest=parent_digest,
        )
    ]
    summary = _text(description, MAX_AMENDMENT_TEXT) or (
        f"{len(added_paths)} additional declared target(s)"
    )
    return {
        "pass": True,
        "reason_codes": [],
        "required_actions": [],
        "version": AMENDMENT_VERSION,
        "root_task_id": root_task_id,
        "objective": objective,
        "intent": state["intent"],
        "project": effective_project,
        "amendment_index": amendment_index,
        "contract_id": contract_id,
        "parent_contract_id": parent_contract_id,
        "parent_contract_digest": parent_digest,
        "chain": chain,
        "amendments": state["prior_amendments"]
        + [
            {
                "index": amendment_index,
                "summary": summary,
                "targets": list(added_paths),
            }
        ],
        "original_targets": [raw for _path, raw in prior_pairs],
        "original_paths": prior_paths,
        "amendment_targets": [raw for _path, raw in new_pairs],
        "added_paths": added_paths,
        "cumulative_targets": cumulative_raw,
        "cumulative_paths": cumulative_paths,
        "amendment_requirements": _build_amendment_requirements(
            root_task_id,
            amendment_index,
            summary,
            prior_paths,
            added_paths,
        ),
        "summary": summary,
    }


def build_amendment_request(
    parent: dict[str, Any],
    *,
    project: str | None,
    description: str,
    targets: Any,
    recovery_context: Any = None,
) -> dict[str, Any]:
    """Build an ordinary amendment or one proof-bound recovery successor."""
    if recovery_context is None:
        return _build_amendment_request(
            parent,
            project=project,
            description=description,
            targets=targets,
        )
    from . import task_parent_recovery

    return task_parent_recovery.build_recovery_request(
        parent=parent,
        project=project,
        description=description,
        targets=targets,
        recovery_context=recovery_context,
        read_parent_state=_read_parent_state,
        refuse=_refuse,
        resolve_root=resolve_project_root,
        validate_target=_validate_target,
        validate_targets=_validated_targets,
        sanitize_amendments=sanitize_amendment_requirements,
        ordinary_builder=_build_amendment_request,
        parent_digest=parent_contract_digest,
        root_contract_id=_root_contract_id,
        max_cumulative_targets=MAX_CUMULATIVE_TARGETS,
        max_requested_targets=MAX_AMENDMENT_TARGETS,
    )


def finalize_amended_contract(
    contract: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    """Pin the immutable root identity and attach the cumulative amendment."""
    if not isinstance(contract, dict) or contract.get("error"):
        return contract
    profile = contract.setdefault("task_profile", {})
    profile["task_id"] = request["root_task_id"]
    profile["root_task_id"] = request["root_task_id"]
    profile["description"] = request["objective"]
    profile["title"] = request["objective"][:120]
    # A compound analysis records ``original_intent`` but has no root-level
    # ``intent``.  Amendments still continue one immutable root intent, so pin
    # its canonical mirror here just like the root task id and objective.
    # Otherwise a mixed-intent rework successor is rejected by the consumer's
    # parent-proof validation even though Indexer produced it from a valid
    # parent contract.
    profile["intent"] = request["intent"]
    profile["amendment_index"] = request["amendment_index"]
    profile["amendment_contract_id"] = request["contract_id"]
    # A run id belongs to the run that produced it, never to a successor.
    profile.pop("run_id", None)
    contract["task_amendment"] = {
        "version": AMENDMENT_VERSION,
        "status": "amended",
        "root_task_id": request["root_task_id"],
        "objective": request["objective"],
        "amendment_index": request["amendment_index"],
        "contract_id": request["contract_id"],
        "parent_contract_id": request["parent_contract_id"],
        "parent_contract_digest": request["parent_contract_digest"],
        "chain": request["chain"],
        "amendments": request["amendments"],
        "original_targets": request["original_targets"],
        "original_paths": request["original_paths"],
        "amendment_targets": request["amendment_targets"],
        "added_paths": request["added_paths"],
        "cumulative_targets": request["cumulative_targets"],
        "cumulative_paths": request["cumulative_paths"],
        "summary": {
            "original_target_count": len(request["original_paths"]),
            "added_target_count": len(request["added_paths"]),
            "cumulative_target_count": len(request["cumulative_paths"]),
            "chain_length": len(request["chain"]),
        },
    }
    recovery = request.get("recovery")
    if isinstance(recovery, dict):
        from . import task_parent_recovery

        evidence = task_parent_recovery.build_successor_recovery_evidence(
            contract, request
        )
        if evidence is None:
            return blocked_result(
                _refuse(["AMENDMENT_RECOVERY_SUCCESSOR_NONCANONICAL"])
            )
        contract["recovery_evidence"] = evidence
    return contract


def validate_amendment_state(
    task_contract: dict[str, Any] | None,
    *,
    allowed_paths: list[str] | None,
) -> list[dict[str, Any]]:
    """Return bounded violations when a carried amendment no longer holds."""
    state = (task_contract or {}).get("task_amendment")
    if not isinstance(state, dict) or not state:
        return []
    if state.get("status") == "blocked":
        return [{"type": "amendment_blocked"}]
    if state.get("version") not in SUPPORTED_AMENDMENT_VERSIONS:
        return [{"type": "amendment_version_unsupported"}]

    violations: list[dict[str, Any]] = []
    profile = (task_contract or {}).get("task_profile") or {}
    root_task_id = _text(state.get("root_task_id"), MAX_IDENTIFIER_LENGTH)
    if not root_task_id or profile.get("task_id") != root_task_id:
        violations.append({"type": "amendment_root_mismatch"})
    index = _index(state.get("amendment_index"))
    if index < 1:
        violations.append({"type": "amendment_chain_invalid"})

    project = _text(profile.get("project"), MAX_TARGET_LENGTH)
    chain, chain_code = verify_chain(
        state.get("chain"),
        root_task_id=root_task_id,
        project=project,
    )
    if chain_code is not None:
        violations.append({"type": "amendment_chain_invalid", "code": chain_code})
    elif index >= 1 and len(chain) != index:
        violations.append(
            {"type": "amendment_chain_invalid", "code": "AMENDMENT_CHAIN_INDEX_INVALID"}
        )

    declared = state.get("cumulative_paths")
    if not isinstance(declared, (list, tuple)) or not declared:
        violations.append({"type": "amendment_chain_invalid"})
        declared = []
    elif not chain_code and chain and index >= 1:
        predecessor = chain[-1]
        expected_id = _contract_id(
            root_task_id,
            index,
            _text(state.get("objective"), MAX_OBJECTIVE_LENGTH),
            list(declared),
            predecessor["contract_id"],
            predecessor["contract_digest"],
        )
        if expected_id != _text(state.get("contract_id"), 64):
            violations.append(
                {"type": "amendment_chain_invalid", "code": "AMENDMENT_CHAIN_TAMPERED"}
            )

    current_allowed = set(allowed_paths or [])
    if "**" in current_allowed:
        violations.append({"type": "amendment_scope_unbounded"})
    dropped = sorted(set(_normalize_allowed_paths(list(declared))) - current_allowed)
    if dropped:
        violations.append({"type": "amendment_scope_shrunk", "paths": dropped[:40]})
    return violations[:40]
