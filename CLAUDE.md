<!-- flyto-indexer begin -->
## Code Intelligence (flyto-indexer)

This project is indexed by [flyto-indexer](https://pypi.org/project/flyto-indexer/). Use its MCP tools for code changes.

### First-time setup
If `.flyto-index/` does not exist in the project root, run this before using any flyto-indexer tools:
```bash
flyto-index scan .
```

### Smart Tools (v2.11+)

flyto-indexer exposes a small set of consolidated MCP tools. Each one
auto-enriches results with related data — no need to pick between dozens of
granular tools. For code changes, keep the loop closed with pre-change
exploration and post-change verification.

| Tool | When to use | Auto-enrichment |
|------|------------|-----------------|
| `search` | Find code by keyword or natural language | Callers (top 5), file siblings, concept expansion |
| `impact` | What breaks if I change this? | Cross-project impact, test files, edit preview |
| `audit` | Code quality review | Auto-expands weak dimensions (security, complexity, dead code, coverage), git hotspots |
| `task` | Plan/gate/validate workflow | Untested changes on validation failure |
| `structure` | Project overview, APIs, dependencies | APIs, categories, index status, contract drift |
| `verify` | Single-project closed-loop gate | Index integrity, context, impact, weak scans, CI, package integrity, MCP runtime, change hygiene, policy, baseline |
| `verify_workspace` | Multi-project closed-loop gate | Aggregated verification, changed-only mode, baseline regression gating |

### Workflow for code changes
1. `task(action='plan')` — get risk dimensions, constraints, and execution plan
2. Use `search` and `impact` before editing shared symbols or public APIs
3. Follow `execution_plan` steps in order — each step has tool name and pre-filled args
4. `task(action='gate')` at gate steps — server-side enforcement blocks skipping gates
5. Respect `constraints.max_files_per_step`
6. `task(action='validate')` — run linter + tests after making changes
7. Run `verify` or `verify_workspace` before committing or handing off

### Key features
- **Smart tools**: 5 intent-based entry points replace 45+ granular tools. Association-based triggering auto-enriches results server-side.
- **Incremental indexing**: Only rebuilds reverse_index, BM25, and dependencies for changed files. Semantic index lazy-rebuilds on next search. 10-50x faster auto-reindex.
- **LSP integration**: Optional type-aware references via pyright, tsserver, gopls, rust-analyzer. Zero deps — graceful fallback when no LSP available.
- **Learned ConceptGraph**: Semantic search learns term relationships from file co-location, import graph, and shared callers (PMI scoring). No manual keyword maps.
- **Enhanced Go scanner**: Struct method deps, interface implementation tracking, struct embedding, type aliases, const/var detection.
- **Execution Guard**: Server-side enforcement prevents skipping execution plan gates. If blocked, the response includes a `recovery_plan` with exact next steps.
- **Atomic writes**: Index files written via temp+rename to prevent corruption on crash.
- **Smart auto-reindex**: Detects file changes every 10s (fast mtime check). Incremental updates proportional to change set.

### Dependency Scanner
- `flyto-index deps .` — scans all package manifests
- Supports 8+ ecosystems: npm, pypi, Go, Rust (Cargo.toml + Cargo.lock),
  Maven/Gradle, PHP (composer.json + composer.lock), Ruby (Gemfile.lock),
  Docker, Dart (pubspec), Swift, .NET, Elixir
- Reads lockfiles for pinned versions
- Detects version conflicts across monorepo
- Available as MCP tool: `list_dependencies`
- Available in smart tool: `structure(focus="packages")`

### Project Profile
- `flyto-index profile .` — comprehensive project fact sheet
- `flyto-index profile . --json` — JSON output for LLM consumption
- `flyto-index profile . --compact` — summary only
- Collects: structure, APIs (classified as definition/call/service), models with fields, dependencies, module connections, patterns, infrastructure, git info
- API classification:
  - `api_definitions` — backend routes
  - `api_calls_internal` — frontend-to-backend calls
  - `api_calls_external` — 3rd party API calls
  - `services` — SDK integrations (Firebase, Stripe, OpenAI, etc.)
- 15+ pattern detection: auth, websocket, queue, cron, orm, migration, i18n, caching, etc.
- Available as MCP tool: `project_profile`
- Available in smart tool: `structure(focus="profile")`

### Scanner improvements
- **Python**: Class field extraction (Pydantic, dataclass, annotations)
- **Go**: Struct field extraction + HTTP handler detection (stdlib, gin, echo, fiber)
- **TypeScript**: Interface/type field extraction + backend route detection (Express, Hono, Fastify)
- Symbol metadata now includes `fields` key for classes/interfaces/structs

### Secret / License / Documentation scanners
- `flyto-index secrets .` — 18 regex patterns, false positive filtering for docs/examples/HTML
- `flyto-index license .` — project + dependency license detection, copyleft warning
- `flyto-index docs .` — README scoring, API/module/inline doc coverage, suggestions
- All integrated into `project_profile` output and available as MCP tools

### Design principle
**Zero external dependencies.** flyto-indexer runs on pure Python stdlib only.
Features that need external APIs (CVE databases, GitHub API, embedding models)
belong in flyto-code's engine layer, not here.

### flyto-engine Upload (v2.10+)

`flyto-index export` bundles scan results for upload to flyto-engine:

```bash
flyto-index export .                    # basic: profile + taint
flyto-index export . --full             # full: + symbol graph (function-level verify)
flyto-index export . --full --commit X  # CI mode: + commit/branch metadata
```

See `integrations/flyto-engine.md` for full docs, CI examples, and security model.

### Roadmap (all stdlib, no external deps)
- [x] Go/TypeScript token-aware scanning (upgrade from regex) — shipped v2.7
- [x] Call graph — function-level call chain — shipped v2.7
- [x] PR risk analysis — git diff → impact score, affected tests — shipped v2.7
- [x] Data flow / taint-lite — source → variable → sink tracking — shipped v2.7
- [x] Framework-aware analysis — Next.js, Vue, FastAPI, Express — shipped v2.7
- [x] Architecture layers + taint DSL — shipped v2.9
- [x] LSP deepening (pyright/tsserver/gopls/rust-analyzer) — shipped v2.9
- [x] Composite complexity scoring (multi-dimensional) — shipped v2.7.3
- [x] Export command for flyto-engine upload — shipped v2.10
- [ ] Cross-repo analysis — shared package version drift, API contract comparison
- [ ] Dead code confidence scoring — definitely/probably/unknown instead of binary
- [ ] Config analysis — .env, docker-compose, CI workflow structure and risk
<!-- flyto-indexer end -->

## Flyto2 Project Memory Contract

Every Flyto2 repository must keep this project-memory scaffold current:

- `AGENTS.md`: agent operating rules, repo-specific constraints, verification commands.
- `CLAUDE.md`: Claude-facing handoff rules when this repo is edited outside Codex.
- `PROJECT.md`: product purpose, owned surfaces, users, and non-goals.
- `ARCHITECTURE.md`: module boundaries, runtime shape, data flow, and integration points.
- `STATE.md`: current status, known risks, release/deploy state, and last verification.
- `ROADMAP.md`: near-term, later, and explicitly out-of-scope work.
- `tasks.md`: actionable checklist with owners/status when known.
- `DECISIONS.md`: durable architectural/product decisions with dates and rationale.
- `CHANGELOG.md`: user-visible or operator-visible changes.
- `docs/README.md`: index for durable docs in this repo.
- `workflows/*.md`: repeatable agent workflows for idea capture, planning, implementation, bugfix, refactor, investigation, and wrap-up.
- `handoffs/_registry.md`: index of handoffs; new handoffs use `YYYY-MM-DD-topic.md`.

When changing behavior, public copy, deployment, security posture, or frontend UX, update the relevant memory files in the same change. Do not leave stale brand, email, module count, route, or deployment information behind.

## Flyto2 Frontend Quality Gate

Any frontend, website, dashboard, extension webview, app screen, or generated UI in this repository must avoid these eight failures:

1. Ignoring accessibility: every interactive control needs keyboard access, visible focus, semantic HTML or ARIA, sufficient contrast, and useful alt/labels.
2. Missing responsive design: verify mobile, tablet, and desktop; no clipped text, overflow, hidden primary actions, or broken navigation.
3. Weak visual hierarchy: users must immediately see page purpose, primary action, status, and next step.
4. Template-looking UI: reuse Flyto2 design tokens and local components, but tailor layout and copy to the actual product surface.
5. Useless elements: remove decorative or placeholder UI that does not help the workflow, trust, navigation, or comprehension.
6. Unclear hierarchy: controls, cards, tables, panels, and modals must have clear grouping, spacing, headings, and state.
7. Unintuitive navigation: current location, back/forward paths, and cross-links to docs/blog/product pages must be obvious.
8. Hard-to-understand content: copy must be concrete, scannable, current, and consistent with Flyto2 terminology.

Frontend verification must include the relevant automated checks plus manual or screenshot review for responsive layout, accessibility states, navigation clarity, loading/empty/error states, and content readability. Public pages must preserve SEO basics: canonical URL, sitemap coverage, metadata, structured data when relevant, and no broken internal or external links.
