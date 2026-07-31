# 2026-07-31 Evidence-First Release Handoff

## Scope

Converted the broad feature surface into public, reproducible trust evidence
and hardened the release path without adding a runtime dependency, default
network call, scanner pass, or MCP tool.

## Changes

- Added a pinned FastAPI full-stack case proving that graph impact reaches four
  transitive request handlers where exact text search sees one implementation
  file.
- Added generated per-language evidence that distinguishes indexing support,
  relationship depth, security depth, gated corpora, and known limits.
- Added public proof workflows, structured accuracy intake, and a contribution
  policy that prioritizes minimal regressions over feature inventory.
- Removed high-signal lint and type exemptions, fixed their findings, and added
  an exact production-code quality-debt ratchet for the remaining categories.
- Made release tags fail closed on version, changelog, manifest, documentation,
  accuracy, quality, test, benchmark, and build drift. PyPI success now precedes
  GitHub Release creation.

## Verification

- Full suite: `1927 passed, 1 skipped`.
- Ruff passed repository-wide; mypy passed 150 source files; exact quality debt
  remained Ruff 1,141 and mypy 736 across the explicitly exempted categories.
- Offline corpus passed 13/13 with precision and recall 1.0, zero false
  positives, and stable fingerprint `203edae3857a360d`.
- A clean clone of the pinned FastAPI repository reproduced public evidence
  fingerprint `691df24f16031b77`.
- Strict baseline-aware self-verify passed 22/22 with 298 scanned files, 4,808
  indexed symbols, health 91/A, and zero warnings.
- The 2.18.0 sdist/wheel build and an isolated installed-policy smoke passed.

Hosted CI, PyPI, and GitHub Release results remain provider-side evidence and
must be recorded after publication.

## Follow-Up

Do not add another scanner or language-depth claim because it is popular.
Promote only a distinct, reported failure with a minimal public reproduction,
positive and negative evidence, and a measured default-path cost.
