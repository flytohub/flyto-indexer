# Decisions

## 2026-07-31 - Development feedback proposes evidence, never policy

Decision: add record, summary, and resolve operations to the existing `task`
tool. Keep observations append-only and local, redact common source/secret
material, aggregate repeats by stable semantic identity, and require human
review plus a benchmark or regression test before changing any scanner rule,
suppression, baseline, or CI policy.

Reason: LLM sessions repeatedly expose false positives, missing relationships,
slow paths, and poor recommendations, but chat history is not a durable product
backlog. Automatic rule learning would create a new path for noisy or hostile
input to weaken enforcement.

## 2026-07-31 - Runtime proof is federated through attestable receipts

Decision: keep browser, race, container, integration, penetration, and
deployment execution in their owning systems. Accept content-addressed receipts
in `task(validate)` and require a fresh passing locally trusted HMAC attestation
when a proof kind is mandatory.

Reason: embedding every runtime would make the indexer slow and operationally
heavy. Visible unsigned evidence remains useful, while trusted required proof
must fail closed on tampering, staleness, cross-project reuse, or unknown keys.

## 2026-07-31 - Framework heuristics stay on the dependency-query path

Decision: detect React lazy imports, dynamic import globs, mounted routers,
authorization guards, and ORM tenant scopes only when a caller requests
dependencies for a concrete supported source file. Label these edges as
heuristic evidence with explicit limits.

Reason: dynamic framework wiring matters for impact analysis, but a universal
deep framework pass would slow ordinary indexing and still could not prove
runtime authorization or business correctness.

## 2026-07-30 - Scanner claims require local reproducible evidence

Decision: keep a small committed positive/negative Python, JavaScript, and Go
corpus in CI. Run it through the real index and taint analyzer and gate exact
findings, cross-file path proof, scan errors, precision, recall, false-positive
rate, and bounded latency.

Reason: external corpus plans and feature inventories do not prove current
behavior. A fast offline gate catches regressions on every change without
cloning repositories, adding a scanner dependency, or slowing ordinary MCP
calls.

## 2026-07-30 - Finding identity is semantic and line-independent

Decision: derive privacy-preserving finding IDs from rule, normalized
repository-relative path, bounded semantic anchor, and discriminator. Exclude
line numbers and raw source bodies; reuse the ID in verify baselines and SARIF.
Legacy baselines without IDs remain status-only.

Reason: line-number fingerprints create review churn, while check-level status
alone lets new findings hide inside an existing warning. One stable local ID
closes both gaps without storing source excerpts.

## 2026-07-30 - Stdio requests are bounded and cancellable

Decision: give MCP tool calls bounded deadlines, accept standard cancellation
notifications while a request is active, return structured retryable errors,
and keep the process available afterward. Use blocking input plus POSIX
main-thread interruption; do not add polling, worker pools, or a new public
tool.

Reason: an unbounded read-only scan can make the whole service appear dead.
Request-scoped interruption restores liveness without recurring CPU cost or
expanding the public API.

## 2026-07-28 - Gate failure starts remediation, not termination

Decision: MCP initialize, plan, and task-tool contracts define `pass=false` as
a phase-local remediation loop. Agents must complete every available
`required_actions` item, set the exact requested `current_state` keys, and
re-run the same gate until `pass=true`.

Reason: a safety gate exists to prevent an unsafe transition while directing
the missing analysis or review. Treating it as task termination abandons the
closed loop and defeats the safety control.

## 2026-07-26 - External MCP verification stays static

Decision: for an analyzed Python project, discover MCP console scripts from
`pyproject.toml` and validate the referenced module and top-level callable with
filesystem checks and AST parsing. Only flyto-indexer's own MCP adapter receives
an import-based runtime smoke.

Reason: reporting "No MCP server module" for packaged MCP entry points hid
real configuration drift, while importing an external target would violate the
indexer's untrusted-repository boundary.

## 2026-07-23 - Index authored module variants, not VitePress caches

Decision: the TypeScript scanner accepts `.mjs`, `.cjs`, `.mts`, and `.cts`
alongside the standard JavaScript and TypeScript extensions. Repository scans
exclude `.vitepress/cache/` while retaining authored `.vitepress` configuration
and theme source.

Reason: modern documentation and frontend repositories place executable build,
SEO, and automation code in module-variant files. Counting generated VitePress
dependency bundles instead hid that code and produced misleading symbol and
documentation-coverage results.

## 2026-07-22 - Interface documentation is generated from source

Decision: non-test Python declarations, CLI arguments, MCP registries, local
OpenAPI operations, defaults, environment readers, and built-in rule files are
rendered by `scripts/generate-reference.py` and checked in CI. A target
repository may declare one source-reference file or a repository-local glob of
split pages; absolute and repository-escaping paths never count as evidence.

Reason: hand-maintained counts and interface tables drifted from the package;
source-backed generation keeps exhaustive detail reviewable without turning the
README into an implementation dump. Safe glob expansion lets large repositories
retain the same exact source-line proof without creating one oversized page.

## 2026-07-22 - One package version feeds every runtime manifest

Decision: `pyproject.toml` is the release-version authority;
`scripts/sync-version.py` checks the MCP registry manifests, while
`src/version.py` resolves source and installed modes.

Reason: clients must be able to detect stale MCP installations and registry
metadata must not advertise a different build.

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
