# Source Reference Coverage

## Context

The documentation scanner historically counted only summaries embedded in the
code index. Repositories with generated, source-backed API references therefore
received a low score even when every entry linked to its implementation.

## Decision

`scan_documentation` now reports three distinct metrics:

- `inline_doc_coverage`: indexed symbols with an inline summary or docstring.
- `source_reference_coverage`: indexed symbols linked by the manifest-declared
  source reference at the exact file and declaration line.
- `symbol_doc_coverage`: the union used by the weighted documentation score.

The scanner accepts repository-local Markdown links and canonical GitHub blob
links for the repository named by `docs/documentation-manifest.json`. A target
outside the repository, a missing file, a wrong repository name, or a line that
does not match an indexed declaration does not count.

## Verification

- Focused documentation and verify tests: 72 passed.
- Full suite: 1,661 passed, 1 skipped.
- Ruff passed; mypy passed across 130 source files.
- Generated references and project-memory lint passed.
- Strict self-verification passed 18/18 with documentation score 95/100.
- Source and wheel packages built successfully.
