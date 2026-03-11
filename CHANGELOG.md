# Changelog

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
