# Problems Flyto2 Indexer Solves

This guide starts with failure modes, not scanner internals. Exact commands,
arguments, schemas, and source links live in the
[generated reference](reference/README.md).

## “I Changed One Thing And Broke Another”

Before an edit, `impact` shows the selected symbol, its callers and dependents,
likely test files, cross-repository connections, and references that still need
manual review.

For a rename, move, delete, or signature change, it separates exact references
from same-name matches and unresolved dynamic uses. The coding agent gets a
reviewable change surface instead of assuming a text search found everything.

For an existing diff, it reports which code and tests are affected and attaches
a small local evidence case file. Generated output, lockfiles, and binary noise
do not consume the evidence budget.

## “The Agent Cannot Find The Right Code”

`search` combines exact terms with related concepts, then adds nearby symbols
and callers. Context and project-profile tools return bounded views of the
repository instead of dumping the whole tree into the model.

The local index covers symbols, imports, routes, calls, models, packages, and
architecture signals. Incremental scans reuse safe unchanged data, so a large
repository does not require a full rebuild for every question.

## “The Agent Missed A Rule Or Requirement”

`task(plan)` loads only the instructions that apply to the files being changed.
Nested rules take precedence; contradictory rules block the next phase instead
of being silently guessed around.

The same plan links each requirement to:

```text
planned step → expected path or symbol → test or proof
```

If the rules or requirements change after planning, the gate detects the stale
contract. If the final diff touches unrelated files or leaves a requirement
without proof, validation fails with a specific remediation.

Projects may keep this guidance advisory or make deterministic architecture,
atomicity, and documentation checks blocking. Waivers must be narrow, explained,
path-scoped, and time-limited.

## “The Task Is Too Vague To Plan Safely”

Decision Grill is optional. It resolves repository facts first, then asks one
high-value product or architecture question at a time. A recommendation and
counterargument travel with the question so the user is not forced to invent
options from scratch.

When the important choices are settled, Grill freezes the evidence, acceptance
criteria, and decision record into the task contract. Changed evidence reopens
only the affected decisions. Straightforward work can skip Grill entirely.

See the [Decision Grill test protocol](GRILL_TESTING.md).

## “The Tests Passed, But The Work Is Not Finished”

`verify` checks more than a test exit code. It closes:

- index freshness and internal consistency;
- context and impact lookup;
- secrets and unsafe data flow;
- documentation and repository policy;
- package, runtime, and generated-file consistency;
- working-tree and baseline regression hygiene.

`task(validate)` runs the project linter and tests, then checks the final diff
against the rules, requirements, decisions, paths, and proof recorded in the
plan.

A failed gate returns the missing actions. The agent completes them and reruns
the same gate; it does not treat the failure as permission to stop the task.

## “The Frontend And Backend Drifted Apart”

The indexer compares frontend calls with backend routes and preserves the HTTP
method when common wrappers are used. Missing calls, unused routes, and
cross-project type differences become visible before release.

Real product contracts remain strict. Mock and development fixtures can be
kept outside the product gate so test helpers do not create false failures.

## “The Audit Is Too Noisy To Trust”

Audits rank quality and security findings instead of presenting an unbounded
dump. They cover complexity, duplication, dead code, stale code, secrets,
unsafe data flow, vulnerable patterns, infrastructure configuration,
dependencies, licenses, documentation, and git hotspots.

Findings carry a stable identity, confidence basis, bounded trace, and
suppression history. Moving a line does not create a brand-new issue. Accepted
debt can be baselined while newly worse findings still fail CI.

Target code is treated as untrusted input. Static checks do not intentionally
import or execute the repository being analyzed.

## “One Change Crosses Several Repositories”

Workspace verification applies the same checks across a selected set of
projects. Impact analysis can show dependents and likely tests outside the
current repository, while API and type checks expose contract drift across
project boundaries.

Changed-only mode keeps the normal path focused. Full workspace verification
remains available for release gates.

## “The Scanner Itself Became The Bottleneck”

Flyto2 Indexer is designed to stay out of the way:

- analysis is local by default;
- the public MCP surface stays at 20 tools;
- large results are bounded and pageable;
- unchanged files are reused safely;
- optional precision adapters are not required for the normal path;
- timeouts and cancellation stop one request without sacrificing the next;
- the tool reports evidence but does not edit or commit product code.

For clients that need a persistent connection, the optional localhost bridge
keeps one child process warm, exposes health and latency, and restarts the child
after a failure. It refuses non-local binds.

## What It Understands

Built-in indexing covers:

- Python
- TypeScript and JavaScript
- Vue
- Go
- Rust
- Java
- Dart
- C and C++

It also reads common package manifests and lockfiles, detects framework and
infrastructure signals, exports software inventory, and can use local language
servers, SCIP, coverage, and test-result artifacts when they are already
available. Missing optional evidence is labeled; it is not presented as proof.

## Ways To Use It

- **MCP:** let an AI coding client call the focused tools.
- **CLI:** use the same planning, impact, audit, and verification flow in a
  terminal or CI job.
- **Python:** embed the index and focused analyzers in local tooling.
- **Local HTTP:** keep an optional loopback bridge available for compatible
  clients.
- **JSON export:** hand an explicit scan bundle to another approved system.

Start with the [root README](../README.md#installation-and-first-result). Use the
[CLI guide](CLI.md), [MCP guide](MCP.md), and
[verification guide](VERIFICATION.md) for daily workflows. Use the
[generated reference](reference/README.md) only when you need exact technical
detail.
