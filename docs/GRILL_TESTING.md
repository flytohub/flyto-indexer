# Decision Grill Test Protocol

This document defines the reproducible closure test for the evidence-backed
Decision Grill in `task`. The acceptance boundary covers the engine, persisted
state, real code indexing, CLI, MCP JSON-RPC, plan attachment, and the
implementation gate. A passing unit test alone is not sufficient.

The latest dated local evidence is recorded in the
[2026-07-29 closure report](GRILL_TEST_REPORT_2026-07-29.md).

## Closed Loop

```mermaid
flowchart LR
    A[Python / TypeScript / C fixture] --> B[IndexEngine full scan]
    B --> C[Repository fact resolution]
    C --> D[Reachable human decisions]
    D --> E[Frozen v2 contract + evidence snapshot + ADR]
    E --> F[task plan]
    F --> G[task gate]
    G --> H[Allowed implementation phase]
    H --> I[task validate: ruff + pytest]
    I --> J[Evidence freshness + decision-to-diff gate]
    J --> M[Local privacy-preserving outcome learning]
    J --> N[Strict full-scan verify]
    E --> O[Changed evidence]
    O --> P[Selective decision reopen]
    E --> K[Tampered copy]
    K --> L[Gate rejects]
```

The fixture under `tests/fixtures/grill_robotics/` intentionally contains:

| File | Test capability |
|---|---|
| `controller.py` | Python capability registry and route compiler |
| `adapter.ts` | TypeScript robot-adapter interface and implementation |
| `safety.c` | C emergency stop and motion-safety functions |
| `decisions.json` | Repository facts plus Traditional Chinese, English, and German decisions |

The fixture is product-shaped test data, not a mock search response. Integration
tests build a fresh index from those files and use the production search
resolver.

## Acceptance Matrix

| Boundary | Required proof | Tests |
|---|---|---|
| Decision graph | prerequisite frontier, missing dependency, cycle rejection, batch bound | `test_grill.py` |
| Decision intelligence | confidence validation, decision cost, reversibility, VOI ordering, bounded adversarial review, acceptance schema | `test_grill.py` |
| Fact precision | exact identifier match by default; unrelated fuzzy hits remain blocked; explicit `all_terms` or `evidence_present` opt-in | `test_grill.py` |
| Human answers | only reachable human nodes, recommendations, option validation, contradictions, idempotent request IDs | `test_grill.py` |
| Persistence | atomic replace, reload, corrupt-state rejection, path traversal rejection, private permissions | `test_grill.py`, `test_grill_deep.py` |
| Concurrency | no lost updates across threads or separate POSIX processes | `test_grill_deep.py` |
| Contract integrity | incomplete freeze rejection, immutable frozen state, SHA-256 tamper rejection | `test_grill.py`, `test_grill_deep.py` |
| Evidence freshness | content hashes, project scoping, only impacted decisions reopen, v1 migration | `test_grill_evidence.py` |
| Diff conformance | expected/forbidden paths and symbols, safe proof matching, unsupported command rejection | `test_grill_conformance.py` |
| Outcome learning | private compact records, idempotency, Bayesian confidence prior, explicit-confidence precedence | `test_grill_outcomes.py` |
| Input safety | bounded arrays/text, arbitrary Unicode, hostile shell/template text treated as inert data | `test_grill_deep.py` |
| Real index | Python, TypeScript, and C symbols resolve through `IndexEngine` and production search | `test_grill_real_data.py` |
| CLI | real scan → start → blocked freeze → answers → freeze → plan → gate → contract-aware validate → tamper rejection | `test_grill_cli_e2e.py` |
| MCP | real stdio JSON-RPC start → fact resolution → answer → freeze → immutable state | `test_mcp_integration.py` |
| Compatibility | v1 contracts/sessions, unchanged 20-tool public count, extracted CLI/dispatch adapters, existing task behavior | `test_tool_registry.py`, `test_smart.py`, `test_task_adapters.py` |
| Large merged index | project-scoped test mapping, dead-code references, roots, and dependency work remain bounded and collision-free | `test_test_mapper.py`, `test_index_store_scope.py`, `test_maintenance_dead_code.py`, `test_task_analysis.py` |

## Evidence Selection Rules

Repository facts fail closed. Their default `resolution_policy` is
`exact_match`, which requires a normalized exact query match in a result name,
symbol ID, path, or summary. This prevents a high-scoring fuzzy result for an
unrelated symbol from silently satisfying a critical fact.

Use `all_terms` when an evidence query is a controlled phrase and every term
must be present. Use `evidence_present` only when the caller supplies a trusted
resolver whose own acceptance threshold is authoritative. At most five compact
evidence records are persisted per query; source bodies and unbounded provider
fields are excluded.

## Reproduce

Run the focused closure suite:

```bash
python -m pytest -q \
  tests/test_grill.py \
  tests/test_grill_deep.py \
  tests/test_grill_evidence.py \
  tests/test_grill_conformance.py \
  tests/test_grill_outcomes.py \
  tests/test_grill_real_data.py \
  tests/test_grill_cli_e2e.py \
  tests/test_mcp_integration.py
```

Run the complete repository release evidence:

```bash
ruff check src tests scripts
mypy src
python -m pytest tests -v
python scripts/generate-reference.py --check
python scripts/sync-version.py --check
python -m build
python -m src.cli verify . --full-scan --strict --json
```

`task(action="validate")` uses a 900-second pytest timeout because the complete
suite includes slow, stress, race, and subprocess coverage. Set
`FLYTO_INDEXER_PYTEST_TIMEOUT` to a value from 30 through 3600 seconds when a
repository needs a different bounded CI budget.

The generated session directory can be isolated for inspection:

```bash
export FLYTO_INDEXER_GRILL_DIR=/tmp/flyto-indexer-grill
export FLYTO_INDEX_DIR=/path/to/project/.flyto-index
python -m src.cli task grill \
  --grill-action start \
  --description "Compose a safe robot route" \
  --project robot-project \
  --decisions tests/fixtures/grill_robotics/decisions.json
```

Each session is a readable JSON document with canonical IDs, answers, compact
evidence, history, readiness, and the frozen contract. Treat it as local
workflow state: it can contain user decisions and repository paths, so do not
commit it or place it in a public shared directory.

## Failure Expectations

- Missing or weak repository evidence leaves the fact open and exposes a
  repository remediation action; it never becomes a human question.
- An incomplete `freeze` returns `pass: false`; the CLI exits with status 2.
- A changed answer, evidence item, readiness field, project, or decision list
  invalidates the contract fingerprint.
- A frozen session cannot be answered or discarded.
- Validation rejects an explicit unknown project instead of silently running
  against the only indexed project.
- Project-scoped planning never walks ignored dependency/build directories or
  borrows same-path tests and references from another indexed project.
- Resolver failures are bounded in the response and cannot be mistaken for
  evidence.
- Unsupported schemas, invalid IDs, dependency cycles, duplicate option IDs,
  oversized values, and traversal-shaped session IDs are rejected.
