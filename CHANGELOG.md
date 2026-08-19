# Changelog

## 2026-08-13

- Made task edit authority exact and deterministic: BM25-only labels such as
  `M1.1` remain unresolved and cannot become allowed paths, hostile filesystem
  probes stay bounded, and execution plans preserve resolved-target order
  across Python hash seeds.
- Fixed cumulative task amendments so the latest diff must cover only newly
  added targets while retaining all prior path authority and fail-closed scope,
  unplanned-diff, chain-integrity, and sanitization checks.

## Unreleased

### Added
- A `security-triage` skill (`skills/security-triage/SKILL.md`) — a budget-aware
  orchestration policy that drives the existing MCP tools as a funnel: proven
  flows first, ranked leads, selective LSP verification on the top ~8 only
  (`call_hierarchy` capped at 8 per run, never the whole graph), then a human
  reading list. It adds no tool and no dependency — the intelligence is in the
  sequencing and the stop rules, not in the engine. Validated end-to-end on
  gradio: the funnel surfaces the four proven flows including the
  `undo_vibe_edit` path traversal, with demo and operator-fed flows demoted.

### Changed
- Made taint propagators configurable through `.flyto-rules.yaml` instead of
  hardcoding them in the engine, matching how sources, sinks and sanitizers
  already work. A project declares `taint.propagators` as `{name, from, to}`
  (positional, `f(src, dst)` taints dst) or `{name, receiver: true}`
  (`recv.m(taint)` taints recv); the built-in tables become defaults the YAML
  extends. `list_taint_rules` now reports propagators too. No new MCP tool —
  propagators are YAML-only, keeping the surface at its fixed tool count.
- Demoted (not dropped) proven flows outside the product attack surface and
  proven flows fed by operator input, so a `with open(...)` sink newly found in
  a demo app or a CLI tool's `argv`-fed write no longer outranks a real library
  lead. On gradio the top of the list is now all library code with the
  `undo_vibe_edit` path traversal in it.
- Scoped research-priority ranking to the attack surface after a run against a
  real 117k-line project (gradio) put nine demo apps, CLI helpers and dev
  scripts in its top twenty. Demo, example, script, docs and generated trees no
  longer seed unproven leads, and a sink fed by operator input (argv, prompt)
  is labelled `operator_input_and_sink` and ranks below one fed by a request
  rather than competing with it.
- Moved git log plumbing (`find_git_root`, `run_git`, the two log parsers and
  the TTL-cached log reader) from `tools/git_intel.py` to `src/git_history.py`
  so analyzers can share it without importing the tool surface. `git_intel`
  keeps its private names and public behavior.

### Security
- Fixed the three code-scanning findings in first-party code: the security
  scanner's string-literal regex could backtrack exponentially on an
  unterminated literal (CodeQL py/redos — the scanner reads repositories it did
  not write, so that is a denial of service); the .vue script-block parser
  missed sloppy closing tags such as `</script bar>`; and the public-site
  evidence script now pins TLS 1.2 as its floor instead of accepting whatever
  `create_default_context()` permits.
- Closed the container image's CVE-2026-53615 (util-linux). The runtime stage
  ran `apt-get upgrade`, which refuses any change that pulls in or removes a
  package, so the security revision — which drags mount, login and libblkid1
  with it — was held back and the vulnerable version shipped while the build
  looked clean. It now runs `dist-upgrade` and asserts the fixed version, the
  same way libexpat1 is asserted. Verified locally: Trivy HIGH,CRITICAL with
  `--ignore-unfixed` now exits 0 against the built image.

### Added
- Taint propagators and multi-hop return summaries (Semgrep- and Pysa-style),
  from diagnosing why mlflow's request handlers produced no flows. Taint now
  spreads through in-place mutation — `list.append(taint)`, `d[k] = taint`,
  `d.update(taint)`, `proto.MergeFrom(taint)`, and `parse_dict(json, proto)`
  (which taints the destination argument) — none of which put the tainted data
  on the left of an assignment, so value-flow taint could not see them. Method
  forms of the Flask body accessors (`request.get_json(`, `request.get_data(`)
  are now sources, matching how request objects threaded through a parameter
  are read. And the return-source registry is now a global fixpoint that
  re-derives function summaries until they stop growing, so a multi-hop chain
  (`read()` → `normalize()` → `parse_dict(json, proto); return proto`) converges
  instead of breaking at the first hop.
- Field sensitivity for instance attributes, and taint through context
  managers — the two recall gaps a diagnostic pass found after studying how
  Pysa, Semgrep and CodeQL model dataflow. Untrusted input stored on `self` in
  one method and used in a sink in another is now tracked (class-scoped, so
  attribute names do not collide across classes). And a sink inside a `with` or
  `async with` — `with open(tainted) as f:` — is now analyzed: the context
  expression was never inspected and `async with` was not matched at all,
  dropping most file/db/subprocess sinks in async code. On gradio this took
  proven source-to-sink flows from 1 to 4, and it now proves automatically the
  path traversal in `undo_vibe_edit` that previously only static reading found.
- Return-value taint propagation. A function that reads untrusted input and
  returns it — `def read_body(): return request.get_json()`, the most common
  input-helper shape — now taints its callers: `body = read_body()` makes
  `body` tainted and any sink it reaches is reported. Previously the engine only
  tainted a call result when one of the call's own arguments was tainted, so
  every zero-argument input helper was invisible. A bounded fixpoint follows
  return chains (`a` returns `b()` which returns a source). Precision-gated to
  short names with exactly one definition in the project: an ambiguous name like
  `predict`, defined in many modules, is dropped rather than attributed to an
  unrelated same-named call. Measured yield is codebase-shaped — zero new
  findings on gradio and mlflow, which inject via framework parameters (already
  handled), and real on codebases that wrap input in single-definition helpers.

### Fixed
- Required sink patterns to match on a token boundary at both ends. The
  right-hand guard alone let `exec(` match `create_subprocess_exec(` and
  `Template(` match `ResourceTemplate(`, which produced critical-severity RCE
  and SSTI leads out of ordinary async and MCP code.
