# Flyto2 Warroom Paid Prerequisites

This is the paid/provider ledger for Flyto2 Warroom. It records what cannot be
fully closed by source code alone and what can still be done without paying.

## Required For Public Release

| Item | Why it costs or needs account action | Required for | No-cost maximum before paying |
| --- | --- | --- | --- |
| GitHub Actions billing/startup | Required checks cannot become release evidence while workflows fail to start | public release, SaaS, Enterprise Cloud | Run local `make verify`, indexer full scan, frontend builds, and keep verdict `CODE_READY_PROVIDER_BLOCKED` |
| Docker Hub publish permission | Public images must be pushed by an account allowed to publish the declared repo and tags | public CE image release | Build local images, run Docker boundary audits, dry-run multi-arch publish commands |
| Domain, DNS, TLS, support contact | Public docs, website links, and commercial support claims need owned reachable endpoints | public release, SaaS | Keep links documented, avoid production availability claims |

## Required For SaaS

| Item | Why it costs or needs account action | No-cost maximum before paying |
| --- | --- | --- |
| VPS or cloud runtime | Hosted engine, worker, frontend, database, object storage, and TLS need infrastructure | Local Docker Compose, Cloudflare tunnel dev loop, and staging runbooks |
| Managed database or backup storage | Production restore evidence needs durable storage and backup rehearsal | Local Postgres reset/seed and backup/restore dry-run docs |
| Object storage and image registry | Reports, evidence packs, screenshots, and release artifacts need durable storage | Local filesystem storage and unsigned local artifacts |
| Email/support channel | Commercial users need a support route and account lifecycle emails | Docs-only contact placeholder, no support SLA claim |

## Required For Enterprise

| Item | Why it costs or needs account action | Required for | No-cost maximum before paying |
| --- | --- | --- | --- |
| Enterprise license signing | Paid/on-prem features need signed entitlement and revocation policy | Enterprise Cloud, self-hosted, airgap | Edition contract, locked UI state, and local enterprise simulation |
| Private registry or image distribution | Airgap/private deployments need controlled image provenance | Enterprise Airgap | CE public image flow and local private-tag dry-run |
| Commercial threat-intelligence feeds | Darkweb, stealer logs, phishing, malware, actor, and ransomware datasets are paid or long-running collection assets | Enterprise add-ons | Public/feed-backed lookups and demo seed evidence |
| Cloud/provider test tenants | Live cloud/container/runtime/VM remediation needs safe customer-like targets | Enterprise Cloud, self-hosted online | Contract tests, mock connectors, local runner evidence |
| Support SLA and legal hold process | Enterprise contracts need support obligations and retention/legal-hold proof | Enterprise Cloud, airgap | Docs, audit export, and local retention simulation |

## Current No-Cost Maximum

Without paying, the strongest honest state is:

```text
CODE_READY_PROVIDER_BLOCKED
```

That means the CE source tree, local install, demo seed, frontend build, i18n
audit, visual-system audit, release-tree audit, Docker build boundary, and
enterprise simulation can be verified locally. It does not mean GitHub Actions,
Docker Hub publishing, SaaS hosting, paid threat feeds, or enterprise private
distribution are commercially ready.

Use:

```sh
make verify
make provider-readiness
```

`make provider-readiness` is intentionally local-only. It does not ask for
credentials and does not call external provider APIs.
