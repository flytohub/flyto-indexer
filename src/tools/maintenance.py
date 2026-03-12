"""Maintenance tools — dead code, TODOs, index status, reindex, sessions."""

import json
import os
import re
from datetime import datetime
from pathlib import Path

try:
    from ..index_store import (
        INDEX_DIR, load_index, load_project_map, get_symbol_content_text,
        invalidate_caches, _get_session_store,
    )
except ImportError:
    from index_store import (
        INDEX_DIR, load_index, load_project_map, get_symbol_content_text,
        invalidate_caches, _get_session_store,
    )

try:
    from .search import _TODO_PATTERNS
except ImportError:
    from tools.search import _TODO_PATTERNS


def _build_reference_sets(dependencies):
    """Extract referenced names, imported files, and referenced classes from dependencies."""
    referenced_names = set()
    imported_files = set()
    referenced_classes = set()

    for _dep_id, dep in dependencies.items():
        dep_type = dep.get("type", "")
        if dep_type == "imports":
            names = dep.get("metadata", {}).get("names", [])
            for name in names:
                referenced_names.add(name)
                if name and name[0].isupper():
                    referenced_classes.add(name)
            target = dep.get("target", "")
            if target:
                imported_files.add(target)
                basename = target.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                imported_files.add(basename)
                if "." in target:
                    last_part = target.rsplit(".", 1)[-1]
                    imported_files.add(last_part)
        elif dep_type == "calls":
            target = dep.get("target", "")
            if target and not target.startswith("__"):
                referenced_names.add(target)
                parts = target.split(".")
                for part in parts:
                    if part and len(part) > 2:
                        referenced_names.add(part)
                        if part[0].isupper():
                            referenced_classes.add(part)

    return referenced_names, imported_files, referenced_classes


def _is_potentially_dead(sym_id, sym, referenced_names, imported_files,
                         referenced_classes, dependencies, symbols,
                         _same_file_content_cache):
    """Return True if the symbol should be considered dead code."""
    sym_type = sym.get("type", "")
    sym_name = sym.get("name", "")
    sym_project = sym_id.split(":")[0] if ":" in sym_id else ""
    sym_path = sym.get("path", "")

    should_be_referenced = {"function", "method", "composable", "component", "class"}
    entry_point_patterns = [
        "main", "index", "app", "App", "Main",
        "__init__", "setup", "teardown",
        "test_", "Test", "_test",
        "register", "init", "configure",
        "handle", "route", "endpoint",
        "do_GET", "do_POST", "do_PUT", "do_DELETE",
        "do_HEAD", "do_OPTIONS", "do_PATCH",
    ]
    lifecycle_methods = {
        "created", "mounted", "updated", "destroyed",
        "beforeCreate", "beforeMount", "beforeUpdate", "beforeDestroy",
        "onMounted", "onUnmounted", "onUpdated",
        "componentDidMount", "componentWillUnmount", "render",
        "setup", "data", "computed", "methods", "watch",
    }

    if sym_type not in should_be_referenced:
        return False
    is_entry_point = any(p in sym_name for p in entry_point_patterns)
    if is_entry_point:
        return False
    if sym_name in lifecycle_methods:
        return False
    if sym_type == "function" and sym_path.endswith(".vue"):
        return False
    if sym_name.startswith("_") and not sym_name.startswith("__"):
        return False
    if sym_name in referenced_names:
        return False

    if sym_type == "method" and "." in sym_name:
        method_only = sym_name.split(".")[-1]
        if method_only in referenced_names:
            return False
        class_name = sym_name.split(".")[0]
        if class_name in referenced_classes or class_name in referenced_names:
            return False
    if sym_type == "class" and sym_name in referenced_classes:
        return False

    file_basename = sym_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if file_basename in imported_files or sym_path in imported_files:
        return False

    if sym_type == "composable":
        is_imported = False
        for dep in dependencies.values():
            if dep.get("type") == "imports":
                target = dep.get("target", "")
                names = dep.get("metadata", {}).get("names", [])
                target_basename = target.rsplit("/", 1)[-1].rsplit(".", 1)[0] if target else ""
                if target_basename == file_basename or target_basename == sym_name:
                    is_imported = True
                    break
                if sym_name in names:
                    is_imported = True
                    break
        if is_imported:
            return False

    if sym_type in ("class", "component") and file_basename == sym_name:
        is_imported = False
        for dep in dependencies.values():
            if dep.get("type") == "imports":
                target = dep.get("target", "")
                if sym_name in target or file_basename in target:
                    is_imported = True
                    break
        if is_imported:
            return False

    # Check for dict/list dispatch patterns: the symbol name may
    # appear as a bare reference (dict value, list element, callback
    # assignment) inside another symbol in the same file.
    bare_name = sym_name.split(".")[-1] if "." in sym_name else sym_name
    if bare_name and len(bare_name) > 2:
        file_key = f"{sym_project}:{sym_path}"
        if file_key not in _same_file_content_cache:
            parts = []
            for other_id, other_sym in symbols.items():
                other_proj = other_id.split(":")[0] if ":" in other_id else ""
                if other_sym.get("path", "") == sym_path and other_proj == sym_project:
                    text = get_symbol_content_text(other_id, other_sym)
                    if text:
                        parts.append((other_id, text))
            _same_file_content_cache[file_key] = parts

        name_pat = re.compile(r'\b' + re.escape(bare_name) + r'\b')
        for other_id, text in _same_file_content_cache[file_key]:
            if other_id == sym_id:
                continue
            if name_pat.search(text):
                return False

    return True


