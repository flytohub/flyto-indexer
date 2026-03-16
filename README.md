<div align="center">
  <h1>Flyto Indexer</h1>
  <p>
    <strong>Know what breaks before you change it.</strong>
  </p>
  <p>
    <a href="https://github.com/flytohub/flyto-indexer/actions"><img src="https://github.com/flytohub/flyto-indexer/workflows/CI/badge.svg" alt="CI"></a>
    <a href="https://pypi.org/project/flyto-indexer/"><img src="https://img.shields.io/pypi/v/flyto-indexer.svg" alt="PyPI"></a>
    <a href="https://github.com/flytohub/flyto-indexer/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
  </p>
  <p>
    MCP server that gives AI assistants impact analysis, cross-project reference tracking, and code health scoring.<br/>
    Zero dependencies. Pure Python. 100% local.
  </p>
  <p>
    <a href="https://flyto2.com"><strong>flyto2.com</strong></a> · <a href="https://docs.flyto2.com">Documentation</a> · <a href="https://www.youtube.com/@Flyto2">YouTube</a>
  </p>
</div>

---

## Without Flyto Indexer

```
You:    "Rename validateOrder to validate_order"

AI:     *renames the function*
        *greps for "validateOrder"*
        *finds 3 matches in the same project*
        *misses 4 callers in the frontend repo*
        *misses the API endpoint that routes to it*
        *pushes broken code*
```

## With Flyto Indexer

```
You:    "Rename validateOrder to validate_order"

AI:     → impact(target="validateOrder", change_type="rename")

        ⚠️ 7 call sites across 3 projects:
          backend/checkout.py:42     — calls validateOrder()
          backend/api/orders.py:18   — imports validateOrder
          frontend/Cart.vue:55       — calls via useCheckout()
          frontend/QuickBuy.vue:23   — calls via useCheckout()
          mobile/OrderScreen.tsx:67  — API call to /api/validate
          tests/test_orders.py:12    — unit test
          tests/test_api.py:88       — integration test
          Risk: HIGH — 3 projects affected
          Test file: tests/test_orders.py
          Cross-project: 2 other repos affected

        *renames all 7 call sites, updates tests, pushes clean code*
```

**That's the difference.** grep finds text. This finds dependencies.

<div align="center">
  <img src="demo.gif" alt="Flyto Indexer — impact analysis before renaming" width="800">
</div>

## Install

```bash
pip install flyto-indexer
flyto-index setup .
```

That's it. One command does everything:
1. **Scans** your project and builds the code index
2. **Writes** `CLAUDE.md` with tool usage instructions
3. **Configures** Claude Code MCP settings (`~/.claude/settings.json`)

Restart Claude Code and start using it. Works with any MCP client — Claude Code, Cursor, Windsurf, etc.

<details>
<summary>Manual setup (other MCP clients)</summary>

If your MCP client doesn't use `~/.claude/settings.json`, add this to your MCP config:

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

Then scan and set up CLAUDE.md separately:
```bash
flyto-index scan .
flyto-index setup-claude .
```
</details>

<details>
<summary>Run from source</summary>

```bash
git clone https://github.com/flytohub/flyto-indexer.git
cd flyto-indexer && pip install -e .
flyto-index setup .
```
</details>

<details>
<summary>Uninstall</summary>

```bash
flyto-index setup . --remove
pip uninstall flyto-indexer
```
</details>

## What It Does

### Impact Analysis — the core feature

Every tool an AI already has (grep, file read, glob) finds **text**. None of them answer **"what depends on this?"**

One call gives you everything — references, blast radius, cross-project impact, and test files:

```
→ impact(target="useAuth")

  References: 12 across 4 projects
    flyto-cloud:  LoginPage.vue, RegisterPage.vue, AuthGuard.ts, api.ts
    flyto-pro:    vscode_agent/tools.py, middleware/auth.py
    flyto-vscode: ChatHandler.ts, AuthProvider.ts
    flyto-core:   modules/auth/login.py
  Risk: HIGH — shared across 4 projects
  Cross-project: 3 other repos affected
  Test file: tests/test_auth.py
```

Works with uncommitted changes too:

```
→ impact(mode="unstaged")

  3 symbols affected by your changes:
    validate_order  — 5 callers, test: tests/test_orders.py
    OrderSchema     — used in 2 API endpoints
    format_receipt  — no callers (safe)
```

