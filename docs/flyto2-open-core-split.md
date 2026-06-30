# Flyto2 Open-Core Split

Flyto2 uses an open-core split: community packages are generated from the
private workspace by a deterministic whitelist, while enterprise product
capabilities remain private.

## Commands

Audit the boundary:

```sh
python -m src.cli flyto2-open-core-audit /Users/chester/flytohub
```

Export the Flyto2 Warroom CE tree:

```sh
python -m src.cli flyto2-open-core-export /Users/chester/flytohub --output /tmp/flyto2-warroom-ce
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
- `flyto-code`: Flyto2 Warroom CE React/Vite frontend source, public assets,
  i18n runtime, UI audit scripts, and vendored design tokens needed for local
  builds.
- `flyto-contracts`: generated public protocol package containing OpenAPI,
  capability catalog, JSON Schemas, examples, conformance helper, and lightweight
  SDK stubs. It is generated from private engine sources without exporting raw
  Go `internal/**` paths.

The generated release tree also contains:

- `install/docker-compose.ce.yml`: local CE stack for engine, worker,
  scan/discovery drainers, runner, verification, brand-vision, pdf, frontend,
  and Postgres.
- `install/docker-compose.ee-sim.yml`: enterprise simulation override that
  turns on `enterprise_airgap`, enterprise JWT auth, internal runner secrets,
  and sealed master-key requirements.
- `install/scripts/build-local-images.sh`: maintainer-only local image builder
  from `/Users/chester/flytohub`.
- `install/scripts/mint-ee-sim-jwt.py`: zero-dependency helper for browser
  enterprise-sim tokens.
- `install/scripts/audit-release-tree.py`: fail-closed release audit for
  private path leaks, CE/private image mixing, and secret-like generated values.
- `docs/local-install.md`, `docs/enterprise-simulation.md`, and
  `docs/code-protection.md`: operator instructions shipped with the package.

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
- Hosted SaaS-only frontend configuration, private preview credentials, and
  enterprise image publishing metadata.

## Merge Rule

The generated community tree is not source of truth. Fix source repos first,
rerun the exporter, and review the generated diff. This keeps private Flyto2
development and OSS publication mergeable without a parallel hand-maintained
fork.

## Local Install Rule

The CE compose is a local/self-hosted delivery shape, not the private development
compose file. It references published CE image coordinates by default, while
maintainers can build the same tags locally:

```sh
sh /tmp/flyto2-warroom-ce/install/scripts/build-local-images.sh /Users/chester/flytohub
cp /tmp/flyto2-warroom-ce/install/.env.ce.example /tmp/flyto2-warroom-ce/install/.env
make -C /tmp/flyto2-warroom-ce ce-up
```

Enterprise behavior can be simulated without publishing enterprise source:

```sh
cp /tmp/flyto2-warroom-ce/install/.env.ee-sim.example /tmp/flyto2-warroom-ce/install/.env.ee-sim
make -C /tmp/flyto2-warroom-ce ee-sim-up
```

Fill secrets only in the local copied env file. Do not commit them.

## Contract Package Rule

`flyto-contracts` is not a partial engine dump. The exporter maps selected
private source files into public locations, for example:

- `api/openapi.yaml` -> `openapi/flyto-engine.openapi.yaml`
- `internal/permission/capabilities.yaml` -> `capabilities/capabilities.yaml`

It then generates protocol-facing schemas, examples, conformance checks, and
SDK type stubs. Export targets matching `internal/**`, `cmd/**`, or private
handler paths fail closed.
