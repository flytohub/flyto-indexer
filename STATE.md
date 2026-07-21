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
- Single-project island fallback discovery reads Vue, Svelte, and Astro source
  in addition to TS/JS/Go/Python, so real SFC imports count as inbound product
  edges instead of false orphan findings.
- Explicit task paths resolve inside the selected project and fail closed when
  absent instead of falling back to a similarly named symbol elsewhere.
- Package version reporting is derived from `pyproject.toml` in source mode and
  installed wheel metadata otherwise. `flyto-index --version` exposes the
  active runtime version.
- `scripts/install-local-cli.sh` installs the current checkout into an isolated
  venv and verifies that the executable version matches the checkout.
- Version 2.14.2 declares PyYAML as the sole runtime dependency so project
  policies and layer rules cannot be silently skipped in installed builds.
  Missing or malformed policy parsing now fails verification closed, and the
  wheel CI smoke loads and evaluates a real rule in an isolated environment.
- Latest local verification: `1655 passed, 1 skipped`; Ruff passed; mypy found
  no issues in 129 source files; sdist/wheel build passed; strict self-verify
  passed 18/18. The rebuilt installed CLI also made Flyto2 Cloud strict full
  verify pass 18/18 with zero single-project islands.

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
