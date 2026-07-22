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
flyto-index task plan --description "change token validation" --targets src/auth.py
flyto-index pr-risk .
flyto-index check . --max-affected 20
```

`task` maintains local workflow state. `check` and `pr-risk` inspect Git
changes; they do not push, merge, or modify remote state.

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
