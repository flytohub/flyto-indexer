from __future__ import annotations

import importlib.util
import subprocess
import sys
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


def test_quality_debt_gate_uses_active_python_when_path_has_no_tools(monkeypatch):
    monkeypatch.setenv("PATH", "")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "--version" in command:
            module = command[2]
            return subprocess.CompletedProcess(command, 0, f"{module} 1\n", "")
        if command[2] == "ruff":
            return subprocess.CompletedProcess(command, 0, "[]", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(MODULE, "_run", fake_run)

    debt = MODULE.collect_debt()

    assert debt["tools"] == {"ruff": "ruff 1", "mypy": "mypy 1"}
    assert commands
    assert all(command[:2] == [sys.executable, "-m"] for command in commands)