- Stopped applying JavaScript-only sinks (`.innerHTML`, `document.write(`,
  `v-html`) to Python source text. The rule tables are shared across languages,
  so JS written inside Python string literals was reported as XSS in .py files.
- Restored cross-project taint recall. The engine reported zero source-to-sink
  flows on every real project in the workspace while still reporting large
  source and sink counts. Four causes: the 1000-function cap counted across the
  whole scan rather than per file and returned out of the scan silently
  (flyto-core saw 21% of its functions); hidden directories such as agent
  worktrees consumed the budget with duplicate copies; framework-injected
  handler parameters (`limit: str = Query(...)`) were treated as
  caller-dependent and deleted, so a route handler's own input could never
  produce a finding; and `await`ed calls were skipped entirely by the statement
  visitor, which covers most sink calls in async code. Caps that are hit are
  now reported in the result instead of resembling a clean scan.
- Stopped reporting parameterized ORM queries as SQL injection. A SQL sink now
  requires an argument assembled as a string at runtime; SQLAlchemy expression
  objects reaching `db.execute` are tracked and excluded, while an unknown
  variable still counts as dynamic so real flows are not dropped.
- Tightened cross-function callee matching: an exact final-segment match
  replaces a substring test that attributed `run(...)` flows to any call whose
  name contained it, and the dangerous function's defining file is now carried
  through both trace strategies so same-named functions in different modules
  are no longer merged.

### Added
- Added type-aware callee verification for the cross-function taint pass. When
  a language server is available, a call site is resolved to the definition it
  actually binds to and compared with the dangerous function's own definition;
  mismatches are dropped as name collisions. Verification is three-state — the
  "no server / no answer" case returns unknown and leaves the name-based result
  standing, so it upgrades the regex floor rather than replacing it. The result
  reports how callees were resolved and how many attributions were rejected.
  Verified against pyright: on an 8,933-function project, 71 of 109 checked
  cross-function attributions resolved somewhere other than the dangerous
  definition, at a cost of 15.4s to 18.5s for the whole scan.
- Added `research-priority`: a ranking that answers "which code paths are worth
  a security researcher's next hour" instead of emitting an undifferentiated
  finding list. It fuses signals the repository already produces — taint
  reachability, sink severity, entry-point exposure, function complexity, git
  churn, test gaps, and swallowed error handling — into one ordered short list,
  one candidate per function, with the evidence tier and plain-language reasons
  attached. Available as `flyto-index research-priority`, as the
  `research_priority` tool, and through `audit(focus='research_priority')`
  without expanding the 20-tool MCP surface. Signals that cannot be measured
  (no git repository, no index, non-Python file) are reported as unavailable
  and excluded from the weighted mean rather than scored as zero, and scan caps
  are reported so "found nothing" and "stopped looking" stay distinguishable.
  Candidates seeded by the weaker unproven tiers are labelled as such, because
  the cross-function taint pass is name-based and completes no flow at all on
  many real projects; parameterized/ORM SQL is suppressed so those endpoints do
  not crowd out real leads.

### Fixed
- Added a task-plan-only, proof-bound generation-2 recovery contract. It binds
  audited prior scope and explicit targets to the exact raw parent, derives
  historical fuzzy-target normalization inside the Indexer, emits
  domain-separated successor evidence, preserves ordinary amendment behavior,
  confines every executable resolution path and symbol to its exact project
  and plan coordinate, and rejects malformed, oversized, non-canonical,
  ambiguous legacy ownership, or non-plan recovery input without falling back
  to a fresh task. Audited prior scope is capped at 32 paths, and the closed
  typed-path grammar retains absent `.7z` archive targets without accepting
  arbitrary dotted identifiers.
- Updated the scanner image to the first Checkov 3.3.x dependency window that
  accepts aiohttp 3.14.3, then pinned and asserted the fixed aiohttp version to
  close CVE-2026-69244 without weakening the HIGH/CRITICAL Trivy gate.
- Synchronized the reviewed quality-debt baseline with the repository's pinned
  Ruff 0.16.2 toolchain and corrected one retired product name in a handoff.
- Unified CLI, MCP/API, task planning, search, Grill, watcher, maintenance,
  and reference loading behind one immutable project/index identity. Explicit
  `FLYTO_INDEX_DIR` paths are authoritative before creation and invalid values
  fail closed; identity-scoped caches no longer mix projects or reload a task
  plan's full index through a second authority.
- Made the public-contract human-review gate conditional on an actual
  `public_contract_change_detected` state. A high breaking-risk constraint no
  longer invents external approval when the current change has no public
  contract evidence; detected public contract changes remain fail-closed until
  `human_review_completed` is supplied.
- Stopped intent-ledger requirement parsing from treating arbitrary dotted
  module and capability IDs such as `human.approval` as file paths. Real
  relative files remain recognized through slash-bearing paths, supported file
  suffixes, and conventional repository-root filenames.
- Made the post-publication GitHub Release step repository-explicit so it works
  in the artifact-only job without requiring a repository checkout.
- Made the task intent ledger accept repository-root file symbol IDs such as
  `repo:smoke.py:file:smoke` and extensionless ones such as
  `repo:Makefile:file:Makefile`, so `task(validate)` no longer rejects the
  planned root-level edit as an unplanned diff. Scanner-produced paths and
  names keep their ordinary spaces and Unicode verbatim, and nested symbol IDs
  still resolve. Only unsafe structure is refused, without normalization:
  absolute paths, `..` traversal, tilde prefixes, backslash separators, ASCII
  control characters, blank project or name segments, non-conforming kinds,
  oversized paths, and prose-shaped IDs.

## [2.18.1] - 2026-08-03

### Fixed
- Scoped project-filtered semantic index loading and lazy rebuilds to the
  requested project, preventing stale sibling indexes from timing out an
  isolated MCP search while preserving unfiltered workspace search.
