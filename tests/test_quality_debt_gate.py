from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "scripts" / "check_quality_debt.py"
SPEC = importlib.util.spec_from_file_location("check_quality_debt", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _snapshot(*, ruff: int, mypy: int) -> dict:
    return {
        "tools": {"ruff": "ruff 1", "mypy": "mypy 1"},
        "ruff": {"codes": {"F401": ruff}, "total": ruff},
        "mypy": {"codes": {"arg-type": mypy}, "total": mypy},
    }


def test_quality_debt_gate_accepts_exact_reviewed_baseline():
    baseline = _snapshot(ruff=4, mypy=3)
    assert MODULE.compare_debt(baseline, baseline) == []


def test_quality_debt_gate_blocks_regression_and_unlocked_improvement():
    baseline = _snapshot(ruff=4, mypy=3)
    regression = MODULE.compare_debt(_snapshot(ruff=5, mypy=3), baseline)
    improvement = MODULE.compare_debt(_snapshot(ruff=4, mypy=2), baseline)

    assert regression == ["ruff:F401 increased 4 -> 5"]
    assert improvement == [
        "mypy:arg-type improved 3 -> 2; update the baseline to lock it in"
    ]
