# Import-bound Python call evidence

Date: 2026-09-19
Owner: codex
Base: `6758b92bf9cf010a50effd7ab3aed3c3a2aa7dfb`
Status: source implementation; final CI and merge state are recorded in the PR.

The existing AST taint pass now uses local import bindings as caller evidence,
not a second dataflow engine. Alias/relative/re-exported calls feed its normal
parameter summaries. Keywords and literal list/tuple/dict unpacking bind to the
callee parameter; dynamic unpacking is not guessed. Summaries preserve original
terminal sink file/line/expression through relays; distinct terminal sinks do
not collapse merely because the caller and expression text are equal.

Explicitly different imported definitions reject name collisions. Duplicate
roots, rebinding, conditional imports, decorators, nested/receiver semantics and
cycles are not promoted into resolved bindings. Unknown same-name calls keep
the legacy candidate floor, with medium confidence and `name_only_callee` basis.
This is static candidate attribution, never a runtime exploitability verdict.

Caller assignments kill overwritten taint. Branches join alternatives rather
than letting the last branch erase the first. Category-aware sanitizer checks
keep HTML encoding from being treated as shell safety. A sanitizer name matches
an exact/dotted callable boundary, not an arbitrary substring such as `print`.

All phases share bounded source bytes/ASTs. Reads reject symlinks, check inode,
device, size and modification time, and enforce 1 MiB/file, 32 MiB/snapshot and
4,000 files. Caller checks, resolved-call discovery, import hops and propagation
rounds are bounded. Parse/read/cap failures become truncation evidence. These
limits do not claim total interpreter RSS, full scan coverage or production KVM
latency. General dynamic dispatch, return-value models and arbitrary frameworks
remain approximate; unsupported semantics are not described as complete.

## Regression proof

Before changes, 12 of the initial 13 real-index regressions failed, including
alias/keyword flows, false same-name attribution, stale snapshots and terminal
sink provenance. The fixed initial suite passes, as do the additional original
adversarial fixtures for sanitizer context, safe/unsafe branches, repeated
terminal sinks, recursion, re-export cycles, ambiguous source roots, shadows,
call budgets, dynamic unpacking, parse/byte caps and symlinks. No external
benchmark labels or case identifiers are used in production source.

The full local suite first passed 2,715 tests (3 skips, 8 existing CI exclusions)
before the final source-budget guards. Final counts and remote Python 3.11/3.12,
ruff/mypy, debt, source-reference and verification results belong in the PR.
Quality-debt baseline changes may lock in decreases only; do not mask increases.

Engine must pin the exact merged Indexer revision and refresh its packaged copy
with its existing sync script. This PR alone does not prove the deployed Engine
uses the new code. No Snyk execution or comparative superiority is claimed.
