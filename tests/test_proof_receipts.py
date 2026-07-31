"""External proof receipt integrity and trust tests."""

from datetime import datetime, timedelta, timezone

from src.tools.proof_receipts import (
    build_proof_receipt,
    validate_proof_receipt,
    validate_proof_receipts,
)


def _issued_at(hours_ago=0):
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ).isoformat().replace("+00:00", "Z")


def test_attested_receipt_satisfies_required_runtime_proof():
    receipt = build_proof_receipt(
        kind="browser",
        status="pass",
        producer="flyto-core",
        subject="checkout smoke",
        project="demo",
        evidence_digest="a" * 64,
        issued_at=_issued_at(),
        key_id="ci",
        key="local-test-key",
    )

    result = validate_proof_receipts(
        [receipt],
        required_kinds=["browser"],
        project="demo",
        trusted_keys={"ci": "local-test-key"},
    )

    assert result["pass"] is True
    assert result["satisfied_kinds"] == ["browser"]
    assert result["receipts"][0]["integrity"] == "attested"


def test_unsigned_receipt_is_visible_but_cannot_close_required_proof():
    receipt = build_proof_receipt(
        kind="race",
        status="pass",
        producer="ci",
        subject="race suite",
        evidence_digest="b" * 64,
        issued_at=_issued_at(),
        project="demo",
    )

    result = validate_proof_receipts(
        [receipt], required_kinds=["race"], project="demo"
    )

    assert result["pass"] is False
    assert result["missing_kinds"] == ["race"]
    assert result["receipts"][0]["integrity"] == "content_addressed"


def test_tamper_and_staleness_fail_closed():
    receipt = build_proof_receipt(
        kind="container_build",
        status="pass",
        producer="ci",
        subject="image build",
        evidence_digest="c" * 64,
        issued_at=_issued_at(hours_ago=200),
        key_id="ci",
        key="local-test-key",
    )
    receipt["subject"] = "tampered"

    result = validate_proof_receipt(
        receipt,
        project="demo",
        trusted_keys={"ci": "local-test-key"},
    )

    assert result["pass"] is False
    assert "stale_receipt" in result["failures"]
    assert "receipt_id_mismatch" in result["failures"]
    assert "invalid_signature" in result["failures"]


def test_explicit_failed_receipt_has_an_action_without_required_kinds():
    receipt = build_proof_receipt(
        kind="browser",
        status="fail",
        producer="flyto-core",
        subject="checkout smoke",
        evidence_digest="d" * 64,
        issued_at=_issued_at(),
    )

    result = validate_proof_receipts([receipt], require_trusted=False)

    assert result["pass"] is False
    assert result["required_actions"] == [
        "replace_explicitly_failing_proof_receipts"
    ]
