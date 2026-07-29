# Decision Grill Closure Report — 2026-07-29

This report records the local evidence for the Decision Grill, C/C++ scanner,
and validation-loop changes. The test protocol and fixture definitions are in
[GRILL_TESTING.md](GRILL_TESTING.md).

## Snapshot

| Item | Value |
|---|---|
| Repository | `flyto-indexer` |
| Base Git HEAD | `b7f424b3843baaf768931891a3a7fa42a8dc768e` |
| Working state | Uncommitted implementation under test |
| Python | 3.11 |
| Full index | 235 files scanned, 3,881 source symbols, 24,531 source dependencies, 0 scanner errors |
| Strict verify fingerprint | `aca9573278ce681db8962c1bcdfd325ac6a183d8a0dc0482a34199ed705edf2d` |

## Results

| Gate | Result | Evidence |
|---|---|---|
| Full repository pytest | Pass | 1,742 passed, 1 skipped in 742.22 seconds |
| Formal `task(action="validate")` closure selection | Pass | 171 passed, 1 skipped in 5.19 seconds |
| Ruff | Pass | 0 errors, 0 warnings |
| Mypy | Pass | 133 source files, 0 issues |
| Package build | Pass | sdist and wheel built successfully |
| Project-memory lint | Pass | no project-memory violations |
| Generated reference drift | Pass | generated documentation current |
| Version synchronization | Pass | package metadata synchronized at 2.15.0 |
| Unstaged impact | Pass | 62 changed indexed symbols; 0 high/moderate risk |
| Strict full-scan verify | Pass | 18 pass, 0 warn, 0 fail |
| Secret scan within verify | Pass | 241 files, 0 findings |
| Taint scan within verify | Pass | 440 sources, 814 sinks, 0 unsanitized or high-risk flows |
| Documentation gate | Pass | overall 100, source-reference coverage 100% |
| MCP registry/runtime | Pass | 20 smart tools, no schema/dispatch mismatch |

The single reported skip is the existing compatibility-path check at
`tests/test_architecture.py:456`, which skips when the legacy
`tool_registry.py` file is absent. It is unrelated to the changed feature.

## Real Closure Assertions

The passing black-box tests proved all of the following:

1. `IndexEngine` performs a fresh scan of real Python, TypeScript, and C files.
2. `emergency_stop` resolves to `safety.c`; `RobotAdapter` resolves to
   `adapter.ts`.
3. An unrelated fuzzy result cannot satisfy a critical repository fact.
4. Traditional Chinese, English, and German decision text round-trips while
   canonical IDs remain language-neutral.
5. An incomplete freeze returns `pass: false`; the CLI exits with status 2.
6. Three dependency-ordered answers create a freeze-ready session.
7. Freeze creates an immutable SHA-256 fingerprinted decision contract.
8. A plan attaches that contract without adding another public MCP tool.
9. The gate accepts the unchanged contract and rejects a modified answer.
10. MCP stdio JSON-RPC and CLI subprocesses both execute the real flow.
11. Concurrent threads and independent POSIX processes preserve all answers.
12. Corrupt state, traversal IDs, oversized input, graph cycles, conflicting
    options, resolver errors, hostile text, and fingerprint mutations fail
    closed.

## Strict Verify Detail

The final strict gate passed all 18 dimensions:

- runtime dependency boundary;
- full scan;
- index integrity;
- single-project island analysis;
- context lookup;
- impact lookup;
- secrets;
- taint;
- documentation;
- repository rules and layers;
- external runtime boundary;
- package integrity;
- CI closed loop;
- change hygiene;
- MCP registry;
- MCP runtime smoke;
- agent guidance hygiene;
- generated-index ignore policy.

This evidence is local to the named filesystem snapshot. It does not claim a
remote CI run, published package, commit, or push.
