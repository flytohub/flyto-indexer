# Flyto Indexer State

## Current State

- Project memory structure has been bootstrapped for repeatable audit and
  handoff work.
- The repository already provides CLI and MCP-oriented code intelligence used by
  Flyto audits.
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
| Flyto2 release packet | `python -m src.cli flyto2-release-packet /Users/chester/flytohub --health-report config/flyto2/health-baseline-2026-06-21.json --json` | Workspace inventory, deliverables, blockers, readiness verdict |
| Flyto2 fresh packet | `python -m src.cli flyto2-release-packet /Users/chester/flytohub --health-report config/flyto2/health-baseline-2026-06-21.json --fresh-evidence-dir reports/flyto2-9h-2026-06-22 --require-fresh --run-start <iso8601> --json` | Nine-hour fresh evidence gate |
