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
    assert first_result["summary"]["p50_case_latency_ms"] > 0
    assert first_result["summary"]["p95_case_latency_ms"] > 0
    assert (
        first_result["summary"]["p95_case_latency_ms"]
        <= first_result["thresholds"]["max_p95_latency_ms"]
    )
    assert (
        first_result["summary"]["p95_case_latency_ms"]
        <= first_result["summary"]["max_case_latency_ms"]
    )
    assert first_result["summary"]["cases"] >= 200
    assert first_result["summary"]["canonical_cases"] >= 40
    assert first_result["summary"]["mutation_cases"] >= 150
    assert (
        first_result["summary"]["unique_source_fingerprints"]
        == first_result["summary"]["cases"]
    )
    assert first_result["summary"]["positive_cases"] >= 100
    assert first_result["summary"]["negative_cases"] >= 80
    assert set(first_result["summary"]["by_language"]) == {
        "go",
        "javascript",
        "python",
        "typescript",
    }
    assert set(first_result["summary"]["canonical_by_language"]) == {
        "go",
        "javascript",
        "python",
        "typescript",
    }
    for name, language in first_result["summary"]["by_language"].items():
        canonical = first_result["summary"]["canonical_by_language"][name]
        assert canonical["positive_cases"] >= 4
        assert canonical["negative_cases"] >= 4
        assert language["precision"] >= 0.98
        assert language["recall"] >= 0.95
        assert language["false_positive_rate"] <= 0.05
    assert set(first_result["summary"]["by_mutation_dimension"]) == {
        "comment_decoys",
        "identifier_rename",
        "receiver_alias",
        "layout_safe_neighbor",
    }
    assert all(
        count >= 40
        for count in first_result["summary"]["by_mutation_dimension"].values()
    )
    assert first_result["metamorphic"]["pass"] is True
    assert first_result["metamorphic"]["groups"] >= 4
    assert first_result["differential"]["pass"] is True
    assert first_result["differential"]["agreements"] == first_result["summary"]["cases"]
    assert first_result["evidence_fingerprint"] == second_result["evidence_fingerprint"]
    cross_file = next(
        case
        for case in first_result["cases"]
        if case["case_id"] == "python-cross-file-sqli"
    )
    assert cross_file["path_proof_pass"] is True
    direct = next(
        case
        for case in first_result["cases"]
        if case["case_id"] == "python-direct-sqli"
    )
    assert cross_file["actual"] == direct["actual"]
