# Configuration

Flyto2 Indexer has three configuration layers: scanner defaults, repository
policy, and process environment. Command arguments override relevant defaults
for a single invocation.

## Scanner Defaults

`config/default.yaml` defines languages, ignored paths, maximum file size,
output directory, summary limits, and impact/context bounds. Treat the shipped
file as package defaults. Put repository-specific enforcement in
`.flyto-rules.yaml` rather than editing installed package data.

The default scan ignores generated dependency and build trees, including
`node_modules`, `dist`, `build`, and `.vitepress/cache`, while retaining
authored `.vitepress` configuration and theme files.

## Repository Policy

Create `.flyto-rules.yaml` in the target repository:

```yaml
architecture:
  - rule: UI code stays in the frontend package
    glob_deny:
      - "backend/**/components/*.tsx"

layers:
  - name: domain
    paths: ["src/domain/**"]
    may_import: []
  - name: api
    paths: ["src/api/**"]
    may_import: ["domain"]

taint:
  sources:
    - pattern: "request.args.get"
      language: python
      taint_type: user_input
  sinks:
    - pattern: "cursor.execute("
      vuln_type: sql_injection
      severity: high
    - pattern: ".find("        # the receiver is whatever the project named it
      vuln_type: nosql_injection
      severity: high
      requires:                # this counts only when it is given a mapping
        - {arg: 0, shape: mapping}
  sanitizers:
    - pattern: "escape_sql("
      cleanses: ["sql_injection"]
  propagators:
    # Spread taint through in-place mutation. Two shapes, matched by callee name.
    - name: "my_populate"   # positional: my_populate(src, dst) taints dst
      from: 0
      to: 1
    - name: "stash"         # receiver: container.stash(taint) taints container
      receiver: true
```

Sources, sinks, sanitizers and propagators share one file. Sources, sinks and
sanitizers also have `add_taint_*` MCP tools; propagators are YAML-only (no
`add_*` tool, to keep the MCP surface at its fixed tool count) and are listed
back by `list_taint_rules`.

### Argument-shape gates (`requires:`)

A sink pattern is matched as a substring, so a rule that names a receiver only
finds the projects that chose the same name: `collection.find(` misses
`users.find(` and `self.db.orders.find(`. Dropping the receiver matches all of
them and also matches `line.find(",")`, which is `str.find`. What separates
them is the argument, so a rule can say so.

Every requirement listed must hold for the match to count:

| Requirement | Holds when |
| --- | --- |
| `{arg: 0, shape: mapping}` | argument 0 is a mapping. `any` and `after_first` may replace the index; shapes are `mapping`, `sequence`, `scalar`, `constant`, `dynamic` |
| `{arg: "any", shape: "sql_statement"}` | the literal text an argument carries reads as a SQL statement |
| `{callee_tail: ["re.search"]}` | the dotted callee ends there at a name boundary, so `store.search` does not qualify |
| `{keyword: "shell", equals: true}` | that keyword argument is present with that value |
| `{min_args: 2}` / `{max_args: 1}` | the positional argument count is in range |
| `{receiver_root: ["request", "req"]}` | the leftmost name of what the sink hangs off is one of these |
| `{enclosing_class_suffix: ["Request"]}` | the sink sits in a class whose name ends this way |
| `{not: {...}}` | the requirement inside does not hold |

Shapes fail open. `shape: mapping` passes unless the argument is *provably*
something else -- a string literal, or a name bound to one earlier in the same
function. An unknown expression stays a candidate, because a gate that guesses
drops real flows silently.

Most requirements describe a call, so a rule that carries one does not apply to
a subscript sink (`resp.headers[name] = value`), and the text-level
`research_priority` ranker skips it rather than evaluating a gate it cannot see.

`receiver_root` and `enclosing_class_suffix` are the exceptions: a subscript
sink has no arguments, but it does have a receiver and a class around it. It is how the built-in header rules say they mean a
*response* -- `{not: {receiver_root: ["request", "req"]}}` -- because server
frameworks make `request.headers` read-only, so an assignment into it is an
HTTP client building its own outgoing request. The same rule adds
`{not: {enclosing_class_suffix: ["Request"]}}` for the client that writes
through `self`: measured over 23,404 files, `ClientRequest` and
`PreparedRequest` write `self.headers` 21 times, while the classes doing it for
a response are `HTTPMove`, `HTTPMethodNotAllowed` and `RedirectResponse`.

Policy files are YAML parsed with `safe_load`; malformed content fails closed.
The built-in corpus under `config/rules/` supplies default complexity,
security, secret, license, Docker, IaC, scoring, and ignore rules.

## Generated State

The default `.flyto-index/` directory contains generated indexes and must stay
out of source control. `FLYTO_INDEX_DIR` can point readers and writers to a
different local directory. When present, its resolved path is the sole index
authority even if the directory does not exist yet; an empty or invalid value
fails closed and never falls back to the current directory. Each CLI, MCP/API,
task, search, Grill, watcher, and maintenance operation freezes the resolved
project root, index path, project label, and cache identity for its lifetime.
`FLYTO_AUTO_REINDEX` controls automatic refresh behavior where supported.

## LSP Enrichment

`FLYTO_LSP_ENABLED` controls optional local language-server enrichment and
`FLYTO_LSP_TIMEOUT` bounds calls. Missing language servers reduce precision to
the deterministic parser or text fallback; they do not require network access.

## Optional LLM Audit

`OPENAI_API_KEY` is read only by the optional LLM auditor. Core indexing,
search, impact, policy, security scans, and verification do not need it. Never
write the key to a repository file or generated report.

The generated [configuration reference](reference/configuration.md) lists all
detected environment readers, flattened defaults, and the complete built-in
rule corpus.

## Local Learning And Proof

`FLYTO_INDEXER_FEEDBACK_DIR` changes the local directory used by the
development-feedback event store. The default is
`~/.flyto-indexer/feedback/`. No network destination is configured or used.

`FLYTO_INDEXER_PROOF_KEYS_JSON` is an optional JSON object mapping local key
IDs to HMAC secrets used to validate external proof receipts. Keep it in the
process environment or an approved secret manager; never commit it. An
unsigned receipt remains visible as content-addressed evidence but cannot
satisfy a required trusted-proof gate.
