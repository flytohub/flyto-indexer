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

The `quality_health` check is the same `health-snapshot.v2` used by `audit` and
project profiles. Project policy may set `min_health_score`,
`min_documentation_score`, `max_complex_functions`,
`max_complexity_burden`, and `max_dead_code`.

The grade prioritizes measured static engineering risk. It does not prove
business behavior, browser behavior, concurrency safety, deployment readiness,
or complete security. Each dimension includes its score semantics and raw
measurement so a budget ceiling cannot be mistaken for zero remaining issues.

## External Runtime Proof

`task(validate)` can require proof produced by the systems that actually own a
runtime check:

```text
task(
  action="validate",
  task_contract=contract,
  required_proof_kinds=["browser", "race", "container_build"],
  proof_receipts=receipts
)
```

A receipt names the proof kind, producer, subject, project, result, issuance
time, and SHA-256 evidence digest. Content addressing detects accidental
changes. When a proof kind is required, the receipt must also carry a valid
HMAC-SHA256 attestation from a key ID trusted through the local
`FLYTO_INDEXER_PROOF_KEYS_JSON` environment setting. Missing, stale, failed,
tampered, cross-project, or untrusted receipts fail closed.

This is federation, not runtime emulation: flyto-core can own browser proof,
CI can own race and container proof, and a security runner can own penetration
proof. Indexer only verifies and closes the declared evidence contract.

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
individual findings. Scanner findings also expose the full fingerprint,
confidence basis, bounded trace, and active/suppressed provenance. Identity
uses rule, repository-relative path, and a bounded semantic anchor, but not
line numbers or raw secrets. Regression mode therefore detects a new finding
even when its parent check was already warning. It also compares canonical
health score, complex-function count, complexity burden, dead-code count, and
documentation score even when the check status remains `pass`. A v1 baseline
without IDs remains readable.

This intentionally follows a “no new debt” gate: absolute policy floors catch
catastrophic drift, while the reviewed baseline blocks only newly-worse
metrics. Existing debt stays visible without making the scanner unusable.

SARIF output carries the compact ID and full fingerprint in
`partialFingerprints`, with confidence, trace, suppression, file, and line
evidence where available. This lets code-scanning consumers correlate a
finding across line-only moves.

## Offline Scanner Evaluation

The repository includes a committed positive/negative corpus for Python,
JavaScript, TypeScript, and Go. It runs the real index and taint analyzer
without network access and fails on missed findings, extra findings, broken
metamorphic relations, differential-category drift, missing cross-file path
proof, scan errors, or p95/max latency beyond the configured bounds:

```bash
python benchmarks/evaluate.py --check --json
```

The report includes per-language precision and recall, negative-case
false-positive rate, p50/p95/max latency and peak memory, plus a deterministic
evidence fingerprint that excludes timing noise. This fast gate
complements—not replaces—the larger optional external corpus described in
[benchmarks/README.md](../benchmarks/README.md).

Task continuity and efficiency evidence has a separate fixed contract:

```bash
python benchmarks/evaluate_task_efficiency.py --check --json
```

It always runs exactly 100 named scenarios and requires at least 90% to pass.
The cases cover provider normalization, dependency-free estimation, read-only
continuity, bounded retention, privacy, idempotency, honest paired comparisons,
four portable report formats, and CLI behavior. The committed receipt keeps
every scenario result and a deterministic fingerprint; a failing case is fixed
and the complete 100-scenario suite is rerun rather than deleting or replacing
the case.

On Python 3.12, CI also emits coverage.py per-test contexts and JUnit XML,
downloads both into the verify job, and proves that at least one covered line
maps back to an executed test. These are dev/CI extras; they do not change the
installed runtime dependency boundary.

## Indexer Release Gate

Run these commands from this repository before release:

```bash
bash scripts/lint-project-memory.sh
python3 scripts/sync-version.py --check
python3 scripts/generate-reference.py --check
python3 scripts/check_language_evidence.py --check
python3 scripts/check_quality_debt.py
ruff check .
mypy src
python -m pytest tests -v
python benchmarks/evaluate.py --check --json
python benchmarks/evaluate_task_efficiency.py --check --json
python scripts/reproduce_impact_case.py --check-snapshot
python -m build
python -m src.cli verify . --full-scan --strict --json
```

Before pushing a release tag, also run:

```bash
python scripts/check_release_tag.py --tag vX.Y.Z
```

CI repeats the reference, language evidence, quality-debt ratchet, lint, type,
test, offline scanner evaluation, self-verify, and wheel checks. The wheel
smoke test installs into an isolated environment and proves that the shipped
rule corpus is usable. A tag publishes only after the same release gate passes;
the GitHub Release is created only after PyPI Trusted Publishing succeeds.

The real-repository proof is intentionally a separate scheduled and pull
request workflow because it clones a pinned external repository. Its committed
receipt is deterministic; normal indexing and installed-package use remain
offline and network-free.

## Reading A Result

- A pass proves only the checks and filesystem snapshot named in the report.
- A warning is actionable evidence; strict mode decides whether it blocks CI.
- A scanner error means the affected dimension was not verified and must not
  be reported as a clean scan.
- Hosted workflow, registry, and publication status require provider-side
  evidence in addition to local success.
