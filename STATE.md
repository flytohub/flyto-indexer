# Flyto2 Indexer State

## Current State

- The repository is a public, general-purpose local code-intelligence package.
  Product packaging, commercial edition policy, and company release gates are
  owned by private product repositories and are not shipped here.
- CLI and MCP surfaces support indexing, context, impact, API/dependency
  closure, security checks, architecture rules, and repeatable verification.
- MCP task guidance treats `pass=false` as a phase-local remediation loop:
  complete `required_actions`, update the exact `current_state` keys, and
  re-run the same gate until it passes. It is not a task-termination signal.
- `verify` runs graph integrity, context, impact, secret, taint, documentation,
  rules/layers, package, runtime, and working-tree checks without requiring an
  external service.
- Verify schema v2 gives checks and sampled findings stable local IDs, compares
  new IDs inside existing warnings, preserves legacy status-only baselines, and
  exports the IDs as SARIF partial fingerprints.
- A committed offline Python/JavaScript/Go evaluation corpus gates exact
  findings, cross-file proof, precision, recall, negative-case false-positive
  rate, scan errors, latency, and memory without network access.
- Stdio MCP requests have bounded deadlines and standard cancellation. A failed
  request leaves the same process ready for the next call; the blocking input
  reader adds no idle polling loop.
- External Python MCP console scripts are discovered from `pyproject.toml` and
  validated through filesystem and AST checks; only the indexer's own MCP
  adapter receives executable runtime smoke.
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
- Durable user, operator, security, architecture, and whitepaper documentation
  links to a generated source reference covering 162 non-test Python modules,
  1,954 declarations, 36 CLI commands, 20 published MCP tools, 47 granular
  compatibility definitions, seven local HTTP operations, 13 environment
  variables, and eight built-in rule files.
- Documentation scoring distinguishes inline summaries from exact source-linked
  reference entries. The latest score is 92/100 with 66.6% inline coverage,
  97.8% source-reference coverage, and 99.6% combined symbol coverage; external
  links count only when they resolve to the indexed declaration inside the
  repository.
- Generated source-reference manifests accept repository-local glob patterns,
  while absolute or escaping paths remain outside the documentation trust
  boundary.
- Documentation-heavy repositories can scope module README coverage with
  `documentation.module_roots`; the scanner then measures declared source roots
  instead of misclassifying locale and content directories as software modules.
- Package, MCP registry, and MCP initialization versions are synchronized and
  enforced by tests and CI. Every built-in rule YAML file is parsed in tests.
- Latest local verification: `1851 passed, 1 skipped`; Ruff passed across the
  repository; mypy found no issues in 144 source files; the offline corpus
  passed 5/5 with 1.0 precision, 1.0 recall, zero false positives, and stable
  evidence fingerprint `b1411e646bc8f7f6`; sdist/wheel build and isolated
  wheel smoke passed; strict self-verify passed 18/18 with 269 files, 4,214
  scanned symbols, and zero warnings.

## Release Blockers

- No repository-local release blocker is currently recorded.
- Remote package publication and hosted CI still require valid provider-side
  permissions and successful remote workflows; local verification cannot prove
  those external conditions.

## Verification Matrix

| Gate | Command | Scope |
| --- | --- | --- |
| Project memory | `bash scripts/lint-project-memory.sh` | Required docs, handoffs, headings, secret-like material |
| Generated docs | `python3 scripts/generate-reference.py --check` | Source-backed API, CLI, MCP, HTTP, configuration, and rule references |
| Version metadata | `python3 scripts/sync-version.py --check` | Package, registry manifest, and runtime version parity |
| Lint | `ruff check src tests scripts` | Python style and static checks |
| Types | `mypy src` | Type consistency |
| Tests | `pytest tests -v` | Functional and regression coverage |
| Verify | `python -m src.cli verify . --full-scan --strict --json` | Self-verification from current source |
| Build | `python -m build` | Source and wheel package integrity |
| Installed CLI | `scripts/install-local-cli.sh && flyto-index --version` | Isolated local installation and version parity |
