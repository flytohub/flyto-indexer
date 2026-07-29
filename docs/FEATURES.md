# Feature Guide

This guide explains the supported product surfaces. Exact signatures,
arguments, schemas, and source links live in the
[generated reference](reference/README.md).

## Index And Search

`scan` walks supported source and configuration files, extracts symbols,
imports, routes, package dependencies, and documentation signals, then writes a
local `.flyto-index/` graph. Incremental scans reuse unchanged data; `--full`
rebuilds it. `search` combines lexical and semantic ranking and enriches results
with callers and neighboring symbols.

JavaScript and TypeScript coverage includes CommonJS, ECMAScript module, and
typed module variants (`.cjs`, `.mjs`, `.cts`, and `.mts`) in addition to the
standard extensions. Authored VitePress config and theme source is indexed,
while generated `.vitepress/cache/` dependency bundles are excluded.
Dart coverage indexes Flutter widget classes, ordinary classes, constructors,
methods, getters, top-level functions, enums, mixins, extensions, and import or
export directives without requiring a local Dart SDK.
C and common C++ coverage indexes function definitions, typedef structs,
includes, and call edges across `.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hh`,
`.hpp`, and `.hxx` without requiring Clang. It is a dependency-free structural
indexer, not a replacement for compiler-accurate preprocessing.

Primary implementation: `src/engine.py`, `src/scanner/`, `src/indexer/`,
`src/index_store.py`, and `src/tools/search.py`.

## Context And Project Profiles

Context tools produce bounded, AI-ready views instead of dumping a repository.
They support project briefs, outlines, file descriptions, architecture
conventions, package inventories, framework detection, API catalogs, type
contracts, and project profiles. Generated context is evidence, not executable
code.

Primary implementation: `src/context/`, `src/profile/`,
`src/tools/project.py`, and `src/tools/structure.py`.

## Impact And Call Graphs

Impact analysis resolves a symbol or diff to references, transitive callers,
cross-project dependents, likely test files, call paths, and a risk level.
Explicit paths are resolved before fuzzy symbol search. Language-server results
can enrich static edges when a supported local LSP is available; deterministic
parsers and text fallback remain available.

Primary implementation: `src/engine.py`, `src/diff_impact.py`,
`src/lsp/call_graph.py`, `src/lsp/`, and `src/tools/references.py`.

## API And Contract Drift

The indexer extracts backend routes and frontend calls, preserves HTTP methods
from common TypeScript wrappers, and reports route definitions without callers
or calls without definitions. Product closure checks apply to `/api/v1/**`;
mock `/api/mock/**` fixtures are excluded from that product gate.

Primary implementation: `src/scanner/api.py`,
`src/analyzer/api_consistency.py`, `src/analyzer/api_drift.py`, and
`src/tools/contracts.py`.

## Quality And Security Audits

Audits cover complexity, duplication, dead code, stale code, coverage gaps,
secrets, license posture, vulnerable patterns, taint flows, infrastructure as
code, AI-agent policy, documentation, dependency health, and git hotspots.
Static analysis never intentionally imports or executes the target project.
Documentation scoring reports inline summaries separately from generated
source-reference coverage. An external reference counts only when its manifest
declares the reference file and the Markdown link resolves inside the target
repository to the exact indexed declaration line.
`documentation.source_reference` accepts repository-local file paths or glob
patterns, allowing large generated references to stay split into navigable
pages without losing exact symbol coverage accounting.
`documentation.source_reference_exclude` accepts repository-relative globs for
vendored dependencies and fixtures whose authoritative reference belongs to
another repository. Absolute paths and parent traversal are ignored;
exclusions affect documentation scoring only, never source or security scans.
Docs-only repositories with no runtime environment can set
`documentation.configuration_not_applicable` to `true`; this suppresses the
`.env.example` recommendation without weakening source or security checks.
They can also set `documentation.module_roots` to an empty list when the
repository intentionally has no source-owning module directories.

Repositories that mix source code with top-level content directories can set
`documentation.module_roots` in `docs/documentation-manifest.json`. The scanner
then measures README or package-docstring coverage only for those declared,
repository-local source roots. This prevents translated pages and other content
collections from being misclassified as undocumented software modules while
keeping the default top-level-directory discovery for repositories without a
manifest scope.

