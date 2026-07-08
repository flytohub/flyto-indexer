# Changelog

## Unreleased

## [2.12.2] - 2026-07-09

### Fixed
- The generated `export-upstream-patches.py` flow-back tool crashed with
  `ValueError: ... is not in the subpath of ...` when `--output` pointed to a
  directory OUTSIDE the repo (it did `patch_path.relative_to(root)` on the
  report line). It now falls back to the absolute path via a `display_path()`
  helper, so both in-repo and external output dirs work. Verified end-to-end:
  a simulated warroom PR touching a source file produces a correctly
  prefix-stripped `<repo>.patch`, and a generated-only file is flagged in
  `REVIEW_GENERATED.md` — the backward (warroom→source) half of the
  bidirectional loop now runs cleanly.

## [2.12.1] - 2026-07-08

### Fixed
- `flyto2-open-core-export` was silently DROPPING 29 hand-authored CE-only files
  that existed only in the published warroom repo (CE distribution docs, install
  tooling, cloud-bundle fixtures, positioning material, `.env.example`,
  `CHANGELOG.md`, generated READMEs). They had no source-repo home, so every
  clean regen deleted them. They are now bundled verbatim under
  `open_core_manual/` (shipped in the wheel) and byte-copied into the generated
  tree, so regeneration reproduces them exactly. `_audit_generated_release` now
  hard-fails if any of them are missing (`manual_ce_file_missing`).

