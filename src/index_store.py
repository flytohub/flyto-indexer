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
import re
import sys
import threading
import time as _time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("flyto-indexer.store")

# ---------------------------------------------------------------------------
# Index directory (configurable via env var)
# ---------------------------------------------------------------------------

_EXPLICIT_INDEX_DIR = os.environ.get("FLYTO_INDEX_DIR")
INDEX_DIR = Path(_EXPLICIT_INDEX_DIR) if _EXPLICIT_INDEX_DIR else Path.cwd() / ".flyto-index"
_PROJECT_SCOPE = threading.local()


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
        try:
            rp = p.resolve()
            exists = rp.exists()
        except OSError:
            return
        if rp not in seen and exists:
            seen.add(rp)
            dirs.append(rp)

    def _scan_children(directory: Path):
        try:
            for child in directory.iterdir():
                try:
                    if child.is_dir() and not child.name.startswith("."):
                        _add(child / ".flyto-index")
                except OSError:
                    continue
        except OSError:
            return

    # 1. CWD/.flyto-index
    _add(INDEX_DIR)

    # 2. Scan child directories for .flyto-index/
    base = INDEX_DIR.parent  # CWD
    _scan_children(base)

    # 3. Also scan parent dir (sub-project → monorepo root pattern)
    parent = base.parent
    _add(parent / ".flyto-index")
    _scan_children(parent)

    return dirs


def _normalize_project_name(project: str | None) -> str:
    if not project:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", project.casefold()).strip("-")


def _peek_index_project(index_dir: Path) -> str:
    """Read only the small index header needed to identify a project."""
    candidates = (
        (index_dir / "index.json", False),
        (index_dir / "index.json.gz", True),
    )
    for path, compressed in candidates:
        if not path.exists():
            continue
        try:
            if compressed:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    prefix = handle.read(4096)
            else:
                with path.open("r", encoding="utf-8") as handle:
                    prefix = handle.read(4096)
        except (OSError, UnicodeError):
            continue
        match = re.search(r'"project"\s*:\s*"([^"]+)"', prefix)
        if match:
            return match.group(1)
    return ""


def _active_index_dirs(project: str | None = None) -> list[Path]:
    """Return all indexes, or only the index matching the active project."""
    dirs = _discover_index_dirs()
    scoped_project = project or _current_project_scope()
    target = _normalize_project_name(scoped_project)
    if not target:
        return dirs

    path_matches = [
        directory
        for directory in dirs
        if _normalize_project_name(directory.parent.name) == target
    ]
    if path_matches:
        return path_matches

    return [
        directory
        for directory in dirs
        if _normalize_project_name(_peek_index_project(directory)) == target
    ]


@contextmanager
def project_index_scope(project: str | None):
    """Limit index loading to one project for the duration of a tool call."""
    normalized = str(project).strip() if project else None
    previous = _current_project_scope()
    _PROJECT_SCOPE.project = normalized or None
    try:
        yield
    finally:
        _PROJECT_SCOPE.project = previous


def _current_project_scope() -> str | None:
    """Return the current thread's project scope, if one is active."""
    return getattr(_PROJECT_SCOPE, "project", None)

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

_index_cache: dict = None
_content_cache: dict = {}
_content_loaded: bool = False
_bm25_cache = None
_semantic_cache = None
_test_mapper = None
_test_mappers: dict[str, object] = {}
_session_store = None
_lsp_manager = None
_cache_generation: float = 0.0
_scoped_index_caches: dict[str, tuple[tuple, dict]] = {}
_scoped_content_caches: dict[str, dict] = {}
_scoped_bm25_caches: dict[str, object] = {}
_scoped_semantic_caches: dict[str, object] = {}

# ---------------------------------------------------------------------------
# Reindex / load locks
# ---------------------------------------------------------------------------

_reindex_lock = threading.Lock()
_load_lock = threading.Lock()

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

_REINDEX_INTERVAL_FAST = 10.0   # fast mtime check (cheap stat calls)
_REINDEX_INTERVAL_FULL = 300.0  # full watcher scan (more expensive)
_NEVER_CHECKED = float("-inf")
_last_reindex_check: float = _NEVER_CHECKED
_last_full_check: float = _NEVER_CHECKED
_project_reindex_checks: dict[str, float] = {}
_project_full_checks: dict[str, float] = {}
_AUTO_REINDEX_ENABLED = os.environ.get("FLYTO_AUTO_REINDEX", "1") != "0"


