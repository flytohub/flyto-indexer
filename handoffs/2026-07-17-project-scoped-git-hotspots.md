# Project-Scoped Git Hotspots

Date: 2026-07-17

## Summary

Project-scoped git history intelligence now fails closed when the requested
project root is absent from disk. This prevents `audit(project=...)` from
reporting hotspot paths from sibling repositories when the index contains stale
or invalid project-root metadata.

## Verification

```text
python3 -m pytest tests/test_git_intel.py -q
python3 -m ruff check src/tools/git_intel.py tests/test_git_intel.py
```

## Notes

- `git_hotspots(project=...)` now returns
  `Project root not found on disk: <project>` for explicit missing roots.
- The fallback to discovered indexes/CWD remains available only when no project
  is explicitly requested.
