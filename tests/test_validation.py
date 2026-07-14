"""Tests for post-change validation helpers."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools import validation


def test_run_pytest_splits_multiple_test_paths(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout="2 passed in 0.01s", stderr="")

    monkeypatch.setattr(validation.subprocess, "run", fake_run)

    result = validation._run_pytest(str(tmp_path), "tests/test_bm25.py tests/test_verify.py")

    assert result["status"] == "pass"
    assert captured["cmd"] == [
        "python",
        "-m",
        "pytest",
        "tests/test_bm25.py",
        "tests/test_verify.py",
        "-x",
        "--tb=short",
        "-q",
    ]


def test_validate_changes_resolves_relative_project_directory(monkeypatch, tmp_path):
    project_root = tmp_path / "flyto-indexer"
    project_root.mkdir()
    captured = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(validation, "load_index", lambda: {"project_roots": {"flytohub": str(tmp_path)}})
    monkeypatch.setattr(validation, "_run_ruff", lambda root: {"status": "pass", "errors": 0, "warnings": 0, "output": ""})

    def fake_pytest(root, test_path=None):
        captured["root"] = root
        captured["test_path"] = test_path
        return {"status": "pass", "passed": 1, "failed": 0, "errors": 0, "output": ""}

    monkeypatch.setattr(validation, "_run_pytest", fake_pytest)

    result = validation.validate_changes(
        project="flyto-indexer",
        run_tests=True,
        test_path="tests/test_bm25.py tests/test_verify.py",
    )

    assert result["overall"] == "pass"
    assert result["project"] == "flyto-indexer"
    assert result["project_root"] == str(project_root.resolve())
    assert captured == {
        "root": str(project_root.resolve()),
        "test_path": "tests/test_bm25.py tests/test_verify.py",
    }
