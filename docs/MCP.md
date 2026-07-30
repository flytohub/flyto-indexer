# MCP Integration

The MCP server runs over stdio and performs local analysis. It does not require
a Flyto2 account or hosted service.

## Start The Server

From a source checkout:

```bash
python -m src.mcp_server
```

From an installed package:

```bash
python -m flyto_indexer.mcp_server
```

Configure the client with the absolute executable path and repository working
directory. Do not put credentials in the client configuration.

## Tool Model

The server publishes a compact smart-tool surface. Start with:

- `search` for code discovery;
- `impact` before changing a symbol or diff;
- `audit` for quality and security findings;
- `task` for decision grilling, plan, gate, and validate phases;
- `structure` for APIs, dependencies, packages, types, and conventions;
- `verify` or `verify_workspace` to close an implementation loop.

Focused tools cover secrets, licenses, documentation, project profiles, PR
risk, frameworks, call hierarchy, layers, and policy authoring. Granular tool
definitions remain in the registry for compatibility and internal dispatch but
are not all advertised to clients. The generated
[MCP tool reference](reference/mcp-tools.md) is authoritative for names,
schemas, annotations, and source locations.

`task(action="plan")` automatically attaches target-scoped JIT Rules and an
Intent Ledger. `gate` checks their fingerprints and conflicts. `validate`
checks requirement coverage, diff scope, proofs, Ruff, and pytest.

`task(action="grill")` adds persistent decision closure without another public
tool. A frozen v2 contract adds evidence freshness, decision-to-diff
conformance, ADR/audit artifacts, and privacy-preserving outcome learning.
Existing v1 contracts remain valid. See the
[Decision Grill test protocol](GRILL_TESTING.md) for the real JSON-RPC closure
test and failure expectations.

`impact` adds semantic preflight for rename, move, delete, and signature
changes: selected identity, ambiguity, unresolved references, and required
production/test/manual-review sites.

`audit` and diff-mode `impact` attach a local `evidence-portfolio.v1` case file
and `evidence-verdict.v1` summary. Both are bounded, omit patch bodies, filter
machine-managed noise, and link each verdict finding to receipts in the same
result. This adds no tool, action, dependency, upload, or model call.

## Protocol Behavior

- Transport is newline-delimited JSON-RPC over stdin/stdout.
- Initialization reports the active package version.
- The server accepts supported MCP protocol versions and otherwise replies with
  its preferred version so the client can decide whether to continue.
- Tool errors return structured JSON-RPC errors; analysis findings are normal
  tool results.
- Tool calls have bounded wall-clock deadlines. Normal calls default to 120
  seconds; full verification and task plan/validate calls default to 600
  seconds. `FLYTO_INDEXER_TOOL_TIMEOUT_SECONDS` overrides the defaults within
  the enforced 1–900 second range.
- MCP `notifications/cancelled` interrupts an active POSIX stdio request and
  returns the standard request-cancelled error. Deadline and cancellation
  errors include a `retryable` flag derived from the tool's read-only
  annotation, and the same server process remains available for the next
  request.
- MCP `ping` returns an empty success response for connection-liveness probes.
- `_runtime.deadline_ms` exposes the applied budget beside runtime version,
  commit, index freshness, elapsed time, and result mode.
- Per-process and per-session limits are configurable through environment
  variables listed in the [configuration reference](reference/configuration.md).

## Trust Boundary

Repository text, index artifacts, policy YAML, diffs, and tool arguments are
untrusted input. Keep the server scoped to repositories the user intends to
analyze. Read-only annotations describe expected tool behavior, but callers
must still request confirmation before invoking tools that write policy,
indexes, baselines, or reports.
