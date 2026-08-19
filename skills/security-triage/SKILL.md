---
name: security-triage
description: >-
  Turn a large codebase into a short, ranked reading list of security-relevant
  code paths worth a human researcher's time, using the flyto-indexer MCP tools.
  Use when asked to find, prioritize, or triage potential vulnerabilities /
  taint flows / attack surface in a repository indexed (or indexable) by
  flyto-indexer — "what should I look at first", "where are the risky paths",
  "security review this repo". Produces a reading list, never a verdict.
---

# Security research triage

A budget-aware funnel over the flyto-indexer MCP tools. It narrows a whole
repository to the handful of code paths worth reading, spends expensive
verification **only on that handful**, and hands the result to a human as an
evidence package.

The intelligence is in the sequencing and the stop rules, not in any one tool.
flyto-indexer stays a set of dumb, lean tools; this skill is the smart caller.

**The output is a prioritized reading list, not a judgement.** Every item says
what evidence put it there and how strong that evidence is. A researcher
decides what is real.

## Tools this skill drives (all already in flyto-indexer — nothing new)

- `research_priority` — the ranked candidate list (fuses taint reachability,
  sink severity, complexity, git churn, test gaps, error handling). CLI:
  `flyto-index research-priority`.
- `call_hierarchy` — LSP-resolved call edges. Type-aware; **expensive**, so
  used only on the shortlist.
- `impact` — references / blast radius / reachability of a symbol.
- `search`, `structure` — context when a candidate needs it.

## The funnel

Run the stages in order. Each stage narrows; cost rises. Stop as soon as a
stage gives the researcher enough — do not run later stages out of habit.

### Stage 0 — Is there an index?

If `.flyto-index/` is missing, run `flyto-index scan .` once. Otherwise skip.

### Stage 1 — Proven flows first (cheap, high confidence)

    research_priority(project=<name>, top_n=20, include_unproven=false)

Proven source-to-sink flows are near-certain leads. If this returns any, they
are the top of the reading list **immediately** — a proven flow outranks every
unproven lead. If it returns enough for the researcher (say ≥ 5 strong ones),
you may hand off now and skip the deeper stages.

### Stage 2 — Ranked leads (the wide net, already fused)

    research_priority(project=<name>, top_n=20)

The score already fuses git churn, complexity, test gaps and evidence tier —
**do not re-run those tools per candidate**, read the `reasons`/`signals` the
result already carries. Keep the top ~8 for Stage 3; the rest stay on the list
below the shortlist.

### Stage 3 — Selective verification (expensive — SHORTLIST ONLY)

This is where name-based imprecision is bought back with type resolution, but
**only for the ~8 shortlisted candidates**, never the whole graph. This is what
lets a polymorphic call (a method with many same-named definitions) resolve
without baking whole-program dataflow into the engine.

For each shortlisted candidate:

- `call_hierarchy(path=<file>, line=<sink line>, direction="incoming")` —
  resolve who really reaches this sink, and whether the cross-function
  attribution holds. If the language server resolves the call somewhere
  harmless, **drop the candidate**.
- `impact(target=<function>)` — is this reachable from a route / entry point,
  or is it dead / internal-only? Unreachable ⇒ demote.

**Hard budget: cap `call_hierarchy` at 8 calls per run.** Never LSP the whole
repository. If the shortlist is larger, verify the top 8 and say so.

### Stage 4 — Evidence package (human handoff)

Emit the top 5–8 as a reading list. For each item:

- `file:line` and function name
- evidence tier (`proven_flow_*` > `source_and_sink_same_function` >
  `sink_with_*`) and whether Stage 3 confirmed or weakened it
- the flow path (source → sink), and the LSP-resolved caller chain if Stage 3
  ran
- the signals that matter (churn, no test, error handling) — one line
- one sentence: **why this is worth 30 minutes**

Then state plainly: this is a reading list; confirming exploitability,
threat-model scope, and severity is the researcher's call.

## Stop / budget rules (the wise part)

- Enough proven flows ⇒ hand off, skip Stages 2–3.
- `call_hierarchy` capped at 8 per run — the single most important rule for
  staying lean. The cost of this skill is bounded by design.
- A candidate the LSP resolves to a safe/validated path ⇒ drop it.
- A candidate not reachable from any entry point ⇒ demote, don't drop (it may
  become reachable).
- Never present a candidate as a vulnerability. The engine finds *paths*; a
  human finds *bugs*.

## Honest limits (state these when handing off)

- Ranking recall is capped by taint recall, which is name-based across
  functions. Deep multi-hop flows through a polymorphic abstraction layer
  (e.g. a store/service interface with many same-named implementations) are
  not proven automatically — Stage 3's selective LSP recovers some, but a
  researcher's read is the backstop.
- A large repo yielding few proven flows is not proof of safety; it is the
  honest floor. `coverage` in the result names what was not measured.

## Where this belongs long-term

This skill is the *first-run* form: the model derives the funnel each time.
Once a run is proven useful, capture it as a **flyto-blueprint** procedure
(parameterized steps + assertions + evidence) so **flyto-ai** can replay it
deterministically on the next repo without re-deriving the sequence. The skill
stays the human-readable source of truth; blueprint/flyto-ai make it cheap and
repeatable. Nothing here is ever added to flyto-indexer's 20-tool surface.
