"""Privacy-preserving local outcome learning for Grill decisions."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    _fcntl = None


MAX_OUTCOMES = 2000
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _project_identity(project: str | None) -> str:
    if not project:
        return "unknown"
    return Path(project).name or str(project)


def _default_root() -> Path:
    configured = os.environ.get("FLYTO_INDEXER_GRILL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".flyto-indexer" / "grill"


class OutcomeStore:
    """Append-only local outcome store with bounded reads and idempotency."""

    def __init__(self, root: Path | None = None):
        self.root = (root or _default_root()).resolve()
        self.path = self.root / "outcomes.jsonl"
        self.lock_path = self.root / ".outcomes.lock"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def read(self) -> list[dict]:
        with _LOCK:
            if not self.path.is_file():
                return []
            records = []
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return []
            for line in lines[-MAX_OUTCOMES:]:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("outcome_id"):
                    records.append(record)
            return records

    def append(self, record: dict) -> bool:
        """Append once by outcome_id; returns False for an idempotent replay."""
        with _LOCK:
            self._ensure_root()
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_EX)
                if any(
                    item.get("outcome_id") == record.get("outcome_id")
                    for item in self.read()
                ):
                    return False
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(_canonical_json(record) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
                return True
            finally:
                if _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_UN)
                os.close(descriptor)


def load_outcome_priors(
    project: str | None,
    *,
    store: OutcomeStore | None = None,
) -> dict[str, dict]:
    """Return Bayesian-smoothed recommendation success priors by decision ID."""
    identity = _project_identity(project)
    aggregate: dict[str, dict] = {}
    for record in (store or OutcomeStore()).read():
        if record.get("project") != identity:
            continue
        successful = bool(record.get("success"))
        for decision in record.get("decisions") or []:
            decision_id = decision.get("id")
            if not isinstance(decision_id, str):
                continue
            stats = aggregate.setdefault(
                decision_id, {"samples": 0, "successes": 0}
            )
            stats["samples"] += 1
            stats["successes"] += int(successful)
    for stats in aggregate.values():
        stats["recommendation_confidence"] = round(
            (stats["successes"] + 1) / (stats["samples"] + 2),
            3,
        )
        stats["source"] = "local_outcomes"
    return aggregate


def record_outcome(
    task_contract: dict,
    *,
    success: bool,
    validation: dict | None = None,
    conformance: dict | None = None,
    store: OutcomeStore | None = None,
) -> dict:
    """Record a compact result without persisting questions, answers, or code."""
    contract = (
        task_contract.get("decision_contract")
        if isinstance(task_contract, dict)
        else None
    )
    if not isinstance(contract, dict) or not contract.get("fingerprint"):
        return {"status": "skipped", "reason": "valid_frozen_contract_required"}
    change_paths = sorted(
        ((conformance or {}).get("change_set") or {}).get("changed_paths") or []
    )
    material = {
        "contract_fingerprint": contract["fingerprint"],
        "success": bool(success),
        "change_paths": change_paths,
        "validation": {
            "ruff": ((validation or {}).get("ruff") or {}).get("status"),
            "pytest": ((validation or {}).get("pytest") or {}).get("status"),
            "conformance": (conformance or {}).get("status"),
        },
    }
    outcome_id = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    record = {
        "outcome_id": outcome_id,
        "recorded_at": _now(),
        "project": _project_identity(contract.get("project")),
        "success": bool(success),
        "decisions": [
            {
                "id": node.get("id"),
                "severity": node.get("severity"),
                "recommendation_confidence": (
                    node.get("confidence") or {}
                ).get("recommendation"),
            }
            for node in contract.get("decisions") or []
            if isinstance(node, dict) and node.get("id")
        ],
        "validation": material["validation"],
        "change_path_count": len(change_paths),
    }
    appended = (store or OutcomeStore()).append(record)
    priors = load_outcome_priors(
        contract.get("project"), store=store or OutcomeStore()
    )
    return {
        "status": "recorded" if appended else "already_recorded",
        "outcome_id": outcome_id,
        "success": bool(success),
        "updated_priors": {
            decision["id"]: priors.get(decision["id"])
            for decision in record["decisions"]
            if priors.get(decision["id"])
        },
        "privacy": "questions_answers_and_code_not_persisted",
    }