- Made automatic index discovery skip inaccessible sibling directories instead
  of aborting the entire scan, including Linux temporary directories protected
  by service-specific filesystem permissions.

## [2.18.0] - 2026-07-31

### Added
- Added project-local task continuity to the existing plan, gate, validate, and
  project-profile surfaces without adding an MCP tool. It keeps bounded,
  gitignored task facts so another AI client can resume unfinished work and
  only asks for a handoff when actionable state remains.
- Added provider-neutral usage accounting and terminal, JSON, CSV, and static
  HTML evidence reports. Raw prompts, responses, source, and provider payloads
  are never stored; reductions require a verified same-policy paired
  experiment and disclose whether counts were reported or estimated.
- Added a deterministic 100-scenario task continuity and efficiency gate. The
  committed receipt and CI require at least 90% success across normalization,
  estimation, lifecycle, privacy, comparison, report, and CLI contracts.
- Added a reproducible impact-analysis case against the pinned FastAPI full
  stack template. The checked receipt proves a real rename where exact text
  search finds one file while the dependency graph identifies four transitive
  request handlers across four files.
- Added an explicit per-language evidence matrix generated from the committed
  benchmark manifest. Indexing support, relationship depth, security depth,
  benchmark status, and known limits are now reported separately.
- Added public benchmark and real-repository proof workflows with readable job
  summaries and downloadable evidence artifacts.
- Added a production-code quality-debt ratchet that locks Ruff and
  dependency-isolated, Linux-targeted mypy debt to exact reviewed counts, tool
  versions, and rule categories. High-signal
  exemptions for duplicate imports, unused variables, ambiguous stripping,
  duplicate set members, and unsafe `zip` calls were removed and fixed.
- Added structured bug and accuracy-report templates plus a change-evidence PR
  template so false positives, false negatives, performance regressions, and
  missing relationships become reproducible product input.
- Added a release-tag gate that requires the Git tag, package version, MCP
  manifests, generated documentation, and dated changelog entry to agree.
- Added a fifth `task(action="feedback")` branch without increasing the
  20-tool MCP surface. It records, summarizes, and resolves local AI-development
  problems such as false positives, missing context, framework gaps, slow
  scans, and bad recommendations. Repeated issues share a stable identity;
  failed validations can add compact reason-code observations automatically.
  The append-only store redacts common secrets, home paths, and fenced code,
  never stores prompts or source code, and cannot change policy automatically.
- Added portable external proof receipts for browser, race, container,
  integration, runtime, security, penetration, and deployment evidence.
  `task(validate)` can require fresh passing receipts and accepts only locally
  trusted HMAC-attested evidence for a required proof kind.
- Added on-demand framework relationship hints to dependency queries for React
  lazy imports, `import.meta.glob`, mounted routers, route authorization, and
  ORM tenant scopes. The default scan path and dependency set remain unchanged.
- Added explicit health-score interpretation and per-dimension semantics so a
  budget ceiling cannot be confused with zero issues or runtime correctness.
- Added owner-aware suppression governance. Waivers now require an owner in
  addition to a rationale, source, scope, and expiry.

### Changed
- Tightened the shared CLI JSON loader's generic return type, removing three
  existing mypy exemptions and lowering the locked quality-debt baseline.
- Changed the PyPI workflow to run the full release evidence gate before
  building, publish only from a version tag, and create the GitHub Release only
  after Trusted Publishing succeeds.
- Refocused the README, contributor path, and roadmap on the measurable
  impact-to-verify loop instead of adding more scanners or public MCP tools.
- Corrected canonical Python complexity measurement to count actual control
  flow from the syntax tree instead of visual indentation inside multiline
  signatures, calls, or data literals. `elif` chains remain sibling branches,
  while genuinely nested conditions, loops, context managers, and exception
  handling still contribute depth. This removes false complexity regressions
  without raising or replacing the reviewed baseline.
- Fixed Git hotspot enrichment to consume the canonical complexity result,
  combining one-year change frequency with structural complexity instead of
  silently treating every file as complexity zero.
- Added dual-era MCP compatibility for the final `2026-07-28` specification
  while preserving all existing handshake-based versions. Modern clients can
  discover capabilities before calling tools, send self-contained stateless
  requests, receive required result and cache metadata, and get standardized
  protocol errors. The loopback HTTP bridge now rejects missing or conflicting
  routing headers before dispatch and maps modern protocol failures to HTTP
  400 without adding a dependency, session store, or public tool.
- Reworked the public README and documentation entry points around common
  change-safety pain, practical outcomes, and clear reader paths. Exhaustive
  implementation details remain in the generated reference and technical
  guides instead of leading the first-use experience. Added a clearly labeled
  illustrative Before/After rename walkthrough without presenting it as a
  customer result. Clarified the intended users, first-screen promise, and how
  the indexer complements coding agents, IDE search, linters, tests, and CI
  without requiring users to replace their existing workflow.

## [2.17.0] - 2026-07-30

### Added
- Added precise optional SCIP ingestion before LSP and heuristic reference
  fallback, without adding a default runtime dependency or public MCP tool.
- Added LCOV, coverage.py dynamic-context, and JUnit evidence correlation so
  diff impact can name the tests that executed changed lines and symbols, with
  content hashes and freshness state.
- Added a common finding evidence envelope across taint, secret, SAST, IaC,
  governance waiver, verify, and SARIF output: stable full fingerprints,
  calibrated confidence, bounded traces, and explicit suppression provenance.
- Added content-addressed incremental manifest v2. Full SHA-256 file addresses
  and a scanner-pipeline fingerprint safely invalidate stale parser output.
- Added opt-in Tree-sitter structural cross-validation with dependency-safe
  fallback to the native scanners.
- Expanded the offline corpus to 13 Python, JavaScript, TypeScript, and Go
  cases with four metamorphic groups, pinned differential categories,
  per-language metrics, and enforced p50/p95/max latency evidence.
- Added CI-produced per-test coverage contexts and JUnit artifacts, plus a
  downstream smoke proving changed-line-to-test correlation.
