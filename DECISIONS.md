# Decisions

## 2026-07-18 - Task workflow has a local CLI fallback

Decision: expose the guarded `task` workflow as
`flyto-index task {plan,gate,validate}` in addition to MCP. The CLI reuses
`smart_task`, accepts exact file/symbol targets, supports JSON or JSON-file
gate inputs, and exits non-zero when gates or validation fail.

Reason: long-running MCP servers can keep stale Python code loaded after an
indexer fix. Large Flyto2 engine/frontend refactors need a current-source
fallback that preserves the same plan/gate/validate discipline instead of
forcing developers to either restart tooling or bypass task gates.

## 2026-07-18 - Task planning resolves explicit paths before symbol search

Decision: `task(action="plan")` treats path-like targets as high-confidence
file targets. Existing absolute paths are normalized through `project_roots`,
project-relative paths are matched exactly, file symbols are preferred, and
unmatched path-like inputs resolve to `unknown` without keyword or semantic
fallback.

Reason: semantic fallback can choose a similarly named symbol in the wrong file
or project. During Flyto2 engine refactoring, an explicit
`cmd/worker/kb_deep_scan_loop.go` path was misplanned as the Python
`IndexEngine` class from the bundled indexer package. Path inputs must fail
closed rather than borrow unrelated symbol risk.

## 2026-07-16 - TypeScript API wrappers preserve HTTP methods

Decision: TypeScript API call extraction treats method-aware frontend wrappers
such as `request<T>('POST', \`/api/v1/.../${id}\`)` as authoritative before
running broad string-literal fallback detection.

Reason: Flyto2 frontend engine clients use template-literal paths for many
mutating actions. The broad `/api/vN/**` string fallback was recording those
paths as GET before the method-aware `request` pattern ran, which polluted API
drift and product-profile evidence with false method mismatches.

## 2026-07-15 - Product API closure ignores mock fixtures

Decision: scope verify-time frontend/backend API closure to `/api/v1/**` and
exclude mock/dev endpoints such as `/api/mock/**` from unmatched API call gates.

Reason: generated Flyto2 Warroom CE packages include frontend mock helpers for
local UI fixtures. Treating those helpers as product API calls creates false
backend contract failures and hides real `/api/v1/**` drift.

## 2026-07-15 - Rules policy is a first-class verify gate

Decision: `flyto-indexer verify` evaluates `.flyto-rules.yaml` through the
rules engine and layer import graph, and reports it as `rules_policy`.

Reason: an empty rules file gave a false sense of architecture coverage. The
indexer now owns a real layer policy for foundation, scanners, analyzers, index
core, runtime services, tool surfaces, and entrypoints, with regression tests
that prove layer edges are checked and violations fail the verification gate.

## 2026-06-30 - Warroom CE install is generated, EE behavior is simulated by override

Decision: extend the open-core exporter so the Flyto2 Warroom CE package
contains an installable local delivery layer: CE Docker Compose, enterprise
simulation Compose override, local image build helper, enterprise JWT mint
helper, release-tree audit script, and operator docs.

Reason: Flyto2 Warroom should support a GitLab-style open-core path where users
can install a CE stack while enterprise implementation stays private. The public
tree must be reproducible and auditable, not a hand-maintained fork. EE
simulation should enable enterprise gates locally without copying enterprise
source or private image coordinates into the generated release.

## 2026-06-30 - Warroom CE publishes frontend source too

Decision: include `flyto-code` in the generated `flyto-warroom` public tree as
`packages/flyto-code`, with sanitized public metadata, public `.env.example`,
and CI frontend build coverage.

Reason: a self-hostable Warroom CE repo that exposes only backend contracts and
installer files cannot support meaningful community UI fixes. Frontend changes
should be reviewable in public PRs, then imported back into the private
`flyto-code` source repo before re-export so Flyto2 does not split into two
long-lived products.

## 2026-06-30 - Engine contracts publish as protocol artifacts

Decision: replace the first-pass `flyto-engine-contracts` raw source export with
`flyto-contracts`, a generated protocol package. It maps selected private engine
source files into public locations and generates schemas, examples,
conformance helpers, and SDK type stubs.

Reason: exporting Go `internal/**` paths has weak community value and creates a
messy boundary. Integration authors need stable protocol contracts, not partial
engine implementation. The exporter must fail closed if a mapped public package
would recreate private paths such as `internal/**`, `cmd/**`, or private API
handler trees.

## 2026-06-30 - Open-core exports are manifest-driven generated artifacts

Decision: add `flyto2-open-core-audit` and `flyto2-open-core-export`, backed by
`config/flyto2/open-core-manifest.json`, as the authority for Flyto2 community
package publication.

Reason: Flyto2 should use open source to build trust, but enterprise control
planes, billing, commercial intelligence, tenant governance, and live
remediation orchestration must not leak through manual copy/paste. A
deterministic whitelist keeps the OSS tree reproducible and mergeable while
failing closed on protected paths and denied secret/provider markers.

