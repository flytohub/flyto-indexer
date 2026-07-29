"""Deep tests for the evidence-backed Grill decision engine."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.tools.grill import (
    CONTRACT_VERSION,
    GrillSessionStore,
    export_decision_contract,
    run_grill,
    validate_decision_contract,
)


@pytest.fixture
def store(tmp_path):
    return GrillSessionStore(tmp_path / "grill-state")


def _decision(
    decision_id,
    *,
    severity="high",
    prerequisites=None,
    kind="decision",
    options=None,
    evidence_queries=None,
    **overrides,
):
    decision = {
        "id": decision_id,
        "kind": kind,
        "severity": severity,
        "question": f"Question for {decision_id}?",
        "recommendation": f"Recommended {decision_id}",
        "prerequisites": prerequisites or [],
        "options": options or [],
        "evidence_queries": evidence_queries or [],
    }
    decision.update(overrides)
    return decision


def _start(store, decisions, **kwargs):
    return run_grill(
        "start",
        description=kwargs.pop("description", "Build a safe robot workflow"),
        project="flyto-robotics",
        decisions=decisions,
        store=store,
        **kwargs,
    )


def _answer(store, session, decision_id, answer="approved", **kwargs):
    return run_grill(
        "answer",
        session_id=session["session_id"],
        decision_id=decision_id,
        answer=answer,
        store=store,
        **kwargs,
    )


class TestDecisionTree:
    def test_interactive_returns_exactly_one_frontier_question(self, store):
        result = _start(
            store,
            [
                _decision("scope", severity="critical"),
                _decision("safety", prerequisites=["scope"]),
                _decision("telemetry", prerequisites=["scope"]),
            ],
        )

        assert result["frontier_ids"] == ["scope"]
        assert result["next_question"]["id"] == "scope"
        assert [item["id"] for item in result["questions"]] == ["scope"]

    def test_parent_answer_recomputes_frontier(self, store):
        started = _start(
            store,
            [
                _decision("scope", severity="critical"),
                _decision("safety", prerequisites=["scope"]),
                _decision("telemetry", prerequisites=["scope"]),
            ],
        )
        answered = _answer(store, started, "scope")

        assert answered["frontier_ids"] == ["safety", "telemetry"]
        assert answered["next_question"]["id"] == "safety"
        assert len(answered["questions"]) == 1

    def test_cannot_answer_a_node_before_its_prerequisite(self, store):
        started = _start(
            store,
            [_decision("scope"), _decision("safety", prerequisites=["scope"])],
        )
        result = _answer(store, started, "safety")

        assert result["pass"] is False
        assert "not on the current frontier" in result["error"]

    def test_batch_mode_is_explicit_and_bounded(self, store):
        decisions = [_decision(f"d{i}", severity="medium") for i in range(1, 8)]
        result = _start(store, decisions, mode="batch", max_questions=3)

        assert result["next_question"]["id"] == "d1"
        assert [item["id"] for item in result["questions"]] == ["d1", "d2", "d3"]

    def test_rejects_cycles_and_missing_dependencies(self, store):
        cycle = _start(
            store,
            [
                _decision("a", prerequisites=["b"]),
                _decision("b", prerequisites=["a"]),
            ],
        )
        missing = _start(store, [_decision("a", prerequisites=["missing"])])

        assert "cycle" in cycle["error"]
        assert "missing prerequisites" in missing["error"]

    def test_frontier_prioritizes_value_of_information_stably(self, store):
        result = _start(
            store,
            [
                _decision(
                    "cheap",
                    severity="critical",
                    confidence={"recommendation": 0.95},
                    decision_cost=1,
                    reversibility="reversible",
                ),
                _decision(
                    "expensive",
                    severity="high",
                    confidence={"recommendation": 0.1},
                    decision_cost=10,
                    reversibility="irreversible",
                ),
                _decision("stable", severity="medium"),
            ],
            mode="batch",
        )

        assert result["frontier_ids"] == ["expensive", "stable", "cheap"]
        assert result["next_question"]["value_of_information"] > 10

    def test_question_exposes_bounded_adversarial_and_acceptance_contract(self, store):
        result = _start(
            store,
            [
                _decision(
                    "scope",
                    confidence={"recommendation": 0.35},
                    acceptance={
                        "expected_paths": ["src/tools/grill.py"],
                        "forbidden_paths": ["src/legacy.py"],
                        "assertions": ["Existing task callers remain compatible."],
                        "proof_commands": ["pytest -q tests/test_grill.py"],
                    },
                    failure_conditions=["The legacy task schema changes."],
                )
            ],
        )
        question = result["next_question"]

        assert question["confidence"]["recommendation"] == 0.35
        assert question["acceptance"]["expected_paths"] == ["src/tools/grill.py"]
        assert question["adversarial_review"]["bounded"] is True
        assert question["adversarial_review"]["max_rounds"] == 2
        assert question["adversarial_review"]["failure_conditions"] == [
            "The legacy task schema changes."
        ]

    @pytest.mark.parametrize(
        "overrides,error",
        [
            ({"confidence": {"recommendation": 1.1}}, "confidence.recommendation"),
            ({"decision_cost": 0}, "decision_cost"),
            ({"reversibility": "magic"}, "reversibility"),
            ({"acceptance": {"proof_commands": "pytest"}}, "proof_commands"),
            ({"adversarial_review": {"max_rounds": 99}}, "max_rounds"),
        ],
    )
    def test_invalid_decision_intelligence_fails_closed(self, store, overrides, error):
        result = _start(store, [_decision("scope", **overrides)])

        assert result["pass"] is False
        assert error in result["error"]


class TestRepositoryFacts:
    def test_repository_fact_is_resolved_without_asking_the_user(self, store):
        calls = []

        def resolver(query, project):
            calls.append((query, project))
            return {
                "results": [
                    {
                    "symbol_id": "flyto-robotics:src/safety.c:function:emergency_stop",
                    "name": "emergency_stop",
                    "type": "function",
                    "path": "src/safety.c",
                        "line": 12,
                        "score": 34,
                        "summary": "Hard real-time emergency stop",
                    }
                ]
            }

        result = _start(
            store,
            [
                _decision(
                    "estop_exists",
                    kind="fact",
                    severity="critical",
                    evidence_queries=["emergency_stop"],
                ),
                _decision("policy", prerequisites=["estop_exists"]),
            ],
            fact_resolver=resolver,
        )

        assert calls == [("emergency_stop", "flyto-robotics")]
        assert result["resolved_from_code"] == ["estop_exists"]
        assert result["next_question"]["id"] == "policy"
        fact = next(item for item in result["decisions"] if item["id"] == "estop_exists")
        assert fact["evidence"][0]["path"] == "src/safety.c"

    def test_unresolved_repository_fact_blocks_without_becoming_a_human_question(self, store):
        result = _start(
            store,
            [
                _decision(
                    "adapter_exists",
                    kind="fact",
                    severity="critical",
                    evidence_queries=["robot adapter"],
                )
            ],
            fact_resolver=lambda query, project: {"results": []},
        )

        assert result["next_question"] is None
        assert result["repository_actions"][0]["decision_id"] == "adapter_exists"
        assert result["readiness"]["repository_fact_blockers"] == ["adapter_exists"]

    def test_fuzzy_but_unrelated_results_do_not_resolve_a_fact(self, store):
        result = _start(
            store,
            [
                _decision(
                    "missing_capability",
                    kind="fact",
                    severity="critical",
                    evidence_queries=["totally_missing_capability_xyz"],
                )
            ],
            fact_resolver=lambda query, project: {
                "results": [
                    {
                        "symbol_id": "demo:controller.py:class:CapabilityRegistry",
                        "name": "CapabilityRegistry",
                        "path": "controller.py",
                        "score": 43.8,
                        "summary": "Expose bounded capabilities.",
                    }
                ]
            },
        )

        assert result["resolved_from_code"] == []
        assert result["readiness"]["repository_fact_blockers"] == [
            "missing_capability"
        ]


class TestAnswersAndContradictions:
    def test_accept_recommendation_and_request_id_are_idempotent(self, store):
        started = _start(store, [_decision("scope")])
        first = _answer(
            store,
            started,
            "scope",
            answer=None,
            accept_recommendation=True,
            request_id="req-1",
        )
        replay = _answer(
            store,
            first,
            "scope",
            answer=None,
            accept_recommendation=True,
            request_id="req-1",
        )

        node = replay["decisions"][0]
        assert node["answer"] == "Recommended scope"
        assert replay["revision"] == first["revision"]

    def test_different_second_answer_fails_closed(self, store):
        started = _start(store, [_decision("scope")])
        answered = _answer(store, started, "scope", answer="first")
        conflict = _answer(store, answered, "scope", answer="second")

        assert conflict["pass"] is False
        assert "different answer" in conflict["error"]

    def test_structured_option_conflict_blocks_freeze(self, store):
        decisions = [
            _decision(
                "storage",
                options=[
                    {
                        "id": "local_only",
                        "label": "Local only",
                        "conflicts_with": ["sharing:public"],
                    }
                ],
            ),
            _decision(
                "sharing",
                options=[
                    {
                        "id": "public",
                        "label": "Public",
                        "conflicts_with": ["storage:local_only"],
                    }
                ],
            ),
        ]
        started = _start(store, decisions)
        first = _answer(
            store, started, "storage", answer="local", selected_option="local_only"
        )
        second = _answer(
            store, first, "sharing", answer="public", selected_option="public"
        )
        frozen = run_grill("freeze", session_id=second["session_id"], store=store)

        assert frozen["pass"] is False
        assert frozen["reason_codes"] == ["DECISION_CONTRADICTIONS"]
        assert frozen["readiness"]["contradictions"]


class TestPersistenceAndContract:
    def test_session_resumes_from_disk(self, store):
        started = _start(store, [_decision("scope"), _decision("safety")])
        _answer(store, started, "scope")

        reloaded = GrillSessionStore(store.root)
        status = run_grill(
            "status", session_id=started["session_id"], store=reloaded
        )

        assert status["revision"] == 2
        assert status["next_question"]["id"] == "safety"

    def test_corrupt_and_traversal_session_ids_fail_closed(self, store):
        invalid = run_grill("status", session_id="../../etc/passwd", store=store)
        assert invalid["pass"] is False
        assert "invalid grill session_id" in invalid["error"]

        path = store.root / "grill_aaaaaaaaaaaaaaaaaaaaaaaa.json"
        store.root.mkdir(parents=True)
        path.write_text("{not-json", encoding="utf-8")
        corrupt = run_grill("status", session_id=path.stem, store=store)
        assert "unreadable" in corrupt["error"]

    def test_freeze_requires_all_blocking_decisions(self, store):
        started = _start(store, [_decision("scope", severity="critical")])
        frozen = run_grill("freeze", session_id=started["session_id"], store=store)

        assert frozen["pass"] is False
        assert frozen["required_actions"] == ["scope"]
        assert frozen["status"] == "active"

    def test_frozen_contract_is_exportable_and_tamper_evident(self, store):
        started = _start(
            store,
            [
                _decision(
                    "scope",
                    severity="critical",
                    acceptance={"expected_paths": ["src/tools/grill.py"]},
                )
            ],
        )
        answered = _answer(store, started, "scope", answer="safe scope")
        frozen = run_grill("freeze", session_id=answered["session_id"], store=store)

        assert frozen["pass"] is True
        contract = export_decision_contract(answered["session_id"], store)
        assert contract["version"] == CONTRACT_VERSION
        assert contract["decisions"][0]["acceptance"]["expected_paths"] == [
            "src/tools/grill.py"
        ]
        assert contract["decisions"][0]["adversarial_review"]["status"] == "closed"
        assert validate_decision_contract({"decision_contract": contract})["pass"] is True

        tampered = json.loads(json.dumps(contract))
        tampered["decisions"][0]["answer"] = "unsafe mutation"
        gate = validate_decision_contract({"decision_contract": tampered})
        assert gate["pass"] is False
        assert gate["reason_codes"] == ["DECISION_CONTRACT_TAMPERED"]

    def test_frozen_session_is_immutable(self, store):
        started = _start(store, [_decision("scope")])
        answered = _answer(store, started, "scope")
        frozen = run_grill("freeze", session_id=answered["session_id"], store=store)

        changed = _answer(store, frozen, "scope", answer="changed")
        discarded = run_grill("discard", session_id=frozen["session_id"], store=store)
        assert changed["pass"] is False
        assert discarded["pass"] is False

    def test_atomic_store_survives_concurrent_idempotent_replays(self, store):
        started = _start(store, [_decision("scope")])

        def submit():
            return _answer(
                store,
                started,
                "scope",
                answer="same",
                request_id="same-request",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: submit(), range(32)))

        status = run_grill("status", session_id=started["session_id"], store=store)
        assert all("error" not in result for result in results)
        assert status["decisions"][0]["answer"] == "same"
        assert status["revision"] == 2
