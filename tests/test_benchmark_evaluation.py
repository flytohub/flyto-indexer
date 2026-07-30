import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_offline_evaluation_corpus_is_precise_reproducible_and_fast():
    command = [
        sys.executable,
        str(ROOT / "benchmarks" / "evaluate.py"),
        "--check",
        "--json",
    ]
    first = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    second = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert first.returncode == 0, first.stderr + first.stdout
    assert second.returncode == 0, second.stderr + second.stdout
    first_result = json.loads(first.stdout)
    second_result = json.loads(second.stdout)
    assert first_result["threshold_pass"] is True
    assert first_result["summary"]["precision"] == 1.0
    assert first_result["summary"]["recall"] == 1.0
    assert first_result["summary"]["false_positive_rate"] == 0.0
    assert first_result["evidence_fingerprint"] == second_result["evidence_fingerprint"]
    cross_file = next(
        case
        for case in first_result["cases"]
        if case["case_id"] == "python-cross-file-sqli"
    )
    assert cross_file["path_proof_pass"] is True
