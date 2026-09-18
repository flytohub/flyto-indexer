# Cross-file evidence correctness

Owner: codex
Baseline: 6758b92bf9cf010a50effd7ab3aed3c3a2aa7dfb
Branch: codex/cross-file-evidence

## Reproduced defects

A two-file indexed Python request -> wrapper -> os.system flow was detected,
but serialized its source at the wrapper invocation and its sink in the caller
file. Multi-hop traces lost terminal provenance. Same-named module methods were
attributed to the wrong import; named import aliases disappeared from the shared
scanner metadata. Reusing the same analyzer lost cross-function findings because
visited state survived. A two-finding budget emitted 23 regex findings from one
file without reporting truncation.

The fix extends the existing parameter-to-sink summary with immutable terminal
provenance. Both dependency and reverse-index paths use the existing walker.
Canonical import alias metadata feeds the shared dependency resolver rather than
a second taint-only resolver. Functions retain their qualified method identity.
Constant overwrites clear stale taint and keyword arguments bind existing named
parameters. Missing unique source coordinates stay zero/unknown, not a guessed
line. The existing serialized confidence object labels these as bounded static
candidates; no runtime/type-completeness assertion is added.

## Validation

New regressions use the real IndexEngine and TaintAnalyzer. Python fixture syntax
is checked so an invalid fixture cannot pass a negative test by being skipped.
Positive and negative cases cover terminal coordinates, multiple sinks, three
files, namesakes, constant/sanitized overwrites, aliases/keywords, repeat runs,
ambiguous source coordinates and the per-file regex finding budget.

Run all existing taint/resolver tests, the full repository CI test command,
source-derived reference generation, project-memory lint, type/lint/debt gates
and the unchanged offline benchmarks. Consult the PR for final exact-head CI;
local tests do not stand in for Python 3.11/3.12 remote acceptance.

## Limits

Import binding is bounded static evidence, not complete type inference. Dynamic
imports, dispatch, callback/closure semantics, unsupported keyword-only or
star-argument bindings and context-sensitive sanitization need separate measured
work. Cross-language and runtime exploitability are not established here.
Actual consumer deployments must pin this merged commit and regenerate their
embedded package; a changed local CLI alone does not update any deployed image.
