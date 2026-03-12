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
- **Learned ConceptGraph**: Semantic search learns term relationships from file co-location, import graph, and shared callers (PMI scoring). No manual keyword maps.
- **Execution Guard**: Server-side enforcement prevents skipping execution plan gates. If blocked, the response includes a `recovery_plan` with exact next steps.
- **Atomic writes**: Index files written via temp+rename to prevent corruption on crash.
- **Smart auto-reindex**: Detects file changes every 10s (fast mtime check) and full scan every 5min. Only reindexes affected projects.
<!-- flyto-indexer end -->
