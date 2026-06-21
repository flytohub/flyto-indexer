# Flyto Indexer State

## Current State

- Project memory structure has been bootstrapped for repeatable audit and
  handoff work.
- The repository already provides CLI and MCP-oriented code intelligence used by
  Flyto audits.
- CI covers lint, tests, verify, build, and no-dependency wheel smoke.

## Release Blockers

- No repo-specific release blocker is recorded from this docs bootstrap.
- Cross-repo production readiness still depends on remote CI stability in
  sibling Flyto repositories and on continued proof that indexer analysis stays
  local-first.

## Verification Matrix

| Gate | Command | Scope |
| --- | --- | --- |
| Project memory | `bash scripts/lint-project-memory.sh` | Required docs, handoffs, architecture headings, secret-like material |
| Lint | `ruff check src/` | Python style and static checks |
| Types | `mypy src/` | Type consistency |
| Tests | `pytest tests/ -v` | Functional coverage |
| Verify | `flyto-index verify . --full-scan --strict --json` | Self-verification |
| Build | `python -m build` | Package integrity |