def find_dead_code(project=None, symbol_type=None, min_lines=5):
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    referenced_names, imported_files, referenced_classes = _build_reference_sets(dependencies)

    dead_code = []
    _same_file_content_cache = {}

    for sym_id, sym in symbols.items():
        sym_type = sym.get("type", "")
        sym_name = sym.get("name", "")
        sym_project = sym_id.split(":")[0] if ":" in sym_id else ""
        sym_path = sym.get("path", "")

        if project and project.lower() not in sym_project.lower():
            continue
        if symbol_type and sym_type != symbol_type:
            continue

        lines = sym.get("end_line", 0) - sym.get("start_line", 0)
        if lines < min_lines:
            continue

        ref_count = sym.get("ref_count", 0)
        callers = reverse_index.get(sym_id, [])
        if ref_count > 0 or len(callers) > 0:
            continue

        if not _is_potentially_dead(sym_id, sym, referenced_names, imported_files,
                                    referenced_classes, dependencies, symbols,
                                    _same_file_content_cache):
            continue

        dead_code.append({
            "symbol_id": sym_id,
            "name": sym_name,
            "type": sym_type,
            "path": sym_path,
            "project": sym_project,
            "lines": lines,
            "start_line": sym.get("start_line", 0),
        })

    dead_code.sort(key=lambda x: x["lines"], reverse=True)

    by_project = {}
    for item in dead_code:
        proj = item["project"]
        if proj not in by_project:
            by_project[proj] = []
        by_project[proj].append(item)

    total_dead_lines = sum(item["lines"] for item in dead_code)

    if dead_code:
        largest = dead_code[0]
        next_action = f"Largest dead symbol: {largest['name']} ({largest['lines']} lines) at {largest['path']}:{largest.get('start_line', 0)}. Use get_symbol_content to review before removing."
    else:
        next_action = "Codebase is clean — no dead code detected."

    return {
        "total": len(dead_code),
        "total_dead": len(dead_code),
        "total_dead_lines": total_dead_lines,
        "by_project": {k: len(v) for k, v in by_project.items()},
        "dead_symbols": dead_code[:20],
        "top_20": dead_code[:20],
        "suggestion": f"Found {len(dead_code)} unreferenced symbols, {total_dead_lines} total lines of code that can be considered for removal.",
        "next_action": next_action,
    }


