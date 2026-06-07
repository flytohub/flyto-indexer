"""Tests for the no-dependency verification gate."""

import os
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cli import cmd_verify, cmd_verify_workspace
from src.verify import format_verification, format_workspace_verification, run_verification, run_workspace_verification


def _write_project(root: Path, *, dependency: str = "", project_name: str = "demo"):
    (root / "src").mkdir(parents=True)
    deps = f'"{dependency}"' if dependency else ""
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f"name = \"{project_name}\"\n"
        "requires-python = \">=3.11\"\n"
        f"dependencies = [{deps}]\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "Use flyto-indexer. Run flyto-index verify before finishing.\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(".flyto-index/\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# Demo\n\n## Installation\n\nRun setup.\n\n## Usage\n\nRun app.\n\n## API\n\nN/A.\n",
        encoding="utf-8",
    )
    (root / "src" / "auth.py").write_text(
        "def handle_auth(user):\n"
        "    return user == 'admin'\n",
        encoding="utf-8",
    )
    (root / "src" / "routes.py").write_text(
        "from auth import handle_auth\n\n"
        "def get_routes():\n"
        "    if handle_auth('admin'):\n"
        "        return ['/dashboard']\n"
        "    return ['/']\n",
        encoding="utf-8",
    )


def test_run_verification_closes_core_loops(tmp_path):
    _write_project(tmp_path)

    result = run_verification(tmp_path, full_scan=True, query="handle_auth")

    assert result["pass"] is True
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["runtime_dependencies"]["status"] == "pass"
    assert checks["index_integrity"]["status"] == "pass"
    assert checks["context_loop"]["status"] == "pass"
    assert checks["impact_loop"]["status"] == "pass"
    assert checks["weak_scan_secrets"]["status"] == "pass"
    assert checks["agent_hygiene"]["status"] == "pass"


def test_run_verification_fails_runtime_dependencies(tmp_path):
    _write_project(tmp_path, dependency="requests>=2", project_name="flyto-indexer")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert result["pass"] is False
    assert checks["runtime_dependencies"]["status"] == "fail"


def test_run_verification_allows_dependencies_for_other_projects(tmp_path):
    _write_project(tmp_path, dependency="requests>=2", project_name="app")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert result["pass"] is True
    assert checks["runtime_dependencies"]["status"] == "pass"
    assert checks["runtime_dependencies"]["metrics"]["dependency_count"] == 1


def test_format_verification_includes_summary(tmp_path):
    _write_project(tmp_path)
    result = run_verification(tmp_path, full_scan=True)

    output = format_verification(result)

    assert "Flyto Verify" in output
    assert "Checks:" in output


def test_cmd_verify_json(tmp_path):
    _write_project(tmp_path)

    result = cmd_verify(Namespace(
        path=str(tmp_path),
        full_scan=True,
        query="handle_auth",
        symbol=None,
        strict=False,
        baseline=None,
        regression_only=False,
        save_baseline=None,
        as_json=True,
    ))

    assert result["pass"] is True


def test_cmd_verify_saves_baseline(tmp_path):
    _write_project(tmp_path)
    baseline = tmp_path / "baseline.json"

    result = cmd_verify(Namespace(
        path=str(tmp_path),
        full_scan=True,
        query="handle_auth",
        symbol=None,
        strict=False,
        baseline=None,
        regression_only=False,
        save_baseline=str(baseline),
        as_json=True,
    ))

    assert result["pass"] is True
    assert baseline.exists()


def test_verify_accepts_git_info_exclude_for_index_ignore(tmp_path):
    _write_project(tmp_path)
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    info_exclude = tmp_path / ".git" / "info" / "exclude"
    info_exclude.write_text(info_exclude.read_text() + "\n.flyto-index/\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("", encoding="utf-8")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["generated_index_ignore"]["status"] == "pass"


def test_verify_checks_index_ignore_without_agent_instructions(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "AGENTS.md").unlink()

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["agent_hygiene"]["status"] == "warn"
    assert checks["generated_index_ignore"]["status"] == "pass"


def test_regression_only_allows_existing_warning(tmp_path):
    _write_project(tmp_path)
    result = run_verification(tmp_path, full_scan=True)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "AGENTS.md").unlink()

    current = run_verification(
        tmp_path,
        full_scan=True,
        baseline_path=baseline,
        regression_only=True,
    )

    checks = {check["name"]: check for check in current["checks"]}
    assert current["pass"] is False
    assert checks["regression_gate"]["status"] == "fail"


def test_regression_only_ignores_unchanged_warning(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    result = run_verification(tmp_path, full_scan=True)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    current = run_verification(
        tmp_path,
        full_scan=True,
        baseline_path=baseline,
        regression_only=True,
    )

    checks = {check["name"]: check for check in current["checks"]}
    assert current["pass"] is True
    assert checks["agent_hygiene"]["status"] == "warn"
    assert checks["regression_gate"]["status"] == "pass"


def test_workspace_verification_aggregates_projects(tmp_path):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    _write_project(project_a)
    _write_project(project_b)

    result = run_workspace_verification(
        tmp_path,
        project_paths=[project_a, project_b],
        full_scan=True,
    )

    assert result["pass"] is True
    assert result["summary"]["projects"] == 2
    assert len(result["projects"]) == 2
    assert "Flyto Workspace Verify" in format_workspace_verification(result)


def test_cmd_verify_workspace_json(tmp_path):
    project = tmp_path / "project"
    _write_project(project)

    result = cmd_verify_workspace(Namespace(
        path=str(tmp_path),
        projects=[str(project)],
        full_scan=True,
        strict=False,
        baseline_dir=None,
        regression_only=False,
        as_json=True,
    ))

    assert result["pass"] is True
    assert result["summary"]["projects"] == 1
