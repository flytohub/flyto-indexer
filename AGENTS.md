# Agent Rules

Before changing this repository, read `STATE.md`, `ARCHITECTURE.md`,
`DECISIONS.md`, and the latest entry in `handoffs/_registry.md`.

- `flyto-indexer` is the local code intelligence and verification tool for
  Flyto2 repositories.
- Keep analysis local by default. Do not introduce network calls for audit,
  indexing, or verification paths unless explicitly designed and documented.
- Prefer deterministic static analysis, typed impact, dependency, taint, and
  project-profile checks over ad hoc text scans.
- Generated index artifacts must stay out of source control.
- Do not store credentials, customer source excerpts beyond necessary test
  fixtures, or local login details in docs, handoffs, tests, or scripts.
- Run `bash scripts/lint-project-memory.sh` after editing project memory files.
