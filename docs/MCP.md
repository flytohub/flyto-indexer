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

`task(action="grill")` keeps the public tool surface stable while providing
persistent start, answer, status, freeze, and discard operations. A frozen
contract can be attached to `task(action="plan")`; `task(action="gate")`
validates its fingerprint before the existing phase gates. See the
[Decision Grill test protocol](GRILL_TESTING.md) for the real JSON-RPC closure
test and failure expectations.

## Protocol Behavior

- Transport is newline-delimited JSON-RPC over stdin/stdout.
- Initialization reports the active package version.
- The server accepts supported MCP protocol versions and otherwise replies with
  its preferred version so the client can decide whether to continue.
- Tool errors return structured JSON-RPC errors; analysis findings are normal
  tool results.
- Per-process and per-session limits are configurable through environment
  variables listed in the [configuration reference](reference/configuration.md).

## Trust Boundary

Repository text, index artifacts, policy YAML, diffs, and tool arguments are
untrusted input. Keep the server scoped to repositories the user intends to
analyze. Read-only annotations describe expected tool behavior, but callers
must still request confirmation before invoking tools that write policy,
indexes, baselines, or reports.
