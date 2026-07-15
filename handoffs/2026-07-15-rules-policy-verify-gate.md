# 2026-07-15 Rules Policy Verify Gate

## Summary

`flyto-indexer` now treats repository rules as an active architecture gate
instead of a passive placeholder. The root `.flyto-rules.yaml` defines real
layers for foundation, scanners, analyzers, index core, runtime services, tool
surfaces, and entrypoints.

## What Changed

- Replaced the empty `.flyto-rules.yaml` placeholder with a real rules/layers
  contract.
- Added `rules_policy` to `src/verify.py`.
- Added verification regressions proving a valid layer edge passes and a
  scanner-to-tool-surface import fails.
- Added README files for top-level directories so documentation coverage is
  not blocked by missing module entry points.

## Verification

```sh
python -m ruff check src/verify.py tests/test_verify.py
python -m pytest tests/test_verify.py tests/test_rules.py tests/test_architecture.py -q
python -m src.cli verify /Users/chester/flytohub/flyto-indexer --full-scan --strict --json
python -m src.cli layers /Users/chester/flytohub/flyto-indexer --json
```

Current evidence:

- verify: 18 pass, 0 warn, 0 fail
- rules policy: 9 rules checked, 7 layers, 226 files, 313 local edges, 0 violations
- docs score: 85, no suggestions
