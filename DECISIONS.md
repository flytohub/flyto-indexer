# Decisions

## 2026-06-21 - Health complexity is severity-weighted

Decision: score the health complexity dimension from high-complexity function
density, cumulative complexity burden, and top-hotspot severity instead of only
counting functions above the `score >= 5` threshold.

Reason: Flyto2 release gating must keep pressure on severe god functions and
dense complexity, while avoiding a misleading fail state where a barely-over
threshold helper has the same impact as a multi-hundred-line hotspot.

## 2026-06-21 - Non-core health exemptions must be explicit

Decision: allow non-core repos to carry an explicit health baseline exemption
when they have no indexed runtime symbols or use an unsupported analyzer
language, but block exemptions for core repos.

Reason: docs-only, deprecated, or unsupported-language repos should not create
permanent missing-health warnings, while core Flyto2 repos must remain covered
by a real score.

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
