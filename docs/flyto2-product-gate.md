# Flyto2 product gate

`flyto2-product-gate` validates whether the Flyto2 workspace is still shaped as
one product system instead of a loose set of repositories.

It is intentionally manifest-driven. The manifest names the five Flyto2 product
lines, maps every workspace repo to those lines, defines each repo status, and
sets health targets for core repos.

## Product lines

- Flyto2 Cloud / Apps / Automation
- Flyto2 Security
- Flyto2 Data
- Flyto2 Zero-person Company Agent
- Flyto2 Big Data / Intelligence

`flyto-core` is the shared execution kernel for these lines. Product repos may
depend on the kernel, but product lines must not collapse into ad hoc imports or
commercially ambiguous surfaces.

## Default manifests

- `config/flyto2/product-lines.json`
- `config/flyto2/health-baseline-2026-06-21.json`

The health baseline is a release-control artifact. A core repo below the target
grade is a P1 production blocker until the score is raised or the exception is
documented in the release handoff.

Complexity health is severity-weighted. The baseline records:

- high-complexity function density (`score >= 5`)
- cumulative complexity burden
- the top hotspot score

This keeps dense complexity and severe god functions visible without treating a
barely-over-threshold helper as equivalent to the worst production hotspot.

Non-core repos may be marked with `"exempt": true` in the health baseline when
the repo has no indexed runtime symbols or the current analyzer does not support
its implementation language. Core repos cannot be exempt; a core exemption is a
release blocker.

## CLI

```bash
flyto-index flyto2-product-gate /Users/chester/flytohub \
  --health-report config/flyto2/health-baseline-2026-06-21.json
```

To scaffold missing memory files without overwriting existing repo notes:

```bash
flyto-index flyto2-memory-bootstrap /Users/chester/flytohub
flyto-index flyto2-memory-bootstrap /Users/chester/flytohub --apply
```

Useful development modes:

```bash
python -m src.cli flyto2-product-gate /Users/chester/flytohub --json
python -m src.cli flyto2-product-gate /Users/chester/flytohub --skip-health
python -m src.cli flyto2-product-gate /Users/chester/flytohub --relaxed-memory
```

## Blocking checks

- Any discovered git repo missing from the manifest.
- Any manifest repo that is expected but absent from the workspace.
- Any repo not mapped to at least one product line.
- Missing project memory files for active, experimental, internal, or tooling
  repos when memory is required.
- Missing workflow docs or handoff registry for memory-required repos.
- Missing or below-target health records for core repos.

Deprecated repos are still reported, but memory gaps are warnings instead of
blockers.

## Release usage

Run the gate before release readiness review and include the JSON output in the
release packet. A failing gate means the release verdict cannot be higher than
`BLOCKED_FOR_PRODUCTION`.
