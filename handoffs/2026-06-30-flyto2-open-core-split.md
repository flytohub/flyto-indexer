# Flyto2 Open-Core Split Gate

Date: 2026-06-30
Status: Active

## Summary

Added a deterministic open-core audit/export path for Flyto2.

- `config/flyto2/open-core-manifest.json` defines the first community package
  split and the closed-source boundaries.
- `python -m src.cli flyto2-open-core-audit <workspace>` validates package
  source whitelists, required contract paths, protected paths, and denied
  content markers.
- `python -m src.cli flyto2-open-core-export <workspace> --output <empty-dir>`
  writes a generated `flyto2-community` tree with `OPEN_CORE_MANIFEST.json`,
  README, and `packages/<name>/...` copies.
- Contract packages may use mapped export targets and generated protocol
  artifacts, so private source paths can become public schemas/examples without
  leaking raw engine internals.
- The generated tree is explicitly not source of truth. Change private source
  first, rerun the exporter, and review the generated artifact.

## First Packages

- `flyto-core`: runtime SDK, YAML recipes, deterministic verification, plugin
  contracts.
- `flyto-indexer`: local-first source intelligence and verification tooling.
- `flyto-i18n`: locale sources and generated dist artifacts.
- `flyto-contracts`: generated protocol package with OpenAPI, capability
  catalog, JSON Schemas, examples, conformance helper, and lightweight SDK
  stubs. It does not export raw Go `internal/**` paths.

## Kept Closed

- Billing/entitlement/commercial gates.
- SSO/SAML/SCIM/legal hold/airgap deployment internals.
- Darkweb, stealer-log, phishing-feed, proprietary threat-intel/correlation.
- Cloud/container/runtime live remediation orchestration.
- Hosted Flyto2 Cloud control plane, runner fleet, telemetry.
- AutoFix commercial promotion/approval/rollback orchestration.
- Enterprise cockpit UI composition until a standalone community shell exists.

## Verification

Run after changes:

```sh
python -m pytest tests/test_flyto2_open_core.py -q
python -m src.cli flyto2-open-core-audit /Users/chester/flytohub --json
python -m src.cli flyto2-open-core-export /Users/chester/flytohub --output /tmp/flyto2-community
```
