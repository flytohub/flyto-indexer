"""Stress, security, and adversarial regression tests for Grill."""

import os
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from src.tools.grill import (
    GrillSessionStore,
    run_grill,
    validate_decision_contract,
)


@pytest.fixture
def store(tmp_path):
    return GrillSessionStore(tmp_path / "deep-state")


def _node(decision_id, **overrides):
    node = {
        "id": decision_id,
        "kind": "decision",
        "severity": "high",
        "question": f"Question {decision_id}?",
        "recommendation": f"Recommendation {decision_id}",
    }
    node.update(overrides)
    return node


def _start(store, decisions=None, **kwargs):
    return run_grill(
        "start",
        description=kwargs.pop("description", "Adversarial decision test"),
        decisions=decisions,
        store=store,
        **kwargs,
    )


def _answer(store, session_id, decision_id, answer):
    return run_grill(
        "answer",
        session_id=session_id,
        decision_id=decision_id,
        answer=answer,
        request_id=f"{decision_id}-{answer}",
        store=store,
    )


def _process_answer(root, session_id, decision_id, start_event, result_queue):
    start_event.wait(timeout=10)
    result_queue.put(
        _answer(
            GrillSessionStore(Path(root)),
            session_id,
            decision_id,
            f"process-{decision_id}",
        )
    )


@pytest.mark.stress
def test_concurrent_distinct_frontier_answers_do_not_lose_updates(store):
    started = _start(
        store,
        [_node("a"), _node("b"), _node("c"), _node("d")],
        mode="batch",
    )

    def submit(decision_id):
        return _answer(store, started["session_id"], decision_id, decision_id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, ["a", "b", "c", "d"]))

    status = run_grill("status", session_id=started["session_id"], store=store)
    assert all("error" not in result for result in results)
    assert {node["id"] for node in status["decisions"] if node["status"] == "resolved"} == {
        "a",
        "b",
        "c",
        "d",
    }
    assert status["revision"] == 5


@pytest.mark.stress
@pytest.mark.skipif(os.name != "posix", reason="cross-process flock is POSIX-only")
def test_cross_process_answers_do_not_lose_updates(store):
    started = _start(
        store,
        [_node("a"), _node("b"), _node("c"), _node("d")],
        mode="batch",
    )
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_process_answer,
            args=(
                str(store.root),
                started["session_id"],
                decision_id,
                start_event,
                result_queue,
            ),
        )
        for decision_id in ["a", "b", "c", "d"]
    ]
    for process in processes:
        process.start()
    start_event.set()
    results = [result_queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)

    assert all(process.exitcode == 0 for process in processes)
    assert all("error" not in result for result in results)
    status = run_grill("status", session_id=started["session_id"], store=store)
    assert {node["id"] for node in status["decisions"] if node["status"] == "resolved"} == {
        "a",
        "b",
        "c",
        "d",
    }
    assert status["revision"] == 5


@pytest.mark.fuzz
@pytest.mark.parametrize(
    "question,answer",
    [
        ("遇到人、病床或低信心時怎麼辦？", "立即停止；不要輸出 PWM。"),
        ("¿Qué ocurre si falla el adaptador?", "Fallar de forma segura."),
        ("障害物を検出した場合は？", "停止して証拠を保存する。"),
        ("Что делать при потере связи?", "Остановиться."),
        ("<script>alert('x')</script>", "${{ secrets.DO_NOT_EVALUATE }} `rm -rf /`"),
        ("emoji 🤖🛑", "blue → yellow → purple"),
    ],
)
def test_arbitrary_unicode_and_hostile_text_round_trip_without_execution(
    store, question, answer
):
    started = _start(
        store,
        [
            {
                "id": "policy",
                "question": question,
                "recommendation": "Keep as inert data.",
                "severity": "critical",
            }
        ],
        locale="und",
    )
    answered = _answer(store, started["session_id"], "policy", answer)
    resumed = run_grill("status", session_id=started["session_id"], store=store)

    assert answered["decisions"][0]["answer"] == answer
    assert resumed["decisions"][0]["question"] == question


