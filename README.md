<div align="center">
  <h1>Flyto2 Indexer</h1>
  <p><strong>Know what breaks. Prove the fix.</strong></p>
  <p>
    <a href="https://github.com/flytohub/flyto-indexer/actions"><img src="https://github.com/flytohub/flyto-indexer/workflows/CI/badge.svg" alt="CI"></a>
    <a href="https://pypi.org/project/flyto-indexer/"><img src="https://img.shields.io/pypi/v/flyto-indexer.svg" alt="PyPI"></a>
    <a href="https://github.com/flytohub/flyto-indexer/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+"></a>
  </p>
</div>

Flyto2 Indexer is local code intelligence for AI coding agents. It maps
symbols, callers, APIs, tests, rules, and requirements before an edit, then
closes the loop with lint, tests, and diff conformance.

No API key. No model lock-in. No source upload.

## Start in 60 seconds

```bash
pip install flyto-indexer
flyto-index setup .
flyto-index verify . --strict
```

`setup` builds the index and configures supported MCP clients. Then ask your
agent:

```text
impact(target="validateOrder", change_type="rename")
```

```text
7 call sites · 3 projects · 2 test files
Risk: high
Manual review: 1 unresolved dynamic reference
```

Text search finds a name. Flyto2 Indexer finds the change surface.

## One closed loop

```text
search → impact → task(plan) → task(gate) → edit → task(validate) → verify
```

- `search` finds symbols and concepts.
- `impact` previews blast radius, ambiguity, callers, tests, and dynamic gaps.
- `task` carries decisions, scoped instructions, requirements, and proof.
- `verify` checks the repository before the agent says done.

The public smart-tool surface stays at 20 tools. Core analysis is local and
works without external services. `audit` and diff-based `impact` also return a
bounded Git evidence portfolio and a short verdict linked to its receipts;
lockfiles and generated artifacts are filtered before they consume context.

## What is different

### JIT Rules

`task(plan)` loads only the instructions that apply to the target path:

- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`
- `QWEN.md`
- Copilot and Cursor project rules

Nested rules override broader scopes. Same-scope contradictions block the gate.
Fingerprints detect rule changes after planning.

### Intent Ledger

Existing Markdown specs remain the source of truth. The plan maps:

```text
requirement → plan step → changed path → test/proof
```

Spec drift, orphan requirements, missing expected paths, and unplanned diff
files fail closed. OpenSpec-style requirements and scenarios work without an
OpenSpec runtime dependency.

### Adaptive Governance

`task(plan)` groups changes by responsibility and requests documentation only
for public contracts, schemas, architecture, user behavior, security, or
deployment. Internal fixes do not inherit a blanket documentation tax.

The default `.flyto-rules.yaml` mode is `advisory`. Projects may opt into
`guarded` or `strict`; deterministic violations then close through the existing
`gate` and `validate` actions. No extra tool or action is added.

### Semantic Refactor Preflight

`impact(change_type="rename"|"move"|"signature_change")` reports:

- exact symbol identity and same-name candidates;
- overload and ambiguity counts;
- indexed, name-only, and unresolved references;
- production, test, and manual-review update sites.

Flyto2 Indexer does not edit files or become an IDE. It makes the edit contract
precise for the agent that already does.

### Decision Grill

Use Grill when the task still has product or architecture decisions:

```text
task(action="grill", grill_action="start", description="Add robot adapter")
  → one high-value question + recommendation + evidence

task(action="grill", grill_action="freeze", grill_session_id="grill_...")
  → immutable decision contract + evidence snapshot + ADR

task(action="plan", grill_session_id="grill_...", ...)
  → risk + JIT Rules + Intent Ledger + frozen decisions

task(action="validate", task_contract=<plan>, project="...")
  → Ruff + pytest + freshness + requirement/diff conformance
