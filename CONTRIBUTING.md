# Contributing to Flyto2 Indexer

Thanks for your interest in contributing!

## Getting Started

```bash
git clone https://github.com/flytohub/flyto-indexer.git
cd flyto-indexer

pip install ".[dev]"

pytest tests/ -v

# Run release checks
python3 scripts/sync-version.py --check
python3 scripts/generate-reference.py --check
ruff check src/ tests/ scripts/
mypy src/
```

## What to Contribute

**Good first issues:**
- Add support for a new language parser (see `src/scanner/` for examples)
- Improve an existing parser's symbol extraction
- Add test cases for edge cases in parsing
- Improve documentation or integration guides

**Bigger contributions:**
- New MCP tools
- Performance improvements to indexing
- Better dependency graph resolution

## Code Style

- Python 3.11+ compatible; keep syntax valid for every version declared in `pyproject.toml`
- Use `ruff` for linting and formatting
- Keep it simple — standard library only for core functionality
- Write tests for new features

## Pull Requests

1. Fork the repo and create your branch from `main`
2. Add tests for any new functionality
3. Make sure all tests pass: `pytest tests/ -v`
4. Run the full gate in [docs/VERIFICATION.md](docs/VERIFICATION.md#indexer-release-gate)
5. Submit your PR with a clear description of what and why

## Adding a New Language Parser

1. Create `src/scanner/your_language.py`
2. Extend `BaseScanner` from `src/scanner/base.py`
3. Implement `scan_file()` to extract symbols (functions, classes, methods)
4. Add tests in `tests/test_scanner_your_language.py`
5. Register the scanner in the scanner factory

See `src/scanner/python.py` for a reference implementation.

## Documentation Changes

Update the durable guide that owns the behavior and add or revise its entry in
`docs/documentation-manifest.json`. Public Python declarations, CLI arguments,
MCP schemas, HTTP operations, defaults, environment variables, and built-in
rule files are generated from source:

```bash
python3 scripts/generate-reference.py
python3 scripts/generate-reference.py --check
```

Do not hand-edit `docs/reference/`. If the package version changes, run
`python3 scripts/sync-version.py` and commit all synchronized manifests.

## Questions?

Open an issue on GitHub or email dev@flyto2.com.
