# Accuracy V2 — impact precision and bounded absence

Date: 2026-09-28
Owner: ChatGPT
Base: `bed83229cfdcc8062742deb4ff96fb0aeed8a47e`
Status: final local repository/release gates passed; landing on `main` in this change.

## Real-repository impact evidence

The pinned `fastapi/full-stack-fastapi-template` 0.10.0 case now owns the complete
expected depth-two affected-function set (7 functions) rather than only four
required transitive handlers. The live pinned reproduction produced:

- 7 true-positive affected functions;
- 0 false-positive affected functions;
- 0 false negatives;
- impact precision = 1.0;
- impact recall = 1.0;
- 0 scan errors.

The receipt fails on either an extra or a missing function edge. This remains a
pinned case-specific proof, not a universal impact-accuracy claim.

## Evidence honesty

Zero indexed callers no longer means `safe`. The main impact response carries
`evidence_scope=current_static_index` and `absence_is_safety_proof=false`; the
edit-impact risk for zero indexed call sites is `low`. Related API, auditor,
quality/dead-code, and cross-project messages now state the static bound instead
of claiming safe modification/removal.

## Regression evidence before final closure

- Impact/public proof/task/API/MCP targeted suite: 192 passed.
- Confidence/truncation/security/public-proof focused suite: 103 passed.
- Security accuracy gate: 230/230 passed, TP=140, FP=0, FN=0, precision=1.0,
  recall=1.0, negative-case FPR=0.0.
- Pinned FastAPI snapshot reproduction: PASS with precision=1.0/recall=1.0.

## Product boundary

No new MCP tool, runtime dependency, coding authority, commit/deploy authority,
or hosted dependency is introduced. Indexer still provides bounded evidence; it
does not turn absence of evidence into evidence of absence.
## Final closure

- Pinned FastAPI real-repository impact proof: 7 TP / 0 FP / 0 FN, precision=1.0, recall=1.0, snapshot reproduction PASS.
- Security accuracy gate: 230/230 passed, TP=140, FP=0, FN=0, precision=1.0, recall=1.0, FPR=0.0.
- Confidence/truncation/public-proof focused evidence: 103 passed.
- Changed-surface targeted regression: 294 passed.
- Fast suite: 2673 passed, 3 skipped, 61 deselected.
- Full pytest: 2734 passed, 3 skipped.
- Ruff, full mypy (188 source files), quality-debt ratchet, generated reference, language evidence, project-memory lint, version metadata, and `git diff --check`: PASS.
- Package build: wheel/sdist PASS. Strict full self-verify: 22 pass / 0 warn / 0 fail.
- Public surface remains 20 MCP tools and 44 CLI commands.

