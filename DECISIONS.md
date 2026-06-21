# Decisions

## 2026-06-21 - Flyto2 product lines are gate-controlled

Decision: keep the Flyto2 five-line product model in a manifest and fail the
release gate when a repo is unclassified, a core health target is missed, or
required project memory is missing.

Reason: Flyto2 must ship as one coherent product system with Cloud/Apps,
Security, Data, Zero-person Agent, and Big Data surfaces sharing `flyto-core`
without losing repo ownership or commercial boundaries.

## 2026-06-21 - Project memory bootstrap must be non-destructive

Decision: generated memory bootstrap may create missing files, but must never
overwrite existing repo-specific notes.

Reason: many repos already have partial memory written by prior agents. Release
automation should close structural gaps without erasing local ownership context.

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
