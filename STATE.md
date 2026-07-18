# Flyto2 Indexer State

## Current State

- Project memory structure has been bootstrapped for repeatable audit and
  handoff work.
- The repository already provides CLI and MCP-oriented code intelligence used by
  Flyto2 audits.
- Flyto2 core health gating now uses severity-weighted complexity scoring; the
  2026-06-21 baseline records count, burden, and top-hotspot evidence for
  continued refactoring.
- The 2026-06-21 health baseline now records `flyto-i18n` at B after the
  2026-06-22 reverse audit reduced its tooling complexity burden.
- The same baseline now records `flyto-vscode` at B after the reverse audit
  reduced webview payload complexity and improved getter documentation.
- The same baseline now records `flyto-modules-pro` at B after the reverse
  audit reduced enterprise/auth, OCR, and reducer complexity with guard tests.
- `flyto2-release-packet` now aggregates product gate output, git inventory,
  health baseline evidence, required release deliverables, and residual P0/P1
  evidence gaps for the Flyto2 workspace. It can also require fresh run
  artifacts with `--fresh-evidence-dir`, `--require-fresh`, and `--run-start`.
- Flyto2 release readiness is now evidence-first: health grades are minimum
  hygiene signals, while the final verdict depends on product-line, deployment,
  security, visibility, and operability evidence gates.
- The release packet now treats deterministic Product Verification as its own
  evidence gate. Fresh `product-verification.json` must prove the
  `warroom.product_verification.v1` contract with intent graph, state graph,
  coverage/confidence scores, and zero P0 findings.
- Fresh release evidence contracts now treat P0/P1 findings as readiness
  blockers even when the artifact schema is valid. This prevents live
  public-site issues, such as AI crawler edge blocks, from being hidden behind
  a passing evidence shape.
- GitHub Actions startup is now a workspace-level P0 fresh release contract.
  `scripts/audit_github_actions_startup.py` writes real remote CI metadata for
  core repositories; release packets block when required workflows are missing,
  fail, report `startup_failure`, or create no jobs.
- `scripts/write_continuous_release_evidence.py` can now generate fresh digest
  artifacts for the remaining release-packet deliverables from current local
  source evidence, while leaving contract artifacts such as Product
  Verification and public-site verification responsible for their own findings.
- `flyto2-open-core-audit` and `flyto2-open-core-export` now define the
  deterministic Flyto2 open-core split. Community source is generated from
  `config/flyto2/open-core-manifest.json`; protected enterprise paths and
  denied secret/provider markers fail closed before export. Engine-facing OSS
  is generated as `flyto-contracts` protocol artifacts, not raw Go
  `internal/**` source.
- `flyto2-open-core-export` now generates the Flyto2 Warroom CE local installer
  layer: CE Docker Compose, enterprise-simulation override, local image build
  helper, enterprise JWT helper, release-tree audit script, and local
  install/enterprise simulation/code-protection docs. The generated release
  audit fails closed on private path leakage, CE/private image mixing, and
  secret-like generated values.
- The Warroom CE export includes `flyto-code` frontend source as
  `packages/flyto-code`, with sanitized public metadata and `.env.example`.
  Public frontend PRs are intended to flow back into the private `flyto-code`
  source repo through generated upstream patches before re-export.
- CI covers lint, tests, verify, build, and no-dependency wheel smoke.
- `verify` now includes a `rules_policy` gate. The repository-owned
  `.flyto-rules.yaml` declares real architecture layers for foundation,
  scanners, analyzers, index core, runtime services, tool surfaces, and
  entrypoints. The gate evaluates rules plus the import graph and currently
  checks 9 rules, 7 layers, 226 files, and 313 local edges with zero
  violations.
- Documentation module coverage is closed for top-level directories through
  README files in config, examples, handoffs, integrations, scripts, and tests;
  `scan_documentation` reports an 85 overall score with no suggestions.
- `single_project_islands` now treats Flyto2 product API closure as `/api/v1`
  only. Mock/dev fixture endpoints such as `/api/mock/**` and `@mock-utils`
  dependency metadata are excluded from product API unmatched-call gates, while
  real `/api/v1/**` frontend calls still require a matching backend/OpenAPI
  contract.
- TypeScript API call extraction now preserves HTTP methods for custom
  `request<T>(method, template-literal-path)` wrappers before fallback string
  literal matching. Flyto2 frontend engine clients such as AI governance and
  container lifecycle actions now report POST/PATCH/DELETE accurately instead
  of being downgraded to GET by broad `/api/vN/**` catch-all matching.
- Project-scoped git history intelligence now fails closed when the requested
  project's indexed root is missing from disk. It no longer falls back to a
  discovered sibling `.flyto-index` or the current working directory, which
  prevents audit hotspot output from borrowing paths from unrelated Flyto2
  repositories.
- `task(action="plan")` target resolution now treats path-like inputs as exact
  file targets. Absolute paths are first reduced through `project_roots`, file
  symbols are preferred, and unmatched path-like targets return `unknown`
  without falling back to keyword or semantic search. This prevents a requested
  Flyto2 engine Go file from being planned against an unrelated Python symbol.

## Release Blockers

- No repo-specific release blocker is recorded from this docs bootstrap.
- Cross-repo production readiness still depends on remote CI stability in
  sibling Flyto2 repositories and on continued proof that indexer analysis stays
  local-first.
- As of the 2026-06-24 workspace audit, required GitHub Actions workflows are
  not green across core repos. Observed blockers include `startup_failure`,
  missing runs for current HEAD, failed jobs with no successful job, and
  no-job workflow runs. Do not claim release completion until a fresh
  `github-actions-startup.json` contract reports `ok=true`.

## Verification Matrix

| Gate | Command | Scope |
| --- | --- | --- |
| Project memory | `bash scripts/lint-project-memory.sh` | Required docs, handoffs, architecture headings, secret-like material |
| Lint | `ruff check src/` | Python style and static checks |
| Types | `mypy src/` | Type consistency |
| Tests | `pytest tests/ -v` | Functional coverage |
| Verify | `flyto-index verify . --full-scan --strict --json` | Self-verification |
| Build | `python -m build` | Package integrity |
| Flyto2 release packet | `python -m src.cli flyto2-release-packet /Users/chester/flytohub --health-report config/flyto2/health-baseline-2026-06-21.json --json` | Workspace inventory, deliverables, blockers, readiness verdict |
| Flyto2 fresh packet | `python -m src.cli flyto2-release-packet /Users/chester/flytohub --health-report config/flyto2/health-baseline-2026-06-21.json --fresh-evidence-dir reports/flyto2-9h-2026-06-22 --require-fresh --run-start <iso8601> --json` | Nine-hour fresh evidence gate |
| Flyto2 open-core split | `python -m src.cli flyto2-open-core-audit /Users/chester/flytohub --json` | Community package whitelist, protected path, denied-content gate |
| Flyto2 Warroom CE export | `python -m src.cli flyto2-open-core-export /Users/chester/flytohub --output /tmp/flyto2-warroom-ce --json` | Generated CE installer, EE-sim override, release audit |
| GitHub Actions startup | `python scripts/audit_github_actions_startup.py --workspace /Users/chester/flytohub --output /tmp/github-actions-startup.json --soft` | Core repo remote CI startup, job creation, and green workflow proof |
