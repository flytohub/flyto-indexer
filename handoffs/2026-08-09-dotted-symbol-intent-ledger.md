# 2026-08-09 Dotted Symbol Intent-Ledger Classification

Owner: Codex
Branch: `main`

## Scope

Repair the mandatory task-validation path after a real Core job showed that
the inline requirement parser classified the module ID `human.approval` as a
file path and demanded an unrelated diff.

## Changes

- Bounded root-file inference to supported file suffixes and conventional
  repository filenames while preserving slash-bearing relative paths.
- Kept other identifier-shaped dotted spans in the symbol ledger.
- Added direct classification coverage and an end-to-end intent-ledger
  regression proving a script-only change is no longer blocked by the module
  identifier.

## Verification

- `pytest -q`: `2081 passed, 1 skipped`.
- Repository-wide Ruff passed; the quality-debt ratchet passed at Ruff 1,141
  and mypy 732.
- Generated-reference, language-evidence, and project-memory checks passed.
- The exact flyto-core `REQ-D5BD47DB` reproduction now records
  `human.approval` under `expected_symbols`, leaves `expected_paths` empty, and
  validates the intended script-only change with zero violations.
- Strict full-scan self-verify passed 20/20 with 310 files, 4,985 indexed
  symbols, health 91/A, and zero warnings or failures.
