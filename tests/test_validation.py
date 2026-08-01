"""Tests for post-change validation helpers."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools import validation


def test_run_ruff_defaults_to_repository_wide(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="All checks passed!", stderr="")

    monkeypatch.setattr(validation.subprocess, "run", fake_run)

    result = validation._run_ruff(str(tmp_path))

    assert result["status"] == "pass"
    assert result["scope"] == "repository"
    assert result["targets"] == ["."]
    assert captured["cmd"] == ["ruff", "check", "."]


def test_run_ruff_scopes_existing_python_targets_and_rejects_escape(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    docs = tmp_path / "README.md"
    docs.write_text("docs\n", encoding="utf-8")
    outside = tmp_path.parent / "outside.py"
    outside.write_text("value = 2\n", encoding="utf-8")
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="All checks passed!", stderr="")

    monkeypatch.setattr(validation.subprocess, "run", fake_run)

    result = validation._run_ruff(
        str(tmp_path),
        ["src/app.py", "README.md", "missing.py", "../outside.py"],
    )

    assert result["status"] == "pass"
    assert result["scope"] == "task_targets"
    assert result["targets"] == ["src/app.py"]
    assert captured["cmd"] == ["ruff", "check", "src/app.py"]


def test_run_ruff_skips_docs_only_task_without_repo_fallback(
    monkeypatch,
    tmp_path,
):
    (tmp_path / "README.md").write_text("docs\n", encoding="utf-8")
    monkeypatch.setattr(
        validation.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Ruff must not run for a docs-only task"),
        ),
    )

    result = validation._run_ruff(str(tmp_path), ["README.md"])

    assert result["status"] == "skipped"
    assert result["scope"] == "task_targets"
    assert result["targets"] == []


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


def test_validate_changes_passes_explicit_lint_paths(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    captured = {}

    monkeypatch.setattr(
        validation,
        "load_index",
        lambda: {"project_roots": {"project": str(project_root)}},
    )

    def fake_ruff(root, lint_paths):
        captured["root"] = root
        captured["lint_paths"] = lint_paths
        return {
            "status": "pass",
            "errors": 0,
            "warnings": 0,
            "output": "",
        }

    monkeypatch.setattr(validation, "_run_ruff", fake_ruff)

    result = validation.validate_changes(
        project="project",
        run_tests=False,
        lint_paths=["src/app.py"],
    )

    assert result["overall"] == "pass"
    assert captured == {
        "root": str(project_root),
        "lint_paths": ["src/app.py"],
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
