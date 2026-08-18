# Handoff: Security research priority ranking

- Date: 2026-08-19
- Owner: claude
- Branch: main (local commit; not pushed)
- Status: implemented, local gates passed

## What landed

`research-priority` ranks functions by how much they are worth a human security
researcher's time, instead of emitting another undifferentiated finding list.
It fuses signals this repository already produced separately — taint
reachability, sink severity, entry-point exposure, function complexity, git
churn, test gaps, swallowed error handling — into one ordered short list, one
candidate per function, each carrying its evidence tier, its raw signals, and
plain-language reasons.

Surfaces:

- `src/analyzer/research_priority.py` — the ranking itself.
- `src/tools/research_priority.py` — index/project resolution adapter.
- `flyto-index research-priority [path] [--top N] [--since-days D]
  [--proven-only] [--no-sanitized] [--json]`.
- `research_priority` in `MCP_TOOLS` + dispatch.
- `audit(focus="research_priority")` — reachable from the MCP surface without a
  21st smart tool, following the existing "expose without expanding the tool
  count" pattern.

## What the work exposed (read this before tuning)

The taint engine finds **no complete source-to-sink flow on real projects** far
more often than its finding counts suggest. On `flyto-cloud` it reports 832
sources and 9,629 sinks and completes **zero** flows. A ranking seeded only by
proven flows is therefore empty on exactly the codebases it is meant to triage.

Two consequences, both deliberate:

1. **Unproven evidence tiers exist and are labelled.** `source_and_sink_same_function`,
   `sink_with_file_source`, and `sink_only_entry_point` say "worth reading", never
   "is a bug". `--proven-only` / `include_unproven=False` drops them.
2. **Parameterized SQL is suppressed.** Without it, every SQLAlchemy endpoint
   (`db.execute(select(...))`) matched the `db.execute` sink and filled the top
   ten. The SQL tier now requires runtime string construction — f-string,
   concatenation, `%`, `.format()`, or `text(...)` — over something matching a
   SQL *statement* shape. Matching a bare keyword was tried first and failed:
   `f"Cannot transition from {state}"` scored as dynamic SQL.

Root cause of the low recall is unchanged and untouched by this work: callee
resolution in `taint.py` compares bare names (`callee_raw.rsplit(".", 1)[-1]`),
and `src/analyzer/call_sites_lsp.py` exists but is not used by the
cross-function pass. That remains the highest-value precision fix.

## Honesty properties (do not regress these)

- A signal that cannot be measured is `None`, is excluded from the weighted
  mean, and is named in `coverage.signals_unavailable`. Scores renormalize over
  measured signals, so a repository without git history is not silently
  penalized. Covered by
  `test_unmeasured_signal_does_not_drag_the_score_down`.
- Scan caps surface in `coverage.truncated` / `truncation_note`. "Found
  nothing" and "stopped looking" must stay distinguishable.
- An empty result explains itself rather than reading as a clean bill of health.

## Verified

- `python3.11 -m pytest tests -q` → 2388 passed, 1 skipped.
- `python3.11 -m ruff check src tests` → clean.
- 26 new tests in `tests/test_research_priority.py`.
- Live runs: `flyto-cloud` (61 candidates, ~24s, top leads manually inspected),
  this repository (0 candidates — it has almost no untrusted-input surface),
  and synthetic fixtures for each evidence tier.

## Not verified / not done

- No LSP-resolved cross-function pass (the recall fix above).
- Non-Python languages: sources/sinks exist in the rule tables for JS/Go, but
  both the taint engine's flow construction and this ranking's unproven pass
  are Python-AST only. C/C++/Rust have no taint rules at all.
- No measurement of researcher hit-rate on an external open-source project;
  the top-20 precision claim is untested outside this workspace.
