# CLI Guide

Run commands from the repository being analyzed. Installing with `pipx` keeps
the command isolated; a source checkout can use `python -m src.cli`.

```bash
pipx install flyto-indexer
flyto-index --version
flyto-index setup .
```

`setup` writes local metadata and client guidance, so review its diff before
committing. Scans and audits otherwise operate on local files and write only
generated index or explicitly requested report paths.

## Everyday Flow

```bash
flyto-index scan . --full
flyto-index context . --query "authentication boundary"
flyto-index impact verify_token --path .
flyto-index verify . --strict --json
```

Use `status` to check freshness, `brief` or `outline` for bounded orientation,
and `describe` to manage a file's semantic description. Use `profile`, `deps`,
`framework`, or `call-sites` when a focused inventory is needed.

## Change Planning

```bash
flyto-index task grill --grill-action start \
  --description "change token validation"
flyto-index task plan --description "change token validation" --targets src/auth.py
flyto-index task validate --project . --task-contract task-contract.json
flyto-index pr-risk .
flyto-index check . --max-affected 20
```

`task plan` adds target-scoped JIT Rules and an Intent Ledger without extra
flags. Complete plan, gate, and validate actions are confined to `--project`,
including when the command runs outside MCP, so nearby worktree indexes do not
enter analysis, context checks, or continuity. Fuzzy search results never grant
task edit authority without an exact symbol identity match. `task grill`
optionally freezes unresolved human decisions with evidence
and ADR artifacts. Pass the plan through `--task-contract` during validation to
require rule/spec freshness, requirement and diff coverage, Ruff, pytest, and
allowlisted proof results. Unsupported proof commands are never executed.
`check` and `pr-risk` inspect Git changes; they do not push or merge. The
complete acceptance path is in the
[Decision Grill test protocol](GRILL_TESTING.md).

## Resume And Measure A Task

```bash
flyto-index task-status .
flyto-index usage-record task-1 . --provider openai --model gpt-5 \
  --usage '{"input_tokens":1200,"output_tokens":300}'
flyto-index usage-report . --task task-1 --format html --output evidence.html
```

`task-status` reads the same local continuity state exposed by MCP project
profiles. It does not create a file when no state exists. Usage commands accept
normalized metadata or character counts, never prompt or response text.
Reports may be terminal text, JSON, CSV, or one static HTML file. A savings
claim requires a verified paired experiment; a single run reports counts only.
See [Task continuity and usage evidence](TASK_CONTINUITY.md) for the privacy and
comparison contract.

## Security And Policy

```bash
flyto-index secrets .
flyto-index taint .
flyto-index agent-audit .
flyto-index license .
flyto-index layers .
flyto-index sbom . --output sbom.cdx.json
```

Rule-authoring commands intentionally modify `.flyto-rules.yaml`:
`add-layer`, `add-taint-source`, `add-taint-sink`, and
`add-taint-sanitizer`. Review and commit that policy like source code.

## Automation

Prefer machine-readable output where a command supports `--json`, and use an
explicit report path in CI. `install-hook` modifies the current repository's
Git hooks. `verify-baseline update` changes the selected baseline file. Neither
operation should run implicitly in a read-only audit.

For every subcommand, argument, default, choice, handler, and source line, use
the [generated CLI reference](reference/cli.md) or run:

```bash
flyto-index <command> --help
flyto-index tools
```
