# Verification

Verification is evidence that code, graph data, policy, documentation, and
package behavior agree. It is not a substitute for the target repository's own
tests.

## Repository Gate

From the target repository:

```bash
flyto-index verify . --full-scan --strict --json
```

The gate rebuilds the index and checks index integrity, context lookup, impact
resolution, secret and taint analysis, documentation coverage, agent guidance,
repository rules and layers, package/runtime metadata, generated-index hygiene,
canonical code health, and relevant working-tree conditions. `--strict`
promotes warnings to failure.

The `quality_health` check is the same `health-snapshot.v1` used by `audit` and
project profiles. Project policy may set `min_health_score`,
`min_documentation_score`, `max_complex_functions`,
`max_complexity_burden`, and `max_dead_code`.

## Workspace Gate

Use an explicit workspace path and report location:

```bash
flyto-index verify-workspace /path/to/workspace --strict --json
```

Workspace verification aggregates project results while preserving each
repository's evidence. It does not commit, merge, push, or publish anything.

## Baselines

Baselines are explicit accepted state, not automatic suppression. Create or
update one only after reviewing the findings, commit it with the reason, and
use comparison mode in CI to reject new regressions. See
`flyto-index verify-baseline --help` for the exact current arguments.

Verify result schema v2 assigns stable, privacy-preserving IDs to checks and
individual secret/taint findings. IDs use rule, repository-relative path, and a
bounded semantic anchor, but not line numbers or source excerpts. Regression
mode therefore detects a new finding even when its parent check was already
warning. It also compares canonical health score, complex-function count,
complexity burden, dead-code count, and documentation score even when the check
status remains `pass`. A v1 baseline without IDs remains readable.

This intentionally follows a “no new debt” gate: absolute policy floors catch
catastrophic drift, while the reviewed baseline blocks only newly-worse
metrics. Existing debt stays visible without making the scanner unusable.

SARIF output carries the same ID in `partialFingerprints.flytoFindingId/v1` and
`properties.findingId`, with file and line evidence when a sampled finding has
it. This lets code-scanning consumers correlate a finding across line-only
moves.

## Offline Scanner Evaluation

The repository includes a small committed positive/negative corpus for Python,
JavaScript, and Go. It runs the real index and taint analyzer without network
access and fails on missed findings, extra findings, missing cross-file path
proof, scan errors, or latency beyond the configured bound:

```bash
python benchmarks/evaluate.py --check --json
```

The report includes precision, recall, negative-case false-positive rate,
per-case latency and peak memory, plus a deterministic evidence fingerprint
that excludes timing noise. This fast gate complements—not replaces—the larger
optional external corpus described in [benchmarks/README.md](../benchmarks/README.md).

## Indexer Release Gate

Run these commands from this repository before release:

```bash
bash scripts/lint-project-memory.sh
python3 scripts/sync-version.py --check
python3 scripts/generate-reference.py --check
ruff check src tests scripts
mypy src
pytest tests -v
python benchmarks/evaluate.py --check --json
python -m build
python -m src.cli verify . --full-scan --strict --json
```

CI repeats the reference, lint, type, test, offline scanner evaluation,
self-verify, and wheel checks. The wheel smoke test installs into an isolated
environment and proves that the shipped rule corpus is usable.

## Reading A Result

- A pass proves only the checks and filesystem snapshot named in the report.
- A warning is actionable evidence; strict mode decides whether it blocks CI.
- A scanner error means the affected dimension was not verified and must not
  be reported as a clean scan.
- Hosted workflow, registry, and publication status require provider-side
  evidence in addition to local success.
