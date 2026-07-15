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

## Deployment / Edition

- Developer mode runs from source in the local workspace.
- CI mode installs the package and runs verify gates without network-required
  services.
- Enterprise mode must support private repositories and airgapped source scans
  with local-only analysis.

## Trust Boundary

Untrusted input includes repository contents, generated indexes, policy files,
diffs, and user-provided queries. The indexer must avoid executing analyzed
project code during static checks and must keep generated artifacts isolated
from source commits.
