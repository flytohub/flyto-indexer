# Taint rule writing and argument-shape gates

Owner: claude
Branch: `fix/adding-a-rule-must-not-rewrite-the-file` (PR #56, merged to main
2026-09-08), then `feat/sink-rules-name-methods-not-receivers` (PR #57)
Date: 2026-09-08

## What changed

Two separable changes, one per pull request.

**PR #56 — writing a rule stops rewriting the file.**
`src/analyzer/taint_dsl.py` no longer parses `.flyto-rules.yaml` and dumps it
back. `_append_entry_in_place` and `_remove_entry_in_place` edit the text:
the new item joins its own list at that list's indent, a missing sub-list is
created inside the `taint:` block above any comment introducing the next
top-level key, and removal deletes exactly the item's lines. `_render_value`
writes values the way the file already writes them (`pattern: "os.system("`,
`vuln_type: rce`, `cleanses: ["rce"]`). Two shapes are refused with the
snippet to paste rather than mangled: a key carrying an inline value, and a
list with no items to align to. Removing the last item takes the emptied key
with it, and `_apply_yaml_rules` in `src/analyzer/taint.py` now tolerates a
declared-but-empty list. Adds `flyto-index remove-taint-rule`.

**PR #57 — a sink rule can name a method without naming its receiver.**
New `src/analyzer/taint_shapes.py` evaluates a rule's `requires:` against the
call's AST: argument shape and constancy (index, `any`, `after_first`), callee
tail at a name boundary, keyword values, argument counts, and `not`. Sink
tuples in `src/analyzer/taint_rules.py` carry an optional fourth element;
`_flatten_sinks` widens to five fields and every consumer follows, including
`_worst_sink` in `src/analyzer/research_priority.py`. The built-in NoSQL and
header rules now match `.find(`, `.findOne(`, `.headers[`, `.setHeader(` and
friends. `TaintAnalyzer` records literal bindings per function so a gate can
resolve `sep = ","`. Identical flows are deduplicated in `analyze()`.
`add_taint_sink(..., requires=...)` and `add-taint-sink --requires` write them.

## Why

`.flyto-rules.yaml` is hand-maintained policy: its comments say why a rule is
there. Adding one sanitizer to flyto-cloud's 332-line file produced 351
insertions, 332 deletions, and deleted both comments. A comment-preserving
parser (ruamel) was rejected: it would be the first runtime dependency this
package has ever taken, against the stated zero-dependency boundary that
`no_external_runtime` enforces in verify.

Sink patterns are substrings, so naming a receiver only found projects that
chose the same name -- `collection.find(` missed every project that called its
Mongo handle anything else. The distinguishing fact is the argument's shape,
not the receiver's name.

Rejected: migrating the existing per-category gates (SQL's ORM-expression
check, the os.path.join segment check) into the declarative vocabulary. The
SQL one reads analyzer state, and the join one has keyword handling the
vocabulary does not reproduce exactly; unifying them would have changed
behaviour for aesthetic reasons.

## Verified

- `pytest -q`: 2577 passed, 4 skipped. 15 new tests in `tests/test_taint_dsl.py`
  (9 of which fail against the previous implementation, confirmed by stashing
  the source change) and 29 in `tests/test_taint_shapes.py`.
- `benchmarks/evaluate.py`: 27/27 cases, precision 1.000, recall 1.000,
  FPR 0.000. Two cases added (`python-nosql-named-handle`, `python-str-find`);
  `--min-cases` raised 25 -> 27, and `docs/LANGUAGE_EVIDENCE.md` regenerated:
  the corpus is what gates the language claims, so adding cases moves Python
  from 17 (12 positive / 5 negative) to 19 (13 / 6).
- `ruff check .` clean. `scripts/check_quality_debt.py`: Ruff=1133, mypy=729,
  unchanged from baseline.
- `flyto-index verify --strict`: 22 PASS, no warnings, no failures.
- `scripts/generate-reference.py --check` in sync on both branches.
- `bash scripts/lint-project-memory.sh` passes.
- Against a copy of flyto-cloud's real 359-line `.flyto-rules.yaml`: adding a
  sanitizer is 2 insertions / 0 deletions with both comments intact (was
  351 / 332 with both deleted), and CLI add-then-remove leaves the file
  byte-identical.
- Seven-case before/after probe: `users.find(request.json)`,
  `db.orders.find({...})`, `Users.findOne(q)` and `resp.headers[...] = v` go
  from no finding to a finding; `line.find(",")` and a bound-separator
  `text.find(sep)` stay silent in both.
- PR #56 CI: 21 checks green (one SBOM run failed on an artifact-upload 403 and
  passed on re-run; the same commit content had already passed that job).

## Measured afterwards (2026-09-09)

The fail-open cost was left unmeasured above. It has now been measured, on
15,798 files of third-party Python (flyto-core's installed dependencies, copied
out of `.venv` because the scanner excludes that path) and on all 43 workspace
repositories, running the merged rules against the pre-change commit 9497948.

- `.find(` call sites in that corpus: 1103. The shape gate rejects 708 (64%)
  and passes 395 (36%). A 30-site random sample of the passers contained no
  database call at all -- `str.find`, ElementTree `Element.find`, BeautifulSoup,
  union-find, and sympy expression search.
- Findings on that corpus: 17 before, 20 after. **Zero** of the three new ones
  is a NoSQL finding: passing the gate is not a finding, and none of those 395
  sites has tainted data reaching it. The fail-open FP class is real in
  principle and empty here.
- The three new findings all come from the receiver-free `.headers[` rule.
  One (`blackd/middlewares.py:34`) is a genuine server-side CORS echo, guarded
  by an allow-list check the engine cannot see. Two (`aiohttp/client_reqrep.py`,
  `aiohttp/client_middleware_digest_auth.py`) set headers on an outgoing
  *client* request -- a real false-positive class, because the rule cannot tell
  a client request object from a server response object.
- Across all 43 workspace repositories: one new finding,
  `flyto-ai/flyto_ai/cli.py:2254`, the same CORS shape, sanitized in code by
  `_get_cors_origin` (strips CR/LF, then checks a whitelist). Zero new NoSQL
  findings.
- The measurement also found a defect this change introduced:
  `total_sinks` counted gated patterns textually, so every `str.find` was
  counted as a sink and the number verify prints went 1554 -> 1890 on this
  repository. Fixed on `fix/a-gated-rule-is-not-a-text-count`.

## Not verified

- Still no measurement against a real Mongo or Express codebase: no
  pymongo/mongoose is installed anywhere in this workspace, so the recall gain
  remains demonstrated on constructed cases and the benchmark corpus, not on
  production database code.
- The client-request false-positive class above is identified but not sized;
  it needs a corpus of HTTP-client-heavy code to bound.
- PR #57's first push ran only 2 checks: its base was PR #56's branch, and most
  workflows run on pull requests into `main` only. Squash-merging #56 and
  deleting that branch closed #57, so it was rebuilt on `main` and reopened;
  the full check set applies from that point.

## Follow-ups

- Consider a `receiver` position in the shape vocabulary if `str.find` noise
  shows up in practice; it would let a rule reject a provably-string receiver.
