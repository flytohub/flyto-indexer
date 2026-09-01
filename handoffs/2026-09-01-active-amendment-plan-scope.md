# Active amendment plan scope

## Status

Implemented locally on `codex/route-active-rework-scope`. Do not call it
accepted, landed, or released until independent review, full verification, and
the paired Flyto AI consumer change pass together.

## Problem reproduced

An authenticated parent with 63 cumulative paths and a small same-scope audit
compiled a 58-step successor because ordinary amendment analysis received the
entire cumulative target union. Flyto AI correctly refused it at the unchanged
32-step executable bound before provider start.

## Producer change

- `src/tools/smart.py` analyzes only validated `amendment_targets` for ordinary
  successors.
- `instruction_context`, `intent_ledger`, and `task_amendment.cumulative_*`
  continue to use the complete ordered cumulative target set.
- First-round planning and proof-bound recovery are unchanged.
- No amendment, cumulative-target, chain, or recovery ceiling was raised.

## Verification recorded so far

- Baseline before patch: 213 amendment and parent-normalization tests passed.
- Focused after patch: 214 amendment and parent-normalization tests passed.
- New regression proves a 63-path parent replans three exact existing targets,
  retains all 63 authorized paths, and compiles no more than 32 steps.

## Required paired closure

Flyto AI must accept exact active-plan coverage while retaining rolling support
for an exact legacy cumulative plan. It must reject partial, extra, malformed,
or unrequested authority and keep the 32-step executable bound unchanged.
