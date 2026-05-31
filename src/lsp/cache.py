"""
LSP response cache — mtime-keyed, session-scoped.

LSP cold start (pyright ~2-5s, gopls ~1-3s) plus per-request latency makes
repeated calls over the same file expensive. This cache memoizes results
per (uri, method, position) and invalidates automatically when the file's
mtime changes.

Session-scoped: cleared when LSPManager.reset_instance() runs, or on
explicit clear(). NOT persisted to disk.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("flyto-indexer.lsp.cache")


@dataclass(frozen=True)
class CacheKey:
    method: str
    uri: str
    line: int
    character: int
    extra: str = ""  # optional discriminator (e.g., call hierarchy depth)


class LSPResponseCache:
    """In-memory cache keyed by (method, uri, position, file_mtime)."""

    def __init__(self, max_entries: int = 4096):
        self._entries: Dict[Tuple[CacheKey, float], Any] = {}
        self._max_entries = max_entries

    def _mtime(self, uri: str) -> float:
        """Best-effort mtime lookup; returns 0.0 if file not readable."""
        if uri.startswith("file://"):
            path = uri[len("file:///") if uri.startswith("file:///") and os.name == "nt" else len("file://"):]
        else:
            path = uri
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def get(self, key: CacheKey) -> Optional[Any]:
        mtime = self._mtime(key.uri)
        return self._entries.get((key, mtime))

    def set(self, key: CacheKey, value: Any) -> None:
        if len(self._entries) >= self._max_entries:
            # Cheap LRU-ish: drop 10% oldest insertion order
            drop = max(1, self._max_entries // 10)
            for k in list(self._entries.keys())[:drop]:
                self._entries.pop(k, None)
        mtime = self._mtime(key.uri)
        self._entries[(key, mtime)] = value

    def clear(self) -> None:
        self._entries.clear()

    def size(self) -> int:
        return len(self._entries)


_global_cache: Optional[LSPResponseCache] = None


def get_cache() -> LSPResponseCache:
    global _global_cache
    if _global_cache is None:
        _global_cache = LSPResponseCache()
    return _global_cache


def clear_cache() -> None:
    global _global_cache
    if _global_cache is not None:
        _global_cache.clear()
