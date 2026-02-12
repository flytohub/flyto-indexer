<div align="center">
  <h1>Flyto Indexer</h1>
  <p>
    <strong>Code intelligence MCP server for AI-assisted development</strong>
  </p>
  <p>
    <a href="https://github.com/flytohub/flyto-indexer/actions"><img src="https://github.com/flytohub/flyto-indexer/workflows/CI/badge.svg" alt="CI"></a>
    <a href="https://pypi.org/project/flyto-indexer/"><img src="https://img.shields.io/pypi/v/flyto-indexer.svg" alt="PyPI"></a>
    <a href="https://github.com/flytohub/flyto-indexer/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
  </p>
  <p>
    Impact analysis &bull; Smart code search &bull; Dependency graphs &bull; Dead code detection
    <br/>
    Works with Claude Code, Cursor, Windsurf, and any MCP client
  </p>
</div>

---

<div align="center">
  <img src="demo.gif" alt="Flyto Indexer demo — impact analysis before renaming" width="800">
</div>

**"What breaks if I change this?"** — Every developer asks this. Flyto Indexer answers it.

It indexes your codebase, understands symbol relationships, and exposes **23 MCP tools** that give any AI assistant deep code intelligence — impact analysis, reference finding, dependency tracking, and more.

**Zero dependencies.** Pure Python standard library. Runs locally. No code leaves your machine.

## Quick Start

### Option A: Install from PyPI

```bash
pip install flyto-indexer

# Index your project (creates .flyto-index/)
flyto-index scan /path/to/your/project

# Start MCP server
python -m flyto_indexer.mcp_server
```

### Option B: Run from source

```bash
git clone https://github.com/flytohub/flyto-indexer.git
cd flyto-indexer
pip install -e .

# Index your project
flyto-index scan /path/to/your/project

# Start MCP server (from repo)
python -m src.mcp_server
```

> **Tip:** Add `.flyto-index/` to your `.gitignore`. The index is typically a few MB for medium projects.

### Connect to Claude Code

Add to `~/.claude/settings.json`:

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
<summary>Running from source instead?</summary>

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

That's it. Claude Code now has access to all 23 tools.

## Why This Exists

AI coding assistants are powerful, but they're **flying blind**:

- They don't know what breaks when they change a function
- They can't trace dependencies across files or projects
- They have no idea which code is dead and safe to remove
- They can't assess the risk of a proposed change

Flyto Indexer gives AI the **structural understanding** it needs to make safe, informed changes.

## See It In Action

```
You:    "Rename validateOrder to validate_order — what breaks?"

Claude: Let me check the impact first.
        → impact_analysis("myproject:src/validators.py:function:validateOrder")
        → find_references("validateOrder")

        ⚠️ Modifying validateOrder affects 5 locations:
          → Cart.vue:42 — calls validateOrder() directly
          → CheckoutAPI.py:18 — imports validateOrder
          → test_validators.py:55 — tests validateOrder
          Risk: MEDIUM — 3 files, 2 projects

        I'll rename all call sites and update the tests.
        → edit_impact_preview("validateOrder", change_type="rename")
```

## What It Does

### Impact Analysis

```
> "What happens if I change the checkout() function?"

⚠️ Modifying checkout() affects:
  → Cart.vue (direct caller)
  → QuickBuy.vue (calls via useCart)
  → /api/checkout (API endpoint)
  Risk: MEDIUM — 3 files affected, no breaking changes detected
```

### Smart Code Search

Search uses symbol-aware ranking with metadata matching — no embeddings or external services required.

```
> search_code("authentication")

  1. src/composables/useAuth.ts — Auth state management, login/logout, JWT tokens
  2. src/api/auth.py — Authentication API endpoints, rate limiting
  3. src/middleware/auth.ts — Route guards, token validation
```

### Dependency Graph

```
> dependency_graph("src/composables/useCart.ts")

  useCart.ts
  ├── imports: useAuth, usePayment, cartApi
  └── depended on by: Cart.vue, QuickBuy.vue, CartSidebar.vue
```

### Cross-Project Tracking

```
> cross_project_impact("validateOrder")

  Defined in: backend/src/validators.py
  Used by:
    → frontend (3 references)
    → mobile-app (1 reference)
    → admin-panel (2 references)
  Risk: HIGH — changes affect 3 other projects
```

## MCP Tools

23 tools organized by category. **Start with these 6:**

| Tool | What it does |
|------|-------------|
| `impact_analysis` | "What breaks if I change this?" |
| `find_references` | "Who calls this function?" |
| `search_code` | "Where is the auth code?" |
| `dependency_graph` | "What does this file depend on?" |
| `get_symbol_content` | "Show me the full function" |
| `find_dead_code` | "What can I safely delete?" |

<details>
<summary>All 23 tools</summary>

### Code Search & Discovery
| Tool | Description |
|------|-------------|
| `search_code` | Symbol-aware search across all indexed projects |
| `get_symbol_content` | Get full source code of a function/class |
| `get_file_symbols` | List all symbols defined in a file |
| `get_file_info` | Get file purpose, category, keywords, dependencies |
| `fulltext_search` | Search inside comments, strings, and TODO markers |

### Impact & Dependencies
| Tool | Description |
|------|-------------|
| `impact_analysis` | Analyze blast radius of modifying a symbol |
| `find_references` | Find all callers and importers of a symbol |
| `dependency_graph` | Show import chains and dependent relationships |
| `cross_project_impact` | Track API usage across multiple projects |
| `edit_impact_preview` | Preview impact before renaming, deleting, or changing signatures |

