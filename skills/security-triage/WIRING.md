# Wiring note for Codex — `indexer.*` blueprint step modules

**Audience:** whoever owns the `flyto-ai` / `flyto-core` control plane.
**Author:** Claude (flyto-indexer lane). I did not touch the control plane —
this is the spec so the work needs no re-derivation.

## What exists and what is missing

`skills/security-triage/blueprint-draft.yaml` is a complete, drop-in blueprint
for the security-triage funnel. It cannot run yet because its steps name three
module ids that no host currently declares executable:

    indexer.scan
    indexer.research_priority
    indexer.call_hierarchy

All three already exist and are tested as **flyto-indexer MCP tools**. What is
missing is only the binding between a blueprint step module id and that tool
call.

## Why this is not a flyto-indexer change

`flyto-blueprint/ARCHITECTURE.md`:

> Host module availability (`flyto_blueprint.availability`) is a trust
> boundary, not a feature flag. The set of executable module IDs is
> authoritative host state passed in by the embedding application; the library
> never imports Flyto2 Core to discover modules and **never accepts the set
> from a model**.

So the executable set is host state owned by the embedding application
(`flyto-ai`), never declared by a blueprint, a model, or by `flyto-indexer`.
Two consequences:

1. The binding belongs in the **embedding application**, next to how
   `http.get` / `browser.*` are already made executable.
2. Adding a module id widens what an LLM-selected blueprint may execute. That
   is a privilege decision for the control-plane owner, which is why I stopped
   at the spec.

`flyto-indexer` needs no change at all: the tools, their schemas and their
dispatch already exist.

## The three modules

Parameters below are the **exact** MCP input schemas (verified against
`tool_registry`, 2026-08-19). Bind step params straight through.

### 1. `indexer.scan`

Ensures an index exists. Idempotent; skip if `.flyto-index/` is present.

| param | type | notes |
| --- | --- | --- |
| `path` | string | project root |

CLI equivalent: `flyto-index scan <path>`.

### 2. `indexer.research_priority` → MCP tool `research_priority`

The ranked reading list. Returns `{candidates[], coverage, weights, projects[]}`.

| param | type | default | notes |
| --- | --- | --- | --- |
| `project` | string | — | indexed project name |
| `top_n` | integer | 20 | max 200 |
| `since_days` | integer | 180 | churn window |
| `include_sanitized` | boolean | true | keep sanitizer-claimed flows |
| `include_unproven` | boolean | true | **false = proven flows only** (stage 1) |
| `sarif_path` | string | — | rank an external scanner's SARIF too |

Each candidate carries `evidence` (the tier), `proven`, `score`, `signals`,
`reasons`, `file`, `line`, `function`. **`coverage.signals_unavailable` must be
surfaced, not dropped** — it is what makes an empty result honest.

### 3. `indexer.call_hierarchy` → MCP tool `call_hierarchy`

LSP-resolved call edges. **Expensive** — see the budget rule below.

| param | type | default | notes |
| --- | --- | --- | --- |
| `path` | string | *required* | file containing the symbol |
| `line` | integer | *required* | 1-based |
| `column` | integer | 0 | 0-based |
| `direction` | enum | `incoming` | `incoming` \| `outgoing` |
| `max_depth` | integer | 2 | capped at 5 |
| `project` | string | cwd | project root |

Returns `{}` with no edges when no language server is installed — that is a
soft fail by design, never an error.

## Non-negotiable execution properties

These are why the funnel stays lean. Enforce them in the adapter, not in prose:

1. **`call_hierarchy` is capped at 8 calls per run.** The draft expresses this
   as `max_iterations: {{lsp_budget}}`. Never run it over a whole call graph —
   the cap is the invariant that keeps this cheap.
2. **Read-only.** All three tools only read the repository and the index. No
   step in this procedure writes to the target project.
3. **The output is a reading list, not a verdict.** Candidates carry evidence
   tiers; nothing here confirms a vulnerability.
4. **Truncation and unmeasured signals must reach the caller.** Dropping
   `coverage` turns "we stopped looking" into "we found nothing".

## Suggested order of work

1. Add the three module ids to whatever registry maps a module id to an
   executable adapter in `flyto-ai`, alongside the existing `flyto-core` ones.
2. Implement the adapters as thin passthroughs to the flyto-indexer MCP tools
   (a `flyto_ai/tools/indexer_tools.py` mirroring `core_tools.py` would match
   the existing shape; `flyto_ai/agents/indexer_context.py` already talks to
   the indexer, so the transport exists).
3. Include the ids in the host-supplied available-module set passed to
   `flyto_blueprint.availability`. Until then the gate correctly fails closed
   and hides the blueprint — that is the desired behaviour, not a bug.
4. Copy `blueprint-draft.yaml` to `flyto-blueprint/blueprints/security_triage.yaml`
   unchanged and regenerate the blueprint catalog reference.

## Verification once wired

    flyto-ai blueprints | grep security_triage         # visible = gate satisfied
    # then expand it against any indexed repo and compare with:
    flyto-index research-priority <repo> --top 20

The blueprint's ranked output must match the direct CLI run. If the blueprint
is missing from `list`/`search`, the availability gate is refusing it — check
the module ids are in the host set rather than editing the blueprint.

## If this is not worth doing

Entirely reasonable. `skills/security-triage/SKILL.md` already describes the
same funnel for an agent to follow directly against the MCP tools, and it was
validated end to end on gradio. The blueprint only buys deterministic replay
without a model re-deriving the sequence each run. Skip it until that replay is
actually wanted.
