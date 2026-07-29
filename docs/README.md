# Flyto2 Indexer Documentation

Flyto2 Indexer builds a local code-intelligence graph and exposes it through a
CLI, MCP server, Python package, and optional localhost HTTP bridge. Start with
the path that matches the work you need to do.

## Start Here

| Goal | Document |
|---|---|
| Install and complete a first scan | [Root README](../README.md#try-it-in-60-seconds) |
| Understand shipped capabilities | [Feature guide](FEATURES.md) |
| Use the command line | [CLI guide](CLI.md) |
| Connect an AI coding client | [MCP guide](MCP.md) |
| Configure scanning, policies, and LSP | [Configuration](CONFIGURATION.md) |
| Run local and CI release gates | [Verification](VERIFICATION.md) |
| Reproduce the Decision Grill closed loop | [Decision Grill test protocol](GRILL_TESTING.md) |
| Review the dated Decision Grill evidence | [Decision Grill closure report](GRILL_TEST_REPORT_2026-07-29.md) |
| Evaluate data handling and threats | [Security model](SECURITY_MODEL.md) |
| Understand the design and evidence model | [Technical whitepaper](WHITEPAPER.md) |

## Generated Reference

The [generated reference index](reference/README.md) is the exhaustive,
source-backed catalog. It covers every non-test Python declaration, CLI command
and argument, published and compatibility MCP tool, local HTTP operation,
environment variable, scanner default, and built-in rule file.

Regenerate it from the repository root after changing a documented surface:

```bash
python3 scripts/generate-reference.py
python3 scripts/generate-reference.py --check
```

Do not edit files under `docs/reference/` by hand.

## Maintainer Memory

- [Project boundary](../PROJECT.md)
- [Architecture](../ARCHITECTURE.md)
- [Current state](../STATE.md)
- [Roadmap](../ROADMAP.md)
- [Decisions](../DECISIONS.md)
- [Changelog](../CHANGELOG.md)
- [Contributor guide](../CONTRIBUTING.md)
- [Documentation coverage manifest](documentation-manifest.json)
