<div align="center">
  <h1>Flyto Indexer</h1>
  <p>
    <strong>"What breaks if I change this?" — for AI coding assistants</strong>
  </p>
  <p>
    <a href="https://github.com/flytohub/flyto-indexer/actions"><img src="https://github.com/flytohub/flyto-indexer/workflows/CI/badge.svg" alt="CI"></a>
    <a href="https://pypi.org/project/flyto-indexer/"><img src="https://img.shields.io/pypi/v/flyto-indexer.svg" alt="PyPI"></a>
    <a href="https://github.com/flytohub/flyto-indexer/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
  </p>
  <p>
    Impact analysis &bull; Cross-project reference tracking &bull; Code health scoring
    <br/>
    Works with Claude Code, Cursor, Windsurf, and any MCP client
  </p>
</div>

---

<div align="center">
  <img src="demo.gif" alt="Flyto Indexer demo — impact analysis before renaming" width="800">
</div>

AI coding assistants can grep, read files, and write code. But they can't answer **"if I change this function, what else breaks?"** — not without reading every file in every project.

Flyto Indexer builds a **dependency graph** of your codebase and exposes it as MCP tools. One call to `impact_analysis` tells the AI exactly which files, functions, and projects are affected — before a single line is changed.

**Zero dependencies.** Pure Python. Runs locally. No code leaves your machine.

## Quick Start

```bash
pip install flyto-indexer

# Index your project
flyto-index scan /path/to/your/project

# Start MCP server
python -m flyto_indexer.mcp_server
```

Add to Claude Code (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "flyto-indexer": {
      "command": "python3",
      "args": ["-m", "flyto_indexer.mcp_server"]
    }
  }
}
```

<details>
<summary>Running from source</summary>

```bash
git clone https://github.com/flytohub/flyto-indexer.git
cd flyto-indexer && pip install -e .
flyto-index scan /path/to/your/project
```

```json
{
  "mcpServers": {
    "flyto-indexer": {
      "command": "python3",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/path/to/flyto-indexer"
    }
  }
}
```
</details>

> Add `.flyto-index/` to your `.gitignore`.

## The Problem

AI assistants are powerful editors, but they're **structurally blind**:

- `grep` finds text matches — it doesn't know that `useCart()` in `Cart.vue` calls `checkout()` in `cartApi.py`
- Reading every file is slow and wastes context window
- Without a dependency graph, renaming a function is a game of "hope nothing breaks"

## What This Solves

### Impact Analysis

> "I want to rename `validateOrder`. What breaks?"

```
→ impact_analysis("validateOrder")

⚠️ Modifying validateOrder affects 5 call sites:
  → Cart.vue:42 — calls validateOrder() directly
  → CheckoutAPI.py:18 — imports validateOrder
  → test_validators.py:55 — tests validateOrder
  Risk: MEDIUM — 3 files, 2 projects

→ edit_impact_preview("validateOrder", change_type="rename")
  Shows exact code lines that need updating.
```

### Cross-Project Reference Tracking

> "Who uses this shared function across all our repos?"

```
→ cross_project_impact("validateOrder")

  Defined in: backend/src/validators.py
  Used by:
    → frontend (3 references)
    → mobile-app (1 reference)
    → admin-panel (2 references)
  Risk: HIGH — changes affect 3 other projects

→ find_references("validateOrder")
  Returns every caller with file, line number, and confidence level.
```

### Cross-Language API Graph

> "Which frontend components call this Python endpoint?"

```
→ list_apis()

  GET /api/users
    Defined in: backend/routes/user.py (list_users)
    Called by: frontend/views/UserList.vue, frontend/api/users.ts
```

Automatically links Python (FastAPI/Flask) endpoints to TypeScript/Vue callers.

### Code Health & Security

> "Give me a quick quality check before release."

```
→ code_health_score(project="backend")

  Score: 74/100 (Grade: C)
  Complexity:    22/25 — 3/120 functions over 50 lines
  Dead code:     18/25 — 12 unreferenced symbols
  Documentation: 16/25 — 65% of symbols documented
  Modularity:    18/25 — avg 3.6 references per symbol

→ security_scan(project="backend")
  2 critical: hardcoded API keys in config.py
  1 high: SQL string concatenation in queries.py

→ suggest_refactoring(project="backend")
  [high] process_data() — 87 lines, depth=6 → extract sub-functions
  [medium] dead_fn() — unreferenced, 45 lines → safe to remove
