# Product API Mock Fixture Exclusion

## Context

`verify_workspace --strict` on the generated `flyto-warroom` CE package flagged
hundreds of unmatched frontend API calls from `packages/flyto-code/src-next/@mock-utils`.
Those paths use `/api/mock/**` fixture endpoints and are not Flyto2 backend
product contracts.

## Change

- Product API contract closure is scoped to `/api/v1/**`.
- `/api/mock/**` definitions and dependency metadata are excluded before
  single-project API definition/call matching.
- Regression tests cover both directions:
  - mock API calls do not create unmatched-call failures
  - unmatched real `/api/v1/**` calls still fail when a product API contract is
    present but missing the called route

## Verification

- `python3.11 -m ruff check src/verify.py tests/test_verify.py`
- `python3.11 -m pytest tests/test_verify.py -q`

## Follow-Up

Any future non-`/api/v1` production API namespace must be added deliberately to
the product API detector with tests. Do not broaden the matcher back to all
`/api/**` paths, because that reintroduces CE mock-fixture false positives.
