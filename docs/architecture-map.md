# Architecture Map

## Source Areas

| Area | Ownership | Primary paths |
|---|---|---|
| Models and safe I/O | Shared records, signatures, serialization, bounded reads | `src/models.py`, `src/safe_io.py`, `src/safe_xml.py` |
| Scanners | Language, manifest, route, framework, secret, license, Docker, and IaC extraction | `src/scanner/`, `src/*_scanner.py` |
| Index core | Symbol resolution, reverse graph, lexical and semantic indexes | `src/indexer/`, `src/search_index.py`, `src/reverse_index.py`, `src/semantic.py` |
| Analyzers | API drift, quality, security, taint, layers, documentation, and Git evidence | `src/analyzer/`, `src/auditor/`, `src/quality.py` |
| Runtime services | Context, profiles, sessions, diff impact, LSP enrichment, and task state | `src/context/`, `src/profile/`, `src/lsp/`, `src/tools/task_analysis.py` |
| Tool services | Focused operations shared by adapters | `src/tools/`, `src/verify.py` |
| Adapters | CLI, MCP stdio, and localhost HTTP | `src/cli.py`, `src/mcp_server.py`, `src/api_server.py` |
| Contracts and automation | Defaults, rule corpus, packaging, generated docs, CI, and publication | `config/`, `pyproject.toml`, `scripts/`, `.github/workflows/` |

The generated [module inventory](reference/modules.md) and
[Python API reference](reference/python-api.md) provide the exact file and
declaration-level map.

## Dependency Direction

Foundation and scanners must not depend on protocol adapters. Analyzers and
index services are reusable from CLI, MCP, CI, or Python. Tool services compose
those lower layers, and entrypoints adapt them to protocol-specific inputs and
outputs. `.flyto-rules.yaml` enforces these boundaries.

## Cross-Repository Edges

- `flyto-engine`, `flyto-code`, `flyto-admin`, and `flyto-core` consume indexer
  evidence for impact, dependency, security, documentation, and release checks.
- Repositories own their `.flyto-rules.yaml`; this package owns parsing and
  enforcement semantics.
- CI consumes the CLI. Agent integrations consume MCP. Both use the same index
  and analyzers.
- JSON export is an explicit handoff boundary; the indexer does not silently
  upload source or findings.
