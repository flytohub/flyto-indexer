# 2026-06-23 Public Site Release Gate

## Scope

Added a release-packet deliverable for live public `flyto2.com` verification.
This prevents SEO/GEO readiness from being inferred from health scores or static
docs alone.

## Changes

- Added `public_site_verification` deliverable.
- Added `flyto2.public_site_verification.v1` fresh evidence validation.
- Added `scripts/write_public_site_verification_evidence.py`.
- Added tests for the evidence helper and invalid contract rejection.
- Extended Big Data / Intelligence, global visibility, and release operations
  gates to require public-site verification.

## Contract

`public-site-verification.json` must include:

- `dns_matrix`
- `tls_matrix`
- `route_matrix`
- `browser_matrix`
- `seo_geo_matrix`
- numeric readiness scores
- `p0_findings = 0`

## Verification

```bash
python -m pytest tests/test_flyto2_release_packet.py tests/test_public_site_verification_evidence_script.py -q
python -m pytest tests/test_flyto2_release_packet.py tests/test_public_site_verification_evidence_script.py tests/test_product_verification_evidence_script.py -q
```

Result: passed.

## Live Evidence

Latest generated artifact:

- `/Users/chester/flytohub/_audits/flyto2-public-site-2026-06-23/public-site-verification.json`
- `/Users/chester/flytohub/_audits/flyto2-public-site-2026-06-23/public-site-verification.md`

Current result:

- P0: 0
- P1: 9
- Public route readiness: 0.938
- SEO/GEO readiness: 0.889
- Browser render readiness: 1.0

Remaining P1s:

- `/api-docs/` live 404
- `ChatGPT-User`, `ClaudeBot`, `Claude-SearchBot`, `Claude-User`,
  `Claude-Web`, `PerplexityBot`, and `Perplexity-User` receive 403
- OpenGraph metadata is missing in the live probe
