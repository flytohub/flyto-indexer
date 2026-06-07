"""Tests for the no-dependency verification gate."""

import os
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cli import cmd_verify, cmd_verify_baseline, cmd_verify_workspace
from src.verify import (
    _check_mcp_runtime_smoke,
    format_verification,
    format_workspace_verification,
    render_report,
    run_verification,
    run_workspace_verification,
)


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
        "Use flyto-indexer. Run search and impact before edits. Run flyto-index verify before finishing.\n",
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


def _write_indexer_ci(root: Path):
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "name: CI\n"
        "jobs:\n"
        "  lint:\n"
        "    steps:\n"
        "      - run: ruff check src/ && mypy src/\n"
        "  test:\n"
        "    steps:\n"
        "      - run: pytest tests/\n"
        "  verify:\n"
        "    steps:\n"
        "      - run: flyto-index verify . --full-scan --report /tmp/verify.sarif --report-format sarif --json\n"
        "  build:\n"
        "    steps:\n"
        "      - run: python -m build\n"
        "      - run: |\n"
        "          python - <<'PY'\n"
        "          runtime_requires = []\n"
        "          assert 'Requires-Dist:'\n"
        "          PY\n"
        "      - run: pip install --no-deps dist/*.whl && flyto-index --help\n",
        encoding="utf-8",
    )


def _write_indexer_package_config(root: Path):
    (root / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
    (root / "NOTICE").write_text("Flyto\n", encoding="utf-8")
    (root / "config" / "rules").mkdir(parents=True)
    (root / "config" / "rules" / "demo.yaml").write_text("rules: []\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[build-system]\n"
        "requires = [\"hatchling\"]\n"
        "build-backend = \"hatchling.build\"\n\n"
        "[project]\n"
        "name = \"flyto-indexer\"\n"
        "requires-python = \">=3.11\"\n"
        "dependencies = []\n"
        "license-files = [\"LICENSE\", \"NOTICE\"]\n\n"
        "[project.scripts]\n"
        "flyto-index = \"flyto_indexer.cli:main\"\n\n"
        "[tool.hatch.build.targets.sdist]\n"
        "include = [\"/src\", \"/config\"]\n\n"
        "[tool.hatch.build.targets.wheel]\n"
        "packages = [\"src\"]\n\n"
        "[tool.hatch.build.targets.wheel.sources]\n"
        "\"src\" = \"flyto_indexer\"\n\n"
        "[tool.hatch.build.targets.wheel.force-include]\n"
        "\"config/rules\" = \"flyto_indexer/config/rules\"\n",
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
        policy=None,
        report=None,
        report_format="json",
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
        policy=None,
        report=None,
        report_format="json",
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
        changed_only=False,
        base="",
        policy=None,
        report=None,
        report_format="json",
        as_json=True,
    ))

    assert result["pass"] is True
    assert result["summary"]["projects"] == 1


