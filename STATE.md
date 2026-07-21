# Flyto2 Indexer State

## Current State

- The repository is a public, general-purpose local code-intelligence package.
  Product packaging, commercial edition policy, and company release gates are
  owned by private product repositories and are not shipped here.
- CLI and MCP surfaces support indexing, context, impact, API/dependency
  closure, security checks, architecture rules, and repeatable verification.
- `verify` runs graph integrity, context, impact, secret, taint, documentation,
  rules/layers, package, runtime, and working-tree checks without requiring an
  external service.
- Product API closure is limited to `/api/v1/**`; `/api/mock/**` fixtures are
  excluded while real API calls still require indexed route or OpenAPI proof.
- TypeScript custom request wrappers preserve their actual HTTP method.
- Explicit task paths resolve inside the selected project and fail closed when
  absent instead of falling back to a similarly named symbol elsewhere.
- Package version reporting is derived from `pyproject.toml` in source mode and
  installed wheel metadata otherwise. `flyto-index --version` exposes the
  active runtime version.
- `scripts/install-local-cli.sh` installs the current checkout into an isolated
  venv and verifies that the executable version matches the checkout.

## Release Blockers

- No repository-local release blocker is currently recorded.
- Remote package publication and hosted CI still require valid provider-side
  permissions and successful remote workflows; local verification cannot prove
  those external conditions.

## Verification Matrix

| Gate | Command | Scope |
| --- | --- | --- |
| Project memory | `bash scripts/lint-project-memory.sh` | Required docs, handoffs, headings, secret-like material |
| Lint | `ruff check src tests` | Python style and static checks |
| Types | `mypy src` | Type consistency |
| Tests | `pytest tests -v` | Functional and regression coverage |
| Verify | `python -m src.cli verify . --full-scan --strict --json` | Self-verification from current source |
| Build | `python -m build` | Source and wheel package integrity |
| Installed CLI | `scripts/install-local-cli.sh && flyto-index --version` | Isolated local installation and version parity |
