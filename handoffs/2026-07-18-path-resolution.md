# Task Path Target Resolution

Date: 2026-07-18

## Summary

`task(action="plan")` now resolves explicit file-path targets before any
keyword or semantic lookup. Absolute paths are normalized through
`project_roots`, project-relative paths are matched exactly, file symbols are
preferred over nested symbols, and unmatched path-like inputs return `unknown`
instead of falling back to search.

## Why

During Flyto2 engine refactoring, the target
`/Users/chester/flytohub/flyto-engine/cmd/worker/kb_deep_scan_loop.go` was
incorrectly planned as `flyto-indexer-pkg/src/engine.py:class:IndexEngine`.
That made a file-level Go refactor look like a public Python API change and
blocked the wrong work. Explicit paths must fail closed when the index cannot
prove the requested file.

## Verification

```text
python -m pytest tests/test_task_analysis.py::TestTaskTargetResolution -q
python -m pytest tests/test_task_analysis.py::TestAnalyzeTask::test_symbol_id_target tests/test_task_analysis.py::TestAnalyzeTask::test_unresolvable_target -q
python -m ruff check src/tools/task_analysis.py tests/test_task_analysis.py
python - <<'PY'
...
PY
```

The live Flyto2 engine path now resolves to:

```text
flyto-engine:cmd/worker/kb_deep_scan_loop.go:file:kb_deep_scan_loop
```

## Notes

- The regression tests patch `tools.task_analysis.load_index` directly because
  local `.flyto-index/.generation` can invalidate the legacy module cache during
  tests.
- The production fix is intentionally narrow and does not change the
  `analyze_task` public signature.
