"""Real-index closed-loop test for Grill across Python, TypeScript, and C."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from src import index_store
from src.engine import IndexEngine
from src.tools.search import search_by_keyword
from src.tools.smart import smart_task


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "grill_robotics"


@pytest.fixture
def indexed_robotics(tmp_path, monkeypatch):
    index_dir = tmp_path / "robotics-index"
    engine = IndexEngine("grill-robotics", FIXTURE_ROOT, index_dir)
    scan = engine.scan(incremental=False)

    monkeypatch.setenv("FLYTO_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("FLYTO_INDEXER_GRILL_DIR", str(tmp_path / "grill-state"))
    index_store.invalidate_caches()
    try:
        yield scan
    finally:
        index_store.invalidate_caches()


def _answer(session_id, decision_id):
    return smart_task(
        action="grill",
        grill_action="answer",
        grill_session_id=session_id,
        decision_id=decision_id,
        accept_recommendation=True,
        request_id=f"answer-{decision_id}",
    )


@pytest.mark.integration
def test_real_multilanguage_index_to_frozen_plan_and_tamper_gate(indexed_robotics):
    """Exercise the production resolver, persistence, plan attachment, and gate."""
    assert indexed_robotics["errors"] == 0
    assert indexed_robotics["files_scanned"] == 3

    python_hits = search_by_keyword("CapabilityRegistry", project="grill-robotics")
    typescript_hits = search_by_keyword("RobotAdapter", project="grill-robotics")
    c_hits = search_by_keyword("emergency_stop", project="grill-robotics")
    assert python_hits["results"][0]["path"] == "controller.py"
    assert any(hit["path"] == "adapter.ts" for hit in typescript_hits["results"])
    assert c_hits["results"][0]["path"] == "safety.c"

    decisions = json.loads(
        (FIXTURE_ROOT / "decisions.json").read_text(encoding="utf-8")
    )
    started = smart_task(
        action="grill",
        grill_action="start",
        description="Compose blue, yellow, and purple robot routes safely",
        project="grill-robotics",
        decisions=decisions,
        locale="und",
    )

    assert started["resolved_from_code"] == [
        "estop_implementation",
        "adapter_contract",
    ]
    assert started["next_question"]["id"] == "execution_policy"
    assert "LLM" in started["next_question"]["question"]
    assert started["decisions"][-1]["question"].startswith("Welche")

    session_id = started["session_id"]
    execution = _answer(session_id, "execution_policy")
    route = _answer(session_id, "route_policy")
    verification = _answer(session_id, "verification_evidence")
    assert execution["next_question"]["id"] == "route_policy"
    assert route["next_question"]["id"] == "verification_evidence"
    assert verification["readiness"]["ready_to_freeze"] is True

    frozen = smart_task(
        action="grill",
        grill_action="freeze",
        grill_session_id=session_id,
    )
    assert frozen["pass"] is True
    assert frozen["status"] == "frozen"

    plan = smart_task(
        action="plan",
        description="Add a safe composed route workflow",
        targets=["controller.py", "adapter.ts", "safety.c"],
        intent="feature",
        project="grill-robotics",
        grill_session_id=session_id,
    )
    assert plan["decision_contract"]["status"] == "frozen"
    assert plan["task_profile"]["decision_session_id"] == session_id

    valid_gate = smart_task(
        action="gate",
        task_contract=plan,
        next_phase="inspect",
        current_state={},
    )
    assert valid_gate["pass"] is True

    tampered = deepcopy(plan)
    tampered["decision_contract"]["decisions"][0]["answer"] = {
        "source": "caller",
        "summary": "invented",
    }
    tamper_gate = smart_task(
        action="gate",
        task_contract=tampered,
        next_phase="inspect",
        current_state={},
    )
    assert tamper_gate["pass"] is False
    assert tamper_gate["reason_codes"] == ["DECISION_CONTRACT_TAMPERED"]
