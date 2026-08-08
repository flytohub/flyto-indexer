# Claude Notes

@AGENTS.md

The rules above are shared with Codex and are the single source of truth. Do not
restate or paraphrase them here — a second copy drifts, and the two agents then
work from different instructions. Repo rules go in `AGENTS.md`; only the
Claude-specific handoff rules below belong in this file.

## Cross-agent handoff

This repo is edited by both Codex and Claude, sometimes on the same day.

- Before starting, read the newest `handoffs/_registry.md` entry and check its
  `Owner` and `Branch`.
- If an `Active` entry is owned by the other agent, do not edit the same files on
  the shared branch. Work on `claude/<topic>` or pick up different work.
- When you finish something durable, write a handoff with `Owner: claude` and the
  branch you worked on. Conversation-only context is not a release record.
- State what you actually verified and what you did not. The other agent will
  treat your handoff as fact.

## Shared code intelligence

Both agents query the same index through the `flyto-indexer` MCP server —
registered in `.mcp.json` for Claude and `~/.codex/config.toml` for Codex.

- A `post-commit` hook reindexes this repo automatically, so committed work by the
  other agent is visible to you. Uncommitted work is not.
- If `search` or `impact` results look stale, run `flyto-index scan .` first.
- Agent scratch checkouts under `.claude/worktrees/` are excluded from the index.
  Delete them when the work is merged; they are full copies of the repo.

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