## 2026-06-24 - Remote CI startup is workspace release evidence

Decision: GitHub Actions startup evidence is now a workspace-level fresh
release contract. `github-actions-startup.json` may use the legacy
single-repo `flyto-code.github-actions-startup-audit.v1` shape or the
workspace `flyto.workspace-github-actions-startup-audit.v1` shape, but it must
prove required workflows completed successfully, created jobs, and include at
least one successful job per workflow.

Reason: local tests and source evidence cannot prove that GitHub actually
accepted a workflow, assigned a runner, created jobs, and produced green remote
CI. Flyto2 release operations must fail closed when workflows report
`startup_failure`, have no jobs, have no runner/log evidence, or have missing
runs for the current core-repo HEAD.

## 2026-06-23 - Fresh release evidence findings must block readiness

Decision: fresh release evidence contracts now propagate P0/P1 finding counts
into the release packet. A schema-valid artifact with P1 findings is not fresh
production proof and is reported with `reason=blocking_findings`.

Reason: a public-site or Product Verification artifact can be structurally
valid while still proving a live release blocker, such as AI crawler traffic
being blocked at the edge. Release readiness must not convert a valid evidence
shape into a false production pass.

## 2026-06-23 - Product Verification must be release evidence

Decision: make deterministic Product Verification a first-class release-packet
deliverable and require fresh `product-verification.json` to satisfy the
`warroom.product_verification.v1` contract before release confidence can be
claimed.

Reason: Warroom is not just a UI or a score. It must prove that Flyto2 can
produce a replayable intent/state graph, coverage and business-logic confidence
signals, and zero P0 deterministic findings from local evidence.

## 2026-06-23 - Release readiness is evidence-first, not score-first

Decision: treat Flyto2 health scores as minimum hygiene signals and move the
final release verdict to explicit evidence gates covering product lines,
deployment, security, GEO/i18n visibility, and operations.

Reason: a high static-analysis score can show that a repo is cleaner, but it
does not prove user workflows, enterprise deployment, security controls,
product positioning, or AI/search visibility. Production readiness must be
grounded in concrete evidence artifacts and fresh validation, not score chasing.

## 2026-06-22 - Release readiness must be evidence-backed

Decision: add `flyto2-release-packet` as a deterministic local CLI that
aggregates product gate results, git inventory, required release deliverable
evidence, P0 blockers, P1 before-production gaps, and a conservative release
verdict.

Reason: the Flyto2 workspace goal requires many artifacts beyond a green build.
The release packet keeps those artifacts machine-checkable and prevents agents
from claiming readiness when a required audit or smoke evidence file is missing.

## 2026-06-22 - Fresh evidence is separate from source evidence

Decision: `flyto2-release-packet` distinguishes source evidence from fresh run
evidence. Source evidence proves a guard/test/doc exists; `--require-fresh`
requires this run's artifacts to exist and be generated at or after
`--run-start`.

Reason: long Flyto2 convergence work must not reuse stale smoke screenshots,
old audit JSON, or previously generated release packets as proof that the
current nine-hour validation actually ran.

## 2026-06-21 - Health complexity is severity-weighted

Decision: score the health complexity dimension from high-complexity function
density, cumulative complexity burden, and top-hotspot severity instead of only
counting functions above the `score >= 5` threshold.

Reason: Flyto2 release gating must keep pressure on severe god functions and
dense complexity, while avoiding a misleading fail state where a barely-over
threshold helper has the same impact as a multi-hundred-line hotspot.

## 2026-06-21 - Non-core health exemptions must be explicit

Decision: allow non-core repos to carry an explicit health baseline exemption
when they have no indexed runtime symbols or use an unsupported analyzer
language, but block exemptions for core repos.

Reason: docs-only, deprecated, or unsupported-language repos should not create
permanent missing-health warnings, while core Flyto2 repos must remain covered
by a real score.

## 2026-06-21 - Flyto2 product lines are gate-controlled

Decision: keep the Flyto2 five-line product model in a manifest and fail the
release gate when a repo is unclassified, a core health target is missed, or
required project memory is missing.

Reason: Flyto2 must ship as one coherent product system with Cloud/Apps,
Security, Data, Zero-person Agent, and Big Data surfaces sharing `flyto-core`
without losing repo ownership or commercial boundaries.

## 2026-06-21 - Project memory bootstrap must be non-destructive

Decision: generated memory bootstrap may create missing files, but must never
overwrite existing repo-specific notes.

Reason: many repos already have partial memory written by prior agents. Release
automation should close structural gaps without erasing local ownership context.

## 2026-06-21 - Project memory is release-controlled

Decision: keep root project memory files, workflow docs, and handoff registry in
the repository and validate them in CI.

Reason: the indexer is used to audit other repositories, so its own memory and
verification loop must be explicit and machine-checked.

## 2026-06-21 - Local-first analysis remains the default

Decision: indexing, verify, audit, and impact analysis should run without
external services by default.

Reason: Flyto2 needs this tool for private source, enterprise deployments, and
airgapped audit workflows.
