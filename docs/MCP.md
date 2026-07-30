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
Every generated execution step names one of the callable public MCP tools.
Blocked gates include `required_state`, the exact key/value evidence the
caller must supply after completing each `required_actions` item. Compound
contracts expose one active subtask at a time.

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

## Protocol Compatibility

MCP `2026-07-28` removed the session handshake and moved client identity,
capabilities, and protocol selection onto every request. Servers that only
understand one era either reject new clients or silently apply old behavior to
new messages. flyto-indexer supports both eras on the same server:

- Modern clients use stateless MCP `2026-07-28`. Every request carries
  `io.modelcontextprotocol/protocolVersion` and
  `io.modelcontextprotocol/clientCapabilities` in `_meta`.
- `server/discover` reports modern and legacy versions, server capabilities,
  identity, instructions, and cache hints before any tool call.
- Successful modern results include `resultType`, server identity in `_meta`,
  and the required cache fields on list and resource-read results.
- Unsupported modern versions return `-32022` with the requested version and
  the complete supported-version list.
- Methods removed from the modern core, including `ping` and
  `logging/setLevel`, return Method Not Found. Legacy clients retain both
  methods.
- Legacy clients continue to initialize with `2025-11-25`, `2025-06-18`,
  `2025-03-26`, or `2024-11-05`. An `initialize` request never upgrades a
  client into the stateless era.

This follows the official
[MCP 2026-07-28 versioning model](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)
and preserves existing client integrations during migration.

## Runtime Behavior

- Stdio uses newline-delimited JSON-RPC over stdin/stdout.
- Legacy initialization and modern discovery report the active package
  version.
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
- Legacy MCP `ping` returns an empty success response for connection-liveness
  probes.
- `_runtime.deadline_ms` exposes the applied budget beside runtime version,
  commit, index freshness, elapsed time, and result mode.
- The optional loopback HTTP bridge serializes stdio responses but admits
  concurrent HTTP callers, interrupts the active child on cancellation, and
  restarts on deadlines, EOF, broken pipes, or corrupt JSON. Only annotation-
  safe read-only requests are replayed after protocol failure; timed-out or
  cancelled work is never replayed.
- Modern Streamable HTTP requests must mirror the body in
  `MCP-Protocol-Version`, `Mcp-Method`, and, where required, `Mcp-Name`.
  Missing, malformed, or conflicting headers fail with HTTP 400 and MCP error
  `-32020`. Encoded non-ASCII names use the standard Base64 sentinel form.
  Protocol errors `-32021` and `-32022` also map to HTTP 400.
- The modern HTTP path is sessionless and does not issue or require
  `Mcp-Session-Id`. Legacy request behavior remains available for older local
  clients.
- `/health` reports active and peak concurrency, request/failure/restart
  counts, the last restart reason, and rolling p50/p95 latency. The default p95
  budget is 8000 ms and is configurable with `--p95-budget-ms`.
- Per-process and per-session limits are configurable through environment
  variables listed in the [configuration reference](reference/configuration.md).

## Trust Boundary

Repository text, index artifacts, policy YAML, diffs, and tool arguments are
untrusted input. Keep the server scoped to repositories the user intends to
analyze. Read-only annotations describe expected tool behavior, but callers
must still request confirmation before invoking tools that write policy,
indexes, baselines, or reports.
