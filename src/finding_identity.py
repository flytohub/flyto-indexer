"""Stable, privacy-preserving identities for scanner and verification findings."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SPACE_RE = re.compile(r"\s+")


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
