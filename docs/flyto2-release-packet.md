# Flyto2 Release Packet

`flyto2-release-packet` generates a local, repeatable release-readiness packet
for the Flyto2 workspace. It does not call external services and does not read
credentials.

## What It Checks

- Flyto2 product gate verdict from the product-line manifest.
- Git inventory for every manifest repo: branch, HEAD, `origin/main`, dirty
  files, language/framework signals, package manager, scripts, deploy targets,
  product-line role, memory status, and health baseline.
- Required release deliverables:
  - workspace inventory
  - architecture / dependency map
  - billing + entitlement audit
  - RBAC / tenant isolation audit
  - product state machine audit
  - enterprise / airgap / open-core audit
  - GEO / AEO / SEO / AI crawler audit
  - i18n / multilingual audit
  - security / performance / CI/CD audit
  - E2E browser smoke matrix
  - release readiness verdict
- P0 blockers, P1 before-production gaps, and post-launch residuals.

## Commands

```bash
python -m src.cli flyto2-release-packet /Users/chester/flytohub \
  --health-report config/flyto2/health-baseline-2026-06-21.json \
  --json

python -m src.cli flyto2-release-packet /Users/chester/flytohub \
  --health-report config/flyto2/health-baseline-2026-06-21.json \
  --report reports/flyto2-release-packet.md \
  --report-format markdown

python -m src.cli flyto2-release-packet /Users/chester/flytohub \
  --health-report config/flyto2/health-baseline-2026-06-21.json \
  --fresh-evidence-dir reports/flyto2-9h-2026-06-22 \
  --require-fresh \
  --run-start 2026-06-22T09:00:00+08:00 \
  --json
```

## Verdict Semantics

- `BLOCKED_FOR_PRODUCTION`: product gate blocker, dirty repo, remote mismatch, or
  other P0 blocker exists.
- `READY_FOR_CONTROLLED_BETA`: no P0 blockers, but at least one required P1
  deliverable still lacks source evidence or fresh run evidence.
- `READY_FOR_CONTROLLED_PRODUCTION`: product gate passes, repos are clean and
  aligned, and every required release deliverable has evidence.

The command is intentionally stricter than a build. It is meant to stop release
claims that are not backed by current workspace evidence.

## Fresh Evidence

By default the packet validates that required source artifacts exist. With
`--require-fresh`, each deliverable also needs a fresh run artifact in
`--fresh-evidence-dir`.

Required fresh artifact names:

- `workspace-matrix.json`
- `workspace-matrix.md`
- `architecture-map.md`
- `billing-entitlement.md`
- `rbac-tenant-isolation.md`
- `state-machine.md`
- `enterprise-airgap.md`
- `geo-ai-crawler.md`
- `i18n.md`
- `security-performance.md`
- `browser-smoke.json`
- `browser-smoke.md`

If `--run-start` is supplied, JSON artifacts can prove freshness through
`run_started_at`, `generated_at`, `created_at`, or `completed_at`; otherwise
file modification time is used.
