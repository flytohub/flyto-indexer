# Decisions

## 2026-06-23 - Product Verification must be release evidence

Decision: make deterministic Product Verification a first-class release-packet
deliverable and require fresh `product-verification.json` to satisfy the
`warroom.product_verification.v1` contract before release confidence can be
claimed.

Reason: Warroom is not just a UI or a score. It must prove that Flyto2 can
produce a replayable intent/state graph, coverage and business-logic confidence
signals, and zero P0 deterministic findings from local evidence.

## 2026-06-23 - Release readiness is evidence-first, not score-first

Decision: treat Flyto2 health scores as minimum hygiene signals and move the
final release verdict to explicit evidence gates covering product lines,
deployment, security, GEO/i18n visibility, and operations.

Reason: a high static-analysis score can show that a repo is cleaner, but it
does not prove user workflows, enterprise deployment, security controls,
product positioning, or AI/search visibility. Production readiness must be
grounded in concrete evidence artifacts and fresh validation, not score chasing.

## 2026-06-22 - Release readiness must be evidence-backed

Decision: add `flyto2-release-packet` as a deterministic local CLI that
aggregates product gate results, git inventory, required release deliverable
evidence, P0 blockers, P1 before-production gaps, and a conservative release
verdict.

Reason: the Flyto2 workspace goal requires many artifacts beyond a green build.
The release packet keeps those artifacts machine-checkable and prevents agents
from claiming readiness when a required audit or smoke evidence file is missing.

## 2026-06-22 - Fresh evidence is separate from source evidence

Decision: `flyto2-release-packet` distinguishes source evidence from fresh run
evidence. Source evidence proves a guard/test/doc exists; `--require-fresh`
requires this run's artifacts to exist and be generated at or after
`--run-start`.

Reason: long Flyto2 convergence work must not reuse stale smoke screenshots,
old audit JSON, or previously generated release packets as proof that the
current nine-hour validation actually ran.

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