- Added one canonical `health-snapshot.v2` across `audit`, project profiles,
  and `verify`, including weighted complexity burden, high-confidence dead
  code, documentation, modularity, stable fingerprints, and project-owned
  quality budgets.
- Added regression-only quality comparison for health score, complex-function
  count, complexity burden, dead code, and documentation score, so CI can
  reject newly-worse code without requiring a disruptive legacy cleanup.
- Added HTTP MCP runtime telemetry and resilience: queued concurrency metrics,
  p50/p95 latency with an explicit budget, deadline-specific 504 responses,
  live-child protocol recovery, active-request cancellation, and child
  self-healing.
- Added `task(action="grill")`, a persistent, provider- and language-neutral
  pre-plan decision workflow with repository fact resolution, dependency
  frontiers, one-question interactive mode, bounded batch mode, recommended
  answers, idempotent updates, contradiction detection, readiness scoring,
  fail-closed freeze, immutable fingerprinted contracts, and plan/gate
  integration.
- Upgraded new Grill sessions and contracts to v2 with confidence calibration,
  value-of-information frontier ordering, decision cost and reversibility,
  bounded adversarial/counterfactual review, machine-readable acceptance
  criteria, content-addressed repository evidence snapshots, selective decision
  reopen plans, Markdown ADRs, and compact audit artifacts. Existing v1
  sessions and contracts remain valid.
- Added a decision-to-diff closure gate to `task(action="validate")`. When a
  frozen `task_contract` is supplied, validation now combines Ruff, pytest,
  contract fingerprint and evidence-freshness checks, expected/forbidden
  path and symbol checks, and safely allowlisted proof-result matching.
- Added privacy-preserving local outcome learning. Closed-loop results are
  idempotently recorded without questions, answers, or source code, then used
  as Bayesian priors for future decision confidence and VOI ordering.
- Added target-scoped JIT Rules and an Intent Ledger to the existing four-action
  `task` workflow. Plans now fingerprint applicable agent instructions and map
  bounded Markdown requirements to plan steps, changed paths, and proof;
  gate/validate fail closed on drift, conflicts, orphan requirements, missing
  coverage, or unplanned diff paths.
- Added semantic refactor preflight to `impact` for rename, move, delete, and
  signature changes, including exact identity, same-name ambiguity, overloads,
  unresolved references, and production/test/manual-review update sites.
- Added bounded, noise-filtered local Git evidence portfolios and deterministic
  evidence-linked verdicts to smart `audit` and diff-mode `impact`, without
  adding a public tool, task action, runtime dependency, patch body, or upload.
- Added dependency-free C/C++ indexing for function definitions, typedef
  structs, includes, and call edges across common source/header extensions.
- Added multilingual robot workflow fixtures and deep unit, concurrency,
  security, real-index, CLI, dispatch, persistence, tamper, and MCP subprocess
  coverage for the complete decision loop.
- Added responsibility-based atomic change guidance and change-aware
  documentation governance to the existing task contract. Enforcement remains
  advisory by default, with opt-in guarded/strict modes and expiring,
  path-scoped waivers.
- Added a committed, offline Python/JavaScript/Go scanner evaluation gate with
  positive, negative, sanitized, and cross-file cases; deterministic evidence
  fingerprints; precision/recall/false-positive metrics; and bounded latency.
- Added stable, privacy-preserving finding identities to taint results,
  verification baselines, and SARIF partial fingerprints. Verify schema v2 now
  detects new findings inside an already-warning check while reading legacy
  status-only baselines.
- Added bounded, cancellable stdio MCP tool execution with structured
  timeout/cancellation errors, annotation-aware retry evidence, deadline
  metadata, and process survival for the next request, plus the standard
  protocol-level `ping` liveness probe.

### Fixed
- Made coverage SQLite readers fail closed without creating a missing artifact,
  and configured CI to preserve the hidden `.coverage` database across jobs.
- Made canonical health expansions project the exact same content-addressed
  snapshot as the top-level audit, and fail closed if evidence diverges.
- Made generated task plans reference only callable public MCP tools and public
  phase names. Gate failures now return exact `required_state` keys, and
  compound contracts advance one subtask at a time.
- Split the CLI parser/dispatcher, task and structure focus branches, and tool
  registry into responsibility-focused units while preserving command, MCP,
  and result schemas.
- Made `audit --focus all` actually expand every dimension and replaced
  internal-only follow-up tool names with executable public next actions.
- Made repository fact resolution require normalized exact evidence by default,
  added explicit alternate policies, serialized session updates across POSIX
  processes, and made an incomplete CLI freeze exit non-zero.
- Rejected out-of-project repository evidence during contract validation and
  made evidence freshness use the contract's authoritative project even when
  lint/tests run from a different project root.
- Extracted focused task CLI and MCP dispatch adapters while preserving every
  existing task flag, action, legacy dispatch alias, and the 20-tool public
  smart-tool surface.
- Prevented security self-scans from reporting detector fixture constants and
  non-security checksum hashes while retaining real secret and password-hash
  findings; compound task plans now preserve every subtask step in the Intent
  Ledger.
- Included untracked source files in unstaged diff impact so newly added
  modules participate in symbol, risk, evidence, and verdict closure.
- Made validation respect pytest's configured `testpaths` instead of forcing
  repository-wide collection, and raised the bounded full-suite timeout to a
  configurable 900-second default.
- Changed MCP task-gate guidance from terminating on `pass=false` to a
  required-actions remediation loop that re-runs the same gate until it passes,
  with regression coverage for initialize, plan, and tool metadata contracts.
- Made `mcp_runtime_smoke` discover Python MCP console scripts from
  `pyproject.toml` and validate their module/callable targets statically,
  instead of reporting that external projects have no MCP server.
- Kept executable MCP runtime smoke limited to flyto-indexer's own adapter so
  verification never imports or executes analyzed project code.
- Reworked the README around one value statement, one quick start, and three
  differentiators; detailed inventories now live in the feature and generated
  references.
