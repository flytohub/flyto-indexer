# Indexer Source

`src/` owns the installable Flyto2 Indexer package: scanners, graph storage,
context and impact analysis, policy checks, verification, CLI dispatch, MCP,
and the optional localhost HTTP bridge.

Start with [Architecture](../ARCHITECTURE.md), then use the
[feature guide](../docs/FEATURES.md) for capability ownership and the generated
[Python reference](../docs/reference/python-api.md) for declaration-level links.
Public behavior changes must update the relevant narrative document, tests, and
generated reference in the same commit.
