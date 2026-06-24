#!/usr/bin/env python3
"""Audit whether required GitHub Actions workflows started and passed.

The script shells out to `gh api` so credentials remain in the GitHub CLI
keychain. It writes only run metadata: repository, HEAD, workflow status, job
status, runner identifiers, and URLs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable
from urllib.parse import quote


DEFAULT_REPOS = (
    "flytohub/flyto-code=CI,Security",
    "flytohub/flyto-engine=CI,Security",
    "flytohub/flyto-core=CI,Security",
    "flytohub/flyto-indexer=CI,Security",
)

BAD_JOB_CONCLUSIONS = {"action_required", "cancelled", "failure", "startup_failure", "timed_out"}


@dataclass(frozen=True)
class RepositorySpec:
    repo: str
    workflows: tuple[str, ...]
    head: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_repo_spec(raw: str) -> RepositorySpec:
    repo_part, sep, workflow_part = raw.partition("=")
    repo = repo_part.strip()
    if "/" not in repo:
        raise ValueError(f"repo spec must use owner/repo form: {raw}")
    workflows = tuple(item.strip() for item in workflow_part.split(",") if item.strip()) if sep else ("CI",)
    if not workflows:
        raise ValueError(f"repo spec must include at least one workflow: {raw}")
    return RepositorySpec(repo=repo, workflows=workflows)


def _run_json_command(args: list[str], *, cwd: Path | None = None) -> Any:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"{' '.join(args)} failed{': ' + detail if detail else ''}")
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{' '.join(args)} returned invalid JSON: {exc}") from exc


def gh_api(path: str) -> Any:
    return _run_json_command(["gh", "api", path])


def git_head(path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"git HEAD lookup failed for {path}{': ' + detail if detail else ''}")
    return completed.stdout.strip()


def _workflow_matches(run: dict[str, Any], workflow: str) -> bool:
    return workflow in {
        str(run.get("workflowName") or ""),
        str(run.get("name") or ""),
        str(run.get("displayTitle") or ""),
    }


def _job_summary(job: dict[str, Any]) -> dict[str, Any]:
    steps = job.get("steps")
    return {
        "name": job.get("name"),
        "status": job.get("status"),
        "conclusion": job.get("conclusion"),
        "startedAt": job.get("started_at"),
        "completedAt": job.get("completed_at"),
        "runnerId": job.get("runner_id"),
        "runnerName": job.get("runner_name"),
        "runnerGroupName": job.get("runner_group_name"),
        "stepsCount": len(steps) if isinstance(steps, list) else 0,
    }


def _workflow_ok(run: dict[str, Any], jobs: list[dict[str, Any]]) -> tuple[bool, str]:
    if run.get("status") != "completed":
        return False, f"status_{run.get('status') or 'missing'}"
    if run.get("conclusion") != "success":
        return False, f"conclusion_{run.get('conclusion') or 'missing'}"
    if not jobs:
        return False, "no_jobs_created"
    bad = [
        job
        for job in jobs
        if str(job.get("conclusion") or "").lower() in BAD_JOB_CONCLUSIONS
        or str(job.get("status") or "").lower() not in {"completed"}
    ]
    if bad:
        first = bad[0]
        return False, f"job_{first.get('name') or 'unknown'}_{first.get('conclusion') or first.get('status')}"
    if not any(job.get("conclusion") == "success" for job in jobs):
        return False, "no_successful_jobs"
    return True, ""


def _resolve_head(
    spec: RepositorySpec,
    workspace: Path | None,
    git_head_fn: Callable[[Path], str],
) -> tuple[str, str]:
    if spec.head:
        return spec.head, ""
    if workspace is not None:
        repo_name = spec.repo.split("/", 1)[1]
        local_path = workspace / repo_name
        if local_path.exists():
            return git_head_fn(local_path), str(local_path)
    raise RuntimeError(f"cannot resolve local HEAD for {spec.repo}; pass --head {spec.repo}=<sha>")


def audit_repositories(
    specs: list[RepositorySpec],
    *,
    workspace: Path | None,
    generated_at: str,
    gh_api_fn: Callable[[str], Any] = gh_api,
    git_head_fn: Callable[[Path], str] = git_head,
) -> dict[str, Any]:
    repositories: list[dict[str, Any]] = []
    failures: list[str] = []
    for spec in specs:
        head, local_path = _resolve_head(spec, workspace, git_head_fn)
        runs = gh_api_fn(
            f"/repos/{spec.repo}/actions/runs?head_sha={quote(head)}&per_page=100"
        ).get("workflow_runs", [])
        repo_result = {
            "repo": spec.repo,
            "localPath": local_path,
            "head": head,
            "requiredWorkflows": list(spec.workflows),
            "ok": True,
            "workflows": [],
        }
        if not isinstance(runs, list) or not runs:
            repo_result["ok"] = False
            repo_result["reason"] = "no_runs_for_head"
            failures.append(f"{spec.repo}: no_runs_for_head")
            repositories.append(repo_result)
            continue

        for workflow in spec.workflows:
            matches = [run for run in runs if isinstance(run, dict) and _workflow_matches(run, workflow)]
            matches.sort(key=lambda run: str(run.get("created_at") or ""), reverse=True)
            latest = matches[0] if matches else None
            if latest is None:
                item = {"workflow": workflow, "ok": False, "reason": "missing_run", "jobs": []}
                repo_result["workflows"].append(item)
                repo_result["ok"] = False
                failures.append(f"{spec.repo}/{workflow}: missing_run")
                continue

            jobs = gh_api_fn(f"/repos/{spec.repo}/actions/runs/{latest.get('id')}/jobs?per_page=100").get(
                "jobs", []
            )
            jobs = jobs if isinstance(jobs, list) else []
            ok, reason = _workflow_ok(latest, jobs)
            item = {
                "workflow": workflow,
                "id": latest.get("id"),
                "url": latest.get("html_url"),
                "event": latest.get("event"),
                "status": latest.get("status"),
                "conclusion": latest.get("conclusion"),
                "path": latest.get("path"),
                "createdAt": latest.get("created_at"),
                "updatedAt": latest.get("updated_at"),
                "jobs": [_job_summary(job) for job in jobs if isinstance(job, dict)],
                "ok": ok,
            }
            if reason:
                item["reason"] = reason
                repo_result["ok"] = False
                failures.append(f"{spec.repo}/{workflow}: {reason}")
            repo_result["workflows"].append(item)
        repositories.append(repo_result)

    return {
        "schema": "flyto.workspace-github-actions-startup-audit.v1",
        "generated_at": generated_at,
        "ok": not failures,
        "repositories": repositories,
        "summary": {
            "repo_count": len(repositories),
            "workflow_count": sum(len(repo.get("workflows", [])) for repo in repositories),
            "failure_count": len(failures),
            "failures": failures,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("/Users/chester/flytohub"))
    parser.add_argument("--repo", action="append", default=[], help="owner/repo=Workflow A,Workflow B")
    parser.add_argument("--head", action="append", default=[], help="owner/repo=<sha>")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generated-at", default=_now_iso())
    parser.add_argument("--soft", action="store_true", help="Write report but do not fail non-zero")
    args = parser.parse_args()

    specs = [parse_repo_spec(raw) for raw in (args.repo or list(DEFAULT_REPOS))]
    heads = dict(item.split("=", 1) for item in args.head)
    specs = [
        RepositorySpec(repo=spec.repo, workflows=spec.workflows, head=heads.get(spec.repo))
        for spec in specs
    ]
    report = audit_repositories(specs, workspace=args.workspace, generated_at=args.generated_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"GitHub Actions startup audit written: {args.output}")
    if not report["ok"]:
        print("; ".join(report["summary"]["failures"]), file=sys.stderr)
        return 0 if args.soft else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