Primary implementation: `src/analyzer/`, `src/auditor/`, `src/quality.py`, and
the rule corpus under `config/rules/`.

## Repository Policy

`.flyto-rules.yaml` can define file placement, forbidden patterns,
architecture layers, allowed imports, and project-specific taint sources,
sinks, and sanitizers. Built-in rules and project rules are merged; malformed
policy fails verification instead of silently disabling checks.

See [Configuration](CONFIGURATION.md#repository-policy) and the generated
[configuration reference](reference/configuration.md).

## Task Workflow

The `task` workflow supports evidence-backed decision interrogation, planning,
phase gates, and post-change validation. `task(action="grill")` keeps the
LLM-visible tool surface small while composing internal decision atoms:

- `start` creates a persistent dependency-ordered decision tree.
- repository-owned facts are resolved from indexed symbols and are never
  redirected to the user;
- repository evidence uses normalized exact matching by default so unrelated
  fuzzy hits cannot satisfy a critical fact; controlled `all_terms` and
  explicit trusted-resolver policies are available;
- `answer` accepts only a currently reachable frontier decision and supports
  idempotency keys;
- interactive mode returns exactly one question and a recommendation; explicit
  batch mode returns a bounded frontier;
- `freeze` fails closed on unresolved blocking decisions or structured option
  contradictions;
- the frozen contract is fingerprinted, immutable, and attachable to
  `task(action="plan")` through `grill_session_id`;
- `task(action="gate")` validates the fingerprint and decision completeness
  before applying the existing implementation gates.

Questions and answers are arbitrary Unicode. `locale` is metadata only;
decision IDs, severities, status, prerequisites, and evidence remain canonical,
so the workflow is not tied to a model provider or language.

Sessions default to `~/.flyto-indexer/grill` with private directory/file
permissions. Set `FLYTO_INDEXER_GRILL_DIR` to select an explicit runtime state
directory, including isolated CI and test environments.

Example CLI sequence:

```bash
flyto-index task grill --grill-action start \
  --description "Add a safe robot adapter" \
  --decisions decisions.json

flyto-index task grill --grill-action answer \
  --grill-session-id grill_... \
  --decision-id failure_policy \
  --accept-recommendation \
  --request-id answer-failure-policy-v1

flyto-index task grill --grill-action freeze \
  --grill-session-id grill_...

flyto-index task plan \
  --description "Add a safe robot adapter" \
  --target src/adapter.py \
  --grill-session-id grill_...
```

Plans continue to include risk dimensions, constraints, affected locations,
tests, and co-change evidence. Gates reject missing evidence rather than
claiming a phase is complete.

Primary implementation: `src/tools/task_analysis.py`, `src/execution_guard.py`,
`src/tools/grill.py`, and `src/tools/smart.py`.
The [Decision Grill test protocol](GRILL_TESTING.md) maps the real
mixed-language fixture, CLI, MCP, persistence, concurrency, and tamper gates.

## Verification And Baselines

`verify` closes the scan, context, impact, security, documentation, policy,
runtime, packaging, and working-tree checks for one repository.
`verify-workspace` applies the same model across repositories.
`verify-baseline` records or compares accepted findings so CI can detect
regressions without hiding existing debt.

See [Verification](VERIFICATION.md) for release commands and report formats.

## Software Inventory

Dependency scanning reads supported package manifests and lockfiles. License
analysis identifies repository and dependency licenses. SBOM export produces a
CycloneDX 1.5 JSON document suitable for artifact and supply-chain workflows.

Primary implementation: `src/dependency_scanner.py`, `src/license_scanner.py`,
`src/sbom.py`, and `src/tools/dependencies.py`.

## Integration Surfaces

- CLI: 36 subcommands; see the [CLI reference](reference/cli.md).
- MCP: smart tools are listed to clients; granular definitions remain for
  compatibility and internal dispatch. See the [MCP reference](reference/mcp-tools.md).
- Python: import `IndexEngine` or the focused modules documented in the
  [Python API reference](reference/python-api.md).
- HTTP: the optional local bridge exposes seven operations documented in the
  [HTTP reference](reference/http-api.md).
- JSON export: `export` creates a portable scan bundle for an explicit caller.