- Scoped MCP index loading and auto-reindex checks to the requested project,
  preventing idle requests from repeatedly scanning sibling repositories.
- Removed a duplicate two-line Go SSRF fallback finding exposed by the new
  accuracy corpus and corrected taint serialization to report the sink line.
- Classified benchmark trees as non-production taint input during a repository
  self-scan while preserving explicit per-case analysis by the evaluator.
- Made the auto-reindex regression module add the checkout root explicitly so
  installed pytest entrypoints collect it consistently on GitHub runners.
- Placed intentionally vulnerable benchmark cases under the established
  `fixture/` boundary so legacy Indexer versions in shared workflows exclude
  them from repository self-scans while the evaluator still scans them.
- Isolated auto-reindex and governance tests from shared lock/index state so
  both Python CI matrices are deterministic under the full-suite order.

## [2.15.0] - 2026-07-23

### Added
- Added dependency-free Dart and Flutter indexing for widgets, classes,
  constructors, methods, getters, functions, type declarations, and imports.
- Added docs-only CI recognition for Markdown lint, link tests, and
  documentation builds, plus an explicit `configuration_not_applicable`
  documentation contract and empty `module_roots` support.
- Added traversal-safe `documentation.source_reference_exclude` globs for
  vendored dependencies and fixtures documented by another repository.
- Added repository-local glob support for generated source-reference pages in
  `documentation.source_reference`.
- Added a structured documentation hub, feature guide, CLI and MCP guides,
  configuration and verification runbooks, security model, technical
  whitepaper, and machine-readable feature coverage manifest.
- Added generated references for every non-test Python declaration, CLI
  command and argument, smart and granular MCP schema, local HTTP operation,
  scanner default, environment reader, and built-in rule file.
- Added CI drift checks for generated references and MCP/package versions.

### Fixed
- Classified workspace frontend/backend roles from indexed UI and API evidence,
  ignored API base URLs, and scoped browser validation to repositories that
  actually own platform-loop registries.
- Indexed `.mjs`, `.cjs`, `.mts`, and `.cts` source files and excluded generated
  `.vitepress/cache/` bundles, so VitePress projects measure authored runtime
  code instead of dependency-cache symbols.
- Let documentation-heavy repositories declare source-owning
  `documentation.module_roots`; documentation coverage no longer treats every
  top-level locale or content directory as an undocumented software module.
- Count generated API references only when their repository-local source links
  resolve to the exact indexed declaration line; report inline, external, and
  combined symbol-documentation coverage separately.
- Synchronized MCP registry metadata and runtime initialization with package
  version 2.14.2.
- Made every shipped rule-corpus YAML file parse in a regression test and fixed
  unquoted IaC guidance values that were invalid YAML.
- Corrected README claims about the active MCP surface and optional LLM data
  handling.
- Marked the production `TestMapper` class as non-test so pytest does not emit
  a false collection warning when test modules import it.
- Included Vue, Svelte, and Astro single-file components in verify-time source
  reference and API contract discovery. Imported Vue components no longer
  produce false `single_project_islands` findings when the dependency scanner
  misses a template/script edge.
- Made `verify`, `verify-workspace`, and `verify-baseline` fail closed with exit
  code 2 when JSON output reports `pass: false`, while preserving the complete
  machine-readable report on stdout for CI evidence collection.

## [2.14.2] - 2026-07-21

### Fixed
- Made project policy evaluation fail closed instead of reporting a false
  `0 rules / 0 layers` pass when PyYAML is absent or the policy is malformed.
  PyYAML is now a runtime dependency, and malformed/non-mapping policies have
  regression coverage.

## [2.14.1] - 2026-07-21

### Added
- Added source-aware package version resolution, `flyto-index --version`, and a
  verified isolated local installer so stale executables are detectable.
- Added `flyto-index task {plan,gate,validate}` as a local CLI entrypoint for
  the same guarded task workflow exposed through MCP. The command supports
  repeatable `--target`, comma-separated `--targets`, JSON/file-backed
  `--task-contract` and `--current-state`, fail-closed gate exits, and
  validation exits suitable for shell and CI use.

### Fixed
- Completed the 2.14.0 public-package boundary: removed the remaining product
  release modules, manifests, evidence generators, and product deployment docs
  after moving the still-used workspace CI audit to its private owner.
- Made `task(action="plan")` path target resolution fail closed. Existing
  absolute and project-relative file targets now resolve by exact path within
  the requested project before any keyword/semantic search. If a path-like
  target is not present in the index, it resolves to `unknown` instead of
  selecting a similar symbol from another file or project.
- Made project-scoped git hotspot analysis fail closed when the requested
  project root is missing, instead of falling back to an arbitrary discovered
  index or CWD git repo. Added regression coverage so audit output cannot mix
  paths from unrelated repositories.

## [2.14.0] - 2026-07-09

### Removed
- **Extracted all Flyto2 company-specific business tooling out of this public,
  general-purpose code-intelligence package** — it never belonged here and it
  leaked the commercial edition boundary / private-code structure to anyone who
  `pip install`ed the tool. Removed the `flyto2-open-core-audit`,
  `flyto2-open-core-export`, `flyto2-product-gate`, `flyto2-release-packet`, and
  `flyto2-memory-bootstrap` CLI commands, their modules (`flyto2_open_core.py`,
  `flyto2_release_packet.py`, `flyto2_product_gate.py`,
  `flyto2_memory_bootstrap.py`), the `config/flyto2/` manifests (open-core
  boundary, product lines, evidence gates, health baseline), and the
  `open_core_manual/` bucket. This tooling now lives in the private flyto-engine
  repo (`release/`). flyto-indexer is once again purely a code-intelligence tool
  (index / search / impact / audit / verify / scan / deps / secrets / taint…).

### Note
- Prior releases (2.12.x, 2.13.0) shipped the open-core generator + manifest and
  should be treated as leaking the edition boundary; consider yanking them.

