# 2026-08-10 Public Contract Review Gate

Owner: Codex
Branch: `main`

## Scope

Repair the mandatory pre-implementation gate after real Flyto coding jobs
proved that a high breaking-risk constraint requested external human approval
without any `public_contract_change_detected` evidence.

## Changes

- Removed unconditional human review from the generic apply-change
  requirements.
- Preserved the dedicated public-contract condition: detected public contract
  changes still require `human_review_completed` and fail closed otherwise.
- Added positive and negative regression coverage for both paths.

## Verification

- Focused public-contract gate tests: `3 passed`.
- Full task-analysis tests: `87 passed`.
- Ruff passed for the changed source and test files.
