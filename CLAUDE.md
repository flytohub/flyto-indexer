<!-- flyto-indexer begin -->
## Code Intelligence (flyto-indexer)

This project is indexed by [flyto-indexer](https://pypi.org/project/flyto-indexer/). Use its MCP tools for code changes.

### First-time setup
If `.flyto-index/` does not exist in the project root, run this before using any flyto-indexer tools:
```bash
flyto-index scan .
```

### 5 Smart Tools (v2.3.0+)

flyto-indexer exposes 5 consolidated tools. Each one auto-enriches results with related data — no need to pick between dozens of granular tools.

| Tool | When to use | Auto-enrichment |
|------|------------|-----------------|
| `search` | Find code by keyword or natural language | Callers (top 5), file siblings, concept expansion |
| `impact` | What breaks if I change this? | Cross-project impact, test files, edit preview |
| `audit` | Code quality review | Auto-expands weak dimensions (security, complexity, dead code, coverage), git hotspots |
| `task` | Plan/gate/validate workflow | Untested changes on validation failure |
| `structure` | Project overview, APIs, dependencies | APIs, categories, index status, contract drift |

### Workflow for code changes
1. `task(action='plan')` — get risk dimensions, constraints, and execution plan
2. Follow `execution_plan` steps in order — each step has tool name and pre-filled args
3. `task(action='gate')` at gate steps — server-side enforcement blocks skipping gates
4. Respect `constraints.max_files_per_step`
5. `task(action='validate')` — run linter + tests after making changes

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
- Supports 8 ecosystems: npm, pypi, Go, Rust, Maven/Gradle, PHP, Ruby, Docker
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
<!-- flyto-indexer end -->
