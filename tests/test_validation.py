"""Tests for post-change validation helpers."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools import validation


def test_run_pytest_without_explicit_path_defers_to_pytest_config(
    monkeypatch, tmp_path
):
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="1 passed in 0.01s", stderr="")

    monkeypatch.setattr(validation.subprocess, "run", fake_run)

    result = validation._run_pytest(str(tmp_path))

    assert result["status"] == "pass"
    assert captured["cmd"] == [
        sys.executable,
        "-m",
        "pytest",
        "-x",
        "--tb=short",
        "-q",
    ]


def test_pytest_timeout_is_configurable_and_bounded(monkeypatch):
    monkeypatch.setenv("FLYTO_INDEXER_PYTEST_TIMEOUT", "45")
    assert validation._pytest_timeout_seconds() == 45

    monkeypatch.setenv("FLYTO_INDEXER_PYTEST_TIMEOUT", "not-a-number")
    assert (
        validation._pytest_timeout_seconds()
        == validation.DEFAULT_PYTEST_TIMEOUT_SECONDS
    )

    monkeypatch.setenv("FLYTO_INDEXER_PYTEST_TIMEOUT", "1")
    assert validation._pytest_timeout_seconds() == validation.MIN_PYTEST_TIMEOUT_SECONDS

    monkeypatch.setenv("FLYTO_INDEXER_PYTEST_TIMEOUT", "99999")
    assert validation._pytest_timeout_seconds() == validation.MAX_PYTEST_TIMEOUT_SECONDS


def test_run_pytest_honors_configured_testpaths(tmp_path):
    tests_dir = tmp_path / "tests"
    examples_dir = tmp_path / "examples"
    tests_dir.mkdir()
    examples_dir.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        encoding="utf-8",
    )
    (tests_dir / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    (examples_dir / "test_example.py").write_text(
        "def test_example(missing_fixture):\n    assert missing_fixture\n",
        encoding="utf-8",
    )

    result = validation._run_pytest(str(tmp_path))

    assert result["status"] == "pass"
    assert result["passed"] == 1


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
        sys.executable,
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


def test_validate_changes_rejects_unknown_explicit_project_with_one_index_root(
    monkeypatch, tmp_path
):
    indexed_root = tmp_path / "indexed"
    indexed_root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        validation,
        "load_index",
        lambda: {"project_roots": {"indexed-project": str(indexed_root)}},
    )
    monkeypatch.setattr(
        validation,
        "_run_ruff",
        lambda _root: (_ for _ in ()).throw(
            AssertionError("validation must not run against the wrong project")
        ),
    )

    result = validation.validate_changes(project="missing-project")

    assert result == {
        "error": "Project 'missing-project' not found. Available: indexed-project",
    }
