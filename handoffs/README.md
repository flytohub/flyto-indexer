# Handoffs

This directory records dated implementation handoffs for Flyto2 Indexer work.

Each handoff should capture what changed, why it changed, verification evidence,
and any remaining blockers. Do not store credentials, customer secrets, or local
login details here.

Do not create a dated handoff merely because a developer switches AI tools or
chat sessions. The ignored `.flyto-index/task-runs.sqlite` continuity record is
the default for resumable in-progress work. Add a durable Markdown handoff only
when a repository-level decision or long-lived implementation boundary must be
reviewed and preserved in Git.

## Ownership

This repository is edited by more than one coding agent. Every handoff declares
who did the work and where, so the next agent can tell live work from finished
work.

Start each handoff file with these three lines:

```text
Owner: codex | claude
Branch: <branch the work happened on, or main>
Date: YYYY-MM-DD
```

- An `Active` handoff owned by another agent means that agent may still be in
  those files. Do not edit the same files on the shared branch — work on your
  own `<owner>/<topic>` branch, or pick up different work.
- Mark the entry `Resolved` or `Superseded` in `_registry.md` when the work
  lands. That is what releases ownership.
- Record what you actually verified and what you did not. The other agent will
  treat this file as fact.

Copy `_template.md` to start a new handoff.