### Cross-Language API Tracking

Python backend endpoints automatically linked to TypeScript/Vue frontend callers:

```
→ structure(focus="apis")

  POST /api/checkout
    Defined in: backend/routes/order.py (create_order)
    Called by:   frontend/Cart.vue, frontend/api/orders.ts
    Call count: 4
```

Detects FastAPI, Flask, Starlette decorators + `fetch()`, `axios`, `$http` calls.

### Code Health & Security

One call audits everything — auto-expands weak dimensions with detailed findings:

```
→ audit()

  Health: 74/100 (C)

  ⚠️ Security (60/100) — auto-expanded:
    2 critical: hardcoded API keys in config.py, settings.py
    1 high: SQL string concatenation in query.py

  ⚠️ Complexity (65/100) — auto-expanded:
    process_data() — 87 lines, depth=6 → extract sub-functions

  ✓ Dead code (90/100) — passing
  ✓ Documentation (85/100) — passing

  Git hotspots: order.py (42 commits, complexity=8.5)

  Refactoring suggestions:
    [high]   process_data() → extract sub-functions
    [medium] dead_fn() — unreferenced, 45 lines → safe to remove
```

### Taint Analysis — track data flow, not just patterns

AST-based engine that tracks how untrusted data flows from sources to dangerous sinks. Unlike regex pattern matching, it follows variables through assignments, f-strings, and function calls — with sanitizer awareness to eliminate false positives.

```
→ audit(focus="security")

  Taint flows detected:
    [high] src/api/users.py:42
      SQL injection: request.args.get('id') → cursor.execute(query)
      Flow: request.args.get('id') → user_id → query (f-string) → cursor.execute()
      Fix: Use parameterized query: cursor.execute(sql, params)

    [critical] src/api/admin.py:18
      RCE: request.form.get('code') → eval(code)
      Fix: Never eval() user-controlled strings
```

- **Python**: Full AST analysis — tracks taint through assignments, f-strings, concat, for loops
- **Cross-function**: Detects when tainted data is passed as an argument to a function with a dangerous sink
- **JS/TS/Go**: Regex-based fallback for common taint patterns
- **Sanitizer-aware**: `int()`, `html.escape()`, `shlex.quote()`, parameterized queries all break the taint chain
- **Custom rules**: Add project-specific sources/sinks/sanitizers via `taint_rules.yaml`

### Project Rules — AI learns from your corrections

`.flyto-rules.yaml` — structured, versionable project conventions that audit enforces automatically.

```yaml
# .flyto-rules.yaml
architecture:
  - rule: "i18n files must be in flyto-i18n/"
    glob_deny: ["flyto-cloud/**/*.locale.json"]

style:
  - rule: "Frontend does no data processing"
    grep_deny: [{ pattern: '\breduce\s*\(', glob: "*.vue" }]

conventions:
  - rule: "Commit messages in English"
```

When you correct the AI ("don't put i18n files there"), it auto-writes a verifiable rule — so the mistake never happens again, for any AI, any tool:

```
User corrects AI → add_rule() writes .flyto-rules.yaml → audit checks compliance
```

- `glob_deny` — files in wrong locations
- `grep_deny` — forbidden code patterns in specific file types
- `conventions` — text-only guidance (no automated check)
- Rules accumulate over time — no upfront config needed

### Task Analysis — plan before you code

Scores risk across 6 dimensions and generates an execution plan:

```
→ task(action="plan", description="Rename validateOrder to validate_order", intent="refactor")

  Dimensions:
    blast_radius:      HIGH (8.0)  — 7 callers across 3 projects
    breaking_risk:     HIGH (7.0)  — public API, used by external consumers
    test_risk:         MEDIUM (5.0) — 2/7 callers have test coverage
    cross_coupling:    HIGH (8.0)  — referenced in 3 projects
    complexity:        LOW (2.0)   — straightforward rename
    rollback_difficulty: MEDIUM (5.0) — multi-project change

  Execution Plan:
    1. scope_callers       → find_references("validateOrder")
    2. verify_test_coverage → find_test_file("checkout.py")
    3. check_cross_project → cross_project_impact("validateOrder")
    4. ⛔ gate_before_plan → task_gate_check(phase="plan")
    5. preview_changes     → edit_impact_preview("validateOrder", "rename")
    6. ⛔ gate_before_apply → task_gate_check(phase="apply")
```

