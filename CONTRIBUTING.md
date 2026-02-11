# Contributing to Flyto Indexer

Thanks for your interest in contributing!

## Getting Started

```bash
# Clone the repo
git clone https://github.com/flytohub/flyto-indexer.git
cd flyto-indexer

# Install in dev mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run linting
ruff check src/
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

- Python 3.10+ compatible (no walrus operator assumptions, no 3.12+ f-string features)
- Use `ruff` for linting and formatting
- Keep it simple — standard library only for core functionality
- Write tests for new features

## Pull Requests

1. Fork the repo and create your branch from `main`
2. Add tests for any new functionality
3. Make sure all tests pass: `pytest tests/ -v`
4. Make sure linting passes: `ruff check src/`
5. Submit your PR with a clear description of what and why

## Adding a New Language Parser

1. Create `src/scanner/your_language.py`
2. Extend `BaseScanner` from `src/scanner/base.py`
3. Implement `scan_file()` to extract symbols (functions, classes, methods)
4. Add tests in `tests/test_scanner_your_language.py`
5. Register the scanner in the scanner factory

See `src/scanner/python.py` for a reference implementation.

## Questions?

Open an issue on GitHub or email dev@flyto2.net.
