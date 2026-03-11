"""
Index store — centralized index loading, caching, and content management.

This module owns all index-related state (caches, config, constants) so that
mcp_server.py and other consumers can import from a single source of truth
without circular dependencies.
"""

import gzip
import json
import os
import sys
import time as _time
from pathlib import Path

# ---------------------------------------------------------------------------
# Index directory (configurable via env var)
# ---------------------------------------------------------------------------

INDEX_DIR = Path(os.environ.get(
    "FLYTO_INDEX_DIR",
    str(Path(__file__).parent.parent / ".flyto-index")
))

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

_index_cache: dict = None
_content_cache: dict = {}
_content_loaded: bool = False
_bm25_cache = None
_test_mapper = None
_session_store = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Symbol type importance weights
TYPE_WEIGHTS = {
    "composable": 15,
    "component": 12,
    "function": 10,
    "class": 8,
    "interface": 6,
    "type": 5,
    "method": 3,
    "store": 12,
    "api": 10,
}

LOW_PRIORITY_PATHS = ["test", "tests", "__test__", "spec", "mock", "fixture", "example"]

# ---------------------------------------------------------------------------
# Auto-reindex
# ---------------------------------------------------------------------------

_last_reindex_check: float = 0.0
_AUTO_REINDEX_INTERVAL = 300.0  # 5 min
_AUTO_REINDEX_ENABLED = os.environ.get("FLYTO_AUTO_REINDEX", "1") != "0"


def _maybe_auto_reindex():
    """Check for file changes and trigger a live reindex if needed."""
    global _last_reindex_check
    if not _AUTO_REINDEX_ENABLED:
        return
    now = _time.monotonic()
    if now - _last_reindex_check < _AUTO_REINDEX_INTERVAL:
        return
    _last_reindex_check = now
    try:
        try:
            from .watcher import FileWatcher
        except ImportError:
            from watcher import FileWatcher
        index = load_index()
        if not index:
            return
        watcher = FileWatcher(index)
        changes = watcher.detect_changes()
        if changes:
            sys.stderr.write(
                f"[flyto-indexer] Auto-reindex: {len(changes)} changed files detected, reindexing...\n"
            )
            sys.stderr.flush()
            # Lazy import to avoid circular dependency
            try:
                from .tools.maintenance import _perform_live_reindex
            except ImportError:
                from tools.maintenance import _perform_live_reindex
            result = _perform_live_reindex()
            sys.stderr.write(
                f"[flyto-indexer] Auto-reindex: done ({result.get('reindexed', 0)} projects updated)\n"
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"[flyto-indexer] Auto-reindex error: {e}\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Index loading
# ---------------------------------------------------------------------------

def load_index() -> dict:
    """Load the symbol index, with caching."""
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    gz_path = INDEX_DIR / "index.json.gz"
    if gz_path.exists():
        with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
            _index_cache = json.load(f)
            return _index_cache
    path = INDEX_DIR / "index.json"
    if path.exists():
        _index_cache = json.loads(path.read_text())
        return _index_cache
    return {}


def load_project_map() -> dict:
    """Load the project map (PROJECT_MAP.json or .gz)."""
    gz_path = INDEX_DIR / "PROJECT_MAP.json.gz"
    if gz_path.exists():
        with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    path = INDEX_DIR / "PROJECT_MAP.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def load_content_file() -> dict:
    """Lazily load content.jsonl into the content cache."""
    global _content_cache, _content_loaded
    if _content_loaded:
        return _content_cache
    content_file = INDEX_DIR / "content.jsonl"
    if content_file.exists():
        try:
            with open(content_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        _content_cache[record["id"]] = record["content"]
        except Exception:
            pass
    _content_loaded = True
    return _content_cache


def get_symbol_content_text(symbol_id: str, symbol_data: dict) -> str:
    """Return the content text for a symbol, falling back to content.jsonl."""
    content = symbol_data.get("content", "")
    if content:
        return content
    content_map = load_content_file()
    return content_map.get(symbol_id, "")


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

def _load_bm25():
    """Load or return the cached BM25 index."""
    global _bm25_cache
    if _bm25_cache is not None:
        return _bm25_cache
    try:
        from .bm25 import BM25Index
    except ImportError:
        from bm25 import BM25Index
    bm25_path = INDEX_DIR / "bm25.json"
    _bm25_cache = BM25Index.load(bm25_path)
    return _bm25_cache


def _get_test_mapper():
    """Return the cached TestMapper instance."""
    global _test_mapper
    if _test_mapper is None:
        try:
            from .test_mapper import TestMapper
        except ImportError:
            from test_mapper import TestMapper
        _test_mapper = TestMapper(load_index())
    return _test_mapper


def _get_session_store():
    """Return the cached SessionStore instance."""
    global _session_store
    if _session_store is None:
        try:
            from .session import SessionStore
        except ImportError:
            from session import SessionStore
        _session_store = SessionStore()
    return _session_store


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def invalidate_caches():
    """Reset all caches to their initial states, forcing a fresh reload."""
    global _index_cache, _content_cache, _content_loaded
    global _bm25_cache, _test_mapper
    _index_cache = None
    _content_cache = {}
    _content_loaded = False
    _bm25_cache = None
    _test_mapper = None
