# Changelog

## [2.9.0] — 2026-04-20

### LSP deepening — precision layer on top of the regex scanners

All changes are strictly additive: when no LSP server is available for the
language, every feature falls back to the existing stdlib path. Set
`FLYTO_LSP_ENABLED=0` to skip LSP globally.

#### Phase 1 — Import resolution (layers + cross-function taint)
- New `src/lsp/resolver.py` — wraps `textDocument/definition` with an open-file memo
- `analyzer/layers.py::resolve_import` now takes optional `line_content` / `line_num_0based` and asks the language server when the static chain (relative → alias → go.mod) comes back empty. Picks up complex tsconfig paths, Python namespace packages, and gopls vendor directories that the heuristic missed

#### Phase 2 — Type-aware taint filter
- New `src/analyzer/type_filter.py` — queries `textDocument/hover` on a source expression and parses the type out of the hover payload (pyright / tsserver / gopls formats all supported)
- `TaintAnalyzer._is_source` post-filters matches: `int`, `bool`, `float`, `datetime`, `UUID`, TS `number` / `boolean` etc. are dropped because string-injection sinks cannot be exploited with non-string values. `TaintAnalyzer._type_filtered` surfaces the FP-suppression count for telemetry

#### Phase 3 — Workspace symbol + call hierarchy
- New `src/lsp/call_graph.py` — walks `textDocument/prepareCallHierarchy` + `callHierarchy/incomingCalls` / `outgoingCalls` up to a bounded depth. Result is the real, type-resolved call graph — same-named functions in different modules no longer collide
- New `src/lsp/workspace_symbols.py` — `workspace/symbol` candidate search across every running language server with dedup
- `tools/references.py::impact_analysis` now merges LSP-resolved indirect callers (depth 2) into its affected list — real blast radius for high-stakes refactors
- New MCP tool `call_hierarchy` — explicit depth-N incoming / outgoing call query

#### Common infrastructure
- New `src/lsp/cache.py` — mtime-keyed in-memory response cache for definition / hover / call-hierarchy results. Bounded at 4096 entries, cleared by `LSPManager.reset_instance()`
- `lsp/client.py` — initialize capabilities now advertise `hover`, `typeDefinition`, `implementation`, `callHierarchy`, `workspace.symbol`
- New client methods: `text_document_hover`, `text_document_type_definition`, `text_document_implementation`, `workspace_symbol`, `text_document_prepare_call_hierarchy`, `call_hierarchy_incoming_calls`, `call_hierarchy_outgoing_calls`

### Environment
- `FLYTO_LSP_ENABLED` — `0` to disable LSP globally (default: on)
- `FLYTO_LSP_TIMEOUT` — per-request timeout in seconds (default: 10)

## [2.8.0] — 2026-04-20

### Added
- **Taint DSL** — unified `taint:` block inside `.flyto-rules.yaml` for project-specific sources / sinks / sanitizers
  - Engine (`TaintAnalyzer`) now reads from `.flyto-rules.yaml → taint:` first, then falls back to the legacy `taint_rules.yaml` / `.flyto-index/taint_rules.yaml`
  - Custom rules are merged on top of built-in defaults — project-declared patterns never replace the framework-aware library
  - CLI writers: `add-taint-source`, `add-taint-sink`, `add-taint-sanitizer`, `list-taint-rules`
  - MCP tools: `add_taint_source`, `add_taint_sink`, `add_taint_sanitizer`, `list_taint_rules`
  - New `analyzer/taint_dsl.py` module (YAML CRUD only; analysis stays in `analyzer/taint.py`)
- **Architecture Layer Rules** — declarative layer membership and import graph enforcement via `.flyto-rules.yaml`
  - New `layers:` schema: `name`, `paths` (glob), `can_import` (whitelist), `cannot_import` (blacklist), `reason`
  - New `cross_imports_deny:` schema for point-to-point forbidden edges
  - Import graph walker supports Python, TypeScript/JavaScript, Vue, Go (via `go.mod` module path)
  - `tsconfig.json paths` auto-resolved as aliases
  - CLI: `flyto-index layers <path>` (human report) and `--json --fail-on-violation` (CI gate, exits non-zero)
  - CLI: `flyto-index add-layer` — writes a layer definition into `.flyto-rules.yaml`
  - MCP tools: `check_layers`, `add_layer`
  - Violations automatically flow through the `audit` smart tool — no change on consumer side
