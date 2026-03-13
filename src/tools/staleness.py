"""Staleness detection — identify stale but heavily-referenced symbols via git."""

import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("flyto-indexer.staleness")

try:
    from ..index_store import load_index
    from .git_intel import _find_git_root, _get_project_root
except ImportError:
    from index_store import load_index
    from tools.git_intel import _find_git_root, _get_project_root


def _run_git_log_for_file(git_root: str, file_path: str, timeout: int = 10) -> List[dict]:
    """Get commit history for a specific file."""
    try:
        result = subprocess.run(
            ["git", "log", "--follow", "--format=%H|%aI|%an", "--", file_path],
            cwd=git_root, capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return []
        entries = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                entries.append({
                    "hash": parts[0],
                    "date": parts[1],
                    "author": parts[2],
                })
        return entries
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _get_file_last_modified(git_root: str, file_path: str) -> Optional[dict]:
    """Get the most recent commit info for a file."""
    entries = _run_git_log_for_file(git_root, file_path, timeout=5)
    return entries[0] if entries else None


def find_stale_symbols(
    project: Optional[str] = None,
    stale_days: int = 180,
    min_refs: int = 3,
    max_results: int = 20,
) -> dict:
    """Find symbols that are heavily referenced but haven't been modified recently.

    These are potential maintenance risks — widely used code that may be outdated.

    Args:
        project: Filter to specific project
        stale_days: Days since last modification to consider stale (default 180)
        min_refs: Minimum reference count to consider (default 3)
        max_results: Maximum results (default 20)

    Returns:
        {stale_symbols: [{symbol_id, name, path, refs, days_since_modified, last_author}]}
    """
    try:
        proj_name, proj_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    git_root = _find_git_root(proj_root)
    if not git_root:
        return {"error": f"No git repository found for {proj_name}"}

    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})

    git_root_abs = os.path.abspath(git_root)
    proj_root_abs = os.path.abspath(proj_root)
    proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
    if proj_prefix == ".":
        proj_prefix = ""

    now = datetime.now(timezone.utc)

    # Cache file modification dates to avoid repeated git calls
    file_mod_cache: Dict[str, Optional[dict]] = {}

    candidates = []
    for sym_id, sym in symbols.items():
        if project and not sym_id.lower().startswith(project.lower()):
            continue

        # Only check referenced symbols
        ref_count = sym.get("ref_count", 0)
        callers = reverse_index.get(sym_id, [])
        total_refs = ref_count + len(callers)
        if total_refs < min_refs:
            continue

        # Skip test files
        path = sym.get("path", "")
        if not path or "test" in path.lower():
            continue

        # Get file modification date (cached)
        if path not in file_mod_cache:
            git_path = os.path.join(proj_prefix, path) if proj_prefix else path
            file_mod_cache[path] = _get_file_last_modified(git_root, git_path)

        mod_info = file_mod_cache[path]
        if not mod_info:
            continue

        try:
            mod_date = datetime.fromisoformat(mod_info["date"].replace("Z", "+00:00"))
            days_since = (now - mod_date).days
        except (ValueError, TypeError):
            continue

        if days_since >= stale_days:
            candidates.append({
                "symbol_id": sym_id,
                "name": sym.get("name", ""),
                "type": sym.get("type", ""),
                "path": path,
                "refs": total_refs,
                "days_since_modified": days_since,
                "last_modified": mod_info["date"][:10],
                "last_author": mod_info["author"],
            })

    # Sort by refs * staleness (highest risk first)
    candidates.sort(key=lambda c: c["refs"] * c["days_since_modified"], reverse=True)
    candidates = candidates[:max_results]

    return {
        "stale_symbols": candidates,
        "threshold_days": stale_days,
        "min_refs": min_refs,
        "total_found": len(candidates),
        "files_checked": len(file_mod_cache),
    }


def enrich_with_freshness(results: list, project: Optional[str] = None) -> list:
    """Add freshness metadata to search/impact results.

    Adds `freshness` field to each result dict:
    - "fresh": modified within 30 days
    - "recent": 30-90 days
    - "aging": 90-180 days
    - "stale": >180 days
    - None: if git info unavailable

    Args:
        results: List of result dicts with "path" field
        project: Project name for git root resolution

    Returns:
        Same list with freshness field added to each entry
    """
    if not results:
        return results

    try:
        _proj_name, proj_root = _get_project_root(project)
    except ValueError:
        return results

    git_root = _find_git_root(proj_root)
    if not git_root:
        return results

    git_root_abs = os.path.abspath(git_root)
    proj_root_abs = os.path.abspath(proj_root)
    proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
    if proj_prefix == ".":
        proj_prefix = ""

    now = datetime.now(timezone.utc)
    file_mod_cache: Dict[str, Optional[dict]] = {}

    for item in results:
        path = item.get("path", "")
        if not path:
            continue

        if path not in file_mod_cache:
            git_path = os.path.join(proj_prefix, path) if proj_prefix else path
            file_mod_cache[path] = _get_file_last_modified(git_root, git_path)

        mod_info = file_mod_cache[path]
        if not mod_info:
            continue

        try:
            mod_date = datetime.fromisoformat(mod_info["date"].replace("Z", "+00:00"))
            days = (now - mod_date).days
        except (ValueError, TypeError):
            continue

        if days <= 30:
            item["freshness"] = "fresh"
        elif days <= 90:
            item["freshness"] = "recent"
        elif days <= 180:
            item["freshness"] = "aging"
        else:
            item["freshness"] = "stale"
        item["days_since_modified"] = days

    return results
