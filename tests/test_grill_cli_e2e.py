"""Black-box CLI closure test using a real mixed-language index."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "grill_robotics"


def _run_cli(environment, *arguments, check=True):
    completed = subprocess.run(
        [sys.executable, "-m", "src.cli", *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"CLI failed ({completed.returncode}): {completed.stderr}\n{completed.stdout}"
        )
    return completed, json.loads(completed.stdout)


@pytest.mark.integration
def test_real_cli_scan_grill_freeze_plan_gate_validate_and_tamper(tmp_path):
    index_dir = tmp_path / "index"
    state_dir = tmp_path / "state"
    environment = {
        **os.environ,
        "FLYTO_INDEX_DIR": str(index_dir),
        "FLYTO_INDEXER_GRILL_DIR": str(state_dir),
        "FLYTO_AUTO_REINDEX": "0",
    }

    _, scan = _run_cli(
        environment,
        "scan",
        str(FIXTURE),
        "--full",
        "--name",
        "grill-robotics",
        "--output",
        str(index_dir),
    )
    assert scan["files_scanned"] == 3
    assert scan["errors"] == 0

    _, started = _run_cli(
        environment,
        "task",
        "grill",
        "--grill-action",
        "start",
        "--description",
        "Compose blue, yellow, and purple routes safely",
        "--project",
        "grill-robotics",
        "--decisions",
        str(FIXTURE / "decisions.json"),
        "--locale",
        "und",
    )
    session_id = started["session_id"]
    assert started["resolved_from_code"] == [
        "estop_implementation",
        "adapter_contract",
    ]
    fact_paths = {
        evidence["path"]
        for decision in started["decisions"]
        if decision["kind"] == "fact"
        for evidence in decision["evidence"]
    }
    assert {"safety.c", "adapter.ts"}.issubset(fact_paths)

    incomplete_process, incomplete = _run_cli(
        environment,
        "task",
        "grill",
        "--grill-action",
        "freeze",
        "--grill-session-id",
        session_id,
        check=False,
    )
    assert incomplete_process.returncode == 2
    assert incomplete["pass"] is False
    assert incomplete["reason_codes"] == ["DECISIONS_INCOMPLETE"]

    for decision_id in [
        "execution_policy",
        "route_policy",
        "verification_evidence",
    ]:
        _, answered = _run_cli(
            environment,
            "task",
            "grill",
            "--grill-action",
            "answer",
            "--grill-session-id",
            session_id,
            "--decision-id",
            decision_id,
            "--accept-recommendation",
            "--request-id",
            f"cli-{decision_id}",
        )
    assert answered["readiness"]["ready_to_freeze"] is True

    _, frozen = _run_cli(
        environment,
        "task",
        "grill",
        "--grill-action",
        "freeze",
        "--grill-session-id",
        session_id,
    )
    assert frozen["pass"] is True
    assert frozen["contract"]["fingerprint"]

    _, plan = _run_cli(
        environment,
        "task",
        "plan",
        "--description",
        "Add a safe composed route workflow",
        "--intent",
        "feature",
        "--project",
        "grill-robotics",
        "--grill-session-id",
        session_id,
        "--target",
        "controller.py",
        "--target",
        "adapter.ts",
        "--target",
        "safety.c",
    )
    contract_path = tmp_path / "task-contract.json"
    contract_path.write_text(json.dumps(plan), encoding="utf-8")

    _, gate = _run_cli(
        environment,
        "task",
        "gate",
        "--task-contract",
        str(contract_path),
        "--current-state",
        "{}",
        "--next-phase",
        "inspect",
    )
    assert gate["pass"] is True

    _, validation = _run_cli(
        environment,
        "task",
        "validate",
        "--project",
        str(ROOT),
        "--task-contract",
        str(contract_path),
        "--test-path",
        "tests/test_grill.py tests/test_grill_real_data.py",
    )
    assert validation["overall"] == "pass"
    assert validation["ruff"]["status"] == "skipped"
    assert validation["ruff"]["scope"] == "task_targets"
    assert validation["ruff"]["targets"] == []
    assert validation["ruff"]["output"] == (
        "No existing Python targets declared by task contract"
    )
    assert validation["pytest"]["status"] == "pass"
    assert validation["pytest"]["passed"] > 0
    assert validation["decision_contract_validation"]["pass"] is True
    assert validation["decision_conformance"]["pass"] is True
    assert validation["outcome_learning"]["status"] == "recorded"

    tampered = deepcopy(plan)
    tampered["decision_contract"]["decisions"][0]["answer"] = "invented"
    tampered_path = tmp_path / "tampered-contract.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    tamper_process, tamper_gate = _run_cli(
        environment,
        "task",
        "gate",
        "--task-contract",
        str(tampered_path),
        "--current-state",
        "{}",
        "--next-phase",
        "inspect",
        check=False,
    )
    assert tamper_process.returncode == 2
    assert tamper_gate["reason_codes"] == ["DECISION_CONTRACT_TAMPERED"]
