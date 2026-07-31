from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "scripts" / "reproduce_impact_case.py"
SPEC = importlib.util.spec_from_file_location("reproduce_impact_case", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_public_case_receipt_proves_transitive_files_beyond_text_search():
    text_matches = [{"path": "backend/app/utils.py", "line": 25, "text": "definition"}]
    scan = {"files_scanned": 10, "symbols_found": 20, "errors": 0}
    impact = {
        "total_direct_references": 1,
        "impact_chain": [{
            "depth": 2,
            "affected": [
                {
                    "path": path,
                    "name": name,
                    "type": "function",
                }
                for path, name in (
                    item.split("::", 1)
                    for item in sorted(MODULE.REQUIRED_TRANSITIVE_FUNCTIONS)
                )
            ],
        }],
    }

    evidence = MODULE.build_evidence(text_matches, scan, impact)

    assert evidence["proof"]["pass"] is True
    assert len(evidence["proof"]["functions_in_files_missed_by_text_search"]) == 4
    assert len(evidence["evidence_fingerprint"]) == 64


def test_public_case_snapshot_comparison_fails_closed():
    receipt = {"proof": {"pass": True}, "evidence_fingerprint": "one"}
    assert MODULE.compare_snapshot(receipt, receipt) == []
    assert MODULE.compare_snapshot(receipt, {**receipt, "evidence_fingerprint": "two"}) == [
        "public proof snapshot is stale"
    ]
