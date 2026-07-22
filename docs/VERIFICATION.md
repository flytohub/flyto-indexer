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
and relevant working-tree conditions. `--strict` promotes warnings to failure.

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

## Indexer Release Gate

Run these commands from this repository before release:

```bash
bash scripts/lint-project-memory.sh
python3 scripts/sync-version.py --check
python3 scripts/generate-reference.py --check
ruff check src tests scripts
mypy src
pytest tests -v
python -m build
python -m src.cli verify . --full-scan --strict --json
```

CI repeats the reference, lint, type, test, self-verify, and wheel checks. The
wheel smoke test installs into an isolated environment and proves that the
shipped rule corpus is usable.

## Reading A Result

- A pass proves only the checks and filesystem snapshot named in the report.
- A warning is actionable evidence; strict mode decides whether it blocks CI.
- A scanner error means the affected dimension was not verified and must not
  be reported as a clean scan.
- Hosted workflow, registry, and publication status require provider-side
  evidence in addition to local success.
