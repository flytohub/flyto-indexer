# Resume Across AI Tools

Switching coding agents should not mean re-explaining the task, copying a chat,
or committing another handoff document. It should also not turn every session
into permanent repository clutter.

Flyto2 Indexer keeps one small, project-local continuity record. The existing
`task(plan)`, `task(gate)`, and `task(validate)` lifecycle updates it
automatically. The next MCP client sees the current state through
`structure(focus="profile")`; the CLI shows the same state with:

```bash
flyto-index task-status .
```

A reminder appears only when there is something actionable to carry across:
remaining steps, changed paths that have not been closed, a blocker, a failed
validation, or an explicit next action. A completed task does not create
handoff noise.

## What Is Kept

The local record contains bounded task facts:

- task and run IDs;
- project, base commit, objective summary, and status;
- completed and remaining steps;
- repository-relative changed paths;
- blockers, next action, and compact verification results;
- normalized token counts, tool-call counts, and duration when explicitly
  recorded.

It never stores prompts, responses, source code, raw provider usage objects, or
credentials. Common secrets, fenced code, and home-directory paths are
redacted from summaries. Absolute and parent-traversal paths are rejected.

The database is `.flyto-index/task-runs.sqlite`, which is already ignored by
Git. Its directory and file use owner-only permissions where the operating
system supports them. Terminal history is retained for at most 90 days and is
capped at 1,000 runs; active work is never removed by retention. Delete the
file at any time to reset local history.

Reading continuity is genuinely read-only. If no database exists,
`task-status` and `structure(profile)` report a closed state without creating a
directory or file.

## Record Usage Without Sending Text

Use provider usage metadata when it is available:

```bash
flyto-index usage-record task-1 . \
  --provider openai \
  --model gpt-5 \
  --usage '{"input_tokens":1200,"output_tokens":300}' \
  --tool-calls 12 \
  --duration-ms 84000 \
  --event-id request-42
```

OpenAI, Anthropic, Gemini, and generic envelopes are normalized into the same
small schema. `--event-id` makes retries idempotent.

If a provider does not return usage, pass character counts—not text:

```bash
flyto-index usage-record task-1 . \
  --provider local \
  --model unknown \
  --estimated-input-chars 4800 \
  --estimated-output-chars 1200
```

This fallback is labeled `estimated`. The default estimator is dependency
free; install `flyto-indexer[token-estimation]` only when an in-process caller
wants optional model-aware `tiktoken` counting. Estimation occurs in memory and
does not retain the text.

## Read The Evidence

```bash
flyto-index usage-report . --task task-1
flyto-index usage-report . --task task-1 --format json
flyto-index usage-report . --task task-1 --format csv
flyto-index usage-report . --task task-1 --format html --output evidence.html
```

The HTML output is one static file with no JavaScript or service dependency.
There is no dashboard to deploy and no frontend runtime to maintain.

An individual run reports counts and verified successes per 1,000 tokens. It
does not claim savings. A before/after reduction is shown only when both runs:

- use different declared variants;
- pass verification;
- share the same experiment ID, task fingerprint, repository commit, provider,
  model, tool policy, verification policy, and sample count;
- use the same reported or estimated measurement method;
- contain usage evidence.

Any mismatch returns a reason instead of a percentage. Reported provider usage
is labeled `measured_reduction`; estimator output is labeled
`estimated_reduction`. Both passing the same proof policy supports the narrow
statement that no regression was observed by that policy—it does not prove
product quality, business correctness, or universal token savings.

## Fixed Product Contract

The repository ships a deterministic 100-scenario contract covering provider
normalization, estimation, continuity and privacy, honest paired comparisons,
portable reports, and CLI behavior:

```bash
python benchmarks/evaluate_task_efficiency.py --check
```

The gate requires at least 90%. The committed receipt records all 100 scenario
IDs and the result fingerprint; cases are not removed when they fail. See the
[current machine-readable evidence](evidence/task-efficiency-100.json).
