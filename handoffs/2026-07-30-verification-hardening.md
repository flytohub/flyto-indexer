# 2026-07-30 Verification Hardening Handoff

## Scope

Strengthened scanner accuracy evidence, finding correlation, and MCP request
liveness without adding a public tool, runtime dependency, network call, or
mandatory architecture policy.

## Changes

- Added a committed offline Python, JavaScript, and Go evaluation corpus that
  gates exact positive/negative results, cross-file path proof, scan errors,
  precision, recall, false-positive rate, latency, memory, and deterministic
  evidence fingerprints.
- Added stable local finding IDs to taint results, verify schema v2 baselines,
  and SARIF partial fingerprints. Legacy baselines remain compatible.
- Added bounded stdio tool deadlines and MCP cancellation handling. Timeout or
  cancellation ends only the active request and leaves the process reusable.
- Removed a duplicate Go two-line fallback finding and corrected serialized
  taint sink line evidence.
- Recorded the external design comparisons and anti-bloat decisions in
  `docs/DESIGN_REFERENCES.md`.

## Verification

- `task(validate)`: Ruff passed; final full suite `1851 passed, 1 skipped`.
- mypy: no issues in 144 source files.
- Offline corpus: 5/5 cases, precision 1.0, recall 1.0, false-positive rate
  0.0, deterministic fingerprint `b1411e646bc8f7f6`.
- Runtime liveness: hard timeout and cancellation returned structured errors;
  the same process answered the following ping; idle CPU time was unchanged
  across a two-second sample.
- sdist/wheel build and isolated installed-wheel import, dependency, policy,
  finding-identity, and MCP ping smokes passed.
- Generated references, version parity, and project-memory lint passed.
- Strict self-verify passed 18/18 with 269 files, 4,214 symbols, zero warnings,
  and zero unsanitized taint findings.

## Follow-Up

Keep the committed corpus small enough for every CI run. Larger OWASP, Semgrep,
and real-repository comparisons remain explicit release research and must not
become a network dependency of normal scans.
