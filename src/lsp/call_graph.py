"""
LSP call-hierarchy traversal.

Uses textDocument/prepareCallHierarchy + callHierarchy/incomingCalls|outgoingCalls
to walk the real (type-aware) call graph up to a bounded depth. Returns a flat
list of edges with from/to file:line so downstream tools can merge with the
regex-built reverse_index.

Unlike the reverse_index (which is built from regex scanning and can confuse
same-named symbols across modules), call hierarchy is resolved by the language
server's semantic model — two functions both called `handle` in different
modules will not collide.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .cache import CacheKey, get_cache
from .manager import LSPManager
from .protocol import Location, path_to_uri, uri_to_path, utf16_offset

logger = logging.getLogger("flyto-indexer.lsp.call_graph")


@dataclass
class CallEdge:
    from_file: str
    from_name: str
    from_line: int
    to_file: str
    to_name: str
    to_line: int
    depth: int


def _prepare_item(
    manager: LSPManager,
    project_root: Path,
    source_file: Path,
    line_0based: int,
    col_0based: int,
) -> Optional[Tuple[object, dict]]:
    """Resolve a (file, line, col) to a CallHierarchyItem. Returns (client, item) or None."""
    language = manager.language_for_path(str(source_file))
    if not language:
        return None
    client = manager.get_client(language, str(project_root))
    if client is None:
        return None

    uri = path_to_uri(str(source_file))

    try:
        from .resolver import _ensure_open
    except ImportError:
        _ensure_open = None
    _LANG_ID = {"python": "python", "typescript": "typescript", "go": "go", "rust": "rust"}
    if _ensure_open:
        _ensure_open(client, uri, _LANG_ID.get(language, language), str(source_file))

    try:
        with open(source_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None
    lines = content.split("\n")
    if not (0 <= line_0based < len(lines)):
        return None
    utf16_col = utf16_offset(lines[line_0based], col_0based)

    try:
        items = client.text_document_prepare_call_hierarchy(uri, line_0based, utf16_col)
    except Exception as e:
        logger.debug("prepareCallHierarchy failed: %s", e)
        return None

    if not items:
        return None
    return client, items[0]


def _item_to_edge_tuple(item: dict) -> Tuple[str, int, str]:
    """Pull (file_path, line, name) out of an LSP CallHierarchyItem / CallHierarchyIncomingCall."""
    uri = item.get("uri") or ""
    rng = item.get("selectionRange") or item.get("range") or {}
    line = (rng.get("start") or {}).get("line", 0) + 1  # LSP 0-based → 1-based
    return uri_to_path(uri), line, item.get("name", "")


def incoming_calls(
    project_root: Path,
    source_file: Path,
    line_0based: int,
    col_0based: int,
    max_depth: int = 2,
    max_edges: int = 200,
) -> List[CallEdge]:
    """Return incoming-call edges up to max_depth, breadth-first.

    Bounded by max_edges to protect against pathological graphs. Cached per
    (uri, line, col, depth).
    """
    manager = LSPManager.get_instance()
    if not manager._enabled:
        return []

    cache = get_cache()
    uri = path_to_uri(str(source_file))
    cache_key = CacheKey(
        method="callHierarchy/in", uri=uri,
        line=line_0based, character=col_0based,
        extra=f"d={max_depth}",
    )
    hit = cache.get(cache_key)
    if isinstance(hit, list):
        return hit

    prepared = _prepare_item(manager, project_root, source_file, line_0based, col_0based)
    if prepared is None:
        cache.set(cache_key, [])
        return []
    client, root_item = prepared

    edges: List[CallEdge] = []
    visited: Set[Tuple[str, int]] = set()
    frontier: List[Tuple[dict, int]] = [(root_item, 0)]

    while frontier and len(edges) < max_edges:
        item, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        key = (item.get("uri", ""), (item.get("selectionRange") or {}).get("start", {}).get("line", 0))
        if key in visited:
            continue
        visited.add(key)

        try:
            incoming = client.call_hierarchy_incoming_calls(item)
        except Exception as e:
            logger.debug("incomingCalls failed: %s", e)
            break

        to_path, to_line, to_name = _item_to_edge_tuple(item)

        for call in incoming:
            if len(edges) >= max_edges:
                break
            from_item = call.get("from") or {}
            from_path, from_line, from_name = _item_to_edge_tuple(from_item)
            if not from_path:
                continue
            edges.append(CallEdge(
                from_file=from_path, from_name=from_name, from_line=from_line,
                to_file=to_path, to_name=to_name, to_line=to_line,
                depth=depth + 1,
            ))
            if depth + 1 < max_depth:
                frontier.append((from_item, depth + 1))

    cache.set(cache_key, edges)
    return edges


def outgoing_calls(
    project_root: Path,
    source_file: Path,
    line_0based: int,
    col_0based: int,
    max_depth: int = 1,
    max_edges: int = 200,
) -> List[CallEdge]:
    """Return outgoing-call edges (what this symbol calls). Single-level is usually enough."""
    manager = LSPManager.get_instance()
    if not manager._enabled:
        return []

    cache = get_cache()
    uri = path_to_uri(str(source_file))
    cache_key = CacheKey(
        method="callHierarchy/out", uri=uri,
        line=line_0based, character=col_0based,
        extra=f"d={max_depth}",
    )
    hit = cache.get(cache_key)
    if isinstance(hit, list):
        return hit

    prepared = _prepare_item(manager, project_root, source_file, line_0based, col_0based)
    if prepared is None:
        cache.set(cache_key, [])
        return []
    client, root_item = prepared

    edges: List[CallEdge] = []
    visited: Set[Tuple[str, int]] = set()
    frontier: List[Tuple[dict, int]] = [(root_item, 0)]

    while frontier and len(edges) < max_edges:
        item, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        key = (item.get("uri", ""), (item.get("selectionRange") or {}).get("start", {}).get("line", 0))
        if key in visited:
            continue
        visited.add(key)

        try:
            outgoing = client.call_hierarchy_outgoing_calls(item)
        except Exception as e:
            logger.debug("outgoingCalls failed: %s", e)
            break

        from_path, from_line, from_name = _item_to_edge_tuple(item)

        for call in outgoing:
            if len(edges) >= max_edges:
                break
            to_item = call.get("to") or {}
            to_path, to_line, to_name = _item_to_edge_tuple(to_item)
            if not to_path:
                continue
            edges.append(CallEdge(
                from_file=from_path, from_name=from_name, from_line=from_line,
                to_file=to_path, to_name=to_name, to_line=to_line,
                depth=depth + 1,
            ))
            if depth + 1 < max_depth:
                frontier.append((to_item, depth + 1))

    cache.set(cache_key, edges)
    return edges
