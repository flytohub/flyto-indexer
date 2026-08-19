# Handoff: Evaluation against external projects

- Date: 2026-08-19
- Owner: claude
- Branch: main
- Status: five projects measured, six precision fixes shipped, one finding pending disclosure

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

## Second project — mlflow

Target: **mlflow** (`mlflow/mlflow`, 2,652 Python files / ~785k lines) — Flask
rather than FastAPI, and eight times gradio's size, to see whether the fixes
generalize or were shaped to one codebase.

    flyto-index scan <repo>                 5m 26s
    flyto-index research-priority --top 20    31s

133 candidates, 1 proven flow, no truncation. Reading the top twenty:

| verdict | count |
| --- | --- |
| worth a researcher's 30 minutes | 8 |
| confirmed security weakness | 0 |

What is interesting is *where* the eight landed. The proven flow is
`server/handlers.py` `gateway_proxy_handler`, which concatenates a
request-supplied `gateway_path` into a proxied URL — and the two ranks below it
point at `_invoke_scorer_handler` (request JSON carrying a serialized scorer)
and the `exec()` sites in `genai/scorers/base.py` that deserialize it. Both
places already carry deliberate controls: `_validate_gateway_path` pins the GET
path to one exact string, and the `exec()` path is gated behind a Databricks
tracking URI with a comment saying why. The ranking did not find bugs there; it
found the two places mlflow's own authors decided needed a guard, which is the
behaviour you want from a triage tool.

Six of the twenty were correctly demoted to `operator_input_and_sink` —
`save_model(path=...)` style library APIs where the path is the caller's. That
tier did its job: they are visible and last, instead of first.

The `sink_with_file_source` weakness called out below was fixed between the two
runs: sources are now located by line, and a lead is tiered by distance
(`sink_with_class_source` > `sink_with_nearby_source` > `sink_with_file_source`,
the last dropping to 0.18 reachability). On mlflow that split 70 file-level
leads into 4 class-level, 25 nearby and 41 distant.

## Round 3 — three more frameworks (django-cms, aiohttp, jupyter_server)

Three projects chosen for framework diversity rather than size, to see whether
the fixes were shaped to FastAPI/Flask: **django-cms** (Django, 81k lines),
**aiohttp** (the server library itself, 98k), **jupyter_server** (Tornado, 35k).
Clones were deleted after measurement.

The run exposed one new false-positive class, and it was the dominant one on
two of the three: **a bare-name sink was matching method calls and definitions**.
`jupyter_server` defines `async def open(self, kernel_id)` WebSocket handlers
that call `super().open()`, and every one of them became a path-traversal lead.
Fixed by requiring a bare-name sink such as `open(` to be a *free call* — not
preceded by `.` and not part of a `def`. Effect:

| project | candidates before | after |
| --- | --- | --- |
| jupyter_server | 13 | **4** |
| aiohttp | 9 | **5** |
| django-cms | 31 | 31 (unaffected — its sinks are dotted calls) |

What survived is markedly better. jupyter_server's four are all in `auth/` and
`nbconvert/` path handling. django-cms produced **5 proven flows — the most of
any project measured** — and the top ones are exactly where a researcher would
look: `request.GET.urlencode()` concatenated into `HttpResponseRedirect`, and
`login()` taking `request.GET.get(REDIRECT_FIELD_NAME)` through a
`_get_login_redirect_url` helper. As with mlflow, the ranking landed on the
place the project's own authors wrote a guard for.

aiohttp is the interesting negative: as a *library*, it has almost no untrusted
entry points of its own, and its remaining leads are ReDoS patterns in
`re.compile` sites. Zero proven flows there is the honest answer, not a miss.

## Honest reading of the result

- The ranking's ceiling is still taint recall, not ranking quality: 117k lines
  produced **one** proven flow. Everything else in the top twenty is
  pattern-adjacency wearing an honest label.
- `sink_with_file_source` was too coarse in large files: in a 1,400-line
  client, one `request.headers` read at line 773 made every `httpx` call in the
  file a lead. Now tiered by distance — but distance is a proxy for reachability,
  not reachability. It is still the weakest evidence tier by design.
- Two projects, one human, one afternoon. Every "worth reading" verdict is this
  reviewer's judgment, not a working researcher's — which is exactly the
  judgment the tool cannot supply and a collaborator could.
- Proven-flow yield is codebase-shaped, not size-shaped: gradio 4, mlflow 1,
  django-cms 5, aiohttp 0, jupyter_server 0. A framework with explicit request
  objects flowing into redirects (Django) proves more than a library whose
  entry points belong to its callers (aiohttp).
- Every round so far has found a distinct, mechanical false-positive class by
  running on one more real project. That loop — measure, find the class, fix it,
  re-measure — has been worth more than any speculative engine work.