### Changed
- `export-upstream-patches.py` flow-back now classifies the full CE-only surface
  as generated-only: added `scripts/` to `GENERATED_REVIEW_PREFIXES` (was an
  existing gap — the generated `scripts/audit-*.py` weren't covered) and added
  `.env.example`, `CHANGELOG.md`, `packages/README.md` to `GENERATED_REVIEW_FILES`,
  so a warroom-side edit to any of them routes back to the generator instead of a
  misdirected source patch.

## [2.12.0] - 2026-07-08

### Added
- `agent-audit` findings now carry a **deterministic exploitability score**
  (0–100) built from category base + confirmation signals + MCP-reachability,
  binned into **confirm / review / drop** bands (`BAND_CONFIRM=70`,
  `BAND_DROP=35`). This is the "minimize-LLM" gate: high-confidence findings
  confirm and low-confidence findings drop without any model call; only the
  ambiguous review band is a candidate for optional downstream LLM triage.
  `score_factors` exposes the per-signal breakdown.

### Changed
- `flyto2-open-core-export` now emits a top-level **`LICENSE`** (full Apache-2.0,
  so GitHub detects the license on the generated flyto-warroom CE repo), a
  **`CLA.md`** (Apache-style inbound grant + dual-edition relicense right), and
  a **`.github/workflows/cla.yml`** (cla-assistant PR gate). `CONTRIBUTING.md`
  gains a CLA section. All three are registered in both the export-time release
  audit and the embedded downstream `audit-release-tree.py` required lists, so
  regeneration no longer drops them.

## [2.11.1] - 2026-07-08

### Added
- `agent-audit` now emits a **CWE id** and stable **rule_id** per finding, and
  an **mcp_reachable** flag — true when the sink is reachable (BFS over
  intra-file call edges) from an MCP/module entrypoint (`@register_module`/tool
  decorators, FastAPI routes, BaseModule `execute`/`run`), i.e. its params are
  attacker-influenced. This is the prioritization / AI-triage gate.
- Three new `agent-audit` classes (same required-guard / caller-controlled
  model): `code-injection` (CWE-95, eval/exec), `path-traversal-read` (CWE-22,
  open-read / read_text/read_bytes), and `ssti` (CWE-1336,
  render_template_string / Template.from_string). (2.11.0 added
  `command-injection` and `unsafe-deserialization`.)
- `AgentPolicyAnalyzer.parse_failures` counter so unparseable files are not a
  silent coverage gap.

### Tests
- `tests/test_agent_policy.py` pins every class (positive + guarded-negative)
  and the mcp_reachable signal.

## [2.11.0] - 2026-07-08

### Added
- Added `flyto-index agent-audit` — an AI-agent / MCP / sandbox security policy
  analyzer that detects vulnerability classes generic SAST and the stock taint
  engine miss (policy / absence / cross-function bugs): outbound HTTP on a
  caller-controlled URL with no SSRF guard, guarded HTTP modules that follow
  redirects unrevalidated, unauthenticated state-changing routes,
  attacker-influenced `os.getenv`/`environ` reads, env credentials reachable at
  a caller-controlled `base_url`, and file writes to caller-controlled paths
  without the sandbox guard. Emits a high/medium/low confidence tier (the gate
  for downstream AI triage). Pure stdlib; no external dependencies.
- Added `flyto2-product-gate` to validate the Flyto2 workspace product-line
  registry, repo classification, project memory completeness, and health
  targets before release readiness review.
- Added `flyto2-memory-bootstrap` to scaffold missing project-memory files,
  workflow docs, and handoff registries from the Flyto2 manifest without
  overwriting existing repo notes.
- Added `flyto2-release-packet` to generate an evidence-backed Flyto2 workspace
  inventory, deliverable matrix, residual blocker list, and readiness verdict.
- Added fresh evidence enforcement to `flyto2-release-packet` through
  `--fresh-evidence-dir`, `--require-fresh`, and `--run-start`.
- Added the Flyto2 product-line manifest and 2026-06-21 health baseline used by
  the new gate.
- Added the Flyto2 evidence gate manifest and `--evidence-gates` release-packet
  option so product readiness is judged by product-line, deployment, security,
  visibility, and operability proof instead of health score alone.
- Added the `deterministic_product_verification` release-packet deliverable and
  `warroom.product_verification.v1` fresh evidence contract for Product
  Verification / Warroom runs.
- Added `scripts/write_product_verification_evidence.py` for local dry-run
  Product Verification release artifacts.
- Added the `public_site_verification` release-packet deliverable,
  `flyto2.public_site_verification.v1` fresh evidence contract, and live public
  site evidence helper for DNS/TLS/route/browser/SEO-GEO proof.
- Added `scripts/write_continuous_release_evidence.py` to turn current local
  release-packet source evidence into fresh workspace, architecture, billing,
  RBAC, state-machine, enterprise, GEO, i18n, security, and browser-smoke digest
  artifacts without hiding P0/P1 findings from contract artifacts.

### Changed
- Updated `code_health_score` complexity scoring to combine high-complexity
  function density, cumulative severity burden, and the top hotspot score
  instead of treating every `score >= 5` function as equal.
- Recalibrated the Flyto2 health baseline for `flyto-ai`, `flyto-core`, and
  `flyto-indexer` using the severity-weighted complexity gate while preserving
  complex-function counts, burden, and top-hotspot evidence in the reasons.
- Lifted the Flyto2 health baseline for `flyto-i18n` from C to B after the
  2026-06-22 reverse audit refactored sync/add-locale tooling and rebuilt the
  index.
- Lifted the Flyto2 health baseline for `flyto-vscode` from C to B after the
  2026-06-22 reverse audit refactored webview payload getters, added compiled
  regression tests, and rebuilt the index.
- Lifted the Flyto2 health baseline for `flyto-modules-pro` from C to B after
  the 2026-06-22 reverse audit refactored enterprise LDAP, SAML metadata, Azure
  OCR, and map-reduce reducer hotspots with guard tests.
- Added explicit non-core health exemptions for docs-only, no-symbol, or
  unsupported-language repos while keeping core repo exemptions blocked.
- Reframed Flyto2 health grades as minimum hygiene signals. Missing P0/P1
  evidence now blocks production readiness even when all repo scores are high,
  and non-core score regressions are warnings unless product evidence depends on
  that repo.
- Extended Flyto2 product-line and release-operation gates so Cloud/Apps,
  Security, Zero-person Agent, and release operations depend on deterministic
  Product Verification evidence.
- Extended visibility, Big Data / Intelligence, and release-operation gates so
  public `flyto2.com` reachability and AI crawler evidence are judged separately
  from static SEO documentation.

### Fixed
- Fresh release evidence contracts now propagate P0/P1 findings into the
  release packet. Schema-valid public-site evidence with AI crawler P1 findings
  is now marked `blocking_findings` instead of being accepted as production
  proof.
- Dead-code detection now treats VitePress markdown component tags such as
  `<BlogHero />` as live component references.
- Cleaned existing test-suite lint violations so `ruff check src tests` can run
  as a full release gate.

## [2.10.3] — 2026-06-13

### Fixed
- Improved `find_dead_code` precision for MCP audits:
  - Treat Python registry/dispatch-table callbacks and decorated functions as live.
  - Treat Go exported symbols and DTO/model/type contracts as public surface, not deletion candidates.
  - Ignore test, fixture, example, and Semgrep fixture paths for production dead-code cleanup.
  - Detect same-project type/class references from source text so Go same-package DTOs are not falsely reported.
- Aligned dead-code filtering across MCP maintenance tools, tag generation, and health scoring.

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
