"""Stable, privacy-preserving identities for scanner and verification findings."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

_SPACE_RE = re.compile(r"\s+")
FINDING_EVIDENCE_SCHEMA = "finding-evidence.v1"
_CONFIDENCE_SCORES = {"none": 0.0, "low": 0.35, "medium": 0.65, "high": 0.9}
_MAX_TRACE_STEPS = 32


def normalize_finding_path(path: str | Path | None) -> str:
    """Return a deterministic repository-style path without touching the filesystem."""
    value = str(path or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value or "."


def normalize_finding_anchor(value: Any) -> str:
    """Bound noisy source expressions while preserving their semantic shape."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    return _SPACE_RE.sub(" ", text).strip()[:1000]


def finding_fingerprint(
    rule_id: str,
    path: str | Path | None = None,
    *,
    anchor: Any = "",
    discriminator: Any = "",
) -> str:
    """Build a full SHA-256 fingerprint stable across line-number-only edits."""
    payload = {
        "rule_id": str(rule_id).strip() or "finding",
        "path": normalize_finding_path(path),
        "anchor": normalize_finding_anchor(anchor),
        "discriminator": normalize_finding_anchor(discriminator),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_finding_id(
    rule_id: str,
    path: str | Path | None = None,
    *,
    anchor: Any = "",
    discriminator: Any = "",
) -> str:
    """Return a compact stable identifier suitable for JSON, baselines, and SARIF."""
    return f"flyto-{finding_fingerprint(rule_id, path, anchor=anchor, discriminator=discriminator)[:24]}"


def _normalise_trace(trace: Any) -> list[dict[str, Any]]:
    """Return a bounded JSON-safe trace without leaking object internals."""
    if not isinstance(trace, (list, tuple)):
        trace = [trace] if trace else []
    result = []
    for index, raw_step in enumerate(trace[:_MAX_TRACE_STEPS]):
        if isinstance(raw_step, dict):
            step = {
                str(key): normalize_finding_anchor(value)
                for key, value in raw_step.items()
                if value not in (None, "")
            }
        else:
            step = {"value": normalize_finding_anchor(raw_step)}
        step.setdefault("index", index)
        result.append(step)
    return result


def suppression_provenance(
    *,
    suppressed: bool = False,
    mechanism: str = "none",
    rule_id: str = "",
    reason: str = "",
    source: str = "",
    expires: str = "",
    owner: str = "",
) -> dict[str, Any]:
    """Create an explicit, bounded record for active or suppressed findings."""
    result: dict[str, Any] = {
        "status": "suppressed" if suppressed else "active",
        "mechanism": normalize_finding_anchor(mechanism) or "none",
    }
    optional = {
        "rule_id": rule_id,
        "reason": reason,
        "source": source,
        "expires": expires,
        "owner": owner,
    }
    result.update({
        key: normalize_finding_anchor(value)
        for key, value in optional.items()
        if value
    })
    governed = suppressed and mechanism in {"baseline", "ignore", "waiver"}
    missing = []
    if governed:
        for key in ("reason", "source", "expires", "owner"):
            if not result.get(key):
                missing.append(key)
        if result.get("expires"):
            try:
                if date.fromisoformat(str(result["expires"])) < date.today():
                    missing.append("unexpired_expiry")
            except ValueError:
                missing.append("valid_expiry")
    result["governance"] = {
        "status": (
            "not_applicable" if not governed else "complete" if not missing else "incomplete"
        ),
        "missing": missing,
        "automatic_policy_change": False,
    }
    return result


def finding_evidence(
    rule_id: str,
    path: str | Path | None = None,
    *,
    anchor: Any = "",
    discriminator: Any = "",
    confidence: str = "medium",
    confidence_score: float | None = None,
    confidence_basis: Any = (),
    trace: Any = (),
    suppression: dict[str, Any] | None = None,
    origin: str = "",
) -> dict[str, Any]:
    """Build the common evidence envelope shared by scanner findings.

    The fingerprint intentionally excludes confidence, trace, line numbers, and
    suppression state, so triage and baselines survive evidence enrichment.
    """
    level = confidence if confidence in _CONFIDENCE_SCORES else "medium"
    score = (
        _CONFIDENCE_SCORES[level]
        if confidence_score is None
        else max(0.0, min(1.0, float(confidence_score)))
    )
    basis = (
        list(confidence_basis)
        if isinstance(confidence_basis, (list, tuple, set))
        else [confidence_basis]
    )
    fingerprint = finding_fingerprint(
        rule_id,
        path,
        anchor=anchor,
        discriminator=discriminator,
    )
    return {
        "schema": FINDING_EVIDENCE_SCHEMA,
        "finding_id": f"flyto-{fingerprint[:24]}",
        "fingerprint": fingerprint,
        "rule_id": normalize_finding_anchor(rule_id) or "finding",
        "origin": normalize_finding_anchor(origin),
        "confidence": {
            "level": level,
            "score": round(score, 3),
            "basis": [
                normalize_finding_anchor(item)
                for item in basis
                if item not in (None, "")
            ][:10],
        },
        "trace": _normalise_trace(trace),
        "suppression": suppression or suppression_provenance(),
    }