### Project Overview
| Tool | Description |
|------|-------------|
| `list_projects` | List all indexed projects with statistics |
| `list_categories` | Show code categories (auth, payment, etc.) |
| `list_apis` | List all API endpoints found in code |
| `check_index_status` | Check if the index is up-to-date |

### Code Quality
| Tool | Description |
|------|-------------|
| `find_dead_code` | Detect unreferenced functions, classes, and components |
| `find_todos` | Find TODO, FIXME, HACK markers across the codebase |

### File Context
| Tool | Description |
|------|-------------|
| `get_file_context` | Complete context package for a file (info + symbols + deps) |
| `find_test_file` | Find the test file for a source file (or vice versa) |
| `get_description` | Get the semantic one-liner for a file |
| `update_description` | Write or update a file description |

### Session & Indexing
| Tool | Description |
|------|-------------|
| `session_track` | Track workspace events for search boosting |
| `session_get` | Inspect current session state |
| `check_and_reindex` | Detect file changes and trigger re-indexing |

</details>

## Supported Languages

| Language | Parser | Symbols Extracted |
|----------|--------|-------------------|
| Python | AST | Functions, classes, methods, decorators |
| TypeScript/JavaScript | Custom parser | Functions, classes, interfaces, types, exports |
| Vue | SFC parser | Components, composables, emits, props |
| Go | Custom parser | Functions, structs, methods, interfaces |
| Rust | Custom parser | Functions, structs, impl blocks, traits |
| Java | Custom parser | Classes, methods, interfaces, annotations |

## Architecture

```
your-project/
├── src/            ← Your code (any language)
└── .flyto-index/   ← Generated index (add to .gitignore)
    ├── index.json             # Symbol index (symbols, deps, reverse index)
    ├── content.jsonl          # Source code content (lazy-loaded)
    ├── PROJECT_MAP.json       # File metadata and categories
    └── manifest.json          # Incremental tracking (content hashes)
```

### How It Works

1. **Scan** — AST parsers extract symbols (functions, classes, components) from your code
2. **Index** — Symbols are organized into a searchable index with dependency relationships
3. **Serve** — The MCP server exposes 23 tools that any AI client can call
4. **Incremental** — Only changed files are re-scanned (tracked via content hashes)

### Key Concepts

**Symbol ID** — Every symbol has a unique, stable identifier:
```
project:path:type:name
─────── ──── ──── ────
  │       │    │    └── Symbol name
  │       │    └── function, class, method, component, composable
  │       └── File path relative to project root
  └── Project name
```

**Depth Levels** — Progressive detail:
- **L0** — Project outline (directory tree + one-liner per file)
- **L1** — File summary (exports, imports, main functionality)
- **L2** — Code chunks (only the specific symbols you need)

## HTTP API

For editors and tools that don't support MCP, there's a local REST API. This runs on your machine — no data is sent externally.

```bash
# Start the local HTTP server
python -m src.api_server --port 8765

# Search
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "authentication"}'

# Impact analysis
curl -X POST http://localhost:8765/impact \
  -H "Content-Type: application/json" \
  -d '{"symbol_id": "myproject:src/auth.py:function:login"}'
```

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search` | POST | Keyword search |
| `/file/info` | POST | File metadata |
| `/file/symbols` | POST | List file symbols |
| `/impact` | POST | Impact analysis |
| `/categories` | GET | List categories |
| `/apis` | GET | List API endpoints |
| `/stats` | GET | Index statistics |
| `/openapi.json` | GET | OpenAPI spec |
| `/health` | GET | Health check |

## Integrations

- **[Claude Code](#connect-to-claude-code)** — MCP server (native)
- **Cursor** — HTTP API + `.cursorrules`
- **VSCode / Copilot** — Tasks + Extension
- **OpenAI GPTs** — HTTP API + OpenAPI schema

## CLI

```bash
# Initialize a project
flyto-index init .

# Scan and index a project
flyto-index scan .

# Check index status
flyto-index status .

# Analyze impact of changing a symbol
flyto-index impact useAuth --path .

# Generate project brief / outline
flyto-index brief .
flyto-index outline .

# Read or write file descriptions
flyto-index describe src/auth.py --path .
flyto-index describe src/auth.py --summary "User auth: login, register, JWT" --path .

# Quick 30-second value demo (scan + impact)
flyto-index demo .

# Install git hook for auto-reindex on commit
flyto-index install-hook .
flyto-index install-hook . --remove

# CI-friendly impact check (exits non-zero if risky)
flyto-index check . --threshold medium
flyto-index check . --json --base main

# List all commands as JSON (for AI integration)
flyto-index tools
```

## CI/CD Integration

```yaml
# .github/workflows/index.yml
on:
  push:
    branches: [main, develop]

jobs:
  index:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install flyto-indexer
      - run: flyto-index scan .
```

## Security & Privacy

- **Runs 100% locally.** No code is sent to any external service.
- Index stored under `.flyto-index/` in your project directory.
- Clean up by deleting `.flyto-index/` — there's no hidden state elsewhere.

## Limitations

- **Static analysis only** — dynamic imports, metaprogramming, and runtime-generated code are not tracked.
- **No type inference** — TypeScript type-level computations and complex generics are simplified.
- **Vue `<script setup>`** — most patterns are supported, but some edge cases with dynamic `defineProps` may be missed.
- **Cross-project tracking** requires all projects to be indexed in the same workspace.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[MIT](LICENSE) — Use it however you want.

<!-- mcp-name: io.github.ChesterHsu/flyto-indexer -->