def find_todos(project=None, priority=None, max_results=100):
    """
    Find all TODO, FIXME, HACK, XXX markers.

    Helps track technical debt and pending items.
    """
    index = load_index()
    symbols = index.get("symbols", {})

    patterns = _TODO_PATTERNS

    todos = []
    seen_files = set()

    for sym_id, sym in symbols.items():
        sym_project = sym_id.split(":")[0] if ":" in sym_id else ""
        sym_path = sym.get("path", "")

        # Filter by project
        if project and project.lower() not in sym_project.lower():
            continue

        # Process each file only once
        file_key = f"{sym_project}:{sym_path}"
        if file_key in seen_files:
            continue
        seen_files.add(file_key)

        content = get_symbol_content_text(sym_id, sym)
        if not content:
            continue

        for tag, (pattern, tag_priority) in patterns.items():
            if priority and priority != tag_priority:
                continue

            for match in pattern.finditer(content):
                # Get matching text
                text = match.group(1) or match.group(2) or match.group(3) or ""
                text = text.strip()[:100]  # Limit length

                # Calculate line number
                line_num = content[:match.start()].count('\n') + sym.get("start_line", 1)

                todos.append({
                    "tag": tag,
                    "priority": tag_priority,
                    "text": text,
                    "path": sym_path,
                    "project": sym_project,
                    "line": line_num,
                })

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    todos.sort(key=lambda x: (priority_order.get(x["priority"], 9), x["project"], x["path"]))

    # Statistics
    by_priority = {"high": 0, "medium": 0, "low": 0}
    by_project = {}
    by_tag = {}

    for todo in todos:
        by_priority[todo["priority"]] = by_priority.get(todo["priority"], 0) + 1
        by_project[todo["project"]] = by_project.get(todo["project"], 0) + 1
        by_tag[todo["tag"]] = by_tag.get(todo["tag"], 0) + 1

    return {
        "total": len(todos),
        "by_priority": by_priority,
        "by_project": by_project,
        "by_tag": by_tag,
        "todos": todos[:max_results],
        "has_more": len(todos) > max_results,
    }


def check_index_status():
    """
    Check if the index is stale and needs to be updated.

    Compares file modification times and hashes with indexed data.
    """
    index = load_index()
    load_project_map()

    # Get index metadata
    index_file = INDEX_DIR / "index.json"
    if not index_file.exists():
        return {
            "status": "missing",
            "message": "Index does not exist. Run 'python index_all.py' to create it.",
            "indexed_at": None,
        }

    index_mtime = datetime.fromtimestamp(index_file.stat().st_mtime)
    indexed_at = index.get("indexed_at", "")

    # Get project roots from index (set by index_all.py)
    projects = index.get("projects", [])
    project_roots = index.get("project_roots", {})

    # Sample check: look at a few files from each project
    stale_files = []
    checked_count = 0
    total_symbols = len(index.get("symbols", {}))

    # Group symbols by project
    by_project = {}
    for sym_id, sym in index.get("symbols", {}).items():
        proj = sym_id.split(":")[0] if ":" in sym_id else ""
        if proj not in by_project:
            by_project[proj] = []
        by_project[proj].append(sym)

    # Check sample files from each project
    for proj, proj_symbols in by_project.items():
        if proj not in project_roots:
            continue

        root = project_roots[proj]
        if not os.path.exists(root):
            continue

        # Sample up to 10 files per project
        checked_paths = set()
        for sym in proj_symbols[:50]:
            path = sym.get("path", "")
            if path in checked_paths:
                continue
            checked_paths.add(path)

            full_path = os.path.join(root, path)
            if not os.path.exists(full_path):
                stale_files.append({
                    "project": proj,
                    "path": path,
                    "reason": "file_deleted",
                })
                continue

            # Check if file was modified after index
            try:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(full_path))
                if file_mtime > index_mtime:
                    stale_files.append({
                        "project": proj,
                        "path": path,
                        "reason": "modified_after_index",
                        "file_mtime": file_mtime.isoformat(),
                    })
            except OSError:
                pass

            checked_count += 1
            if len(checked_paths) >= 10:
                break

    # Determine status
    if len(stale_files) == 0:
        status = "fresh"
        message = "Index is up to date."
    elif len(stale_files) <= 5:
        status = "slightly_stale"
        message = f"Index has {len(stale_files)} stale files. Consider re-indexing."
    else:
        status = "stale"
        message = f"Index has {len(stale_files)}+ stale files. Re-indexing recommended."

    return {
        "status": status,
        "message": message,
        "indexed_at": indexed_at,
        "index_file_mtime": index_mtime.isoformat(),
        "total_symbols": total_symbols,
        "total_projects": len(projects),
        "files_checked": checked_count,
        "stale_files": stale_files[:20],
        "stale_count": len(stale_files),
        "recommendation": "Run 'python index_all.py' to update the index." if status != "fresh" else None,
    }


