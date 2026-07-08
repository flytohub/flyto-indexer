# Flyto2 Warroom Public Roadmap

This roadmap is intentionally split by edition. It should prevent two failure
modes: promising Enterprise-only work as CE, and hiding useful CE work behind a
sales story.

The canonical product loop is Findings -> Attack Paths -> Offensive Validation -> Evidence -> Remediation.

## Shipped In CE

- Self-hosted Docker Compose stack for engine, worker, frontend, runner,
  verification, brand-vision, pdf, and Postgres.
- Local JWT authentication for the CE install path.
- Public contracts for capabilities, scanner manifests, runner callbacks,
  product verification scenarios, audit events, and evidence events.
- Warroom cockpit package with BYO finding normalization, code, external, cloud,
  container, evidence, reports, AutoFix, and governance surfaces.
- Demo seed workspace bundle covering code/container/cloud/external/evidence/autofix.
- Release audits for CE boundary, protected paths, Docker image coordinates,
  GitHub protection files, and public docs.

## CE Next

- Importable demo seed that populates native code/container/cloud/external
  tables when the running engine exposes a supported seed endpoint.
- Browser smoke for the published Docker Compose stack.
- Public screenshots and replay artifacts for CE install, seed, and report flow.
- Importers for common scanner, ASM, SAST, DAST, CSPM, and SIEM exports.
- Stronger local attack-path templates that convert imported findings into
  safe validation tasks.
- More deterministic AutoFix rules that do not require a commercial AI provider.
- Clearer empty states for CE surfaces when a connector is not configured.

## Enterprise Cloud Bridge

- Commercial darkweb, stealer-log, leak, phishing, actor, malware, and
  ransomware intelligence.
- Managed runner fleet for automated security testing and red team workflows.
- Live cloud/container/runtime/VM remediation with connector, entitlement,
  action, and evidence-signature gates.
- AI proposal workflow with quota, provider policy, approval, promotion, and
  rollback evidence.
- Enterprise identity, SSO/SAML/SCIM, billing entitlement, legal hold, and
  support SLAs.

## Enterprise Airgap

- Private image set and offline license.
- Customer-owned object storage, database, identity, logging, backup, restore,
  and update bundle.
- Offline evidence export, legal hold, retention, and compliance packet.
- Local AI endpoint support or deterministic rules-only fallback.

## Non-Claims

Flyto2 Warroom does not claim guaranteed coverage, 100% AutoFix success, or full
replacement of Aikido or any scanner without independent evidence. The product
promise is an evidence-backed offensive validation layer: bring your own tools,
then turn their findings into attack paths, replayable evidence, and remediation
records.
