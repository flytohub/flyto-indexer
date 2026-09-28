# Accuracy gate expansion

Date: 2026-09-28
Owner: ChatGPT
Base: `fc91228919033a7befffb9bc188e44e4b25a238b`
Status: final local gates passed; landing on `main` in this change.

## What changed

The offline security benchmark now has two evidence layers:

- 46 human-readable canonical ground-truth projects;
- 184 deterministic semantic-preserving/adversarial mutations;
- 230 evaluated source trees total, with 230 unique source fingerprints.

Mutation dimensions are comment/source-sink decoys, identifier renames,
receiver aliases, and layout/safe-neighbour noise. Expected labels continue to
come only from canonical human-authored cases; mutation generation never reads
analyzer findings to decide expected results.

JavaScript gained negative controls and is now eligible for `gated` evidence.
Python, JavaScript, TypeScript, and Go all have at least four positive and four
negative canonical controls.

## Precision fix found by the new negatives

The expansion exposed a real false positive in non-Python regex fallback:
parameterized JavaScript/TypeScript and Go SQL calls such as
`query("... $1", user_value)` were reported as injection because user data
appeared anywhere inside the call.

The fallback now suppresses only a narrow safe shape: a complete static quoted
first SQL argument containing a parameter placeholder followed by separately
bound arguments. String concatenation/interpolation in the first argument still
reports SQL injection.

## Gate contract

Default committed-corpus checks require:

- at least 40 canonical cases;
- at least 200 total cases;
- at least 150 mutation cases;
- at least 40 cases in every mutation dimension;
- every evaluated source fingerprint unique;
- global precision=1.0, recall=1.0, negative-case FPR=0.0;
- per-language precision >=0.98, recall >=0.95, FPR <=0.05;
- at least four positive and four negative cases per language;
- existing category, metamorphic, differential, cross-file path, scan-error,
  latency and evidence-fingerprint gates remain active.

`--canonical-only --check` keeps the canonical proof usable without requiring
mutation counts.

## Quality-debt baseline

The repository's quality-debt baseline still described the tree before the
`fc91228` authority-decomposition landing. Using the repo-pinned Ruff 0.16.7 and
mypy 2.3.0, the current accuracy work was compared directly with `fc91228` and
produced zero debt-count deltas after fixing one import-order issue in
`taint_regex.py`. The baseline was refreshed to that already-shipped main state.
The five pre-existing mixin `has-type` errors were then fixed with explicit state
annotations; full mypy reports zero issues across 188 source files and the exact
baseline was tightened to lock the improvement. No headroom was granted to the
accuracy change.

## Accuracy boundary

This is a stronger deterministic regression proof, not a universal claim that
all real repositories have 100% precision/recall. Dynamic dispatch, framework
aliases, runtime behavior, and unsupported language semantics retain the
published limitations in `docs/LANGUAGE_EVIDENCE.md`.
## Final verification

- Accuracy gate: 230/230 passed; 46 canonical + 184 mutation; 230 unique source fingerprints; TP=140, FP=0, FN=0; precision=1.0, recall=1.0, negative-case FPR=0.0.
- Canonical language controls: Go 5+/4-, JavaScript 5+/5-, Python 13+/6-, TypeScript 4+/4-.
- Fast suite: 2671 passed, 3 skipped, 61 deselected.
- Full pytest: 2732 passed, 3 skipped.
- Ruff: PASS. Full mypy: 188 source files, 0 issues.
- Quality-debt ratchet: PASS (Ruff=1403, mypy=888).
- Generated references, language evidence, project-memory lint, version metadata, and `git diff --check`: PASS.
- Package build: wheel/sdist PASS. Self-verify strict/full-scan: 22 pass, 0 warn, 0 fail.
- Public surface unchanged: 20 MCP tools, 44 CLI commands.

