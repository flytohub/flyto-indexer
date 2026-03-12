"""
Index store — centralized index loading, caching, and content management.

This module owns all index-related state (caches, config, constants) so that
mcp_server.py and other consumers can import from a single source of truth
without circular dependencies.
"""

import gzip
import json
import logging
import os
import sys
import time as _time
from pathlib import Path

logger = logging.getLogger("flyto-indexer.store")

# ---------------------------------------------------------------------------
# Index directory (configurable via env var)
# ---------------------------------------------------------------------------

_EXPLICIT_INDEX_DIR = os.environ.get("FLYTO_INDEX_DIR")
INDEX_DIR = Path(_EXPLICIT_INDEX_DIR) if _EXPLICIT_INDEX_DIR else Path.cwd() / ".flyto-index"


def _discover_index_dirs() -> list:
    """Discover all .flyto-index/ directories.

    If FLYTO_INDEX_DIR is explicitly set, only use that directory (no discovery).
    Otherwise searches:
    1. CWD/.flyto-index
    2. Direct child directories (monorepo: each sub-project may have its own index)
    3. Parent directory (running from a sub-project)
    """
    # Explicit env var = no auto-discovery
    if _EXPLICIT_INDEX_DIR:
        return [INDEX_DIR] if INDEX_DIR.exists() else []

    seen = set()
    dirs = []

    def _add(p: Path):
        rp = p.resolve()
        if rp not in seen and rp.exists():
            seen.add(rp)
            dirs.append(rp)

    # 1. CWD/.flyto-index
    if INDEX_DIR.exists():
        _add(INDEX_DIR)

    # 2. Scan child directories for .flyto-index/
    base = INDEX_DIR.parent  # CWD
    if base.exists():
        for child in base.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                sub_index = child / ".flyto-index"
                if sub_index.exists():
                    _add(sub_index)

    # 3. Also scan parent dir (sub-project → monorepo root pattern)
    parent = base.parent
    parent_index = parent / ".flyto-index"
    if parent_index.exists():
        _add(parent_index)
    if parent.exists():
        for child in parent.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                sub_index = child / ".flyto-index"
                if sub_index.exists():
                    _add(sub_index)

    return dirs

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

_index_cache: dict = None
_content_cache: dict = {}
_content_loaded: bool = False
_bm25_cache = None
_semantic_cache = None
_test_mapper = None
_session_store = None
_cache_generation: float = 0.0

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
_REINDEX_INTERVAL_FAST = 10.0   # fast mtime check (cheap stat calls)
_REINDEX_INTERVAL_FULL = 300.0  # full watcher scan (more expensive)
_last_full_check: float = 0.0
_AUTO_REINDEX_ENABLED = os.environ.get("FLYTO_AUTO_REINDEX", "1") != "0"


