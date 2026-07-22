# Flyto2 Indexer Architecture

## Boundaries

- CLI and MCP surfaces call indexing, search, impact, verify, and audit services
  through explicit module APIs.
- Index artifacts are generated state, not source-of-truth repository files.
- Project rules live in repository-owned policy files and are merged with safe
  defaults.
- `.flyto-rules.yaml` is an active architecture contract. It must not be an
  empty placeholder: `verify` runs the rules/layers policy gate and reports the
  number of checked rules, layers, files, and local import edges.
- The indexer reports risk and evidence. It does not mutate product code unless
  a caller explicitly applies a separate edit.

The detailed ownership and dependency map is maintained in
`docs/architecture-map.md`. Every non-test Python declaration is linked from
`docs/reference/python-api.md`; adapter contracts have dedicated CLI, MCP, and
HTTP references.

## Data Flow

1. Scanner walks the repository and extracts files, symbols, imports, routes,
   dependencies, docs, and security signals.
2. Index data is stored locally under generated index directories.
3. Query and impact tools read that local index plus current filesystem state.
4. Verify combines index integrity, context lookup, impact closure, secret
   checks, taint rules, documentation checks, rules/layers policy checks, and
   release hygiene checks.
5. CI and agents consume structured findings and decide what to fix.

## Product API Contract Detection

- Product API closure is scoped to `/api/v1/**`. Mock and development-only
  endpoints under `/api/mock/**` are excluded before API definition/call
  matching so generated CE packages do not fail on fixture helpers.
- This exclusion is only for mock/dev API contracts. Real `/api/v1/**` calls
  remain strict and must be backed by backend route or OpenAPI evidence.
- TypeScript frontend call extraction must prefer method-aware call patterns
  (`request<T>('POST', path)`, `axios.post`, `api.patch`, etc.) before broad
  string-literal `/api/vN/**` fallback patterns. Fallback patterns may discover
  otherwise-hidden calls, but must not overwrite a real method-bearing wrapper
  call for the same normalized path.
- Source-reference fallback covers Vue, Svelte, and Astro single-file
  components as plain static text. It never executes frontend source; component
  names and API literals only supplement typed dependency edges when framework
  parsing cannot resolve an import.

## Deployment / Edition

- Developer mode runs from source in the local workspace.
- CI mode installs the package and runs verify gates without network-required
  services.
- Installed mode runs from an isolated environment and reports its active
  package version so callers can detect stale runtimes.
- Airgapped mode keeps scans local and does not require a hosted control plane.
- Product editions, commercial feature policy, and release orchestration are
  outside this repository.

## Interface Adapters

- `src/cli.py` translates shell arguments and exit-code requirements into core
  service calls.
- `src/mcp_server.py` handles stdio JSON-RPC, protocol negotiation, rate limits,
  tool listing, and dispatch.
- `src/api_server.py` provides a separate localhost bridge described by its
  embedded OpenAPI contract.
- `src/__init__.py` exports the stable Python package entry surface.

Adapters compose shared scanners, analyzers, indexes, and tools; lower layers
must not import protocol entrypoints. `.flyto-rules.yaml` encodes that direction.

## Documentation Contract

`scripts/generate-reference.py` derives interface references from AST,
registries, OpenAPI data, defaults, environment readers, and rule files.
`docs/documentation-manifest.json` maps broader feature surfaces to source,
durable explanation, and test evidence. Manifest-declared source references may
use repository-local glob patterns for split generated pages; resolved files and
their exact linked declaration targets must remain inside the indexed
repository. CI rejects either kind of drift.

## Trust Boundary

Untrusted input includes repository contents, generated indexes, policy files,
diffs, and user-provided queries. The indexer must avoid executing analyzed
project code during static checks and must keep generated artifacts isolated
from source commits.