- `analyzer/layers.py` module (stdlib-only core, PyYAML used only for write-back)

## [1.4.0] — 2026-03-11

### Added
- `flyto-index setup .` — single command that does everything: scan + CLAUDE.md + MCP config
- `flyto-index setup . --remove` — clean uninstall (removes CLAUDE.md section + MCP settings)
- Auto-detects Python path for MCP server configuration

### Changed
- README simplified to two-line install: `pip install flyto-indexer` + `flyto-index setup .`
- `setup-claude` kept for backward compatibility but `setup` is now the recommended command

## [1.3.2] — 2026-03-11

### Changed
- `setup-claude` template now includes auto-index instructions — tells AI to run `flyto-index scan .` if `.flyto-index/` doesn't exist

## [1.3.1] — 2026-03-11

### Added
- `flyto-index setup-claude` CLI command — auto-appends task contract and tool usage instructions to CLAUDE.md
  - Idempotent (skips if already added)
  - `--remove` flag to cleanly remove the section
  - Uses HTML comment markers to avoid interfering with other CLAUDE.md content

## [1.3.0] — 2026-03-11

### Added
- **Task Contract system** (`analyze_task`, `task_gate_check`) — multi-dimensional risk assessment with data-driven execution plans
  - 6 dimensions: blast_radius, breaking_risk, test_risk, cross_coupling, complexity, rollback_difficulty
  - Execution plan: concrete tool call sequences with pre-filled args and step dependencies
  - Gate checks: phase-based validation before proceeding
  - Signal-based scoring with cross-dimension constraint escalation
  - Strategy mode override by dimension levels
  - index_confidence metric for data completeness
- Tool count: 30 → 32

### Changed
- Refactored tool dispatch into focused modules (`search`, `references`, `code_info`, `maintenance`, `task_analysis`)
- Extracted `IndexStore` from `mcp_server.py` for cleaner separation of concerns
- Improved `code_health_score` reuse in project signals

## [1.2.3] — 2026-02-12

### Fixed
- Improved scanners, analyzers, and dead code detection accuracy
- Better health score formulas for documentation and modularity metrics

## [1.2.1] — 2026-02-11

### Removed
- Dual-AI tool definitions from MCP server (strategic pivot to A+C strategy)

### Added
- Index metadata fields

## [1.2.0] — 2026-02-09

### Added
- Auto-reindex on file changes (`check_and_reindex`)
- `impact_from_diff` — git diff → symbol impact analysis
- Semantic diff analysis
- MCP resources support

## [1.1.0] — 2026-02-06

### Added
- 6 code quality MCP tools: `find_dead_code`, `find_complex_functions`, `suggest_refactoring`, `find_duplicates`, `find_stale_files`, `find_todos` (23 → 29 tools)
- Tool annotations (`readOnlyHint`, `openWorldHint`)
- MCP protocol upgrade from 2024-11-05 to 2025-11-25
- BM25 search ranking
- Cross-language API graph (Python ↔ TypeScript/Vue)
- Live reindex capability
- `install-hook`, `demo`, `check` CLI commands

### Fixed
- 7 CodeQL security alerts resolved

## [1.0.0] — 2026-01-30

### Added
- Initial release — MCP server for code intelligence
- AST-based indexing for Python, TypeScript/JS, Vue, Go, Rust, Java
- Impact analysis, dependency graph, reverse index
- Incremental indexing with content hash tracking
- CLI: `flyto-index scan`, `flyto-index impact`

---

## Roadmap (V2 — not yet started)

Priorities depend on real-world usage feedback from V1.

- **Execution plan consumer** — AI agent integration that actually follows the generated execution plan steps
- **Gate history tracking** — session-aware gates that remember which phases were completed (currently stateless)
- **Feedback loop** — post-task result recording (success/failure, actual files changed) to improve future scoring
- **Multi-target dependency analysis** — analyze interactions between targets, not just individual target scoring
