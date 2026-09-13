# Guard recognition without our own function names

Owner: claude
Branch: claude/agent-guard-recognition
Date: 2026-09-13

## What changed

`src/analyzer/agent_guards.py` (new) — recognizes protective calls and reports
the basis for the recognition. Single responsibility: it answers whether a
guard is present and how it was identified, and does not decide whether to
report a finding, its severity, or its wording.

`src/analyzer/agent_policy.py` — `PATH_GUARDS`, `SSRF_GUARDS` and `CRED_GUARDS`
are gone. The analyzer takes an injectable `GuardRecognizer`. Every remediation
string that named an internal helper now describes the required property
instead. `redirect-follow` requires a conclusive guard.

`tests/test_agent_guards.py` (new) — 14 tests, including one that asserts no
internal helper name can appear in any recommendation.

## Why

`PATH_GUARDS = {"validate_path_with_env_config"}` is a house rule for one
codebase. Applied to a repository somebody else wrote, it reports their own
protection as an absence of protection, and the remediation told them to call a
function their project does not contain. On justvugg/colibri that was 98 of 114
findings.

Rejected: adding more names to the set. That reproduces the defect for the next
project with a different convention.

Rejected: treating a guard-shaped name as proof. Any project could then silence
the analyzer by renaming a function. The shape tier lowers confidence and
annotates instead, so the evidence is neither trusted nor discarded.

## Verified

- `pytest tests/test_agent_policy.py` — 18 passed, unchanged from before the
  refactor. This is the regression evidence: those tests were not edited.
- `pytest tests/` — 2664 passed, 4 skipped
- `ruff check src/ tests/` — clean
- `flyto-index verify --strict` — 22 pass, 0 warn, 0 fail

Measured on a real third-party repository (justvugg/colibri, 114 findings),
before → after:

| | before | after |
| --- | --- | --- |
| recommendations naming internal helpers | 98 | 0 |
| file-write-no-guard | 32 | 32 |
| findings softened by a shape guard | 0 | 0 |

## Not verified

No false-positive reduction is claimed. The expectation going in was that most
of colibri's 32 file-write findings were misjudgements caused by their own
naming; measurement refuted that — colibri calls nothing guard-shaped in those
functions, so the writes are genuinely unconfirmed. The shape tier did not fire
on colibri at all, nor on six sampled third-party packages (requests, httpx,
aiohttp, jinja2, werkzeug, starlette), so its behaviour on real code is
exercised only by unit tests.

The `agent_guards` section of `.flyto-rules.yaml` has no CLI writer. Projects
must hand-edit it; there is no `add-agent-guard` command equivalent to
`add-taint-sanitizer`.

## Follow-ups

`mcp_reachable` is computed for every finding and carries the evidence for why a
finding is agent-relevant, but it stops at the engine boundary:
`flyto-engine/internal/scanner.AgentFinding` has no such field, so it never
reaches the published report. Readers therefore see a traditional SAST category
under an agent-shaped heading with nothing explaining the connection. Closing
that needs a change in flyto-engine (carry the field, surface it on
`SecurityFinding`) and flyto-landing-page (type, parser, rendering).

A CLI writer for `agent_guards`, mirroring `add-taint-sanitizer`, would let a
project declare its guards without hand-editing YAML.
