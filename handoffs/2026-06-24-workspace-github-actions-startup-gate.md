# Workspace GitHub Actions Startup Gate

## Context

Flyto2 release closure requires remote CI to actually start, create jobs, and
pass. Local tests were green for recent `flyto-code` and `flyto-indexer`
changes, but GitHub Actions reported remote failures that local source evidence
cannot prove away:

- `flyto-code` `CI`: `startup_failure`, `jobs=[]`
- `flyto-indexer` `CI`: jobs existed, but runner metadata showed no successful
  job; rerun attempt still failed before normal step logs
- `flyto-indexer` `Security`: `startup_failure`, `jobs=[]`
- Other core repos lacked current-HEAD workflow proof or had failing required
  workflows during the workspace audit

## Change

Added `scripts/audit_github_actions_startup.py`.

The script uses `gh api` and writes
`flyto.workspace-github-actions-startup-audit.v1` evidence containing:

- core repo name and current local HEAD
- required workflow names
- latest workflow run status/conclusion for that HEAD
- job count, job conclusion, runner metadata, step count, and URLs
- summary failure list

`flyto2-release-packet` now accepts both:

- legacy `flyto-code.github-actions-startup-audit.v1`
- workspace `flyto.workspace-github-actions-startup-audit.v1`

The `github_actions_startup` deliverable is P0 and now requires source evidence
for the workspace auditor plus core repo CI workflow files. A fresh contract is
not accepted unless every required workflow is completed, successful, creates
jobs, and has at least one successful job.

## Verification

- `python -m pytest tests/test_github_actions_startup_audit.py tests/test_flyto2_release_packet.py tests/test_continuous_release_evidence_script.py -q`
- `ruff check scripts/audit_github_actions_startup.py src/flyto2_release_packet.py tests/test_github_actions_startup_audit.py tests/test_flyto2_release_packet.py`
- `python scripts/audit_github_actions_startup.py --workspace /Users/chester/flytohub --output /tmp/flyto-workspace-github-actions-startup.json --soft`
- `python -m src.cli flyto2-release-packet /Users/chester/flytohub --health-report config/flyto2/health-baseline-2026-06-21.json --fresh-evidence-dir /tmp/flyto-release-actions-evidence --require-fresh --report /tmp/flyto-release-actions-packet.json --report-format json --json`

## Residual

The latest workspace audit correctly blocks production readiness. Required
GitHub Actions workflows are not green across core repos. This is now visible
as a P0 release-packet blocker instead of being hidden behind local test
success.

Do not claim Flyto2 release completion until a fresh
`github-actions-startup.json` reports `ok=true` for the required core
workflows.