def test_verify_policy_budget_fails_named_warning(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    policy = tmp_path / ".flyto-rules.yaml"
    policy.write_text(
        "verify:\n"
        "  warn_as_fail: [agent_hygiene]\n",
        encoding="utf-8",
    )

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["agent_hygiene"]["status"] == "warn"
    assert checks["policy_budget"]["status"] == "fail"
    assert result["pass"] is False


def test_verify_policy_budget_allows_named_warning(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    policy = tmp_path / ".flyto-rules.yaml"
    policy.write_text(
        "verify:\n"
        "  warn_as_fail: ['*']\n"
        "  allow_warn:\n"
        "    - agent_hygiene\n"
        "    - docs_coverage\n",
        "    - ci_closed_loop\n",
        encoding="utf-8",
    )

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["agent_hygiene"]["status"] == "warn"
    assert checks["policy_budget"]["status"] == "pass"
    assert result["pass"] is True


def test_no_external_runtime_and_ci_closed_loop_pass_for_indexer(tmp_path):
    _write_project(tmp_path, project_name="flyto-indexer")
    _write_indexer_ci(tmp_path)

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["no_external_runtime"]["status"] == "pass"
    assert checks["ci_closed_loop"]["status"] == "pass"


def test_package_integrity_passes_for_indexer_config(tmp_path):
    _write_project(tmp_path, project_name="flyto-indexer")
    _write_indexer_ci(tmp_path)
    _write_indexer_package_config(tmp_path)

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["package_integrity"]["status"] == "pass"


def test_baseline_integrity_fails_wrong_project(tmp_path):
    _write_project(tmp_path)
    baseline_result = run_verification(tmp_path, full_scan=True)
    baseline_result["project"] = "other-project"
    baseline_result["metadata"]["project"] = "other-project"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(baseline_result, ensure_ascii=False), encoding="utf-8")

    result = run_verification(tmp_path, full_scan=True, baseline_path=baseline, regression_only=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["baseline_integrity"]["status"] == "fail"
    assert result["pass"] is False


def test_mcp_runtime_smoke_passes_for_repo():
    root = Path(__file__).parent.parent
    checks = []

    def add_check(name, status, summary, *, metrics=None):
        checks.append({"name": name, "status": status, "summary": summary, "metrics": metrics or {}})

    _check_mcp_runtime_smoke(root, add_check)

    by_name = {check["name"]: check for check in checks}
    assert by_name["mcp_runtime_smoke"]["status"] == "pass"


def test_ci_closed_loop_warns_without_verify(tmp_path):
    _write_project(tmp_path, project_name="flyto-indexer")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "name: CI\njobs:\n  test:\n    steps:\n      - run: pytest tests/\n",
        encoding="utf-8",
    )

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["ci_closed_loop"]["status"] == "warn"
    assert "verify" in checks["ci_closed_loop"]["metrics"]["missing"]


def test_change_hygiene_warns_on_high_risk_paths(tmp_path):
    _write_project(tmp_path)
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )
    (tmp_path / ".env.production").write_text("TOKEN=\n", encoding="utf-8")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["change_hygiene"]["status"] == "warn"
    assert ".env.production" in checks["change_hygiene"]["metrics"]["high_risk"]


def test_render_report_formats(tmp_path):
    _write_project(tmp_path)
    result = run_verification(tmp_path, full_scan=True)

    markdown = render_report(result, "markdown")
    junit = render_report(result, "junit")
    sarif = render_report(result, "sarif")

    assert "# Flyto Verify" in markdown
    assert "<testsuite" in junit
    assert '"version": "2.1.0"' in sarif


def test_cmd_verify_writes_report(tmp_path):
    _write_project(tmp_path)
    report = tmp_path / "verify.md"

    result = cmd_verify(Namespace(
        path=str(tmp_path),
        full_scan=True,
        query="handle_auth",
        symbol=None,
        strict=False,
        baseline=None,
        regression_only=False,
        save_baseline=None,
        policy=None,
        report=str(report),
        report_format="markdown",
        as_json=True,
    ))

    assert result["pass"] is True
    assert "# Flyto Verify" in report.read_text(encoding="utf-8")


def test_workspace_changed_only_skips_clean_git_project(tmp_path):
    project = tmp_path / "project"
    _write_project(project)
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(project), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )

    result = run_workspace_verification(
        tmp_path,
        project_paths=[project],
        full_scan=True,
        changed_only=True,
    )

    assert result["summary"]["projects"] == 0
    assert result["summary"]["skipped"] == 1


def test_workspace_changed_only_detects_untracked_files(tmp_path):
    project = tmp_path / "project"
    _write_project(project)
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(project), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )
    (project / "new_module.py").write_text("def handle_new_event():\n    return True\n", encoding="utf-8")

    result = run_workspace_verification(
        tmp_path,
        project_paths=[project],
        full_scan=True,
        changed_only=True,
    )

    assert result["summary"]["projects"] == 1
    assert result["summary"]["skipped"] == 0
    assert result["projects"][0]["project"] == "project"


def test_cmd_verify_baseline_create_and_compare(tmp_path):
    _write_project(tmp_path)
    baseline_dir = tmp_path / "baselines"

    created = cmd_verify_baseline(Namespace(
        action="create",
        path=str(tmp_path),
        output_dir=str(baseline_dir),
        baseline=None,
        full_scan=True,
        as_json=True,
    ))
    compared = cmd_verify_baseline(Namespace(
        action="compare",
        path=str(tmp_path),
        output_dir=str(baseline_dir),
        baseline=None,
        full_scan=True,
        as_json=True,
    ))

    assert created["ok"] is True
    assert compared["pass"] is True
