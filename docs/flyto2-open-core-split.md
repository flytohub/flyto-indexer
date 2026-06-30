# Flyto2 Open-Core Split

Flyto2 uses an open-core split: community packages are generated from the
private workspace by a deterministic whitelist, while enterprise product
capabilities remain private.

## Commands

Audit the boundary:

```sh
python -m src.cli flyto2-open-core-audit /Users/chester/flytohub
```

Export the community tree:

```sh
python -m src.cli flyto2-open-core-export /Users/chester/flytohub --output /tmp/flyto2-community
```

The exporter refuses to write into a non-empty output directory and refuses to
export when the audit finds blockers.

## First Split

The first generated community package contains:

- `flyto-core`: YAML runtime, module SDK, deterministic verification, recipes,
  and plugin contracts.
- `flyto-indexer`: local-first source indexing, dependency/taint/security
  analysis, SBOM, release evidence gates, CLI/MCP adapters.
- `flyto-i18n`: shared locale source and generated distribution files.
- `flyto-engine-contracts`: OpenAPI/capability/scheduler/product-verification
  contract source, not the hosted enterprise control plane.

## Kept Closed

- Billing, entitlement mutation, commercial gates, and Stripe/offline-license
  adapters.
- SSO/SAML/SCIM, legal hold, airgap installers, and deployment edition
  internals.
- Darkweb, stealer-log, phishing-feed, commercial threat-intel, and proprietary
  correlation datasets.
- Cloud/container/runtime live remediation orchestration and customer connector
  credential paths.
- Flyto Cloud multi-tenant SaaS control plane, runner fleet control, and hosted
  telemetry.
- AutoFix promotion, approval, rollback orchestration, and commercial AI
  proposal workflows.
- Full enterprise cockpit UI composition until a standalone community shell is
  generated.

## Merge Rule

The generated community tree is not source of truth. Fix source repos first,
rerun the exporter, and review the generated diff. This keeps private Flyto2
development and OSS publication mergeable without a parallel hand-maintained
fork.
