# Decisions

## 2026-08-24 - Static production chunks are generated evidence, not source

Decision: exclude the exact `static/assets` path sequence from symbol indexing
and classify files below it as generated for taint analysis.

Reason: Vite and Rollup chunks copied beneath a Python static backend are
minified, content-addressed build output. Parsing and tainting them duplicates
frontend source, creates parser errors, and reports XSS inside framework code.
The exact two-component boundary avoids substring exclusions of authored paths.

## 2026-08-22 - Amendment successors preserve schema and intent identity

Decision: emit `intent-ledger.v1` for new intent ledgers, keep instruction
context on `task-context.v1`, and accept the former shared ledger label only
when reading an existing parent contract. Finalization pins the root intent on
every amendment successor, including compound plans whose analyzer profile
otherwise exposes only `original_intent`.

Reason: first-round plans did not compare the two context schemas, so the
shared label and missing compound intent remained latent until an audited
rework attempted parent-proof validation. The producer must emit the canonical
contract and preserve immutable identity; consumers should not have to weaken
digest, path, chain, or fingerprint checks to make rework usable.

## 2026-08-14 - Recovery authority is proof-bound and producer-derived

Decision: expose recovery only on `task plan` as an exact content-bound
request containing the raw parent digest, audited prior paths, and explicit
targets. Derive normalization inside the Indexer, permit a historical fuzzy
target to be dropped only from a root parent when current exact resolution,
literal filesystem state, and one-to-one legacy resolution evidence all prove
it is not authority. A dropped record must also own its symbol and path
coordinates uniquely across the complete root or compound source matrix.
Successor resolution rows may expose no path or symbol outside their exact
project and canonical plan target. Bind the raw parent, normalized parent,
executable successor, and evidence with domain-separated canonical digests.

Reason: a terminal orchestration job may need one deterministic successor
without creating a new session or trusting caller-selected drops. Keeping job,
session, mission, and retry identities outside the public producer contract
preserves ordinary amendment replay and idempotency while letting a private
host bind its own recovery envelope to generic repository evidence.

## 2026-08-14 - Project and index identity has one immutable authority

Decision: resolve project root, index directory, human label, and a structured
SHA-256 cache key at an operation boundary, then pass that frozen identity
through task planning and tool scopes. A present `FLYTO_INDEX_DIR` is
authoritative even before creation; malformed or empty explicit values fail
closed rather than falling back to the current directory.

Reason: imported directory constants, later cwd/environment reads, and
label-only cache keys could make one request read or refresh another project's
index. One immutable identity preserves lazy loading and atomic reindexing
while removing ambient and mutable-global authority.

## 2026-08-13 - Task edit authority requires exact identity

Decision: general search remains fuzzy, but task planning accepts a search hit
as edit authority only when its symbol identity exactly matches the requested
target. Raw targets become Intent Ledger paths only when they have bounded path
shape. Preserve first-seen resolved-target order when compiling execution
plans.

Reason: a milestone label such as `M1.1` previously selected an unrelated logo
component through BM25 and was also recorded as an allowed path. Set-based path
deduplication separately changed step order and the dependency-map target across
Python hash seeds. Both behaviors violate reproducible, fail-closed planning.

## 2026-08-13 - Amendment authority is cumulative; diff coverage is incremental

Decision: preserve the cumulative union of amendment `allowed_paths`, but
require the current captured diff to cover only targets newly added by the
current amendment. Continue to reject removed prior authority, unplanned diff
paths, invalid chains, and malformed carried requirements independently.

Reason: prior targets describe retained edit authority, not mandatory repeated
work. Requiring every successor diff to re-edit all earlier targets made a
valid beta-only amendment fail after an alpha root task and compounded with
each later amendment.

## 2026-08-10 - Public-contract review requires public-contract evidence

Decision: apply the `human_review_completed` gate only when the task state
contains `public_contract_change_detected`. Keep the review constraint as an
opt-in policy and continue to fail closed when that evidence is present.

Reason: the constraint is derived from high breaking risk and names a
conditional review policy; treating the constraint itself as proof of a public
contract change blocked private and behavior-preserving maintenance before an
implementer could start.

## 2026-08-09 - Inline requirement paths use bounded file evidence

Decision: classify a slash-bearing code span, a conventional repository
filename, or a filename with a supported source, documentation, configuration,
or data suffix as a requirement path. Treat other identifier-shaped dotted
spans as symbols.

Reason: the former arbitrary-suffix heuristic treated module IDs such as
`human.approval` as files. That invented an unrelated required diff path and
made the mandatory post-work intent-ledger gate reject an otherwise correctly
scoped change.

## 2026-08-02 - Project-filtered semantic search owns its index scope

Decision: enter `project_index_scope` before loading both symbol and semantic
indexes whenever semantic search receives a project filter. Preserve aggregate
multi-project discovery only when no filter is provided.

Reason: loading a selected project while rebuilding stale sibling indexes made
an otherwise isolated MCP search exceed its request deadline. The filter must
constrain storage work as well as final result filtering.

## 2026-08-01 - Cross-AI continuity stays local and evidence claims stay paired

Decision: update the existing task lifecycle and project profile with one
gitignored SQLite continuity record; do not add an MCP tool, frontend, or
per-session handoff document. Store bounded task facts and normalized counters,
never prompts, responses, source, or raw provider payloads. Report a reduction
only when distinct variants pass the same declared proof policy under an equal
model, commit, task fingerprint, tool policy, and sample count.

Reason: developers switch among AI tools, but repeating context wastes time and
committing chat-shaped documents creates noise. A local shared state solves the
handoff problem without expanding the public API. Strict paired evidence keeps
useful efficiency measurement from becoming an unverifiable marketing claim.

## 2026-07-31 - Evidence growth precedes feature growth

Decision: hold the public MCP surface at 20 tools and require a reproducible
external failure, a bounded implementation, positive and negative fixtures,
and measured runtime cost before adding a scanner, framework adapter, or
language-depth claim. Publish indexing support separately from semantic and
security evidence.

Reason: a broad inventory makes a code-intelligence product look impressive
but does not make its conclusions trustworthy. Real cases and explicit limits
increase confidence without slowing every scan or making the package harder to
use.

## 2026-07-31 - Quality debt can decrease but cannot silently move

Decision: keep the remaining Ruff and dependency-isolated, Linux-targeted mypy
exemptions visible in an exact, production-only baseline tied to pinned tool
versions. CI fails when a count
increases and also requires deliberate baseline review when debt decreases.

Reason: a ceiling alone lets debt migrate between files and rules, while a
one-shot strict conversion would produce hundreds of unrelated edits. The
ratchet prevents new debt and makes each cleanup auditable.

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
