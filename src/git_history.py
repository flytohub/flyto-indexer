"""Git log plumbing shared by analyzers and tools.

These helpers used to live in `tools/git_intel.py`. The analyzer layer may not
import the tool surface (`.flyto-rules.yaml`: "Analysis engines must remain
callable from CLI, MCP, CI, and package users"), and research-priority ranking
needs the same churn numbers `git_churn` reports. Duplicating the parsing would
have let the two definitions of "churn" drift, so the plumbing moved down a
layer and both sides call it.

Pure stdlib, 30-second timeouts, and a short TTL cache so several analyses in
one process share a single `git log`.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Dict, List, Optional, Tuple

_log_cache: Dict[Tuple[str, tuple], List[dict]] = {}
_log_cache_ts = 0.0    # monotonic timestamp of last cache fill
_LOG_CACHE_TTL = 60.0  # seconds


def find_git_root(path: str) -> Optional[str]:
    """Walk up from *path* looking for a `.git/` directory. None if not found."""
    current = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def run_git(args: List[str], cwd: str, timeout: int = 30) -> str:
    """Run a git command and return stdout. Raises RuntimeError on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except FileNotFoundError:
        raise RuntimeError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git command timed out after {timeout}s: git {' '.join(args)}")

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"git failed (rc={result.returncode}): {stderr}")
    return result.stdout


def parse_log_with_files(log_text: str) -> List[dict]:
    """Parse ``git log --format='COMMIT:%H|%at|%an|%s' --name-only``.

    Returns [{hash, timestamp, author, message, files: [str]}].
    """
    entries: List[dict] = []
    current: Optional[dict] = None

    for line in log_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("COMMIT:"):
            if current is not None:
                entries.append(current)
            parts = line[len("COMMIT:"):].split("|", 3)
            if len(parts) < 4:
                current = None
                continue
            current = {
                "hash": parts[0],
                "timestamp": int(parts[1]) if parts[1].isdigit() else 0,
                "author": parts[2],
                "message": parts[3],
                "files": [],
            }
        elif current is not None:
            current["files"].append(line)

    if current is not None:
        entries.append(current)

    return entries


def parse_log_with_numstat(log_text: str) -> List[dict]:
    """Parse ``git log --format='COMMIT:%H|%at|%an|%s' --numstat``.

    Returns [{hash, timestamp, author, message,
              files: [{path, insertions, deletions}]}].
    """
    entries: List[dict] = []
    current: Optional[dict] = None

    for line in log_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("COMMIT:"):
            if current is not None:
                entries.append(current)
            parts = line[len("COMMIT:"):].split("|", 3)
            if len(parts) < 4:
                current = None
                continue
            current = {
                "hash": parts[0],
                "timestamp": int(parts[1]) if parts[1].isdigit() else 0,
                "author": parts[2],
                "message": parts[3],
                "files": [],
            }
        elif current is not None:
            # numstat lines: "insertions\tdeletions\tpath"
            numstat_match = re.match(r"^(\d+|-)\t(\d+|-)\t(.+)$", line)
            if numstat_match:
                ins = numstat_match.group(1)
                dels = numstat_match.group(2)
                current["files"].append({
                    "path": numstat_match.group(3),
                    "insertions": int(ins) if ins != "-" else 0,
                    "deletions": int(dels) if dels != "-" else 0,
                })

    if current is not None:
        entries.append(current)

    return entries


def get_cached_log(git_root: str, extra_args: tuple) -> List[dict]:
    """Parsed log entries with a module-level TTL cache."""
    global _log_cache, _log_cache_ts

    now = time.monotonic()
    if now - _log_cache_ts > _LOG_CACHE_TTL:
        _log_cache.clear()
        _log_cache_ts = now

    key = (git_root, extra_args)
    if key in _log_cache:
        return _log_cache[key]

    args = ["log", '--format=COMMIT:%H|%at|%an|%s', "--name-only"] + list(extra_args)
    raw = run_git(args, cwd=git_root)
    entries = parse_log_with_files(raw)
    _log_cache[key] = entries
    return entries