```

Repository facts are resolved from the index instead of being asked back to the
user. Critical uncertainty and contradictions fail closed.

## Core tools

| Tool | Answer |
| --- | --- |
| `search` | Where is the relevant code? |
| `impact` | What breaks if this changes? |
| `task` | Are decisions, rules, requirements, and proof closed? |
| `audit` | Where are the quality and security risks? |
| `structure` | How is the project connected? |
| `verify` | Is this repository ready to finish or merge? |

Focused tools also cover secrets, taint, SBOM, licenses, architecture layers,
documentation, PR risk, and workspace verification. See the
[source-backed MCP reference](docs/reference/mcp-tools.md).

### MCP transport and response budgets

`stdio` remains the default transport. For clients that benefit from a
persistent local connection, run the optional loopback-only Streamable HTTP
bridge:

```bash
flyto-index-mcp-http --port 8765
# MCP endpoint: http://127.0.0.1:8765/mcp
# health:       http://127.0.0.1:8765/health
```

The bridge keeps one stdio child warm, restarts it after a failure, and only
replays requests declared read-only. It refuses non-loopback binds.

Stdio tool calls have bounded deadlines and support MCP
`notifications/cancelled`. Normal analysis defaults to 120 seconds; full
verification and task plan/validate calls default to 600 seconds. Override
both, within the enforced 1–900 second range, with
`FLYTO_INDEXER_TOOL_TIMEOUT_SECONDS`. A timeout or cancellation fails only that
request, so the same process can serve the next call instead of entering a busy
loop.

`structure(focus="profile")` and `project_profile` now default to bounded
`compact` results. Use `limit` and `cursor` to page lists, `result_mode="paged"`
for the complete paged shape, or explicit `result_mode="full"` for the legacy
unbounded response. Profile counts distinguish filesystem total, production
source, indexed, test, fixture, example, and generated files. Test fixtures do
not affect production API/model signals unless
`include_non_production=true`.

Every MCP tool result includes `_runtime` metadata with the runtime version,
commit, index freshness, elapsed time, result mode, and request deadline.
`audit`, `structure(focus="profile")`, and `verify` also share the same
`health-snapshot.v1`; a score or complexity count therefore means the same
thing on every surface.

## CLI

```bash
flyto-index scan . --full
flyto-index impact useAuth --path .
flyto-index context --path . --query "auth routes query keys"
flyto-index task plan --description "Refactor auth" --target src/auth.py
flyto-index verify . --strict
flyto-index verify-workspace . --changed-only --base origin/main
```

For an existing-warning baseline:

```bash
flyto-index verify . \
  --save-baseline .flyto-baselines/flyto-indexer.json \
  --json

flyto-index verify . \
  --baseline .flyto-baselines/flyto-indexer.json \
  --regression-only
```

Current baselines compare stable, privacy-preserving finding IDs as well as
check status and canonical quality metrics, so a new finding or newly-worse
complexity/dead-code/documentation result cannot hide behind accepted debt.
Line-number-only moves keep the same ID. Legacy baselines remain readable.

## CI

```yaml
- run: pip install flyto-indexer
- run: flyto-index scan . --full
- run: flyto-index verify . --strict
- run: flyto-index check . --threshold medium --base main
```

Project policy lives in `.flyto-rules.yaml`:

```yaml
verify:
  allow_warn: [docs_coverage]
  warn_as_fail: [agent_hygiene, generated_index_ignore, mcp_registry]
  min_docs_score: 60
  min_health_score: 80
  max_dead_code: 14

layers:
  - name: ui
    paths: ["src/ui/**"]
    cannot_import: [db]

taint:
  sources:
    - pattern: "ctx.payload"
      language: python
  sinks:
    - pattern: "dangerousEval("
      vuln_type: rce
      severity: critical
```

## Languages

| Language | Indexing |
| --- | --- |
| Python | AST: functions, classes, methods, decorators, routes |
| TypeScript / JavaScript | Functions, classes, interfaces, types, API calls |
| Vue | Components, composables, props, emits |
| Go | Functions, structs, methods, interfaces, embeddings |
| Rust | Functions, structs, impl blocks, traits |
| Java | Classes, methods, interfaces, annotations |
| Dart | Widgets, classes, constructors, methods, imports |
| C / C++ | Functions, typedef structs, includes, call edges |

Optional local language servers enrich references. The built-in index remains
the dependency-free fallback.

## How it works

```text
source → symbols → dependency graph → reverse index → MCP
                         ↓
              task contracts + verify
```

Generated data stays under `.flyto-index/`:

```text
index.json      symbols and dependency graph
content.jsonl   lazy source records
bm25.json       keyword index
semantic.json   local semantic index
```

Delete that directory to remove generated index data.

## Design choices

The workflow borrows proven ideas without copying entire products:

- clear spec-to-implementation traceability from
  [GitHub Spec Kit](https://github.com/github/spec-kit);
- plain Markdown and brownfield-first deltas from
  [OpenSpec](https://github.com/Fission-AI/OpenSpec);
- path-scoped, just-in-time project context from
  [Gemini CLI](https://github.com/google-gemini/gemini-cli);
- symbol-aware refactor preflight from
  [Serena](https://github.com/oraios/serena);
- compact, evidence-first code case files from
  [Grillme](https://grillme.dev/).

What stays out of core: hosted documentation fetchers, model bindings,
auto-editing, auto-commit, and another workflow tree. See
[design references](docs/DESIGN_REFERENCES.md).

## Documentation

- [Features](docs/FEATURES.md)
- [CLI](docs/CLI.md)
- [MCP](docs/MCP.md)
- [Configuration](docs/CONFIGURATION.md)
- [Security model](docs/SECURITY_MODEL.md)
- [Decision Grill tests](docs/GRILL_TESTING.md)
- [Generated references](docs/reference/)

## Contributing

```bash
python -m ruff check src tests
python -m pytest
python benchmarks/evaluate.py --check
flyto-index verify . --strict
```

Security reports: `security@flyto2.com`.

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE).

<!-- mcp-name: io.github.flytohub/flyto-indexer -->
