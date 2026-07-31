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
- MCP is dual-era: stateless `2026-07-28` requests use per-request metadata,
  `server/discover`, required result/cache fields, and validated Streamable
  HTTP routing headers, while initialize-based `2025-11-25`, `2025-06-18`,
  `2025-03-26`, and `2024-11-05` clients remain supported.
- `verify` runs graph integrity, context, impact, secret, taint, documentation,
  rules/layers, package, runtime, and working-tree checks without requiring an
  external service.
- Verify schema v2 gives checks and sampled findings stable local IDs, compares
  new IDs inside existing warnings, preserves legacy status-only baselines, and
  exports the IDs as SARIF partial fingerprints.
- Canonical `health-snapshot.v2` evidence is content-addressed and shared by
  audit, verification, and smart-tool expansions; divergent evidence fails
  closed instead of presenting contradictory health scores. Health output now
  identifies itself as a static engineering-risk signal and explains that a
  dimension ceiling means its configured budget was met, not that the issue
  count is zero or runtime behavior is proven.
- A committed offline Python/JavaScript/TypeScript/Go evaluation corpus gates
  13 exact positive/negative cases, cross-file proof, four metamorphic
  relations, pinned differential categories, precision, recall,
  false-positive rate, scan errors, p50/p95/max latency, and memory without
  network access.
- Optional SCIP artifacts precede LSP and native reverse-index fallback for
  precise references. Coverage.py contexts, LCOV, and JUnit artifacts map
  changed lines and symbols to executed tests with hashes, freshness, and
  explicit confidence.
- Secret, SAST, IaC, taint, verification, and SARIF findings carry stable
  fingerprints, confidence basis, traces, and suppression provenance while
  preserving legacy scanner fields. Governed waivers require an owner,
  rationale, source, scope, and unexpired expiry.
- The existing 20-tool MCP surface now exposes a fifth `task` action for local
  AI-development feedback. Explicit observations and failed-validation reason
  codes enter an append-only store with stable issue grouping, bounded
  summaries, common-secret/code redaction, resolution history, and no
  automatic policy changes.
- `task(validate)` accepts external browser, race, container, integration,
  runtime, security, penetration, and deployment proof receipts. Content
  identity and freshness are always checked; a required proof kind closes only
  with a passing locally trusted HMAC attestation.
- Dependency-focused structure queries add on-demand React lazy import,
  dynamic glob, route mount, route authorization, and ORM tenant-scope hints
  for explicitly requested TypeScript, JavaScript, or Python files. The default
  indexing path does not run this adapter.
- Incremental manifests hash full content and the scanner/parser pipeline.
  Optional Tree-sitter validation is lazy, environment-gated, and falls back
  to native scanners; the default path imports no parser dependency.
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
- Version 2.17.0 retains PyYAML as the sole runtime dependency. Coverage test
  evidence and Tree-sitter grammars are explicit optional extras, so the
  default install and scan path remain small. Missing or malformed policy
  parsing still fails verification closed, and wheel CI verifies the runtime
  dependency boundary in an isolated environment.
- Durable user, operator, security, architecture, and whitepaper documentation
  links to a generated source reference covering 171 non-test Python modules,
  2,113 declarations, 36 CLI commands, 20 published MCP tools, 47 granular
  compatibility definitions, seven local HTTP operations, 16 environment
  variables, and eight built-in rule files.
- Documentation scoring distinguishes inline summaries from exact source-linked
  reference entries. The latest score is 98/100 with a 78/100 README score,
  66.6% inline coverage, 99.9% source-reference coverage, and 99.9% combined
  symbol coverage; external links count only when they resolve to the indexed
  declaration inside the repository.
- Generated source-reference manifests accept repository-local glob patterns,
  while absolute or escaping paths remain outside the documentation trust
  boundary.
- Documentation-heavy repositories can scope module README coverage with
  `documentation.module_roots`; the scanner then measures declared source roots
  instead of misclassifying locale and content directories as software modules.
- Package, MCP registry, and MCP initialization versions are synchronized and
  enforced by tests and CI. Every built-in rule YAML file is parsed in tests.
- Latest local verification: `1915 passed, 1 skipped`; Ruff passed across the
  repository; mypy found no issues in 150 source files; the offline corpus
  passed 13/13 with 1.0 precision, 1.0 recall, zero false positives, p95 case
  latency 170.66 ms, and stable evidence fingerprint `203edae3857a360d`;
  sdist/wheel, isolated installed-feature, and real optional Tree-sitter
  grammar smokes passed; strict self-verify passed 20/20 with 290 files,
  4,753 indexed symbols, health 82/100, and zero warnings.

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
