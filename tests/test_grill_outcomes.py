"""Outcome recording, privacy, idempotency, and learned-prior tests."""

import json

from src.tools.grill import GrillSessionStore, run_grill
from src.tools.grill_outcomes import OutcomeStore, record_outcome


def _contract(project, fingerprint="a" * 64):
    return {
        "decision_contract": {
            "version": "flyto.decision-contract.v2",
            "status": "frozen",
            "fingerprint": fingerprint,
            "project": project,
            "decisions": [
                {
                    "id": "architecture",
                    "severity": "high",
                    "question": "Secret question must not persist",
                    "answer": "Secret answer must not persist",
                    "confidence": {"recommendation": 0.5},
                }
            ],
        }
    }


def test_outcome_store_is_private_compact_and_idempotent(tmp_path):
    store = OutcomeStore(tmp_path / "outcomes")
    contract = _contract("demo")

    first = record_outcome(
        contract,
        success=False,
        validation={"ruff": {"status": "pass"}, "pytest": {"status": "fail"}},
        conformance={
            "status": "blocked",
            "change_set": {"changed_paths": ["src/adapter.py"]},
        },
        store=store,
    )
    replay = record_outcome(
        contract,
        success=False,
        validation={"ruff": {"status": "pass"}, "pytest": {"status": "fail"}},
        conformance={
            "status": "blocked",
            "change_set": {"changed_paths": ["src/adapter.py"]},
        },
        store=store,
    )

    assert first["status"] == "recorded"
    assert replay["status"] == "already_recorded"
    records = store.path.read_text(encoding="utf-8")
    assert len(records.splitlines()) == 1
    assert "Secret question" not in records
    assert "Secret answer" not in records
    assert json.loads(records)["change_path_count"] == 1
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_failed_outcomes_lower_implicit_confidence_and_raise_voi(tmp_path):
    root = tmp_path / "shared-grill"
    outcome_store = OutcomeStore(root)
    for suffix in ("b", "c"):
        record_outcome(
            _contract("demo", fingerprint=suffix * 64),
            success=False,
            store=outcome_store,
        )
    session_store = GrillSessionStore(root)
    result = run_grill(
        "start",
        description="Use learned priors",
        project="demo",
        decisions=[
            {
                "id": "architecture",
                "severity": "high",
                "question": "Architecture?",
                "recommendation": "Use the bounded architecture.",
            },
            {
                "id": "explicit",
                "severity": "high",
                "question": "Explicit confidence?",
                "recommendation": "Preserve caller confidence.",
                "confidence": {"recommendation": 0.9},
            },
        ],
        mode="batch",
        store=session_store,
    )
    architecture = next(
        node for node in result["decisions"] if node["id"] == "architecture"
    )
    explicit = next(node for node in result["decisions"] if node["id"] == "explicit")

    assert architecture["confidence"]["recommendation"] == 0.25
    assert architecture["learning_prior"]["samples"] == 2
    assert architecture["value_of_information"] > explicit["value_of_information"]
    assert explicit["confidence"]["recommendation"] == 0.9
    assert explicit["learning_prior"] is None

    answered = run_grill(
        "answer",
        session_id=result["session_id"],
        decision_id="architecture",
        accept_recommendation=True,
        store=session_store,
    )
    answered = run_grill(
        "answer",
        session_id=answered["session_id"],
        decision_id="explicit",
        accept_recommendation=True,
        store=session_store,
    )
    frozen = run_grill(
        "freeze", session_id=answered["session_id"], store=session_store
    )
    contract_node = next(
        node
        for node in frozen["contract"]["decisions"]
        if node["id"] == "architecture"
    )
    assert contract_node["learning_prior"]["source"] == "local_outcomes"
