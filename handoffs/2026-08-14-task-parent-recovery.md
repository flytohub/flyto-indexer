# 2026-08-14 Proof-Bound Task-Parent Recovery

Owner: Codex

## Scope

Add one generic, task-plan-only producer contract for a content-bound
generation-2 successor. The public Indexer evidence contains repository
authority only; private hosts retain job, session, mission, retry, and
compensation identity.

## Changes

- Added strict canonical JSON and domain-separated digests for the complete raw
  parent, normalized parent authority, executable successor, legacy resolution
  record, and final evidence.
- Added producer-derived normalization that preserves ordinary and amended
  parents exactly, and drops a historical non-exact target only from a root
  parent with one-to-one recorded resolution, no current exact identity, and no
  file, directory, live symlink, or broken symlink at the literal path. Its
  symbol and path coordinates must be unique across the complete root or
  compound source matrix.
- Added deterministic ordered authority union across normalized parent paths,
  audited prior implementation paths, and explicit requested targets. Audited
  prior scope and the executable ordered union are each capped at 32 paths.
- Bound every nonempty executable resolution path and symbol ID to the exact
  canonical plan input and project, including compound successors, so a
  rehashed foreign owner cannot widen host execution authority.
- Added `.7z` to the closed typed-path suffix set, retaining absent archive
  outputs while arbitrary dotted capability identifiers remain non-paths.
- Added exact CLI and MCP schema/adapter support with bounded lists, closed
  keys, plan-only routing, and fail-closed behavior for malformed recovery.
- Isolated normalizer, digest/evidence validation, and recovery attack-matrix
  tests so the ordinary amendment adapter does not own recovery policy.

## Verification

Verification passed locally on `4c3532c8d4f957a863197f7c94626a1521f2cd08`:

- Recovery normalizer/evidence attack matrix: 123 passed, including real Code
  four-file authority, Engine six-parent/twenty-one-audited authority,
  noncompound and compound successor attribution, and `PYTHONHASHSEED=0/3`
  identity equality and exact raw-symbol history retention.
- Focused producer, CLI/smart/adapters/registry, ordinary amendment, task
  context, and task resolver closure: 521 passed.
- CI-like full host suite with an isolated `FLYTO_INDEX_DIR` and
  `FLYTO_INDEXER_TASK_DB` explicitly unset: 2,362 passed, 1 skipped in 29.57s,
  peak RSS 140,902,400 bytes. A shared external task DB was separately proved
  to make the fixed benchmark stateful on both clean base and this candidate;
  no benchmark evidence or assertion was changed.
- Repository Ruff passed; mypy reported no issues in 156 source files. The
  pinned Ruff 0.16.2 quality ratchet stayed exact at Ruff 1,140 and mypy 729.
- Generated references, language evidence, version parity, project-memory lint,
  and `git diff --check` passed.
- Isolated strict full-scan self-verify passed 20/20 with 316 files, health
  91/A, documentation score 25/25, and no warnings or
  failures.

No commit or push is performed in this worktree. The final diff requires an
independent exact-revision audit before landing.
