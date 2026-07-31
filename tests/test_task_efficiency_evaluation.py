"""The fixed task evidence suite is complete, reproducible, and committed."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _run_evaluation() -> dict:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "benchmarks" / "evaluate_task_efficiency.py"),
            "--check",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


def test_fixed_100_scenarios_exceed_threshold_and_match_committed_evidence():
    first = _run_evaluation()
    second = _run_evaluation()
    committed = json.loads(
        (ROOT / "docs" / "evidence" / "task-efficiency-100.json").read_text(encoding="utf-8")
    )

    assert first["total"] == 100
    assert first["passed"] >= 90
    assert first["success_rate"] >= first["required_success_rate"] == 0.9
    assert first["pass"] is True
    assert len(first["scenarios"]) == 100
    assert len({scenario["id"] for scenario in first["scenarios"]}) == 100
    assert first["evidence_fingerprint"] == second["evidence_fingerprint"]
    assert first == committed