def _maybe_auto_reindex(project: str | None = None):
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
    scope_key = _normalize_project_name(project)

    # Tier 1: fast generation check (every 10s)
    if scope_key:
        if now - _project_reindex_checks.get(scope_key, _NEVER_CHECKED) < _REINDEX_INTERVAL_FAST:
            return
        _project_reindex_checks[scope_key] = now
    else:
        if now - _last_reindex_check < _REINDEX_INTERVAL_FAST:
            return
        _last_reindex_check = now

    # If generation file changed, cache will auto-invalidate on next load_index()
    # No action needed here for tier 1 — _check_generation() handles it in load_index()

    # Tier 2: full file watcher scan (every 300s)
    if scope_key:
        if now - _project_full_checks.get(scope_key, _NEVER_CHECKED) < _REINDEX_INTERVAL_FULL:
            return
        _project_full_checks[scope_key] = now
    else:
        if now - _last_full_check < _REINDEX_INTERVAL_FULL:
            return
        _last_full_check = now

    # Non-blocking acquire: skip this cycle if another reindex is in progress
    if not _reindex_lock.acquire(blocking=False):
        logger.debug("Auto-reindex skipped: another reindex is in progress")
        return
    try:
        with project_index_scope(project):
            try:
                from .watcher import FileWatcher
            except ImportError:
                from watcher import FileWatcher
            index = load_index()
            if not index:
                return
            watcher = FileWatcher(index)
            changes = (
                watcher.detect_changes(project=project)
                if project
                else watcher.detect_changes()
            )
            if not changes:
                return

            changed_projects = {change.project for change in changes}
            if project:
                changed_projects = {
                    changed_project
                    for changed_project in changed_projects
                    if _normalize_project_name(changed_project) == scope_key
                }
            if not changed_projects:
                return

            sys.stderr.write(
                f"[flyto-indexer] Auto-reindex: {len(changes)} changes in "
                f"{len(changed_projects)} project(s)\n"
            )
            sys.stderr.flush()

            try:
                from .tools.maintenance import _perform_live_reindex_unlocked
            except ImportError:
                from tools.maintenance import _perform_live_reindex_unlocked

            total_reindexed = 0
            for proj in changed_projects:
                result = _perform_live_reindex_unlocked(project=proj)
                total_reindexed += result.get("reindexed", 0)

            sys.stderr.write(
                f"[flyto-indexer] Auto-reindex: done "
                f"({total_reindexed} projects updated)\n"
            )
            sys.stderr.flush()
    except (OSError, json.JSONDecodeError, RuntimeError) as e:
        logger.warning("Auto-reindex error: %s", e, exc_info=True)
    finally:
        _reindex_lock.release()


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
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _check_generation() -> bool:
    """Return True if any discovered index dir has a newer .generation file."""
    global _cache_generation
    for d in _active_index_dirs():
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


def _merge_index_into(merged: dict, idx: dict):
    """Merge a single index dict into the base merged dict in-place.

    Handles symbols, dependencies, reverse_index, files, and
    routes/api_endpoints merging.
    """
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


def _record_project_roots(index: dict, roots: dict[str, str]) -> None:
    """Preserve every project root while combining independently-built indexes."""
    for project, root in (index.get("project_roots") or {}).items():
        if project and root:
            roots[str(project)] = str(root)
    project = index.get("project")
    root_path = index.get("root_path")
    if project and root_path:
        roots[str(project)] = str(root_path)


def _index_dirs_fingerprint(dirs: list[Path]) -> tuple:
    """Return a stable freshness fingerprint without loading index bodies."""
    fingerprint = []
    for directory in dirs:
        marker = directory / ".generation"
        if not marker.exists():
            marker = directory / "index.json"
        try:
            mtime = marker.stat().st_mtime_ns
        except OSError:
            mtime = 0
        fingerprint.append((str(directory), mtime))
    return tuple(fingerprint)


def _load_merged_indexes(dirs: list[Path]) -> dict:
    """Load and combine a preselected set of project indexes."""
    if not dirs:
        return {}

    merged = _load_single_index(dirs[0])
    if not merged and len(dirs) <= 1:
        return {}
    if not merged:
        merged = {}

    projects = list(merged.get("projects", []))
    project_roots: dict[str, str] = {}
    _record_project_roots(merged, project_roots)
    if merged.get("project") and merged["project"] not in projects:
        projects.append(merged["project"])

    for directory in dirs[1:]:
        index = _load_single_index(directory)
        if not index:
            continue
        project = index.get("project", "")
        if project and project not in projects:
            projects.append(project)
        _record_project_roots(index, project_roots)
        _merge_index_into(merged, index)

    merged["projects"] = projects
    merged["project_roots"] = project_roots
    return merged


