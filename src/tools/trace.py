"""Call path tracing — find execution paths through the call graph."""

import logging
from collections import deque
from typing import Optional

logger = logging.getLogger("flyto-indexer.trace")

try:
    from ..index_store import load_index
except ImportError:
    from index_store import load_index


# Entry point patterns (functions that are call-graph roots)
_ENTRY_PATTERNS = {
    "main", "app", "index", "__init__", "setup", "configure",
    "handle", "route", "endpoint", "cli",
    "do_GET", "do_POST", "do_PUT", "do_DELETE",
}
_ENTRY_PREFIXES = ("test_", "handle_", "on_", "route_")
_ENTRY_TYPES = {"api", "route", "component"}


def _is_entry_point(sym_id: str, sym: dict) -> bool:
    """Check if a symbol is an entry point (call-graph root)."""
    name = sym.get("name", "")
    bare = name.split(".")[-1] if "." in name else name
    if bare in _ENTRY_PATTERNS:
        return True
    if any(bare.startswith(p) for p in _ENTRY_PREFIXES):
        return True
    if sym.get("type", "") in _ENTRY_TYPES:
        return True
    # Exported functions with no callers are de-facto entry points
    return bool(sym.get("exports") and sym.get("ref_count", 0) == 0)


def trace_paths(
    target: str,
    direction: str = "up",
    max_depth: int = 8,
    max_paths: int = 10,
    project: Optional[str] = None,
) -> dict:
    """Trace call paths to/from a symbol.

    Args:
        target: Symbol ID or name to trace
        direction: "up" (entry→target) or "down" (target→leaves)
        max_depth: Maximum path depth (default 8)
        max_paths: Maximum paths to return (default 10)
        project: Filter to specific project

    Returns:
        {target, direction, paths: [{path: [sym_ids], entry_point, depth}],
         summary: {total_paths, max_depth, entry_points}}
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    # Resolve target to symbol ID(s)
    target_ids = _resolve_target(target, symbols, project)
    if not target_ids:
        return {"error": f"Symbol not found: {target}", "target": target}

    # Build adjacency for "down" direction (caller → callees)
    callee_map = {}  # symbol_id → [callee_ids]
    if direction == "down":
        callee_map = _build_callee_map(dependencies, symbols)

    all_paths = []
    for tid in target_ids[:3]:  # limit to 3 target matches
        if direction == "up":
            paths = _trace_up(tid, reverse_index, symbols, max_depth, max_paths - len(all_paths))
        else:
            paths = _trace_down(tid, callee_map, symbols, max_depth, max_paths - len(all_paths))
        all_paths.extend(paths)
        if len(all_paths) >= max_paths:
            break

    all_paths = all_paths[:max_paths]

    # Build summary
    entry_points = set()
    max_d = 0
    for p in all_paths:
        if p.get("entry_point"):
            entry_points.add(p["path"][0] if direction == "up" else p["path"][-1])
        max_d = max(max_d, p.get("depth", 0))

    # Annotate paths with symbol names for readability
    for p in all_paths:
        p["names"] = [_sym_name(sid, symbols) for sid in p["path"]]

    return {
        "target": target,
        "resolved_ids": target_ids[:3],
        "direction": direction,
        "paths": all_paths,
        "summary": {
            "total_paths": len(all_paths),
            "max_depth": max_d,
            "entry_points": list(entry_points)[:10],
        },
    }


def _resolve_target(target: str, symbols: dict, project: Optional[str]) -> list:
    """Resolve target string to symbol IDs."""
    # Exact match
    if target in symbols:
        return [target]
    # Name match
    matches = []
    target_lower = target.lower()
    for sid, sym in symbols.items():
        if project and not sid.lower().startswith(project.lower()):
            continue
        name = sym.get("name", "")
        if name == target or name.split(".")[-1] == target or name.lower() == target_lower:
            matches.append(sid)
    # Sort by reference count (most referenced first)
    matches.sort(key=lambda s: symbols.get(s, {}).get("ref_count", 0), reverse=True)
    return matches[:5]


def _sym_name(sid: str, symbols: dict) -> str:
    """Get readable name for a symbol ID."""
    sym = symbols.get(sid, {})
    name = sym.get("name", sid.split(":")[-1] if ":" in sid else sid)
    path = sym.get("path", "")
    if path:
        fname = path.rsplit("/", 1)[-1]
        return f"{fname}:{name}"
    return name


def _build_callee_map(dependencies: dict, symbols: dict) -> dict:
    """Build caller → [callees] map from dependencies."""
    callee_map = {}
    for _dep_id, dep in dependencies.items():
        if dep.get("type", "") != "calls":
            continue
        source = dep.get("source", "")
        resolved = dep.get("metadata", {}).get("resolved_target", "")
        if source and resolved and resolved in symbols:
            if source not in callee_map:
                callee_map[source] = []
            callee_map[source].append(resolved)
    return callee_map


def _trace_up(target_id: str, reverse_index: dict, symbols: dict,
              max_depth: int, max_paths: int) -> list:
    """BFS upward from target to entry points via reverse_index."""
    paths = []
    # BFS: queue of (current_path, visited)
    queue = deque([([target_id], {target_id})])

    while queue and len(paths) < max_paths:
        current_path, visited = queue.popleft()
        head = current_path[-1]

        sym = symbols.get(head, {})
        if len(current_path) > 1 and _is_entry_point(head, sym):
            # Found a path from entry point to target
            paths.append({
                "path": list(reversed(current_path)),  # entry → target order
                "entry_point": head,
                "depth": len(current_path) - 1,
            })
            continue

        if len(current_path) >= max_depth:
            # Reached max depth without finding entry point — still record
            paths.append({
                "path": list(reversed(current_path)),
                "entry_point": None,
                "depth": len(current_path) - 1,
                "truncated": True,
            })
            continue

        # Expand: who calls head?
        callers = reverse_index.get(head, [])
        if not callers:
            # Dead end — record path to root
            if len(current_path) > 1:
                paths.append({
                    "path": list(reversed(current_path)),
                    "entry_point": head,
                    "depth": len(current_path) - 1,
                })
            continue

        for caller in callers[:10]:  # limit branching factor
            if caller not in visited:
                new_visited = visited | {caller}
                queue.append((current_path + [caller], new_visited))

    return paths


def _trace_down(target_id: str, callee_map: dict, symbols: dict,
                max_depth: int, max_paths: int) -> list:
    """BFS downward from target to leaf calls via callee_map."""
    paths = []
    queue = deque([([target_id], {target_id})])

    while queue and len(paths) < max_paths:
        current_path, visited = queue.popleft()
        tail = current_path[-1]

        callees = callee_map.get(tail, [])
        if not callees or len(current_path) >= max_depth:
            if len(current_path) > 1:
                paths.append({
                    "path": current_path,
                    "leaf": tail,
                    "depth": len(current_path) - 1,
                    "truncated": len(current_path) >= max_depth and bool(callees),
                })
            continue

        for callee in callees[:10]:
            if callee not in visited:
                new_visited = visited | {callee}
                queue.append((current_path + [callee], new_visited))

    return paths
