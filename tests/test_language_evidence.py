from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "scripts" / "check_language_evidence.py"
SPEC = importlib.util.spec_from_file_location("check_language_evidence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _language(level: str) -> dict:
    return {
        "label": "Demo",
        "indexing": "AST",
        "relationship_analysis": "graph",
        "security_analysis": "taint",
        "evidence_level": level,
        "limitations": "dynamic code",
    }


def test_language_evidence_requires_balanced_cases_for_gated_claim():
    config = {"languages": {"python": _language("gated")}}
    positive_only = {"cases": [{"language": "python", "kind": "positive"}]}
    balanced = {
        "cases": [
            {"language": "python", "kind": "positive"},
            {"language": "python", "kind": "negative"},
        ]
    }

    assert MODULE.validate_evidence(config, positive_only) == [
        "python claims gated evidence without positive and negative cases"
    ]
    assert MODULE.validate_evidence(config, balanced) == []


def test_language_evidence_rejects_undocumented_benchmark_language():
    config = {"languages": {"python": _language("indexing-only")}}
    manifest = {"cases": [{"language": "go", "kind": "positive"}]}

    messages = MODULE.validate_evidence(config, manifest)

    assert "benchmark language is undocumented: go" in messages