def load_index() -> dict:
    """Load and merge all discovered indexes, with caching.

    Thread-safe: uses _load_lock to prevent duplicate disk loads when
    multiple threads see a stale generation simultaneously.
    """
    global _index_cache
    scope_key = _normalize_project_name(_current_project_scope())

    if scope_key:
        dirs = _active_index_dirs()
        fingerprint = _index_dirs_fingerprint(dirs)
        cached_entry = _scoped_index_caches.get(scope_key)
        if cached_entry and cached_entry[0] == fingerprint:
            return cached_entry[1]
        with _load_lock:
            dirs = _active_index_dirs()
            fingerprint = _index_dirs_fingerprint(dirs)
            cached_entry = _scoped_index_caches.get(scope_key)
            if cached_entry and cached_entry[0] == fingerprint:
                return cached_entry[1]
            merged = _load_merged_indexes(dirs)
            _scoped_index_caches[scope_key] = (fingerprint, merged)
            return merged

    # Fast path (no lock): return cached if still valid. Snapshot the
    # global into a local so a concurrent invalidate_caches() can't turn
    # the value into None between the truthiness check and the return.
    cached = _index_cache
    if cached is not None:
        if not _check_generation():
            return cached

    with _load_lock:
        # Double-check after acquiring lock — another thread may have loaded already
        cached = _index_cache
        if cached is not None:
            if not _check_generation():
                return cached
            # Use unlocked variant to avoid deadlock with _reindex_lock
            _invalidate_caches_unlocked()

        dirs = _active_index_dirs()
        merged = _load_merged_indexes(dirs)
        if not merged:
            return {}
        _index_cache = merged
        # Record the latest generation mtime so subsequent checks are relative
        _update_cache_generation()
        # Return the local — `_index_cache` may be cleared by a concurrent
        # invalidate_caches() between this assignment and the return.
        return merged