def _perform_live_reindex(project=None):
    """Reindex one or all projects.

    Uses _reindex_lock to prevent concurrent reindex operations.
    If the lock is already held, skips this call and returns a
    'skipped' result instead of blocking.
    """
    try:
        from ..index_store import _reindex_lock
    except ImportError:
        from index_store import _reindex_lock

    if not _reindex_lock.acquire(blocking=False):
        return {"reindexed": 0, "errors": 0, "results": [], "skipped": True}
    try:
        return _perform_live_reindex_unlocked(project)
    finally:
        _reindex_lock.release()


def _perform_live_reindex_unlocked(project=None):
    """Internal reindex — caller must hold _reindex_lock."""
    try:
        from ..engine import IndexEngine
    except ImportError:
        from engine import IndexEngine

    index = load_index()
    project_roots = index.get("project_roots", {})
    projects = index.get("projects", [])
    target_projects = [p for p in projects if project.lower() in p.lower()] if project else projects

    reindex_results = []
    for proj in target_projects:
        root = project_roots.get(proj)
        if not root or not Path(root).exists():
            reindex_results.append({"project": proj, "error": f"Root not found: {root}"})
            continue
        try:
            engine = IndexEngine(proj, Path(root), index_dir=INDEX_DIR)
            scan_result = engine.scan(incremental=True)
            reindex_results.append({
                "project": proj,
                "files_scanned": scan_result["files_scanned"],
                "symbols_found": scan_result["symbols_found"],
                "timing": scan_result.get("timing", {}),
            })
        except Exception as e:
            reindex_results.append({"project": proj, "error": str(e)})

    invalidate_caches()
    return {
        "reindexed": len([r for r in reindex_results if "error" not in r]),
        "errors": len([r for r in reindex_results if "error" in r]),
        "results": reindex_results,
    }


def check_and_reindex(dry_run=True, project=None, auto_reindex=False):
    try:
        from ..watcher import FileWatcher
    except ImportError:
        from watcher import FileWatcher

    index = load_index()
    watcher = FileWatcher(index)
    changes = watcher.detect_changes(project=project)
    summary = watcher.get_summary(changes)

    result = {
        "dry_run": dry_run,
        "auto_reindex": auto_reindex,
        "total_changes": summary["total"],
        "by_type": summary["by_type"],
        "by_project": summary["by_project"],
        "changes": [{"path": c.path, "project": c.project, "type": c.change_type} for c in changes[:50]],
        "has_more": len(changes) > 50,
    }

    if auto_reindex and changes:
        reindex_result = _perform_live_reindex(project=project)
        result["reindex"] = reindex_result
        result["caches_cleared"] = True
        result["recommendation"] = f"Live reindex complete. {reindex_result['reindexed']} projects updated."
    elif not dry_run:
        # Always invalidate caches when dry_run=false, even with 0 changes.
        # The on-disk index may have been rebuilt externally (e.g. flyto-index scan).
        invalidate_caches()
        result["caches_cleared"] = True
        if changes:
            result["recommendation"] = "Run 'python index_all.py' to rebuild the index."
        else:
            result["recommendation"] = "Caches cleared. Index is up to date."
    elif changes:
        result["recommendation"] = "Run with auto_reindex=true for live update, or dry_run=false to clear caches."
    else:
        result["recommendation"] = "Index is up to date."

    return result


def session_track(session_id, event_type, target, workspace_root=""):
    store = _get_session_store()
    session = store.get_or_create(session_id, workspace_root)
    if event_type == "file_open":
        session.add_file(target)
    elif event_type == "query":
        session.add_query(target)
    elif event_type == "edit":
        session.add_edit(target)
    else:
        return {"error": f"Unknown event_type: {event_type}. Use: file_open, query, edit"}
    return {"ok": True, "session_id": session_id, "event_type": event_type, "target": target, "boost_paths_count": len(session.get_boost_paths())}


def session_get(session_id):
    store = _get_session_store()
    session = store.get(session_id)
    if not session:
        return {"error": f"Session not found or expired: {session_id}"}
    return session.to_dict()
