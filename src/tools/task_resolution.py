"""Exact target resolution for task analysis.

Fuzzy search may discover candidates, but edit authority is granted only by exact
identity or exact bounded path evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

try:
    from .search import search_by_keyword
    from ..index_store import load_index
except ImportError:
    from tools.search import search_by_keyword
    from index_store import load_index


_PATH_EXTENSIONS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".rs",
    ".java", ".kt", ".rb", ".cpp", ".c", ".h", ".cs", ".swift",
)

def _looks_like_path(target: str) -> bool:
    normalized = target.replace("\\", "/")
    return (
        normalized.startswith(("/", "./", "../", "~/"))
        or "/" in normalized
        or any(normalized.endswith(ext) for ext in _PATH_EXTENSIONS)
    )

def _normalize_path_text(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized

def _relative_target_path(target: str, project: str, project_roots: dict) -> Optional[str]:
    if not target:
        return None

    normalized = _normalize_path_text(target)
    if not Path(target).expanduser().is_absolute():
        return normalized

    target_path = Path(target).expanduser().resolve(strict=False)
    root_value = project_roots.get(project) if project else None
    if not root_value:
        return None

    root_path = Path(root_value).expanduser().resolve(strict=False)
    try:
        return _normalize_path_text(str(target_path.relative_to(root_path)))
    except ValueError:
        return None

def _symbol_path_matches_target(sym_path: str, target: str, relative_target: Optional[str]) -> bool:
    if not sym_path:
        return False

    normalized_symbol_path = _normalize_path_text(sym_path)
    normalized_target = _normalize_path_text(target)
    if relative_target and normalized_symbol_path == relative_target:
        return True
    if normalized_symbol_path == normalized_target:
        return True
    return Path(target).expanduser().is_absolute() and normalized_target.endswith(
        "/" + normalized_symbol_path
    )

def _symbol_sort_key(item: tuple) -> tuple:
    sid, sym = item
    return (
        0 if sym.get("type") == "file" else 1,
        sym.get("start_line") or 0,
        sid,
    )

def _append_resolved_target(
    resolved: List[dict],
    seen_ids: set,
    target: str,
    sid: str,
    sym: dict,
) -> bool:
    if sid in seen_ids:
        return False
    resolved.append({
        "input": target,
        "symbol_id": sid,
        "name": sym.get("name", ""),
        "type": sym.get("type", ""),
        "path": sym.get("path", ""),
    })
    seen_ids.add(sid)
    return True

def _is_exact_task_target_match(target: str, item: dict) -> bool:
    """Require exact identity before a search hit can grant edit authority."""
    requested = target.strip().casefold()
    if not requested:
        return False
    symbol_id = str(item.get("symbol_id") or "")
    identities = {
        str(item.get("name") or "").strip().casefold(),
        symbol_id.rsplit(":", 1)[-1].strip().casefold(),
    }
    identities.discard("")
    return requested in identities

def _resolve_exact_search_target(
    target: str,
    project: str | None,
    seen_ids: set[str],
    *,
    search_fn=search_by_keyword,
) -> dict | None:
    """Return the first exact-identity search hit not already resolved."""
    results = search_fn(target, max_results=5, project=project)
    for item in results.get("results", []):
        if not _is_exact_task_target_match(target, item):
            continue
        symbol_id = item.get("symbol_id", "")
        if symbol_id and symbol_id not in seen_ids:
            return {
                "input": target,
                "symbol_id": symbol_id,
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "path": item.get("path", ""),
            }
    return None

def _resolve_targets(
    targets: List[str],
    project: str = None,
    *,
    load_index_fn=load_index,
    search_fn=search_by_keyword,
) -> List[dict]:
    """Resolve target names to symbol IDs.

    Explicit file paths are resolved by path only and do not fall back to
    semantic search. Returning unknown is safer than planning against a
    same-named symbol in the wrong file or project.
    """
    resolved = []
    seen_ids = set()
    index = load_index_fn()
    symbols = index.get("symbols", {})
    project_roots = index.get("project_roots", {})

    for target in targets:
        # Try as exact symbol_id first
        if target in symbols:
            _append_resolved_target(resolved, seen_ids, target, target, symbols[target])
            continue

        # Try exact file path matching before any keyword or semantic lookup.
        is_path = _looks_like_path(target)
        if is_path:
            candidates = []
            project_names = (
                [project] if project else list(project_roots.keys())
            ) or [None]

            for sid, sym in sorted(symbols.items(), key=_symbol_sort_key):
                if project and not sid.lower().startswith(project.lower() + ":"):
                    continue
                for project_name in project_names:
                    relative_target = _relative_target_path(target, project_name, project_roots)
                    if _symbol_path_matches_target(sym.get("path", ""), target, relative_target):
                        candidates.append((sid, sym))
                        break

            if candidates:
                sid, sym = candidates[0]
                _append_resolved_target(resolved, seen_ids, target, sid, sym)
                continue

            resolved.append({
                "input": target,
                "symbol_id": None,
                "name": target,
                "type": "unknown",
                "path": "",
            })
            continue

        # Search by keyword (with project filter)
        matched = _resolve_exact_search_target(
            target, project, seen_ids, search_fn=search_fn
        )
        if matched is None:
            resolved.append({
                "input": target,
                "symbol_id": None,
                "name": target,
                "type": "unknown",
                "path": "",
            })
        else:
            resolved.append(matched)
            seen_ids.add(matched["symbol_id"])

    return resolved

