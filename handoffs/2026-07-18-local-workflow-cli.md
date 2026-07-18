# 2026-07-18 Local Task Workflow CLI

## Context

During Flyto2 engine refactoring, the long-running MCP server still had stale
indexer source loaded after the task path-resolution fix. MCP planning could
therefore keep resolving an explicit engine Go file to an unrelated bundled
Python symbol until the process was restarted.

## Change

- Added `flyto-index task {plan,gate,validate}` in `src/cli.py`.
- The CLI reuses `tools.smart.smart_task`; it does not fork task planning,
  gating, or validation logic.
- `--target` is repeatable and `--targets` accepts comma-separated values.
- `--task-contract` and `--current-state` accept inline JSON objects or paths
  to JSON files.
- `task gate` requires `--task-contract`, `--current-state`, and
  `--next-phase`.
- Gate denial, validation failure, or task errors exit with code 2 after
  printing the JSON result.

## Verification

```text
python -m ruff check src/cli.py tests/test_cli_commands.py
python -m pytest tests/test_cli_commands.py -q
python -m src.cli task plan --project flyto-engine --intent refactor \
  --description 'verify local task path target resolution' \
  --target /Users/chester/flytohub/flyto-engine/cmd/worker/kb_deep_scan_loop.go
```

The real smoke resolved the target to:

```text
flyto-engine:cmd/worker/kb_deep_scan_loop.go:file:kb_deep_scan_loop
```

## Follow-up

- Restart MCP server processes when possible so MCP and CLI both run the same
  source version.
- Prefer this CLI when a tool process appears stale but the task workflow gate
  still needs to be preserved.
