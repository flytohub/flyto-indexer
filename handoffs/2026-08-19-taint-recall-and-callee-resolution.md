# Handoff: Taint recall and type-aware callee resolution

- Date: 2026-08-19
- Owner: claude
- Branch: main (local commits; not pushed)
- Status: implemented, local gates passed
- Follows: `2026-08-19-research-priority.md`

## Why this exists

Building the research-priority ranking exposed that the taint engine reported
**zero source-to-sink flows on every real project in this workspace** —
flyto-cloud (832 sources / 9,629 sinks), flyto-core, flyto-ai — while still
reporting large source and sink counts. The ranking sitting on top of it was
therefore ranking nothing. Four separate causes, found in this order.

## What was wrong, and what each fix changed

**1. The function cap was project-wide, not per-file.** `MAX_FUNCTIONS = 1000`
counted across the whole scan and `return`ed out of `_scan_python_files`
entirely, in alphabetical file order. flyto-core analyzed 1,000 of its 4,778
functions — 21% — and reported nothing about the rest. Now per-file, with
`MAX_TOTAL_FUNCTIONS = 20000` as a project budget, and every cap hit recorded
in `DataFlowResult.truncation`. flyto-core now analyzes 4,778 functions,
flyto-cloud 8,933, neither truncated.

**2. Hidden directories were scanned.** `.claude/worktrees/` copies burned the
budget on duplicates of the same source and produced duplicate leads. Skipped
now (hidden *directories* only — a dotfile is still scanned).

**3. Framework-injected parameters were discarded.** A parameter was always
marked `param:<name>`, meaning "tainted only if some caller passes tainted
data", and all `param:`-sourced findings were deleted at the end of the
function. But a route handler is called by the framework, not by project code,
so `limit: str = Query(...)` — the single most common way untrusted input
enters a modern Python service — could never produce a finding. A parameter
whose default or annotation matches a configured source (`Query(`, `Body(`,
`Form(`, `Annotated[str, Query()]`, plus anything a project adds via
`taint.sources`) is now a real source.

**4. `await`ed calls were invisible.** The statement visitor matched
`ast.Expr(value=ast.Call)` and `ast.Return(value=ast.Call)`. In async code the
node is `ast.Await` wrapping the call, so `await db.execute(...)`,
`return await run(cmd)` and `x = await helper(tainted)` were all skipped. Await
is now unwrapped in the statement visitor, in assignments, and in expression
taint checks.

Result on flyto-cloud: 0 → 18 flows.

**5. Which then surfaced a false-positive class worth fixing.** All 18 were
SQLAlchemy `select(...).where(...)` objects reaching `db.execute` — a bound
query cannot carry an injection. SQL sinks now require an argument that is a
string assembled at runtime (f-string, `+`, `%`, `.format()`, `.join()`,
`text(...)`), tracking which locals hold ORM expression objects. An unknown
variable still counts as dynamic: dropping a real flow is worse than one extra
lead. 18 → 3 flows, all in-function, all plausible.

## Type-aware callee resolution (the original ask)

`src/analyzer/taint_lsp.py` puts the language server in front of the
cross-function pass's name matching, with a deliberate three-state contract:

- `True` — the call site resolves to that definition; keep the flow.
- `False` — it binds elsewhere (a name collision); drop it.
- `None` — no server, unsupported language, budget spent, or no answer; keep
  the name-based result.

`None` is the load-bearing case: no language server is installed on this
machine or in CI, so verification is an upgrade over the regex floor and never
a precondition for it. `src/lsp/call_graph.py` gained `resolve_definition()`
(cached, soft-fail). Budget is 500 call sites per scan;
`DataFlowResult.callee_resolution` reports mode, checks, verified, rejected,
unknown, and whether the budget ran out.

Two name-matching bugs were fixed regardless of LSP availability:

- `callee_name in call_name` was a substring test, so a call to `prerun_hook`
  counted as a call to `run`. Now an exact final-segment match.
- Both trace strategies discarded the dangerous function's defining file, so
  same-named functions in different modules were one function. The file is now
  threaded through and is what the verifier compares against.

## Verified

- `python3.11 -m pytest tests -q` → 2406 passed, 1 skipped.
- `python3.11 -m ruff check src tests` → clean.
- 18 new tests in `tests/test_taint_callee_resolution.py` covering call-site
  positions, all three verdicts, budget bounding, the `prerun_hook`
  false-attribution case, and resolution reporting.
- Live: flyto-cloud 0 → 3 proven flows (SSRF ×2, path traversal ×1) with the
  ORM gate on; flyto-core and flyto-ai remain 0 flows at full coverage, which
  is consistent with their code (a browser-automation library and an agent
  runtime, neither taking untrusted web input into string sinks).

## NOT verified — read before trusting the LSP path

**No language server is installed here, so the `True`/`False` branches were
never exercised against a live server.** They are covered only by tests with a
stubbed resolver. Before relying on LSP verification, install pyright and
re-run against a project with same-named functions across modules. Everything
else in this handoff was verified against real repositories.

Known remaining false-positive class, left in deliberately: a constrained
FastAPI parameter (`Query(default="directory", pattern="^(file|directory)$")`)
is still treated as a full source — see
`src/ui/web/backend/api/utils.py:22` in flyto-cloud, which ranks #3 but is
constrained by the pattern. Gating on `pattern=` / `regex=` would need care,
since a permissive pattern must not silently suppress a real flow.
