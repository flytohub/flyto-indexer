#!/usr/bin/env python3
"""Run the committed, offline scanner quality and latency corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analyzer.taint import TaintAnalyzer
from src.engine import IndexEngine

DEFAULT_CORPUS = Path(__file__).resolve().parent / "fixture" / "corpus"


def _stable_fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_manifest(corpus_root: Path) -> dict[str, Any]:
    manifest_path = corpus_root / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
        raise ValueError(f"Unsupported corpus manifest: {manifest_path}")
    return data


def _evaluate_case(corpus_root: Path, case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case["id"])
    project_root = (corpus_root / str(case["path"])).resolve()
    if not project_root.is_dir() or corpus_root.resolve() not in project_root.parents:
        raise ValueError(f"Invalid corpus path for {case_id}: {project_root}")

    tracemalloc.start()
    started_at = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"flyto-eval-{case_id}-") as index_dir:
        index_path = Path(index_dir)
        engine = IndexEngine(case_id, project_root, index_dir=index_path)
        scan_result = engine.scan(incremental=False)
        index = json.loads((index_path / "index.json").read_text(encoding="utf-8"))
        flows = TaintAnalyzer(project_root, index=index).analyze()
    elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    findings = [flow.to_dict() for flow in flows if not flow.sanitized]
    findings.sort(key=lambda item: (
        item.get("finding_id", ""),
        item.get("category", ""),
    ))
    actual_counts = Counter(str(item.get("category", "")) for item in findings)
    expected = {
        str(category): int(count)
        for category, count in (case.get("expected") or {}).items()
    }
    missing = {
        category: max(0, count - actual_counts.get(category, 0))
        for category, count in expected.items()
        if actual_counts.get(category, 0) < count
    }
    unexpected = {
        category: count - expected.get(category, 0)
        for category, count in actual_counts.items()
        if count > expected.get(category, 0)
    }
    max_path_hops = max((len(item.get("path") or []) for item in findings), default=0)
    minimum_path_hops = int(case.get("minimum_path_hops") or 0)
    path_proof_pass = max_path_hops >= minimum_path_hops

    stable_evidence = {
        "case_id": case_id,
        "expected": expected,
        "actual": dict(sorted(actual_counts.items())),
        "finding_ids": [item.get("finding_id") for item in findings],
        "max_path_hops": max_path_hops,
    }
    return {
        **stable_evidence,
        "pass": not missing and not unexpected and path_proof_pass,
        "missing": missing,
        "unexpected": unexpected,
        "minimum_path_hops": minimum_path_hops,
        "path_proof_pass": path_proof_pass,
        "files_scanned": scan_result.get("files_scanned", 0),
        "scan_errors": scan_result.get("errors", 0),
        "latency_ms": elapsed_ms,
        "peak_memory_mb": round(peak_bytes / 1024 / 1024, 3),
        "evidence_fingerprint": _stable_fingerprint(stable_evidence),
    }


def evaluate_corpus(corpus_root: str | Path = DEFAULT_CORPUS) -> dict[str, Any]:
    """Evaluate every committed case and return deterministic quality evidence."""
    root = Path(corpus_root).resolve()
    manifest = _read_manifest(root)
    cases = [_evaluate_case(root, case) for case in manifest["cases"]]

    true_positives = sum(
        min(result["actual"].get(category, 0), expected_count)
        for result in cases
        for category, expected_count in result["expected"].items()
    )
    false_negatives = sum(sum(result["missing"].values()) for result in cases)
    false_positives = sum(sum(result["unexpected"].values()) for result in cases)
    negative_cases = [result for result in cases if not result["expected"]]
    negative_case_failures = sum(1 for result in negative_cases if result["actual"])
    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives
    precision = true_positives / precision_denominator if precision_denominator else 1.0
    recall = true_positives / recall_denominator if recall_denominator else 1.0
    false_positive_rate = (
        negative_case_failures / len(negative_cases) if negative_cases else 0.0
    )
    stable_summary = {
        "schema_version": 1,
        "case_fingerprints": [
            {"case_id": result["case_id"], "fingerprint": result["evidence_fingerprint"]}
            for result in cases
        ],
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }
    return {
        "schema_version": 1,
        "pass": all(result["pass"] and result["scan_errors"] == 0 for result in cases),
        "corpus": str(root),
        "summary": {
            "cases": len(cases),
            "passed": sum(1 for result in cases if result["pass"]),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "false_positive_rate": round(false_positive_rate, 6),
            "latency_ms": round(sum(result["latency_ms"] for result in cases), 2),
            "max_case_latency_ms": max(
                (result["latency_ms"] for result in cases),
                default=0,
            ),
            "peak_memory_mb": max(
                (result["peak_memory_mb"] for result in cases),
                default=0,
            ),
        },
        "evidence_fingerprint": _stable_fingerprint(stable_summary),
        "cases": cases,
    }


def _threshold_pass(result: dict[str, Any], args: argparse.Namespace) -> bool:
    summary = result["summary"]
    return (
        result["pass"]
        and summary["precision"] >= args.min_precision
        and summary["recall"] >= args.min_recall
        and summary["false_positive_rate"] <= args.max_false_positive_rate
        and summary["max_case_latency_ms"] <= args.max_case_latency_ms
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline Flyto Indexer scanner evaluation corpus",
    )
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--min-precision", type=float, default=1.0)
    parser.add_argument("--min-recall", type=float, default=1.0)
    parser.add_argument("--max-false-positive-rate", type=float, default=0.0)
    parser.add_argument("--max-case-latency-ms", type=float, default=2500.0)
    args = parser.parse_args()

    result = evaluate_corpus(args.corpus)
    result["thresholds"] = {
        "min_precision": args.min_precision,
        "min_recall": args.min_recall,
        "max_false_positive_rate": args.max_false_positive_rate,
        "max_case_latency_ms": args.max_case_latency_ms,
    }
    result["threshold_pass"] = _threshold_pass(result, args)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    if args.json:
        print(rendered)
    else:
        summary = result["summary"]
        print(
            "Flyto Indexer evaluation: "
            f"{summary['passed']}/{summary['cases']} cases, "
            f"precision={summary['precision']:.3f}, "
            f"recall={summary['recall']:.3f}, "
            f"FPR={summary['false_positive_rate']:.3f}, "
            f"max={summary['max_case_latency_ms']:.2f}ms, "
            f"fingerprint={result['evidence_fingerprint'][:16]}"
        )
    return 1 if args.check and not result["threshold_pass"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
