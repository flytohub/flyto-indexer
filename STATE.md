# Flyto2 Indexer State

## Current State

- The repository is a public, general-purpose local code-intelligence package.
  Product packaging, commercial edition policy, and company release gates are
  owned by private product repositories and are not shipped here.
- CLI and MCP surfaces support indexing, context, impact, API/dependency
  closure, security checks, architecture rules, and repeatable verification.
- Project-filtered semantic searches now load and lazily rebuild only the
  selected project's index. Stale sibling project markers no longer consume a
  bounded MCP request's deadline; unfiltered workspace search is unchanged.
- Automatic index discovery skips inaccessible sibling directories while
  retaining every readable project index, so Linux service-private temporary
  directories cannot abort MCP startup or release validation.
- Existing task plan, gate, validate, and project-profile surfaces share one
  bounded, gitignored SQLite continuity record. It carries resumable task facts
  across AI clients without adding an MCP tool or committed handoff file.
  Normalized usage reports support terminal, JSON, CSV, and static HTML, while
  reduction claims fail closed unless a same-policy verified pair exists.
- The fixed task-continuity contract passes 100/100 scenarios against a 90%
  release threshold with evidence fingerprint `3e319632e54fd4c12c01803ca0452627`.
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
- A generated language-evidence contract now separates built-in indexing,
  relationship depth, security-analysis depth, committed positive/negative
  cases, and known limits. CI rejects any capability claim stronger than the
  corpus.
- A pinned FastAPI full-stack public case proves the primary product promise:
  exact text search sees four lines in one implementation file, while depth-2
  impact identifies four transitive request handlers across four files. A
  scheduled workflow reclones the exact commit and requires the checked
  evidence fingerprint to match.
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
- Version 2.18.1 retains PyYAML as the sole runtime dependency. Coverage test
  evidence and Tree-sitter grammars are explicit optional extras, so the
  default install and scan path remain small. Missing or malformed policy
  parsing still fails verification closed, and wheel CI verifies the runtime
  dependency boundary in an isolated environment.
- Durable user, operator, security, architecture, and whitepaper documentation
  links to a generated source reference covering 175 non-test Python modules,
  2,145 declarations, 36 CLI commands, 20 published MCP tools, 47 granular
  compatibility definitions, seven local HTTP operations, 16 environment
  variables, and eight built-in rule files.
- Documentation scoring distinguishes inline summaries from exact source-linked
  reference entries. The latest score is 100/100 with an 88/100 README score,
  66.8% inline coverage, 100% source-reference coverage, and 100% combined
  symbol coverage; external links count only when they resolve to the indexed
  declaration inside the repository. Benchmark fixtures are excluded from
  product-source documentation scoring.
- Generated source-reference manifests accept repository-local glob patterns,
  while absolute or escaping paths remain outside the documentation trust
  boundary.
- Documentation-heavy repositories can scope module README coverage with
  `documentation.module_roots`; the scanner then measures declared source roots
  instead of misclassifying locale and content directories as software modules.
- Package, MCP registry, and MCP initialization versions are synchronized and
  enforced by tests and CI. Every built-in rule YAML file is parsed in tests.
- Remaining production-source Ruff and dependency-isolated, Linux-targeted
  mypy exemptions are locked to exact reviewed counts and pinned tool versions.
  CI fails both increases and silent
  decreases, so every cleanup updates the baseline deliberately; repository-
  wide Ruff remains a separate zero-finding gate.
- Release publication is tag-only. The tag, package version, dated changelog,
  MCP manifests, generated references, language evidence, quality ratchet,
  lint, types, tests, benchmark, and build must pass before OIDC publication;
  a GitHub Release is created only after PyPI succeeds.
- PyPI 2.18.1 is published through Trusted Publishing with wheel and sdist
  artifacts. Its GitHub Release contains the verified CI build artifacts; the
  future release step now identifies the repository explicitly and does not
  depend on a checkout in its artifact-only job.
- Latest local verification: `1972 passed, 1 skipped`; Ruff passed across the
  full repository; mypy found no issues in 153 source files; the offline corpus
  passed 13/13 with 1.0 precision, 1.0 recall, zero false positives, p95 case
  latency 197.28 ms, and stable evidence fingerprint `203edae3857a360d`; the
  task-continuity and efficiency contract passed 100/100 against its 90% gate;
  the pinned FastAPI case matched fingerprint `691df24f16031b77` after a clean
  clone; sdist/wheel and isolated installed-policy smokes passed; strict
  baseline-aware self-verify passed 20/20 with 308 files, 4,946 indexed symbols,
  health 92/100, and zero warnings. The current sdist/wheel build and Twine
  metadata checks also passed.

## Release Blockers

- No repository-local release blocker is currently recorded.
- PyPI Trusted Publishing is confirmed for `flytohub/flyto-indexer`,
  `.github/workflows/publish-pypi.yml`, and environment `pypi`. The v2.18.0 tag
  remains unpublished because its Linux verification job stopped before build
  and publication; v2.18.1 supersedes that failed release attempt.
- Provider-side evidence records successful main-branch CI, tag verification,
  build provenance, PyPI OIDC publication, and the repaired GitHub Release for
  v2.18.1. Future provider state must still be checked per release.

## Verification Matrix

| Gate | Command | Scope |
| --- | --- | --- |
| Project memory | `bash scripts/lint-project-memory.sh` | Required docs, handoffs, headings, secret-like material |
| Generated docs | `python3 scripts/generate-reference.py --check` | Source-backed API, CLI, MCP, HTTP, configuration, and rule references |
| Language claims | `python3 scripts/check_language_evidence.py --check` | Capability wording does not exceed the committed corpus |
| Quality debt | `python3 scripts/check_quality_debt.py` | Exact production-source Ruff/mypy debt and pinned tools |
| Version metadata | `python3 scripts/sync-version.py --check` | Package, registry manifest, and runtime version parity |
| Lint | `ruff check .` | Repository-wide Python style and static checks |
| Types | `mypy src` | Type consistency |
| Tests | `pytest tests -v` | Functional and regression coverage |
| Public case | `python scripts/reproduce_impact_case.py --check-snapshot` | Pinned FastAPI impact evidence and fingerprint |
| Verify | `python -m src.cli verify . --full-scan --strict --json` | Self-verification from current source |
| Build | `python -m build` | Source and wheel package integrity |
| Installed CLI | `scripts/install-local-cli.sh && flyto-index --version` | Isolated local installation and version parity |