def test_repository_resolver_failure_is_evidence_not_a_human_question(store):
    def failing_resolver(query, project):
        raise RuntimeError("index unavailable " + ("x" * 500))

    result = _start(
        store,
        [
            _node(
                "fact",
                kind="fact",
                severity="critical",
                evidence_queries=["CapabilityRegistry"],
            )
        ],
        fact_resolver=failing_resolver,
    )

    assert result["next_question"] is None
    assert result["repository_actions"][0]["decision_id"] == "fact"
    assert len(result["repository_actions"][0]["errors"][0]) <= 300
    frozen = run_grill(
        "freeze",
        session_id=result["session_id"],
        fact_resolver=failing_resolver,
        store=store,
    )
    assert frozen["pass"] is False
    assert frozen["required_actions"] == ["fact"]


def test_repository_evidence_is_capped_and_compacted(store):
    def noisy_resolver(query, project):
        return {
            "results": [
                {
                    "symbol_id": f"project:src/file{i}.py:function:symbol{i}",
                    "path": f"src/file{i}.py",
                    "line": i,
                    "score": 100 - i,
                    "summary": "summary",
                    "content": "secret implementation body",
                    "unbounded": "x" * 10000,
                }
                for i in range(100)
            ]
        }

    result = _start(
        store,
        [_node("fact", kind="fact", evidence_queries=["symbol"])],
        fact_resolver=noisy_resolver,
    )
    evidence = result["decisions"][0]["evidence"]

    assert len(evidence) == 5
    assert set(evidence[0]) == {
        "query",
        "symbol_id",
        "name",
        "type",
        "path",
        "line",
        "score",
        "summary",
        "match",
    }


def test_session_and_directory_permissions_are_private_on_posix(store):
    started = _start(store, [_node("scope")])
    session_path = store.root / f"{started['session_id']}.json"

    if os.name == "posix":
        assert store.root.stat().st_mode & 0o777 == 0o700
        assert session_path.stat().st_mode & 0o777 == 0o600


def test_invalid_ids_oversize_inputs_and_invalid_options_fail_closed(store):
    traversal = _start(store, [_node("../scope")])
    oversized = _start(
        store,
        [_node("scope", question="q" * 4001)],
    )
    options = _start(
        store,
        [
            _node(
                "scope",
                options=[
                    {"id": "same", "label": "one"},
                    {"id": "same", "label": "two"},
                ],
            )
        ],
    )

    assert traversal["pass"] is False
    assert oversized["pass"] is False
    assert "duplicate option ids" in options["error"]


def test_default_tree_runs_to_a_frozen_contract(store):
    session = _start(store, decisions=None)
    answered_ids = []
    while session["next_question"]:
        decision_id = session["next_question"]["id"]
        answered_ids.append(decision_id)
        session = run_grill(
            "answer",
            session_id=session["session_id"],
            decision_id=decision_id,
            accept_recommendation=True,
            request_id=f"default-{decision_id}",
            store=store,
        )

    frozen = run_grill("freeze", session_id=session["session_id"], store=store)
    assert answered_ids == ["outcome", "compatibility", "failure_policy", "verification"]
    assert frozen["pass"] is True
    assert frozen["readiness"]["score"] == 100


def test_every_contract_shape_mutation_fails_fingerprint_validation(store):
    started = _start(store, [_node("scope", severity="critical")])
    answered = _answer(store, started["session_id"], "scope", "bounded")
    frozen = run_grill("freeze", session_id=answered["session_id"], store=store)
    contract = frozen["contract"]

    mutations = []
    changed_answer = deepcopy(contract)
    changed_answer["decisions"][0]["answer"] = "changed"
    mutations.append(changed_answer)
    removed_decision = deepcopy(contract)
    removed_decision["decisions"] = []
    mutations.append(removed_decision)
    changed_readiness = deepcopy(contract)
    changed_readiness["readiness"]["score"] = 0
    mutations.append(changed_readiness)
    changed_project = deepcopy(contract)
    changed_project["project"] = "another-project"
    mutations.append(changed_project)

    for mutated in mutations:
        result = validate_decision_contract({"decision_contract": mutated})
        assert result["pass"] is False
        assert result["reason_codes"] == ["DECISION_CONTRACT_TAMPERED"]


@pytest.mark.stress
def test_many_sessions_remain_isolated_and_resumable(store):
    sessions = [
        _start(store, [_node("scope")], description=f"session {index}")
        for index in range(50)
    ]
    for index, session in enumerate(sessions):
        _answer(store, session["session_id"], "scope", f"answer-{index}")

    resumed = [
        run_grill("status", session_id=session["session_id"], store=store)
        for session in sessions
    ]
    assert len({session["session_id"] for session in resumed}) == 50
    assert [
        session["decisions"][0]["answer"] for session in resumed
    ] == [f"answer-{index}" for index in range(50)]
