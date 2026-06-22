# Flyto2 Release Packet Guard

Date: 2026-06-22

## Context

The Flyto2 workspace goal requires a repeatable 24/25-project inventory,
architecture map evidence, billing/RBAC/state-machine/enterprise/GEO/i18n/
security/E2E deliverables, and an explicit release verdict. The existing
`flyto2-product-gate` proved product-line mapping and health, but did not
produce the broader release packet.

## Change

- Added `src/flyto2_release_packet.py`.
- Added CLI command `flyto2-release-packet`.
- Added focused tests covering complete evidence and missing P1 evidence.
- Added fresh evidence enforcement so the packet can reject stale audit/smoke
  artifacts during a long validation run.
- Added documentation in `docs/flyto2-release-packet.md`.

## Verification

- `python -m pytest tests/test_flyto2_release_packet.py tests/test_flyto2_product_gate.py -q`
- `ruff check src/flyto2_release_packet.py tests/test_flyto2_release_packet.py src/cli.py`
- Fresh packet mode reports P1 gaps until this run writes the required fresh
  evidence files.