### Changed
- `flyto2-open-core-export` no longer VENDORS the already-open, separately
  published packages `flyto-core`, `flyto-indexer`, and `flyto-i18n` into the
  generated warroom tree. Copying their source only duplicated thousands of
  files and caused regeneration churn while not being used at runtime (the CE
  `docker-compose` runs prebuilt images). They are now declared external
  dependencies (new generated `DEPENDENCIES.md`) installed from their public
  registries; each keeps its own repo as the single source of truth. Removed
  them from the manifest `packages[]`, `PACKAGE_PATCHES`, `LICENSES.md`, the
  flow-back path-ownership doc, and the embedded CI (which had lint/test steps
  pointing at the now-absent `packages/flyto-indexer/`). The bundled CE product
  content — `packages/flyto-code` (frontend) and `packages/flyto-contracts`
  (engine API contracts) — is unchanged.

## [2.12.2] - 2026-07-09

### Fixed
- The generated `export-upstream-patches.py` flow-back tool crashed with
  `ValueError: ... is not in the subpath of ...` when `--output` pointed to a
  directory OUTSIDE the repo (it did `patch_path.relative_to(root)` on the
  report line). It now falls back to the absolute path via a `display_path()`
  helper, so both in-repo and external output dirs work. Verified end-to-end:
  a simulated warroom PR touching a source file produces a correctly
  prefix-stripped `<repo>.patch`, and a generated-only file is flagged in
  `REVIEW_GENERATED.md` — the backward (warroom→source) half of the
  bidirectional loop now runs cleanly.

## [2.12.1] - 2026-07-08

### Fixed
- `flyto2-open-core-export` was silently DROPPING 29 hand-authored CE-only files
  that existed only in the published warroom repo (CE distribution docs, install
  tooling, cloud-bundle fixtures, positioning material, `.env.example`,
  `CHANGELOG.md`, generated READMEs). They had no source-repo home, so every
  clean regen deleted them. They are now bundled verbatim under
  `open_core_manual/` (shipped in the wheel) and byte-copied into the generated
  tree, so regeneration reproduces them exactly. `_audit_generated_release` now
  hard-fails if any of them are missing (`manual_ce_file_missing`).

