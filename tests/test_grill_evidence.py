"""Evidence freshness, selective reopen, migration, and ADR tests."""

import json
import subprocess

from src.tools.grill import (
    GrillSessionStore,
    run_grill,
    validate_decision_contract,
)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "grill@example.test")
    _git(repo, "config", "user.name", "Grill Test")
    source = repo / "policy.py"
    source.write_text("POLICY = 'safe'\n", encoding="utf-8")
    _git(repo, "add", "policy.py")
    _git(repo, "commit", "-m", "initial")
    return repo, source


def _frozen_fact_contract(tmp_path):
    repo, source = _repository(tmp_path)
    store = GrillSessionStore(tmp_path / "state")
    decisions = [
        {
            "id": "policy_fact",
            "kind": "fact",
            "severity": "critical",
            "question": "Which policy is implemented?",
            "recommendation": "Use the repository policy.",
            "evidence_queries": ["POLICY"],
        },
        {
            "id": "scope",
            "severity": "critical",
            "prerequisites": ["policy_fact"],
            "question": "Should the policy remain safe?",
            "recommendation": "Keep the safe policy.",
        },
    ]

    def resolver(query, project):
        return {
            "results": [
                {
                    "symbol_id": "repo:policy.py:variable:POLICY",
                    "name": "POLICY",
                    "path": "policy.py",
                    "line": 1,
                    "summary": "Repository safety policy",
                    "score": 100,
                }
            ]
        }

    started = run_grill(
        "start",
        description="Keep policy behavior explicit",
        project=str(repo),
        decisions=decisions,
        fact_resolver=resolver,
        store=store,
    )
    answered = run_grill(
        "answer",
        session_id=started["session_id"],
        decision_id="scope",
        accept_recommendation=True,
        fact_resolver=resolver,
        store=store,
    )
    frozen = run_grill(
        "freeze",
        session_id=answered["session_id"],
        fact_resolver=resolver,
        store=store,
    )
    assert frozen["pass"] is True
    return frozen["contract"], source


def test_frozen_contract_contains_snapshot_and_audit_artifacts(tmp_path):
    contract, _ = _frozen_fact_contract(tmp_path)

    assert contract["evidence_snapshot"]["status"] == "captured"
    assert contract["evidence_snapshot"]["file_count"] == 1
    assert contract["evidence_snapshot"]["decisions"]["policy_fact"]["paths"][
        "policy.py"
    ]["sha256"]
    assert contract["artifacts"]["adr_markdown"].startswith("# ADR:")
    assert contract["artifacts"]["decision_audit"]["decision_count"] == 2


def test_changed_evidence_reopens_only_impacted_decision(tmp_path):
    contract, source = _frozen_fact_contract(tmp_path)
    source.write_text("POLICY = 'unsafe'\n", encoding="utf-8")

    validation = validate_decision_contract({"decision_contract": contract})

    assert validation["pass"] is False
    assert validation["reason_codes"] == ["DECISION_EVIDENCE_STALE"]
    assert validation["required_actions"] == ["reopen_decision:policy_fact"]
    assert validation["selective_reopen"] == [
        {
            "decision_id": "policy_fact",
            "status": "open",
            "previous_answer": {
                "source": "repository",
                "summary": "Resolved from 1 indexed evidence item(s).",
            },
            "reason": "repository_evidence_changed",
            "changed_paths": ["policy.py"],
        }
    ]


def test_legacy_contract_and_session_remain_compatible(tmp_path):
    store = GrillSessionStore(tmp_path / "legacy")
    started = run_grill(
        "start",
        description="Legacy migration",
        decisions=[
            {
                "id": "scope",
                "question": "Scope?",
                "recommendation": "Bound it.",
                "severity": "critical",
            }
        ],
        store=store,
    )
    path = store.root / f"{started['session_id']}.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted["schema_version"] = "flyto.grill-session.v1"
    for field in (
        "confidence",
        "decision_cost",
        "reversibility",
        "value_of_information",
        "acceptance",
        "adversarial_review",
    ):
        persisted["decisions"][0].pop(field)
    path.write_text(json.dumps(persisted), encoding="utf-8")

    resumed = run_grill("status", session_id=started["session_id"], store=store)

    assert resumed["schema_version"] == "flyto.grill-session.v2"
    assert resumed["next_question"]["confidence"]["recommendation"] == 0.5


def test_out_of_scope_repository_evidence_fails_closed(tmp_path):
    repo, _ = _repository(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE = True\n", encoding="utf-8")
    store = GrillSessionStore(tmp_path / "scope-state")

    def resolver(query, project):
        return {
            "results": [
                {
                    "symbol_id": "repo:outside.py:variable:OUTSIDE",
                    "name": "OUTSIDE",
                    "path": "../outside.py",
                    "line": 1,
                    "summary": "Out-of-scope evidence",
                    "score": 100,
                }
            ]
        }

    started = run_grill(
        "start",
        description="Reject out-of-scope evidence",
        project=str(repo),
        decisions=[
            {
                "id": "outside_fact",
                "kind": "fact",
                "severity": "critical",
                "question": "Does outside evidence exist?",
                "recommendation": "Do not trust it.",
                "evidence_queries": ["OUTSIDE"],
            }
        ],
        fact_resolver=resolver,
        store=store,
    )
    frozen = run_grill(
        "freeze",
        session_id=started["session_id"],
        fact_resolver=resolver,
        store=store,
    )
    validation = validate_decision_contract(
        {"decision_contract": frozen["contract"]}
    )

    assert validation["pass"] is False
    assert validation["reason_codes"] == ["DECISION_EVIDENCE_SCOPE_INVALID"]
    assert validation["evidence_freshness"]["status"] == "invalid_evidence_scope"
    assert validation["required_actions"] == ["reopen_decision:outside_fact"]
