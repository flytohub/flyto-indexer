# Decisions

## 2026-07-22 - Island fallback covers frontend single-file components

Decision: verify-time source-name and API contract fallback scans Vue, Svelte,
and Astro files alongside TS, JS, Go, and Python. Typed dependency edges remain
authoritative; text references only prevent a component from being called an
island when another non-test source file names it.

Reason: framework SFC import/template edges are not always represented in the
language-neutral graph. Excluding SFC source produced a confirmed false orphan
for a Vue component that was imported and rendered by a view.

## 2026-07-21 - Public indexer excludes product release policy

Decision: keep this repository limited to reusable code intelligence. Product
packaging, edition manifests, workspace release packets, provider release
audits, and commercial capability policy live in their owning private product
repository.

Reason: a reusable public scanner should not duplicate company release logic or
expose product boundaries. A single private owner also prevents stale copies.

## 2026-07-21 - Runtime version must be observable

Decision: derive the version from `pyproject.toml` when running a checkout and
from package metadata when installed. Expose it through `flyto-index --version`
and the machine-readable `tools` result.

Reason: a stale global executable can produce different audit results from the
current source while appearing to be the same tool.

## 2026-07-18 - Task workflow has a local CLI fallback

Decision: expose the guarded task workflow as
`flyto-index task {plan,gate,validate}` in addition to MCP.

Reason: a current-source CLI keeps plan, gate, and validation available when a
long-running MCP process has not reloaded updated Python modules.

## 2026-07-18 - Explicit paths resolve before symbol search

Decision: path-like task targets resolve exactly inside the requested project;
unmatched paths return unknown without semantic fallback.

Reason: exact paths must not silently select similarly named symbols from
another file or repository.

## 2026-07-16 - TypeScript API wrappers preserve HTTP methods

Decision: method-aware wrapper calls are authoritative before broad API string
fallback extraction.

Reason: fallback discovery must not turn POST, PATCH, or DELETE calls into GET
and create false contract drift.

## 2026-07-15 - Product API closure ignores mock fixtures

Decision: verify-time API closure covers `/api/v1/**` and excludes
`/api/mock/**` development fixtures.

Reason: fixture helpers are not deployed backend contracts, while real product
calls must still match route or OpenAPI evidence.

## 2026-07-15 - Rules policy is a first-class verify gate

Decision: `verify` evaluates `.flyto-rules.yaml` through the rules engine and
layer import graph.

Reason: architecture policy must produce checked files and edges instead of an
empty configuration that only appears to provide coverage.

## 2026-06-21 - Local-first analysis remains the default

Decision: indexing, verification, audit, and impact analysis run without
external services by default.

Reason: private and airgapped repositories must remain analyzable without code
egress.
