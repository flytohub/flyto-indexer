# 2026-07-23 Module Variant Indexing Handoff

## Scope

Corrected a production scan discovered while verifying `flyto-blog`: the
indexer skipped authored `.mjs`, `.cjs`, `.mts`, and `.cts` files but indexed
generated VitePress dependency-cache JavaScript.

## Changes

- Added ECMAScript, CommonJS, and typed module variants to the TypeScript
  scanner's supported extensions.
- Excluded `.vitepress/cache/` from full and incremental source discovery while
  preserving `.vitepress/config.*` and `.vitepress/theme/**` authored code.
- Synchronized scanner defaults, generated configuration/API references,
  feature guidance, configuration guidance, state, and changelog.
- Added regression coverage for all four extensions and for the authored-theme
  versus generated-cache boundary.

## Verification

- Focused scanner and engine suite: `94 passed`.
- Full suite: `1668 passed, 1 skipped, 1 deselected`.
- Ruff: passed.
- mypy: no issues in 130 source files.
- Generated documentation, version parity, and project-memory lint: passed.
- Source distribution and wheel build: passed.
- Strict self-verification: 18/18 checks passed, 223 files and 3,716 symbols,
  with documentation score 95 and no warnings.

## Follow-Up

Reinstall the current checkout before auditing frontend or documentation
repositories so the global `flyto-index` executable uses this scanner behavior.
