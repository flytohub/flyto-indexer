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

Large repositories may declare repository-local glob patterns such as
`docs/reference/source-*.md`. Each matched page is resolved before reading, and
absolute, malformed, or repository-escaping paths are ignored rather than
trusted or allowed to crash verification.

## Verification

- Focused documentation regression tests passed, including split-page and
  absolute-glob cases.
- Full suite: 1,664 passed, 1 skipped.
- Ruff passed; mypy passed across 130 source files.
- Generated references and project-memory lint passed.
- Strict self-verification passed 18/18 with 223 files, 3,714 scanned symbols,
  zero warnings, and documentation score 95/100.
- Source and wheel packages built successfully; the local CLI installation
  reported version 2.14.2.
