# Internal authority decomposition

Date: 2026-09-28
Owner: ChatGPT
Base: `d8ac48ae18228bc6ad0d341daa0f0b57272096d0`
Status: final local gates passed; landing on `main` in this change.

## Scope

This refactor reduces God-file authority without adding product capability. The
20 published MCP tools, CLI contracts, runtime dependency allowlist, static
analysis semantics, and evidence/truncation rules remain unchanged.

### Verification

`src/verify.py` is now the thin orchestration facade. Internal responsibilities
are split across `src/verification/contracts.py`, `hygiene.py`, `regression.py`,
`reporting.py`, and `support.py`.

### Taint

`TaintAnalyzer` remains import-compatible from `analyzer.taint`. Source admission,
Python intraprocedural flow, cross-file attribution, regex fallback, common
helpers, evidence serialization, policy, and propagator defaults are isolated.
The import-bound caller semantics from the 2026-09-19 work are unchanged.

### Task analysis

`task_analysis.py` remains the task-contract orchestrator while exact target
resolution, risk scoring, plan construction, and phase gating live in focused
modules. Historical monkeypatch seams used by compatibility tests are preserved
through dependency-injecting facade wrappers.

### CLI

Command grammar and dispatch remain in `src/cli.py`. Workspace/setup, scanner/
policy, quality/security, and verification handlers moved to
`src/cli_workspace.py`, `src/cli_scanners.py`, `src/cli_quality.py`, and
`src/cli_verify.py`.

## Boundary

Indexer continues to answer what a change affects, what evidence exists, and
what verification/planning constraints apply. It does not edit product code, run
a coding agent, commit, deploy, or replace Flyto2 Runtime.
## Final verification

- `scripts/test_fast.sh`: PASS (the chained command continued into the full suite).
- Full `pytest -q`: 2728 passed, 3 skipped.
- Focused decomposition regressions: 506 passed, 3 skipped.
- CLI/verify focused regressions: 141 passed.
- Ruff over `src` and `tests`: PASS.
- Generated reference `--check`: PASS.
- Project-memory lint and `git diff --check`: PASS.
- Facade compatibility against base `d8ac48a`: zero missing baseline names and
  zero function signature-shape mismatches across `verify.py`, `taint.py`,
  `task_analysis.py`, and `cli.py`.
- Published surfaces remain 20 MCP tools and 44 CLI commands.

