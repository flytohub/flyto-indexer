# Architecture Map

## Core Areas

- `src/cli` and CLI entry points: local command execution.
- `src/mcp_server.py`: MCP surface for agent-driven code intelligence.
- `src/indexer`, `src/analyzers`, and related modules: scanners, dependency
  extraction, symbols, APIs, docs, security, and verify logic.
- `tests/`: self-tests for indexing, MCP, verification, and packaging.
- `scripts/`: local project-memory and release support gates.

## Cross-Repo Edges

- `flyto-engine`, `flyto-code`, `flyto-admin`, and `flyto-core` use the indexer
  for impact, dependency, security, docs, and verify closure.
- Project-specific `.flyto-rules.yaml` files may extend layer and taint rules.
- CI jobs consume the CLI, while agents may use MCP tools for deeper navigation.
