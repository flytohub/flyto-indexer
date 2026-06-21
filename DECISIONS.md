# Decisions

## 2026-06-21 - Project memory is release-controlled

Decision: keep root project memory files, workflow docs, and handoff registry in
the repository and validate them in CI.

Reason: the indexer is used to audit other repositories, so its own memory and
verification loop must be explicit and machine-checked.

## 2026-06-21 - Local-first analysis remains the default

Decision: indexing, verify, audit, and impact analysis should run without
external services by default.

Reason: Flyto needs this tool for private source, enterprise deployments, and
airgapped audit workflows.
