"""Validation for content-addressed and optionally attested external proof receipts."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any


PROOF_RECEIPT_SCHEMA = "flyto-proof-receipt.v1"
VALID_KINDS = {
    "browser",
    "container_build",
    "deployment",
    "integration",
    "penetration",
    "race",
    "runtime",
    "security",
}
VALID_STATUSES = {"pass", "fail"}


def _canonical_payload(receipt: dict[str, Any]) -> bytes:
    payload = {
        key: value
        for key, value in receipt.items()
        if key not in {"attestation", "receipt_id", "validation"}
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def receipt_digest(receipt: dict[str, Any]) -> str:
    """Return the stable SHA-256 identity for a proof payload."""
    return hashlib.sha256(_canonical_payload(receipt)).hexdigest()


def _trusted_keys_from_env() -> dict[str, str]:
    raw = os.environ.get("FLYTO_INDEXER_PROOF_KEYS_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in parsed.items()
        if str(key).strip() and str(value)
    }


def build_proof_receipt(
    *,
    kind: str,
    status: str,
    producer: str,
    subject: str,
    evidence_digest: str,
    issued_at: str,
    project: str = "",
    key_id: str = "",
    key: str = "",
) -> dict[str, Any]:
    """Build a portable receipt; HMAC attestation is optional and local-key based."""
    receipt = {
        "schema": PROOF_RECEIPT_SCHEMA,
        "kind": kind,
        "status": status,
        "producer": producer,
        "subject": subject,
        "project": project,
        "evidence_digest": evidence_digest,
        "issued_at": issued_at,
    }
    digest = receipt_digest(receipt)
    receipt["receipt_id"] = f"proof-{digest[:24]}"
    if key_id and key:
        receipt["attestation"] = {
            "algorithm": "hmac-sha256",
            "key_id": key_id,
            "signature": hmac.new(
                key.encode("utf-8"), _canonical_payload(receipt), hashlib.sha256
            ).hexdigest(),
        }
    return receipt


def validate_proof_receipt(
    receipt: dict[str, Any],
    *,
    project: str | None = None,
    trusted_keys: dict[str, str] | None = None,
    max_age_hours: float = 168.0,
) -> dict[str, Any]:
    """Validate schema, content identity, freshness, subject, and optional attestation."""
    failures: list[str] = []
    if not isinstance(receipt, dict):
        return {"pass": False, "trusted": False, "failures": ["invalid_receipt"]}
    if receipt.get("schema") != PROOF_RECEIPT_SCHEMA:
        failures.append("unsupported_schema")
    kind = str(receipt.get("kind") or "")
    status = str(receipt.get("status") or "")
    if kind not in VALID_KINDS:
        failures.append("invalid_kind")
    if status not in VALID_STATUSES:
        failures.append("invalid_status")
    for field in ("producer", "subject", "evidence_digest", "issued_at"):
        if not str(receipt.get(field) or "").strip():
            failures.append(f"missing_{field}")
    evidence_digest = str(receipt.get("evidence_digest") or "")
    if evidence_digest and not all(char in "0123456789abcdef" for char in evidence_digest.casefold()):
        failures.append("invalid_evidence_digest")
    if evidence_digest and len(evidence_digest) != 64:
        failures.append("invalid_evidence_digest")
    if project and receipt.get("project") not in (None, "", project):
        failures.append("project_mismatch")
    age_hours = None
    try:
        issued_at = datetime.fromisoformat(
            str(receipt.get("issued_at") or "").replace("Z", "+00:00")
        )
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - issued_at).total_seconds() / 3600
        if age_hours < -0.25:
            failures.append("issued_in_future")
        if age_hours > max_age_hours:
            failures.append("stale_receipt")
    except ValueError:
        failures.append("invalid_issued_at")
    digest = receipt_digest(receipt)
    expected_id = f"proof-{digest[:24]}"
    if receipt.get("receipt_id") not in (None, "", expected_id):
        failures.append("receipt_id_mismatch")
    keys = trusted_keys if trusted_keys is not None else _trusted_keys_from_env()
    attestation = receipt.get("attestation") or {}
    trusted = False
    integrity = "content_addressed"
    if attestation:
        if attestation.get("algorithm") != "hmac-sha256":
            failures.append("unsupported_attestation")
        else:
            key_id = str(attestation.get("key_id") or "")
            key = keys.get(key_id)
            if not key:
                failures.append("untrusted_key")
            else:
                expected_signature = hmac.new(
                    key.encode("utf-8"), _canonical_payload(receipt), hashlib.sha256
                ).hexdigest()
                if hmac.compare_digest(
                    expected_signature, str(attestation.get("signature") or "")
                ):
                    trusted = True
                    integrity = "attested"
                else:
                    failures.append("invalid_signature")
    return {
        "receipt_id": expected_id,
        "kind": kind,
        "status": status,
        "pass": not failures and status == "pass",
        "trusted": trusted and not failures,
        "integrity": integrity,
        "age_hours": round(age_hours, 3) if age_hours is not None else None,
        "failures": failures,
    }


def validate_proof_receipts(
    receipts: list[dict[str, Any]] | None,
    *,
    required_kinds: list[str] | None = None,
    project: str | None = None,
    trusted_keys: dict[str, str] | None = None,
    require_trusted: bool = True,
) -> dict[str, Any]:
    """Require one fresh passing receipt per declared runtime-proof kind."""
    required = sorted(set(required_kinds or []))
    invalid_required = [kind for kind in required if kind not in VALID_KINDS]
    validations = [
        validate_proof_receipt(
            receipt,
            project=project,
            trusted_keys=trusted_keys,
        )
        for receipt in receipts or []
    ]
    satisfied = sorted({
        item["kind"]
        for item in validations
        if item.get("pass") and (item.get("trusted") or not require_trusted)
    })
    missing = sorted(set(required) - set(satisfied))
    explicit_failures = [
        item for item in validations if item.get("status") == "fail" and not item.get("failures")
    ]
    passed = not invalid_required and not missing and not explicit_failures
    required_actions = []
    if missing or invalid_required:
        required_actions.append(
            "attach_fresh_passing_attested_receipts_for:"
            + ",".join(missing or invalid_required)
        )
    if explicit_failures:
        required_actions.append("replace_explicitly_failing_proof_receipts")
    return {
        "schema": "external-proof-validation.v1",
        "pass": passed,
        "decision": "pass" if passed else "blocked",
        "required_kinds": required,
        "satisfied_kinds": satisfied,
        "missing_kinds": missing,
        "invalid_required_kinds": invalid_required,
        "require_trusted": require_trusted,
        "receipts": validations,
        "reason_codes": [] if passed else ["EXTERNAL_PROOF_NONCONFORMANT"],
        "required_actions": required_actions,
    }
