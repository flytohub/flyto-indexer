# Public Package Boundary And Runtime Parity

## Summary

- Removed product-specific release modules, manifests, evidence writers, and
  product deployment documentation from the public indexer source tree.
- Moved the still-required workspace GitHub Actions evidence audit to its
  private product owner before removing the public copy.
- Added a checkout-aware package version, `flyto-index --version`, and a local
  installer that verifies executable/source version parity.
- Added a regression test that rejects product release modules, manifests, and
  company packaging commands in the public tree and CLI.

## Boundary

The public package owns static analysis, local indexing, dependency and API
closure, security checks, and verification. It does not own edition policy,
commercial packaging, product release manifests, or workspace release verdicts.

## Verification

```bash
pytest tests/test_version.py tests/test_verify.py
ruff check src tests
mypy src
python -m build
python -m src.cli verify . --full-scan --strict --json
scripts/install-local-cli.sh
flyto-index --version
```