```

## MCP Tools

29 tools. The ones that matter most:

### Core — what grep can't do

| Tool | Purpose |
|------|---------|
| `impact_analysis` | Blast radius of changing a symbol |
| `find_references` | All callers/importers with file + line |
| `cross_project_impact` | Track usage across multiple projects |
| `edit_impact_preview` | Preview exact code lines affected by rename/delete/signature change |
| `dependency_graph` | Import chains and reverse dependencies |

### Code Quality

| Tool | Purpose |
|------|---------|
| `code_health_score` | Aggregate 0-100 score with A-F grade |
| `security_scan` | Hardcoded secrets, SQL injection, unsafe functions |
| `find_dead_code` | Unreferenced functions/classes safe to remove |
| `find_complex_functions` | Functions with high nesting, too many params/branches |
| `suggest_refactoring` | Prioritized refactoring suggestions |
| `find_duplicates` | Copy-pasted code blocks |
| `find_stale_files` | Files untouched for months (via git) |
| `find_todos` | TODO/FIXME/HACK markers |

<details>
<summary>All 29 tools</summary>

### Search & Discovery
| Tool | Description |
|------|-------------|
| `search_code` | BM25-ranked symbol search across all projects |
| `get_symbol_content` | Full source code of a function/class |
| `get_file_symbols` | All symbols defined in a file |
| `get_file_info` | File purpose, category, keywords, dependencies |
| `get_file_context` | One-call summary: symbols + deps + test file |
| `fulltext_search` | Search inside comments, strings, TODO markers |

### Project Overview
| Tool | Description |
|------|-------------|
| `list_projects` | All indexed projects with statistics |
| `list_categories` | Code categories (auth, payment, etc.) |
| `list_apis` | API endpoints with cross-language callers |
| `check_index_status` | Index freshness check |

### File Metadata
| Tool | Description |
|------|-------------|
| `find_test_file` | Find test file for source file (or vice versa) |
| `get_description` | Semantic one-liner for a file |
| `update_description` | Write/update a file description |

### Session & Indexing
| Tool | Description |
|------|-------------|
| `session_track` | Track workspace events for search boosting |
| `session_get` | Inspect session state |
| `check_and_reindex` | Detect changes and live-reindex |

</details>

## Supported Languages

| Language | Parser | What's Extracted |
|----------|--------|-----------------|
| Python | AST | Functions, classes, methods, decorators, API endpoints |
| TypeScript/JavaScript | Custom | Functions, classes, interfaces, types, exports, API calls |
| Vue | SFC | Components, composables, emits, props, API calls |
| Go | Custom | Functions, structs, methods, interfaces |
| Rust | Custom | Functions, structs, impl blocks, traits |
| Java | Custom | Classes, methods, interfaces, annotations |

## How It Works

```
your-project/
├── src/            ← Your code (any language)
└── .flyto-index/   ← Generated index (add to .gitignore)
    ├── index.json       # Symbols + dependency graph + reverse index
    ├── content.jsonl    # Source code (lazy-loaded)
    ├── bm25.json        # Search index
    └── manifest.json    # Incremental tracking (content hashes)
```

1. **Scan** — AST/regex parsers extract symbols and their relationships
2. **Index** — Build dependency graph + reverse index (who calls whom)
3. **Serve** — MCP server exposes tools that query the graph
4. **Incremental** — Only changed files are re-scanned

## CLI

```bash
flyto-index scan .                          # Index a project
flyto-index impact useAuth --path .         # Check impact from terminal
flyto-index check . --threshold medium      # CI gate: fail if risky changes
flyto-index demo .                          # 30-second value demo
flyto-index install-hook .                  # Auto-reindex on git commit
```

<details>
<summary>All CLI commands</summary>

```bash
flyto-index init .                          # Initialize project
flyto-index scan .                          # Scan and index
flyto-index status .                        # Check index status
flyto-index impact useAuth --path .         # Impact analysis
flyto-index brief .                         # Project brief
flyto-index outline .                       # Directory outline
flyto-index describe src/auth.py --path .   # Read file description
flyto-index describe src/auth.py --summary "Auth module" --path .
flyto-index demo .                          # Quick demo (scan + impact)
flyto-index install-hook .                  # Git hook for auto-reindex
flyto-index check . --threshold medium      # CI impact check
flyto-index check . --json --base main      # JSON output for CI
flyto-index tools                           # List tools as JSON
```
</details>

## CI/CD Integration

Block risky changes in pull requests:

```yaml
# .github/workflows/impact-check.yml
on: [pull_request]

jobs:
  impact:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install flyto-indexer
      - run: flyto-index scan .
      - run: flyto-index check . --threshold medium --base main
```

## HTTP API

For tools that don't support MCP:

```bash
python -m src.api_server --port 8765
curl -X POST http://localhost:8765/impact \
  -d '{"symbol_id": "myproject:src/auth.py:function:login"}'
```

## Security & Privacy

- **100% local.** No code is sent anywhere.
- Index stored in `.flyto-index/`. Delete it to clean up completely.

## Limitations

- **Static analysis only** — dynamic imports and metaprogramming are not tracked
- **No type inference** — complex TypeScript generics are simplified
- **Cross-project tracking** requires all projects indexed in the same workspace

## License

[MIT](LICENSE)

<!-- mcp-name: io.github.ChesterHsu/flyto-indexer -->
