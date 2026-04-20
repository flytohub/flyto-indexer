"""
LSP-backed import resolver.

Given a source file + import-statement line, asks the language server where
the imported symbol is actually defined and returns the project-local file.
Falls back to None when the LSP is unavailable or the target is external.

Used as a precision layer on top of the regex / alias / go.mod heuristics in
analyzer/layers.py and analyzer/taint.py. When LSP is available it catches
path aliases the static heuristic missed (e.g., complex tsconfig paths,
Python namespace packages, gopls vendor resolution).
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from .cache import CacheKey, get_cache
from .manager import LSPManager
from .protocol import path_to_uri, uri_to_path, utf16_offset

logger = logging.getLogger("flyto-indexer.lsp.resolver")


_LANG_ID = {
    "python": "python",
    "typescript": "typescript",
    "go": "go",
    "rust": "rust",
}


@dataclass
class _OpenedFiles:
    """Per-client memoization of did_open calls to avoid redundant notifications."""
    opened: Dict[str, bool]


# Module-level memo, keyed by id(client) — cleared when client is reset.
_opened_by_client: Dict[int, _OpenedFiles] = {}


def _ensure_open(client, uri: str, language_id: str, abs_path: str) -> None:
    memo = _opened_by_client.setdefault(id(client), _OpenedFiles(opened={}))
    if memo.opened.get(uri):
        return
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return
    try:
        client.did_open(uri, language_id, text)
        memo.opened[uri] = True
    except Exception as e:
        logger.debug("did_open failed for %s: %s", uri, e)


def _find_column(line_content: str, needle: str) -> Optional[int]:
    """Return a safe 0-based column placed in the middle of `needle` inside the line."""
    if not needle:
        return None
    # Strip a leading '.' so we can still match Python relative imports
    needle_stripped = needle.lstrip(".")
    if needle_stripped and needle_stripped in line_content:
        idx = line_content.find(needle_stripped)
        return idx + max(0, len(needle_stripped) // 2)
    if needle in line_content:
        idx = line_content.find(needle)
        return idx + max(0, len(needle) // 2)
    return None


def _uri_to_project_path(uri: str, project_root: Path) -> Optional[Path]:
    try:
        abs_target = Path(uri_to_path(uri)).resolve()
        abs_root = project_root.resolve()
        if abs_target == abs_root:
            return abs_target
        # Must be inside the project
        abs_target.relative_to(abs_root)
        return abs_target
    except (ValueError, OSError):
        return None


def resolve_import_via_lsp(
    project_root: Path,
    source_file: Path,
    line_content: str,
    line_num_0based: int,
    needle: str,
) -> Optional[Path]:
    """Ask the LSP server where `needle` (import path or symbol) resolves to.

    Returns an absolute Path within project_root, or None if unavailable.
    Safe to call when LSP is disabled — returns None cleanly.
    """
    manager = LSPManager.get_instance()
    if not manager._enabled:
        return None

    language = manager.language_for_path(str(source_file))
    if not language:
        return None

    client = manager.get_client(language, str(project_root))
    if client is None:
        return None

    col = _find_column(line_content, needle)
    if col is None:
        return None

    # LSP uses UTF-16 offsets; convert
    utf16_col = utf16_offset(line_content, col)

    uri = path_to_uri(str(source_file))

    # Cache check
    cache = get_cache()
    key = CacheKey(method="definition", uri=uri, line=line_num_0based, character=utf16_col)
    hit = cache.get(key)
    if hit is not None:
        # None or Path both valid cached values; use a sentinel
        return hit if isinstance(hit, Path) else None

    _ensure_open(client, uri, _LANG_ID.get(language, language), str(source_file))

    try:
        locations = client.text_document_definition(uri, line_num_0based, utf16_col)
    except Exception as e:
        logger.debug("LSP definition failed: %s", e)
        cache.set(key, False)  # cache negative
        return None

    for loc in locations:
        resolved = _uri_to_project_path(loc.uri, project_root)
        if resolved is not None and resolved != source_file.resolve():
            cache.set(key, resolved)
            return resolved

    cache.set(key, False)
    return None


def lsp_available_for(path: str) -> bool:
    """Quick check for callers deciding whether to attempt LSP at all."""
    manager = LSPManager.get_instance()
    if not manager._enabled:
        return False
    lang = manager.language_for_path(path)
    if not lang:
        return False
    return lang in manager.detect_available()


def clear_open_memo() -> None:
    """Clear the did_open memo — call when LSPManager is reset."""
    _opened_by_client.clear()
