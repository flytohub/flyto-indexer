# 2026-08-13 Direct Task Workflow Project Scope

Owner: Codex

## Scope

Keep direct Python and CLI plan, gate, and validate actions from loading indexes
belonging to ambient sibling worktrees. Keep task edit authority exact and the
compiled plan deterministic. This patch is rebased on `c91d78f`, whose frozen
project identity owns the complete public action boundary for direct and MCP
callers.

## Changes

- Preserved the upstream immutable project-identity boundary and added direct
  CLI plan plus public gate/validate regressions with selected and hostile
  sibling indexes under both supported import identities.
- Required exact task-target identity before a keyword result can grant edit
  authority. `M1.1` remains unresolved instead of selecting `GitLabLogo`, and
  non-path labels cannot enter Intent Ledger `allowed_paths` or
  `expected_paths`.
- Bounded filesystem-probe failures and preserved real file, root filename,
  dotfile, Unicode, and spaced symbol-path handling.
- Replaced set-based execution-plan path ordering with first-seen deduplication
  and pinned identical output under `PYTHONHASHSEED=0` and `3`.
- Kept `_resolve_targets` at the configured complexity threshold by isolating
  exact search authority in a focused helper.
- Narrowed the upstream optional Grill evidence loader for mypy without runtime
  behavior changes.
- Regenerated the source-derived module and Python API references.

## Verification

Verification passed locally on `c91d78f`:

- `tests/test_task_cli.py`: 4 passed.
- `tests/test_task_context.py`: 133 passed.
- `tests/test_task_amendment.py`: 87 passed.
- Task target resolution, execution-plan, and cross-seed regressions: 23 passed.
- `tests/test_smart.py`: 39 passed.
- Real Grill CLI end-to-end: 1 passed.
- Fully isolated host suite: 2,230 passed, 1 skipped in 31.72s. The task
  efficiency benchmark intentionally retains its own per-project temporary task
  database; a global `FLYTO_INDEXER_TASK_DB` override is not used for that gate.
- Real Code CLI plans completed in 1.65s and 2.81s; `M1.1` remained unresolved,
  and only the real `WorkspaceSidebar.tsx` target entered path authority.
- Generated-reference check, exact Ruff, exact mypy, and `git diff --check`
  passed.

No commit or push was performed here; the diff is ready for independent audit.
