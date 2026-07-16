# TypeScript Request Method Extraction

Date: 2026-07-16

## Context

Flyto2 frontend engine clients commonly call backend actions through
`request<T>('POST', \`/api/v1/.../${id}/action\`)`.

The TypeScript scanner's broad `/api/vN/**` string-literal fallback could
record those template literals as GET before the method-aware `request`
pattern ran. That made project profiles and API drift evidence overstate GET
calls for mutating endpoints such as AI governance approvals and container
finding lifecycle actions.

## Change

- `src/scanner/typescript.py` now marks API call patterns as fallback or
  method-aware.
- Method-aware patterns run before broad string fallbacks.
- Fallback patterns skip URLs already discovered by real call patterns.
- Deduplication is method+URL based so distinct methods on the same path can
  still be represented.
- Regression coverage was added for multiline
  `request<T>('POST', \`/api/v1/.../${id}/approve\`)`.

## Verification

```text
python3.11 -m ruff check src/scanner/typescript.py tests/test_cross_language_api.py
python3.11 -m pytest tests/test_cross_language_api.py -q
python3.11 -m pytest tests/test_scanner_typescript.py -q
python3.11 -m src.cli verify . --full-scan --strict --json
```

Manual proof:

```text
flyto-code surfaces.ts:
POST /api/v1/code/ai-governance/use-cases/*/request-approval
POST /api/v1/code/ai-governance/use-cases/*/approve
POST /api/v1/code/ai-governance/use-cases/*/reject

flyto-code containerScan.ts:
PATCH /api/v1/code/orgs/*/container/connections/*
POST /api/v1/code/orgs/*/container-findings/*/verify
POST /api/v1/code/orgs/*/container-findings/*/reopen
POST /api/v1/code/orgs/*/container-findings/*/false-positive
```
