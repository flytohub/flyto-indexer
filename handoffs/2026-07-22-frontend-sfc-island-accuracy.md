# Frontend SFC Island Accuracy

## Problem

Cloud strict verification reported `ReportDialog.vue` as an unwired product
component even though `TemplateDetail.vue` imports and renders it. Typed graph
extraction missed the edge and fallback source references did not read Vue SFCs.

## Change

`_CONTRACT_SOURCE_EXTENSIONS` now includes `.vue`, `.svelte`, and `.astro`.
These files are read as static text for component-name and API-contract fallback
only; analyzed application code is never executed.

## Regression

`test_single_project_islands_accepts_vue_imported_component` creates a Vue
component and importing view and requires a zero-island result. The complete
single-project-island group passes with 8 tests.

## Verification

- Full Indexer suite: `1655 passed, 1 skipped`.
- Ruff: passed.
- Mypy: no issues in 129 source files.
- Package: sdist and wheel built successfully.
- Strict Indexer self-verify: 18/18 passed.
- Rebuilt installed CLI: version 2.14.2.
- Flyto2 Cloud strict full verify: 18/18 passed; 1,528 candidate symbols and
  zero islands. `ReportDialog.vue` is no longer a false finding.