### Changed
- `export-upstream-patches.py` flow-back now classifies the full CE-only surface
  as generated-only: added `scripts/` to `GENERATED_REVIEW_PREFIXES` (was an
  existing gap — the generated `scripts/audit-*.py` weren't covered) and added
  `.env.example`, `CHANGELOG.md`, `packages/README.md` to `GENERATED_REVIEW_FILES`,
  so a warroom-side edit to any of them routes back to the generator instead of a
  misdirected source patch.

## [2.12.0] - 2026-07-08

### Added
- `agent-audit` findings now carry a **deterministic exploitability score**
  (0–100) built from category base + confirmation signals + MCP-reachability,
  binned into **confirm / review / drop** bands (`BAND_CONFIRM=70`,
  `BAND_DROP=35`). This is the "minimize-LLM" gate: high-confidence findings
  confirm and low-confidence findings drop without any model call; only the
  ambiguous review band is a candidate for optional downstream LLM triage.
  `score_factors` exposes the per-signal breakdown.

### Changed
- `flyto2-open-core-export` now emits a top-level **`LICENSE`** (full Apache-2.0,
  so GitHub detects the license on the generated flyto-warroom CE repo), a
  **`CLA.md`** (Apache-style inbound grant + dual-edition relicense right), and
  a **`.github/workflows/cla.yml`** (cla-assistant PR gate). `CONTRIBUTING.md`
  gains a CLA section. All three are registered in both the export-time release
  audit and the embedded downstream `audit-release-tree.py` required lists, so
  regeneration no longer drops them.

## [2.11.1] - 2026-07-08

### Added
- `agent-audit` now emits a **CWE id** and stable **rule_id** per finding, and
  an **mcp_reachable** flag — true when the sink is reachable (BFS over
  intra-file call edges) from an MCP/module entrypoint (`@register_module`/tool
  decorators, FastAPI routes, BaseModule `execute`/`run`), i.e. its params are
  attacker-influenced. This is the prioritization / AI-triage gate.
- Three new `agent-audit` classes (same required-guard / caller-controlled
  model): `code-injection` (CWE-95, eval/exec), `path-traversal-read` (CWE-22,
  open-read / read_text/read_bytes), and `ssti` (CWE-1336,
  render_template_string / Template.from_string). (2.11.0 added
  `command-injection` and `unsafe-deserialization`.)
- `AgentPolicyAnalyzer.parse_failures` counter so unparseable files are not a
  silent coverage gap.

### Tests
- `tests/test_agent_policy.py` pins every class (positive + guarded-negative)
  and the mcp_reachable signal.

## [2.11.0] - 2026-07-08

### Added
- Added `flyto-index agent-audit` — an AI-agent / MCP / sandbox security policy
  analyzer that detects vulnerability classes generic SAST and the stock taint
  engine miss (policy / absence / cross-function bugs): outbound HTTP on a
  caller-controlled URL with no SSRF guard, guarded HTTP modules that follow
  redirects unrevalidated, unauthenticated state-changing routes,
  attacker-influenced `os.getenv`/`environ` reads, env credentials reachable at
  a caller-controlled `base_url`, and file writes to caller-controlled paths
  without the sandbox guard. Emits a high/medium/low confidence tier (the gate
  for downstream AI triage). Pure stdlib; no external dependencies.
- Added `flyto2-product-gate` to validate the Flyto2 workspace product-line
  registry, repo classification, project memory completeness, and health
  targets before release readiness review.
- Added `flyto2-memory-bootstrap` to scaffold missing project-memory files,
  workflow docs, and handoff registries from the Flyto2 manifest without
  overwriting existing repo notes.
- Added `flyto2-release-packet` to generate an evidence-backed Flyto2 workspace
  inventory, deliverable matrix, residual blocker list, and readiness verdict.
- Added fresh evidence enforcement to `flyto2-release-packet` through
  `--fresh-evidence-dir`, `--require-fresh`, and `--run-start`.
- Added the Flyto2 product-line manifest and 2026-06-21 health baseline used by
  the new gate.
- Added the Flyto2 evidence gate manifest and `--evidence-gates` release-packet
  option so product readiness is judged by product-line, deployment, security,
  visibility, and operability proof instead of health score alone.
- Added the `deterministic_product_verification` release-packet deliverable and
  `warroom.product_verification.v1` fresh evidence contract for Product
  Verification / Warroom runs.
- Added `scripts/write_product_verification_evidence.py` for local dry-run
  Product Verification release artifacts.
- Added the `public_site_verification` release-packet deliverable,
  `flyto2.public_site_verification.v1` fresh evidence contract, and live public
  site evidence helper for DNS/TLS/route/browser/SEO-GEO proof.
- Added `scripts/write_continuous_release_evidence.py` to turn current local
  release-packet source evidence into fresh workspace, architecture, billing,
  RBAC, state-machine, enterprise, GEO, i18n, security, and browser-smoke digest
  artifacts without hiding P0/P1 findings from contract artifacts.

### Changed
- Updated `code_health_score` complexity scoring to combine high-complexity
  function density, cumulative severity burden, and the top hotspot score
  instead of treating every `score >= 5` function as equal.
- Recalibrated the Flyto2 health baseline for `flyto-ai`, `flyto-core`, and
  `flyto-indexer` using the severity-weighted complexity gate while preserving
  complex-function counts, burden, and top-hotspot evidence in the reasons.
- Lifted the Flyto2 health baseline for `flyto-i18n` from C to B after the
  2026-06-22 reverse audit refactored sync/add-locale tooling and rebuilt the
  index.
- Lifted the Flyto2 health baseline for `flyto-vscode` from C to B after the
  2026-06-22 reverse audit refactored webview payload getters, added compiled
  regression tests, and rebuilt the index.
- Lifted the Flyto2 health baseline for `flyto-modules-pro` from C to B after
  the 2026-06-22 reverse audit refactored enterprise LDAP, SAML metadata, Azure
  OCR, and map-reduce reducer hotspots with guard tests.
- Added explicit non-core health exemptions for docs-only, no-symbol, or
  unsupported-language repos while keeping core repo exemptions blocked.
- Reframed Flyto2 health grades as minimum hygiene signals. Missing P0/P1
  evidence now blocks production readiness even when all repo scores are high,
  and non-core score regressions are warnings unless product evidence depends on
  that repo.
- Extended Flyto2 product-line and release-operation gates so Cloud/Apps,
  Security, Zero-person Agent, and release operations depend on deterministic
  Product Verification evidence.
- Extended visibility, Big Data / Intelligence, and release-operation gates so
  public `flyto2.com` reachability and AI crawler evidence are judged separately
  from static SEO documentation.

### Fixed
- Fresh release evidence contracts now propagate P0/P1 findings into the
  release packet. Schema-valid public-site evidence with AI crawler P1 findings
  is now marked `blocking_findings` instead of being accepted as production
  proof.
- Dead-code detection now treats VitePress markdown component tags such as
  `<BlogHero />` as live component references.
- Cleaned existing test-suite lint violations so `ruff check src tests` can run
  as a full release gate.

## [2.10.3] — 2026-06-13

### Fixed
- Improved `find_dead_code` precision for MCP audits:
  - Treat Python registry/dispatch-table callbacks and decorated functions as live.
  - Treat Go exported symbols and DTO/model/type contracts as public surface, not deletion candidates.
  - Ignore test, fixture, example, and Semgrep fixture paths for production dead-code cleanup.
  - Detect same-project type/class references from source text so Go same-package DTOs are not falsely reported.
- Aligned dead-code filtering across MCP maintenance tools, tag generation, and health scoring.

## [2.9.0] — 2026-04-20

### LSP deepening — precision layer on top of the regex scanners

All changes are strictly additive: when no LSP server is available for the
language, every feature falls back to the existing stdlib path. Set
`FLYTO_LSP_ENABLED=0` to skip LSP globally.

#### Phase 1 — Import resolution (layers + cross-function taint)
- New `src/lsp/resolver.py` — wraps `textDocument/definition` with an open-file memo
- `analyzer/layers.py::resolve_import` now takes optional `line_content` / `line_num_0based` and asks the language server when the static chain (relative → alias → go.mod) comes back empty. Picks up complex tsconfig paths, Python namespace packages, and gopls vendor directories that the heuristic missed

#### Phase 2 — Type-aware taint filter
- New `src/analyzer/type_filter.py` — queries `textDocument/hover` on a source expression and parses the type out of the hover payload (pyright / tsserver / gopls formats all supported)
- `TaintAnalyzer._is_source` post-filters matches: `int`, `bool`, `float`, `datetime`, `UUID`, TS `number` / `boolean` etc. are dropped because string-injection sinks cannot be exploited with non-string values. `TaintAnalyzer._type_filtered` surfaces the FP-suppression count for telemetry

#### Phase 3 — Workspace symbol + call hierarchy
- New `src/lsp/call_graph.py` — walks `textDocument/prepareCallHierarchy` + `callHierarchy/incomingCalls` / `outgoingCalls` up to a bounded depth. Result is the real, type-resolved call graph — same-named functions in different modules no longer collide
- New `src/lsp/workspace_symbols.py` — `workspace/symbol` candidate search across every running language server with dedup
- `tools/references.py::impact_analysis` now merges LSP-resolved indirect callers (depth 2) into its affected list — real blast radius for high-stakes refactors
- New MCP tool `call_hierarchy` — explicit depth-N incoming / outgoing call query

#### Common infrastructure
- New `src/lsp/cache.py` — mtime-keyed in-memory response cache for definition / hover / call-hierarchy results. Bounded at 4096 entries, cleared by `LSPManager.reset_instance()`
- `lsp/client.py` — initialize capabilities now advertise `hover`, `typeDefinition`, `implementation`, `callHierarchy`, `workspace.symbol`
- New client methods: `text_document_hover`, `text_document_type_definition`, `text_document_implementation`, `workspace_symbol`, `text_document_prepare_call_hierarchy`, `call_hierarchy_incoming_calls`, `call_hierarchy_outgoing_calls`

### Environment
- `FLYTO_LSP_ENABLED` — `0` to disable LSP globally (default: on)
- `FLYTO_LSP_TIMEOUT` — per-request timeout in seconds (default: 10)

## [2.8.0] — 2026-04-20

### Added
- **Taint DSL** — unified `taint:` block inside `.flyto-rules.yaml` for project-specific sources / sinks / sanitizers
  - Engine (`TaintAnalyzer`) now reads from `.flyto-rules.yaml → taint:` first, then falls back to the legacy `taint_rules.yaml` / `.flyto-index/taint_rules.yaml`
  - Custom rules are merged on top of built-in defaults — project-declared patterns never replace the framework-aware library
  - CLI writers: `add-taint-source`, `add-taint-sink`, `add-taint-sanitizer`, `list-taint-rules`
  - MCP tools: `add_taint_source`, `add_taint_sink`, `add_taint_sanitizer`, `list_taint_rules`
  - New `analyzer/taint_dsl.py` module (YAML CRUD only; analysis stays in `analyzer/taint.py`)
- **Architecture Layer Rules** — declarative layer membership and import graph enforcement via `.flyto-rules.yaml`
  - New `layers:` schema: `name`, `paths` (glob), `can_import` (whitelist), `cannot_import` (blacklist), `reason`
  - New `cross_imports_deny:` schema for point-to-point forbidden edges
  - Import graph walker supports Python, TypeScript/JavaScript, Vue, Go (via `go.mod` module path)
  - `tsconfig.json paths` auto-resolved as aliases
  - CLI: `flyto-index layers <path>` (human report) and `--json --fail-on-violation` (CI gate, exits non-zero)
  - CLI: `flyto-index add-layer` — writes a layer definition into `.flyto-rules.yaml`
  - MCP tools: `check_layers`, `add_layer`
  - Violations automatically flow through the `audit` smart tool — no change on consumer side
- `analyzer/layers.py` module (stdlib-only core, PyYAML used only for write-back)

## [1.4.0] — 2026-03-11

### Added
- `flyto-index setup .` — single command that does everything: scan + CLAUDE.md + MCP config
- `flyto-index setup . --remove` — clean uninstall (removes CLAUDE.md section + MCP settings)
- Auto-detects Python path for MCP server configuration

### Changed
- README simplified to two-line install: `pip install flyto-indexer` + `flyto-index setup .`
- `setup-claude` kept for backward compatibility but `setup` is now the recommended command

## [1.3.2] — 2026-03-11

### Changed
- `setup-claude` template now includes auto-index instructions — tells AI to run `flyto-index scan .` if `.flyto-index/` doesn't exist

## [1.3.1] — 2026-03-11

### Added
- `flyto-index setup-claude` CLI command — auto-appends task contract and tool usage instructions to CLAUDE.md
  - Idempotent (skips if already added)
  - `--remove` flag to cleanly remove the section
  - Uses HTML comment markers to avoid interfering with other CLAUDE.md content

## [1.3.0] — 2026-03-11

### Added
- **Task Contract system** (`analyze_task`, `task_gate_check`) — multi-dimensional risk assessment with data-driven execution plans
  - 6 dimensions: blast_radius, breaking_risk, test_risk, cross_coupling, complexity, rollback_difficulty
  - Execution plan: concrete tool call sequences with pre-filled args and step dependencies
  - Gate checks: phase-based validation before proceeding
  - Signal-based scoring with cross-dimension constraint escalation
  - Strategy mode override by dimension levels
  - index_confidence metric for data completeness
- Tool count: 30 → 32

### Changed
- Refactored tool dispatch into focused modules (`search`, `references`, `code_info`, `maintenance`, `task_analysis`)
- Extracted `IndexStore` from `mcp_server.py` for cleaner separation of concerns
- Improved `code_health_score` reuse in project signals

## [1.2.3] — 2026-02-12

### Fixed
- Improved scanners, analyzers, and dead code detection accuracy
- Better health score formulas for documentation and modularity metrics

## [1.2.1] — 2026-02-11

### Removed
- Dual-AI tool definitions from MCP server (strategic pivot to A+C strategy)

### Added
- Index metadata fields

## [1.2.0] — 2026-02-09

### Added
- Auto-reindex on file changes (`check_and_reindex`)
- `impact_from_diff` — git diff → symbol impact analysis
- Semantic diff analysis
- MCP resources support

## [1.1.0] — 2026-02-06

### Added
- 6 code quality MCP tools: `find_dead_code`, `find_complex_functions`, `suggest_refactoring`, `find_duplicates`, `find_stale_files`, `find_todos` (23 → 29 tools)
- Tool annotations (`readOnlyHint`, `openWorldHint`)
- MCP protocol upgrade from 2024-11-05 to 2025-11-25
- BM25 search ranking
- Cross-language API graph (Python ↔ TypeScript/Vue)
- Live reindex capability
- `install-hook`, `demo`, `check` CLI commands

### Fixed
- 7 CodeQL security alerts resolved

## [1.0.0] — 2026-01-30

### Added
- Initial release — MCP server for code intelligence
- AST-based indexing for Python, TypeScript/JS, Vue, Go, Rust, Java
- Impact analysis, dependency graph, reverse index
- Incremental indexing with content hash tracking
- CLI: `flyto-index scan`, `flyto-index impact`

---

## Roadmap (V2 — not yet started)

Priorities depend on real-world usage feedback from V1.

- **Execution plan consumer** — AI agent integration that actually follows the generated execution plan steps
- **Gate history tracking** — session-aware gates that remember which phases were completed (currently stateless)
- **Feedback loop** — post-task result recording (success/failure, actual files changed) to improve future scoring
- **Multi-target dependency analysis** — analyze interactions between targets, not just individual target scoring
