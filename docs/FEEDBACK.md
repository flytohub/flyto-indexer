# Learn From Every AI Miss

An AI coding session can finish successfully and still expose a weakness in the
development system: a noisy secret warning, a missing lazy route, a slow scan,
or advice that did not fit the repository. Those observations are usually lost
in chat history. Flyto2 Indexer can keep them as local, structured improvement
evidence.

## Record A Problem

Use the existing `task` tool, so the public MCP surface does not grow:

```text
task(
  action="feedback",
  feedback_action="record",
  project="my-project",
  feedback_category="false_positive",
  feedback_summary="A documented demo credential was classified as a real secret",
  feedback_tool="scan_secrets",
  rule_id="secret/password",
  feedback_severity="medium",
  request_id="session-42-secret-demo"
)
```

Useful categories include false positives, false negatives, missing context,
framework gaps, slow scans, gate friction, runtime mismatches, and bad
recommendations.

Failed `task(validate)` runs also create a compact automatic observation. They
store reason codes—not prompts, answers, patches, or source code.

## Find Repeated Pain

```text
task(
  action="feedback",
  feedback_action="summary",
  project="my-project",
  since_days=90
)
```

Repeated observations share a stable feedback ID. The summary ranks them by
frequency, severity, and observed latency so maintainers can choose what to
benchmark and fix first.

## Close The Loop

After a fix and regression test are reviewed:

```text
task(
  action="feedback",
  feedback_action="resolve",
  feedback_id="feedback-...",
  resolution="Added a negative fixture and tightened the demo-value classifier",
  resolved_by="security-team",
  request_id="resolution-104"
)
```

Resolution is append-only. The original evidence stays available for trend
analysis, but resolved groups disappear from the normal open-issue summary.

## Privacy And Control

- Feedback is stored under `~/.flyto-indexer/feedback/` by default.
- `FLYTO_INDEXER_FEEDBACK_DIR` selects another local directory.
- Files use restrictive local permissions.
- Common tokens, credentials, home-directory names, and fenced code blocks are
  redacted before persistence.
- Notes are bounded. Do not paste source code into feedback fields.
- Nothing is uploaded by this workflow.
- Feedback cannot change a rule, suppression, baseline, or CI policy by itself.

Every proposed rule change still needs human review and a benchmark or
regression test. This prevents a stream of complaints from silently weakening
the safety gate.

## What Makes Feedback Useful

A good observation names the problem, where it appeared, and the expected
behavior. Attach a stable finding or rule ID when one exists. For performance
problems, include `duration_ms`. Avoid customer data, prompts, or code excerpts.

The goal is not to collect more telemetry. The goal is to turn repeated AI
development pain into a short, evidence-backed product backlog.