def _maybe_auto_reindex():
    """Check for file changes and trigger incremental reindex if needed.

    Two-tier strategy:
    - Every 10s: fast check via .generation file mtime (near-zero cost)
    - Every 300s: full watcher scan (stat() on indexed files)

    Only reindexes projects with actual changes, not all projects.
    """
    global _last_reindex_check, _last_full_check
    if not _AUTO_REINDEX_ENABLED:
        return
    now = _time.monotonic()

    # Tier 1: fast generation check (every 10s)
    if now - _last_reindex_check < _REINDEX_INTERVAL_FAST:
        return
    _last_reindex_check = now

    # If generation file changed, cache will auto-invalidate on next load_index()
    # No action needed here for tier 1 — _check_generation() handles it in load_index()

    # Tier 2: full file watcher scan (every 300s)
    if now - _last_full_check < _REINDEX_INTERVAL_FULL:
        return
    _last_full_check = now

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
        if not changes:
            return

        # Group changes by project — only reindex affected projects
        changed_projects = set()
        for c in changes:
            changed_projects.add(c.project)

        sys.stderr.write(
            f"[flyto-indexer] Auto-reindex: {len(changes)} changes in {len(changed_projects)} project(s)\n"
        )
        sys.stderr.flush()

        try:
            from .tools.maintenance import _perform_live_reindex
        except ImportError:
            from tools.maintenance import _perform_live_reindex

        total_reindexed = 0
        for proj in changed_projects:
            result = _perform_live_reindex(project=proj)
            total_reindexed += result.get("reindexed", 0)

        sys.stderr.write(
            f"[flyto-indexer] Auto-reindex: done ({total_reindexed} projects updated)\n"
        )
        sys.stderr.flush()
    except (OSError, json.JSONDecodeError, RuntimeError) as e:
        logger.warning("Auto-reindex error: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
# Index loading
# ---------------------------------------------------------------------------

def _load_single_index(index_dir: Path) -> dict:
    """Load index.json(.gz) from a single directory."""
    gz_path = index_dir / "index.json.gz"
    if gz_path.exists():
        with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    path = index_dir / "index.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _check_generation() -> bool:
    """Return True if any discovered index dir has a newer .generation file."""
    global _cache_generation
    for d in _discover_index_dirs():
        gen_file = d / ".generation"
        if gen_file.exists():
            try:
                mtime = gen_file.stat().st_mtime
                if mtime > _cache_generation:
                    return True
            except OSError:
                pass
    return False


def _write_generation(index_dir: Path):
    """Write current timestamp to index_dir/.generation to signal cache staleness."""
    gen_file = index_dir / ".generation"
    try:
        gen_file.write_text(str(_time.time()))
    except OSError:
        pass


def load_index() -> dict:
    """Load and merge all discovered indexes, with caching."""
    global _index_cache
    if _index_cache is not None:
        if _check_generation():
            invalidate_caches()
        else:
            return _index_cache

    dirs = _discover_index_dirs()
    if not dirs:
        return {}

    # Load first index as base
    merged = _load_single_index(dirs[0])
    if not merged and len(dirs) <= 1:
        return {}
    if not merged:
        merged = {}

    # Merge additional indexes
    projects = list(merged.get("projects", []))
    if merged.get("project") and merged["project"] not in projects:
        projects.append(merged["project"])

    for d in dirs[1:]:
        idx = _load_single_index(d)
        if not idx:
            continue
        proj = idx.get("project", "")
        if proj and proj not in projects:
            projects.append(proj)
        # Merge symbols
        for k, v in idx.get("symbols", {}).items():
            merged.setdefault("symbols", {})[k] = v
        # Merge dependencies
        for k, v in idx.get("dependencies", {}).items():
            merged.setdefault("dependencies", {})[k] = v
        # Merge reverse_index
        for k, v in idx.get("reverse_index", {}).items():
            existing = merged.setdefault("reverse_index", {}).get(k, [])
            if isinstance(v, list):
                for item in v:
                    if item not in existing:
                        existing.append(item)
                merged["reverse_index"][k] = existing
        # Merge files
        for k, v in idx.get("files", {}).items():
            merged.setdefault("files", {})[k] = v
        # Merge routes/api_endpoints (may be list or dict depending on index version)
        for key in ("routes", "api_endpoints"):
            incoming = idx.get(key, [])
            existing = merged.get(key)
            if isinstance(incoming, list) and isinstance(existing, list):
                existing.extend(incoming)
            elif isinstance(incoming, dict):
                merged.setdefault(key, {}).update(incoming)
            elif isinstance(incoming, list) and existing is None:
                merged[key] = list(incoming)

    merged["projects"] = projects
    _index_cache = merged
    # Record the latest generation mtime so subsequent checks are relative
    _update_cache_generation()
    return _index_cache


def load_project_map() -> dict:
    """Load and merge project maps from all discovered index dirs."""
    merged = None
    for d in _discover_index_dirs():
        gz_path = d / "PROJECT_MAP.json.gz"
        path = d / "PROJECT_MAP.json"
        data = {}
        if gz_path.exists():
            with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
                data = json.load(f)
        elif path.exists():
            data = json.loads(path.read_text())
        if not data:
            continue
        if merged is None:
            merged = data
            continue
        # Merge dict-type fields
        for k in ("files", "categories", "api_map"):
            for fk, fv in data.get(k, {}).items():
                merged.setdefault(k, {})[fk] = fv
    return merged or {}


def load_content_file() -> dict:
    """Lazily load content.jsonl from all discovered index dirs."""
    global _content_cache, _content_loaded
    if _content_loaded:
        return _content_cache
    for d in _discover_index_dirs():
        content_file = d / "content.jsonl"
        if content_file.exists():
            try:
                with open(content_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            record = json.loads(line)
                            _content_cache[record["id"]] = record["content"]
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning("Failed to load content from %s: %s", content_file, e)
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


def _load_semantic():
    """Load or return the cached semantic (TF-IDF) index."""
    global _semantic_cache
    if _semantic_cache is not None:
        return _semantic_cache
    try:
        from .semantic import SemanticIndex
    except ImportError:
        from semantic import SemanticIndex
    semantic_path = INDEX_DIR / "semantic.json"
    _semantic_cache = SemanticIndex.load(semantic_path)
    return _semantic_cache


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

def _update_cache_generation():
    """Record the max .generation mtime across all discovered index dirs."""
    global _cache_generation
    max_mtime = 0.0
    for d in _discover_index_dirs():
        gen_file = d / ".generation"
        if gen_file.exists():
            try:
                mtime = gen_file.stat().st_mtime
                if mtime > max_mtime:
                    max_mtime = mtime
            except OSError:
                pass
    _cache_generation = max_mtime


def invalidate_caches():
    """Reset all caches to their initial states, forcing a fresh reload."""
    global _index_cache, _content_cache, _content_loaded
    global _bm25_cache, _semantic_cache, _test_mapper, _cache_generation
    _index_cache = None
    _content_cache = {}
    _content_loaded = False
    _bm25_cache = None
    _semantic_cache = None
    _test_mapper = None
    _cache_generation = 0.0