Each step has pre-filled arguments — AI follows the data structure, not prompts.
Server-side enforcement blocks skipping gates.

## Tools

5 smart tools. Each one auto-enriches results with related data — no need to pick between dozens of granular tools.

| Tool | What it answers | Auto-enrichment |
|------|----------------|-----------------|
| `search` | "Find code by name or description" | Merges BM25 + semantic search, attaches callers and file context |
| `impact` | "What breaks if I change this?" | References + blast radius + cross-project + test files in one call |
| `audit` | "How healthy is this project?" | Health score (0-100), auto-expands weak dimensions, taint analysis, rules compliance |
| `task` | "Plan, gate-check, or validate changes" | Risk scoring, execution plans, linter + tests |
| `structure` | "Show me the project layout" | Projects, APIs, dependencies, type contracts |

<details>
<summary>What each tool replaces</summary>

**`search`** replaces: `search_code`, `semantic_search`, `fulltext_search`, `get_file_info`, `get_file_symbols`, `get_symbol_content`, `get_file_context`

**`impact`** replaces: `find_references`, `impact_analysis`, `batch_impact_analysis`, `edit_impact_preview`, `cross_project_impact`, `impact_from_diff`

**`audit`** replaces: `code_health_score`, `security_scan`, `taint_analysis`, `rules_check`, `find_dead_code`, `find_complex_functions`, `find_duplicates`, `suggest_refactoring`, `find_stale_files`, `find_todos`, `coverage_gaps`

**`task`** replaces: `analyze_task`, `task_gate_check`, `validate_changes`

**`structure`** replaces: `list_projects`, `list_apis`, `list_categories`, `dependency_graph`, `check_api_contracts`, `contract_drift`, `extract_type_schema`

All legacy tools remain available in dispatch for backward compatibility and execution plan steps.

</details>

## Languages

| Language | Parser | Extracts |
|----------|--------|----------|
| Python | AST | Functions, classes, methods, decorators, API routes |
| TypeScript/JS | Custom | Functions, classes, interfaces, types, API calls |
| Vue | SFC | Components, composables, emits, props |
| Go | Custom | Functions, structs, methods, interfaces, embeddings, type aliases, const/var, impl tracking |
| Rust | Custom | Functions, structs, impl blocks, traits |
| Java | Custom | Classes, methods, interfaces, annotations |

## How It Works

```
flyto-index scan .
```

1. **Parse** — AST (Python) or regex (others) extracts every function, class, and import
2. **Graph** — Builds dependency graph + reverse index (caller → callee)
3. **Serve** — MCP server answers queries from the graph in memory
4. **Incremental** — Re-scans only changed files, incrementally patches reverse_index and BM25 (10-50x faster than full rebuild)
5. **LSP** — Optional type-aware references via pyright/tsserver/gopls/rust-analyzer (zero deps, graceful fallback)

```
.flyto-index/
├── index.json       # Symbols + dependency graph + reverse index
├── content.jsonl    # Source code (lazy-loaded)
├── bm25.json        # BM25 keyword search index
├── semantic.json    # TF-IDF + learned ConceptGraph (v2.2+)
└── manifest.json    # Change tracking
```

## CI: Block Risky Changes

```yaml
# Fail the PR if changes affect too many call sites
- run: pip install flyto-indexer
- run: flyto-index scan .
- run: flyto-index check . --threshold medium --base main
```

## CLI

```bash
flyto-index setup .                       # One command: scan + CLAUDE.md + MCP config
flyto-index scan .                        # Index (or re-index)
flyto-index impact useAuth --path .       # Impact analysis
flyto-index check . --threshold medium    # CI gate
flyto-index demo .                        # 30-second demo
flyto-index install-hook .                # Auto-reindex on commit
flyto-index setup . --remove              # Uninstall
```

## Privacy

100% local. No code is sent anywhere. Delete `.flyto-index/` to clean up completely.

## Limitations

- Static analysis only — dynamic imports and metaprogramming not tracked
- No type inference — complex TypeScript generics simplified
- Cross-project tracking requires all projects indexed together

## License

[MIT](LICENSE)

<!-- mcp-name: io.github.ChesterHsu/flyto-indexer -->