def load_project_map() -> dict:
    """Load and merge project maps from all discovered index dirs."""
    merged = None
    for d in _active_index_dirs():
        gz_path = d / "PROJECT_MAP.json.gz"
        path = d / "PROJECT_MAP.json"
        data = {}
        if gz_path.exists():
            with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
                data = json.load(f)
        elif path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
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
    scope_key = _normalize_project_name(_current_project_scope())
    if scope_key:
        if scope_key in _scoped_content_caches:
            return _scoped_content_caches[scope_key]
        content_cache = {}
    else:
        if _content_loaded:
            return _content_cache
        content_cache = _content_cache

    for d in _active_index_dirs():
        content_file = d / "content.jsonl"
        if content_file.exists():
            try:
                with open(content_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            record = json.loads(line)
                            content_cache[record["id"]] = record["content"]
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning("Failed to load content from %s: %s", content_file, e)
    if scope_key:
        _scoped_content_caches[scope_key] = content_cache
    else:
        _content_loaded = True
    return content_cache


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
    scope_key = _normalize_project_name(_current_project_scope())
    if scope_key and scope_key in _scoped_bm25_caches:
        return _scoped_bm25_caches[scope_key]
    if _bm25_cache is not None:
        if not scope_key:
            return _bm25_cache
    try:
        from .bm25 import BM25Index
    except ImportError:
        from bm25 import BM25Index
    dirs = _active_index_dirs()
    bm25_path = (
        dirs[0] / "bm25.json"
        if scope_key and dirs
        else INDEX_DIR / "bm25.json"
    )
    bm25 = BM25Index.load(bm25_path)
    if scope_key:
        _scoped_bm25_caches[scope_key] = bm25
    else:
        _bm25_cache = bm25
    return bm25


def _rebuild_semantic_index(index_dir: Path):
    """Rebuild the semantic index from current index data.

    Called lazily when a .semantic_stale marker is found.
    """
    try:
        from .semantic import SemanticIndex
        from .search_documents import build_symbol_document
    except ImportError:
        from semantic import SemanticIndex
        from search_documents import build_symbol_document

    index_data = load_index()
    if not index_data:
        return None

    symbols = index_data.get("symbols", {})
    if not symbols:
        return None

    # Build document texts (same logic as engine._build_symbol_doc)
    documents = {}
    for sid, sym in symbols.items():
        documents[sid] = build_symbol_document(sym)

    if not documents:
        return None

    semantic = SemanticIndex()
    semantic.build(documents, index_data=index_data)

    try:
        from .safe_io import atomic_write_json
    except ImportError:
        from safe_io import atomic_write_json

    semantic.save(index_dir / "semantic.json")
    return semantic


def _load_semantic():
    """Load or return the cached semantic (TF-IDF) index.

    If a .semantic_stale marker exists, rebuilds the semantic index
    from current index data before loading.
    """
    global _semantic_cache
    scope_key = _normalize_project_name(_current_project_scope())
    if scope_key and scope_key in _scoped_semantic_caches:
        return _scoped_semantic_caches[scope_key]
    if _semantic_cache is not None:
        if not scope_key:
            return _semantic_cache
    try:
        from .semantic import SemanticIndex
    except ImportError:
        from semantic import SemanticIndex

    # Check for stale marker in all discovered index dirs
    for d in _active_index_dirs():
        stale_marker = d / ".semantic_stale"
        if stale_marker.exists():
            logger.info("Semantic index stale, rebuilding from %s", d)
            try:
                rebuilt = _rebuild_semantic_index(d)
                if rebuilt:
                    if scope_key:
                        _scoped_semantic_caches[scope_key] = rebuilt
                    else:
                        _semantic_cache = rebuilt
                stale_marker.unlink(missing_ok=True)
            except (OSError, RuntimeError) as e:
                logger.warning("Failed to rebuild semantic index: %s", e)
            if scope_key and scope_key in _scoped_semantic_caches:
                return _scoped_semantic_caches[scope_key]
            if not scope_key and _semantic_cache is not None:
                return _semantic_cache

    dirs = _active_index_dirs()
    semantic_path = (
        dirs[0] / "semantic.json"
        if scope_key and dirs
        else INDEX_DIR / "semantic.json"
    )
    semantic = SemanticIndex.load(semantic_path)
    if scope_key:
        _scoped_semantic_caches[scope_key] = semantic
    else:
        _semantic_cache = semantic
    return semantic


def _get_test_mapper(project: str | None = None):
    """Return a cached mapper, scoped to one project when available."""
    global _test_mapper, _test_mappers
    if project:
        with project_index_scope(project):
            index = load_index()
    else:
        index = load_index()
    if project:
        try:
            from .test_mapper import TestMapper
        except ImportError:
            from test_mapper import TestMapper
        mapper = _test_mappers.get(project)
        if mapper is None or mapper._index is not index:
            mapper = TestMapper(index, project=project)
            _test_mappers[project] = mapper
        return mapper
    if _test_mapper is None or _test_mapper._index is not index:
        try:
            from .test_mapper import TestMapper
        except ImportError:
            from test_mapper import TestMapper
        _test_mapper = TestMapper(index)
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


def _get_lsp_manager():
    """Return the cached LSPManager instance, or None if LSP is disabled."""
    global _lsp_manager
    if _lsp_manager is None:
        try:
            try:
                from .lsp.manager import LSPManager
            except ImportError:
                from lsp.manager import LSPManager
            _lsp_manager = LSPManager.get_instance()
        except Exception:
            return None
    return _lsp_manager


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def _update_cache_generation():
    """Record the max .generation mtime across all discovered index dirs."""
    global _cache_generation
    max_mtime = 0.0
    for d in _active_index_dirs():
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
    """Reset all caches to their initial states, forcing a fresh reload.

    Thread-safe: acquires _reindex_lock to prevent cache reset while
    a reindex operation is in progress.
    """
    with _reindex_lock:
        _invalidate_caches_unlocked()


def _invalidate_caches_unlocked():
    """Internal cache reset — caller must hold _reindex_lock or _load_lock."""
    global _index_cache, _content_cache, _content_loaded
    global _bm25_cache, _semantic_cache, _test_mapper, _test_mappers
    global _lsp_manager, _cache_generation
    _index_cache = None
    _content_cache = {}
    _content_loaded = False
    _bm25_cache = None
    _semantic_cache = None
    _test_mapper = None
    _test_mappers = {}
    _cache_generation = 0.0
    _scoped_index_caches.clear()
    _scoped_content_caches.clear()
    _scoped_bm25_caches.clear()
    _scoped_semantic_caches.clear()
    # Shutdown LSP servers on cache invalidation
    if _lsp_manager is not None:
        try:
            _lsp_manager.shutdown_all()
        except Exception:
            pass
        _lsp_manager = None
        try:
            try:
                from .lsp.manager import LSPManager
            except ImportError:
                from lsp.manager import LSPManager
            LSPManager.reset_instance()
        except Exception:
            pass
