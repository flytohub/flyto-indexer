# Generated static asset scan boundary

Owner: codex
Branch: codex/convergence-closure
Date: 2026-08-24

## What changed

Symbol indexing ignores the exact `static/assets` path sequence and non-Python
taint analysis classifies the same path as generated. Focused tests cover a
hashed bundle below a Python static backend while retaining ordinary authored
JavaScript.

## Why

Cloud strict verification parsed minified Vite chunks as authored source,
reported three parser errors, and raised three framework-XSS findings. The
security scanner already excluded this generated boundary; indexing and taint
analysis now agree with it.

## Verified

Focused incremental and non-Python taint coverage passed 40 tests. The complete
Indexer suite passed 2,459 tests with 4 skips and 8 deliberate deselections;
Ruff, mypy, quality-debt, language-evidence, version, project-memory, generated
reference, and whitespace checks pass. Downstream Cloud strict verification is
also green at 19/19: 3,123 files scanned, zero parser errors, zero secret
findings, and zero high-risk taint findings.

## Not verified

No external repository corpus or language-server enrichment was exercised.

## Follow-ups

Keep generated-output exclusions path-precise and add a regression before
recognizing another framework output convention.
