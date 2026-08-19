# Handoff: First evaluation against an external project

- Date: 2026-08-19
- Owner: claude
- Branch: main
- Status: measured, three precision fixes shipped, one finding to disclose

## What was run

Target: **gradio** (`gradio-app/gradio`, shallow clone, 774 Python files /
~117k lines) — Python, real HTTP attack surface, actively maintained, in the
size band the ranking is meant for.

    flyto-index scan <repo>                 12.9s
    flyto-index research-priority --top 20    8.4s

**Time from clone to twenty ranked leads: 21 seconds.** That number is the one
metric this round establishes cleanly.

## Round 1 — the ranking was mostly noise

83 candidates, 1 proven flow. Reading all twenty by hand:

| verdict | count |
| --- | --- |
| worth a researcher's 30 minutes | 1 |
| confirmed security weakness | 0 |

Nineteen of twenty were noise, and they fell into exactly three mechanical
classes — which is the useful outcome, because all three were fixable:

1. **No attack-surface scoping (9 of 20).** `demo/gif_maker/run.py`,
   `demo/depth_estimation/run.py`, `gradio/cli/commands/deploy_space.py`,
   `scripts/profile_e2e/analyze.py`. Sample apps and CLI helpers ranked
   alongside library code, and `input()` / `sys.argv` were treated as
   attacker-controlled when they are the operator.
2. **Sink patterns matched mid-identifier (3 of 20).** `exec(` matched
   `asyncio.create_subprocess_exec(`; `Template(` matched
   `types.ResourceTemplate(`. Both surfaced as critical RCE / high SSTI.
3. **JavaScript sinks applied to Python (2 of 20).** `.innerHTML` matched
   JavaScript inside Python string literals — gradio embeds JS in template
   strings — and was reported as XSS in `.py` files.

The remainder were granularity failures: `create_app` (a 700-line FastAPI
factory) and `Examples.__init__` are technically "a function containing input
and a sink", which is useless as a reading instruction.

## Fixes shipped from that round

- `_is_attack_surface()` reuses `profile.filesystem.classify_path`, so demo,
  example, fixture, generated, script, docs and bin trees no longer seed
  unproven leads. Proven flows are still reported wherever they occur.
- Operator-controlled sources (`input(`, `sys.argv`, `argparse`,
  `click.prompt(`) get their own evidence tier, `operator_input_and_sink`,
  which ranks below every remote-input tier and says so in its reason. They are
  demoted, not hidden: for a CLI tool that *is* the attack surface.
- Sink matching requires a token boundary at both ends, in the engine and in
  the ranking's text pass.
- JS-only sinks are dropped from the Python pass.

Careful with the boundary rule: a pattern ending in `(` is already delimited,
and applying the right-hand guard to it silently dropped every `open(` lead for
one iteration. Only patterns ending in an identifier character get that check.

## Round 2 — after the fixes

67 candidates (from 83), and the top twenty is now entirely `gradio/` library
code. Re-read by hand:

| verdict | count |
| --- | --- |
| worth a researcher's 30 minutes | 8 |
| confirmed security weakness | 1 |

The eight: the upload path (`route_utils.upload_fn`), three `vibe-edit` route
handlers, two OpenAPI endpoint builders that construct request URLs from a
user-supplied spec, and the two upload `_process_single_file` type gates.

## The finding — details withheld pending disclosure

One of the eight is a real defect: a route handler joins a request-supplied
value into a filesystem path without the `safe_join` helper the same file uses
everywhere else, and that inconsistency is what makes it credible rather than
theoretical. Its impact is bounded by a development-mode flag.

**This document previously named the file, function and code.** That was wrong:
this repository is public, and the maintainers had not been contacted. The
details now live only in a local draft advisory and go to the project through
its security policy first. Note that the earlier revision remains in this
repository's git history — redaction limits further spread, it does not undo
publication.

Nothing was exploited, and no report has been filed anywhere yet.

## Honest reading of the result

- The ranking's ceiling is still taint recall, not ranking quality: 117k lines
  produced **one** proven flow. Everything else in the top twenty is
  pattern-adjacency wearing an honest label.
- `sink_with_file_source` is too coarse in large files. `gradio_client/client.py`
  is 1,400 lines: one `request.headers` read at line 773 made every `httpx`
  call in the file a lead. Proximity (same class, or a line-distance decay)
  is the obvious next fix and is not done.
- One human, one project, one afternoon. 8/20 and 1/20 are this reviewer's
  judgment, not an external researcher's, and a second project may behave
  differently.
