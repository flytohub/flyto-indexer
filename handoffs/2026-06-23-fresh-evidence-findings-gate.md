# Fresh Evidence Findings Gate

## Context

Flyto2 release readiness is evidence-first. During the 2026-06-23 continuous
audit, live `https://flyto2.com/api-docs/` returned `200` after the landing-page
middleware fix, and homepage SEO/GEO signals passed. Public-site verification
still reported six P1 findings because Cloudflare edge rules blocked AI/search
crawler user agents:

- `ChatGPT-User`
- `Claude-SearchBot`
- `Claude-User`
- `Claude-Web`
- `PerplexityBot`
- `Perplexity-User`

## Change

`flyto2-release-packet` now treats P0/P1 findings inside fresh evidence
contracts as readiness blockers even when the JSON schema is valid.

For `public-site-verification.json`, the fresh evidence entry now reports:

- `reason=blocking_findings`
- `contract_valid=true`
- `contract_finding_counts.P1=6`
- `contract_blocking_findings=[{"severity":"P1","count":6}]`

`scripts/write_continuous_release_evidence.py` also writes fresh digest
artifacts for workspace matrix, architecture, billing/entitlement, RBAC,
state-machine, enterprise/airgap, GEO/i18n, security/performance, and
browser-smoke release-packet inputs. The digest writer does not overwrite
Product Verification or public-site contract artifacts and does not convert
contract findings into passes.

## Verification

- `python -m pytest tests/test_flyto2_release_packet.py tests/test_public_site_verification_evidence_script.py tests/test_product_verification_evidence_script.py -q`
- `python -m pytest tests/test_continuous_release_evidence_script.py tests/test_flyto2_release_packet.py tests/test_public_site_verification_evidence_script.py tests/test_product_verification_evidence_script.py -q`
- `python -m src.cli flyto2-release-packet /Users/chester/flytohub --health-report /Users/chester/flytohub/flyto-indexer/config/flyto2/health-baseline-2026-06-21.json --fresh-evidence-dir /Users/chester/flytohub/reports/flyto2-continuous-2026-06-23T081254Z --require-fresh --run-start 2026-06-23T08:12:54+00:00 --json`

## Residual

Production readiness remains blocked until the Cloudflare AI crawler allowlist
is applied and a fresh public-site verification run reports zero P0/P1 findings.
The workspace also still has unrelated dirty files in deprecated `flyto-app`
that must not be overwritten without user direction.
