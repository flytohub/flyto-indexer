# Flyto2 Product Verification release gate

Date: 2026-06-23 Asia/Taipei

Summary:

- Added `deterministic_product_verification` to `flyto2-release-packet`.
- Required source evidence now covers the four-repo Warroom loop:
  `flyto-core` recipes/tests, `flyto-engine` server-owned verification API and
  permission tests, `flyto-code` Product Verification UI/client, and
  `flyto-cloud` packaged Warroom bundle/tests/docs.
- Fresh evidence now requires:
  - `product-verification.json`
  - `product-verification.md`
- Added `scripts/write_product_verification_evidence.py` to write local dry-run
  Product Verification artifacts without claiming staging or enterprise smoke.
- `product-verification.json` must satisfy
  `warroom.product_verification.v1`:
  - non-empty `site_graph.intents`
  - non-empty `site_graph.state_graph`
  - numeric `observed_coverage`, `reachable_coverage`,
    `api_ui_consistency`, and `business_logic_confidence`
  - `p0_findings = 0`
- Evidence gates for Cloud/Apps, Security, Zero-person Agent, and release
  operations now depend on deterministic Product Verification.

Why:

- Product Verification should be a release-blocking proof, not only a page or a
  health score. The packet now fails closed if a run lacks a replayable
  intent/state graph or has P0 deterministic findings.

Verification to run:

```text
/opt/homebrew/bin/python3.11 -m pytest tests/test_flyto2_release_packet.py tests/test_flyto2_product_gate.py -q
/opt/homebrew/bin/python3.11 -m pytest tests/test_product_verification_evidence_script.py -q
ruff check src/flyto2_release_packet.py tests/test_flyto2_release_packet.py src/cli.py
bash scripts/lint-project-memory.sh
python scripts/write_product_verification_evidence.py <fresh-dir>
python -m src.cli flyto2-release-packet /Users/chester/flytohub --health-report config/flyto2/health-baseline-2026-06-21.json --fresh-evidence-dir <fresh-dir> --require-fresh --run-start <iso8601>
```

Residual risk:

- This gate validates local dry-run evidence. It does not prove authenticated
  staging login smoke, payment live-mode smoke, enterprise offline deploy,
  backup/restore, rollback, or multilingual screenshot drills.
