# Target-project Ruff selection

Owner: coding worker
Date: 2026-08-24

## What changed

Post-change validation now prefers an executable `.venv/bin/python` (or the
conventional Windows equivalent) at the exact lexical location inside the
target repository. Its containing venv directory must remain inside the
repository, while the interpreter may follow the normal venv symlink chain to
the base Python. Ruff runs as fixed argv through `-m ruff check`; results
expose the selected tool source and exact command. Ambient/runtime fallback is
allowed only when the project interpreter or Ruff module is unavailable.

## Safety boundary

A nonzero project-owned Ruff result remains a failure and does not fall back.
Virtual-environment directory escape is rejected, subprocesses retain explicit
argv, target cwd, captured text output, and a 30-second timeout, and task
target plus docs-only behavior is unchanged. The source-owned coding contract
uses the prepared Indexer venv for Python, Ruff, and test checks.
The frozen `scripts/test_fast.sh` entrypoint also selects that venv before
checking supported ambient Python candidates, so an already-dispatched command
cannot fall back to the sanitized runner's Python 3.9.

## Regression coverage

Tests cover local-over-ambient preference, mocked and real-subprocess no-
fallback behavior after a local lint failure, missing local Ruff fallback,
normal interpreter symlinks, virtual-environment directory containment,
repository-wide behavior, exact task targets, escape rejection, and docs-only
skipping. The real Blueprint checkout selects its `.venv/bin/python`, reports
Ruff 0.15.15, and completes its repository lint successfully.

## Verification

Generated references were refreshed after the implementation changes. The
implementation worker exercised focused and repository gates with the prepared
venv; the host still owns the authoritative source-controlled check result and
independent audit.
