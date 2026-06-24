from pathlib import Path
import importlib.util
import sys


SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "audit_github_actions_startup.py"
SPEC = importlib.util.spec_from_file_location("audit_github_actions_startup", SCRIPT_PATH)
assert SPEC and SPEC.loader
audit_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit_module
SPEC.loader.exec_module(audit_module)


def test_audit_repositories_reports_startup_failure_without_jobs():
    spec = audit_module.RepositorySpec(repo="flytohub/flyto-code", workflows=("CI",), head="abc123")

    def fake_gh_api(path):
        if path == "/repos/flytohub/flyto-code/actions/runs?head_sha=abc123&per_page=100":
            return {
                "workflow_runs": [
                    {
                        "workflowName": "CI",
                        "name": "CI",
                        "displayTitle": "CI",
                        "id": 28072960830,
                        "html_url": "https://github.com/flytohub/flyto-code/actions/runs/28072960830",
                        "event": "workflow_dispatch",
                        "status": "completed",
                        "conclusion": "startup_failure",
                        "path": ".github/workflows/ci.yml",
                        "created_at": "2026-06-24T03:24:59Z",
                        "updated_at": "2026-06-24T03:25:00Z",
                    }
                ]
            }
        if path == "/repos/flytohub/flyto-code/actions/runs/28072960830/jobs?per_page=100":
            return {"jobs": []}
        raise AssertionError(path)

    report = audit_module.audit_repositories(
        [spec],
        workspace=None,
        generated_at="2026-06-24T04:00:00+00:00",
        gh_api_fn=fake_gh_api,
    )

    assert report["schema"] == "flyto.workspace-github-actions-startup-audit.v1"
    assert report["ok"] is False
    assert report["summary"]["failure_count"] == 1
    workflow = report["repositories"][0]["workflows"][0]
    assert workflow["ok"] is False
    assert workflow["conclusion"] == "startup_failure"
    assert workflow["reason"] == "conclusion_startup_failure"
    assert workflow["jobs"] == []


def test_audit_repositories_accepts_completed_success_with_successful_job():
    spec = audit_module.RepositorySpec(repo="flytohub/flyto-indexer", workflows=("CI",), head="def456")

    def fake_gh_api(path):
        if path == "/repos/flytohub/flyto-indexer/actions/runs?head_sha=def456&per_page=100":
            return {
                "workflow_runs": [
                    {
                        "workflowName": "CI",
                        "name": "CI",
                        "displayTitle": "CI",
                        "id": 1,
                        "html_url": "https://github.com/flytohub/flyto-indexer/actions/runs/1",
                        "event": "push",
                        "status": "completed",
                        "conclusion": "success",
                        "path": ".github/workflows/ci.yml",
                        "created_at": "2026-06-24T03:45:16Z",
                        "updated_at": "2026-06-24T03:50:00Z",
                    }
                ]
            }
        if path == "/repos/flytohub/flyto-indexer/actions/runs/1/jobs?per_page=100":
            return {
                "jobs": [
                    {
                        "name": "lint",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-06-24T03:45:17Z",
                        "completed_at": "2026-06-24T03:46:17Z",
                        "runner_id": 10,
                        "runner_name": "GitHub Actions 1",
                        "runner_group_name": "GitHub Actions",
                        "steps": [{"name": "Checkout"}],
                    },
                    {
                        "name": "optional",
                        "status": "completed",
                        "conclusion": "skipped",
                        "steps": [],
                    },
                ]
            }
        raise AssertionError(path)

    report = audit_module.audit_repositories(
        [spec],
        workspace=None,
        generated_at="2026-06-24T04:00:00+00:00",
        gh_api_fn=fake_gh_api,
    )

    assert report["ok"] is True
    assert report["summary"]["failure_count"] == 0
    workflow = report["repositories"][0]["workflows"][0]
    assert workflow["ok"] is True
    assert workflow["jobs"][0]["runnerId"] == 10
    assert workflow["jobs"][0]["stepsCount"] == 1
