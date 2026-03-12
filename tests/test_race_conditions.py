"""
Tests for reindex lock mechanism and race condition safety.

Verifies that concurrent reindex, load, and invalidate operations
do not corrupt the index or cause crashes.
"""

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
import os

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_index():
    """Return a minimal valid index dict."""
    return {
        "project": "test-project",
        "projects": ["test-project"],
        "symbols": {"sym1": {"name": "foo", "type": "function", "file": "a.py"}},
        "files": {"a.py": {"path": "a.py"}},
        "dependencies": {},
        "reverse_index": {},
        "project_roots": {"test-project": "/tmp/fake"},
    }


@pytest.fixture(autouse=True)
def _reset_caches():
    """Reset all module-level caches before and after each test."""
    import index_store
    index_store._index_cache = None
    index_store._content_cache = {}
    index_store._content_loaded = False
    index_store._bm25_cache = None
    index_store._semantic_cache = None
    index_store._test_mapper = None
    index_store._cache_generation = 0.0
    # Ensure locks are released even if a previous test failed badly
    if index_store._reindex_lock.locked():
        index_store._reindex_lock.release()
    if index_store._load_lock.locked():
        index_store._load_lock.release()
    yield
    index_store._index_cache = None
    index_store._content_cache = {}
    index_store._content_loaded = False
    index_store._cache_generation = 0.0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestConcurrentReindexSkips:
    """Two threads both trying _perform_live_reindex — only one runs."""

    def test_concurrent_reindex_skips(self):
        import index_store

        barrier = threading.Barrier(2, timeout=3)
        started = threading.Event()
        results = {}

        real_index = _make_fake_index()

        def slow_reindex(project=None):
            """Simulate a slow reindex that holds the lock for a while."""
            started.set()
            time.sleep(0.3)
            return {
                "reindexed": 1,
                "errors": 0,
                "results": [{"project": "test-project"}],
            }

        def thread_fn(thread_id):
            from tools.maintenance import _perform_live_reindex
            barrier.wait()
            result = _perform_live_reindex(project="test-project")
            results[thread_id] = result

        with patch("tools.maintenance.load_index", return_value=real_index), \
             patch("tools.maintenance._perform_live_reindex_unlocked", side_effect=slow_reindex):
            t1 = threading.Thread(target=thread_fn, args=(1,))
            t2 = threading.Thread(target=thread_fn, args=(2,))
            t1.start()
            t2.start()
            t1.join(timeout=3)
            t2.join(timeout=3)

        # One thread should succeed, the other should be skipped
        skipped = [r for r in results.values() if r.get("skipped")]
        ran = [r for r in results.values() if not r.get("skipped")]
        assert len(skipped) >= 1, f"Expected at least one skip, got: {results}"
        assert len(ran) >= 1, f"Expected at least one run, got: {results}"


class TestConcurrentLoadIndex:
    """Multiple threads calling load_index() simultaneously."""

    def test_concurrent_load_index(self, tmp_path):
        import index_store

        # Set up a real index file on disk
        idx_dir = tmp_path / ".flyto-index"
        idx_dir.mkdir()
        fake_index = _make_fake_index()
        (idx_dir / "index.json").write_text(json.dumps(fake_index))

        results = {}
        errors = []
        barrier = threading.Barrier(4, timeout=3)

        def thread_fn(tid):
            try:
                barrier.wait()
                result = index_store.load_index()
                results[tid] = result
            except Exception as e:
                errors.append((tid, e))

        with patch.object(index_store, "_discover_index_dirs", return_value=[idx_dir]):
            threads = [threading.Thread(target=thread_fn, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=3)

        assert not errors, f"Threads raised errors: {errors}"
        assert len(results) == 4
        # All threads should get the same valid result
        for tid, result in results.items():
            assert result.get("project") == "test-project", f"Thread {tid} got invalid result"


class TestReindexDuringQuery:
    """One thread reindexing, another searching — no crashes."""

    def test_reindex_during_query(self, tmp_path):
        import index_store

        idx_dir = tmp_path / ".flyto-index"
        idx_dir.mkdir()
        fake_index = _make_fake_index()
        (idx_dir / "index.json").write_text(json.dumps(fake_index))

        search_results = []
        errors = []
        reindex_started = threading.Event()
        search_done = threading.Event()

        def reindex_thread():
            """Simulate reindex: invalidate caches and reload."""
            reindex_started.set()
            index_store.invalidate_caches()
            time.sleep(0.1)
            index_store.load_index()
            search_done.wait(timeout=2)

        def search_thread():
            """Run searches during reindex."""
            reindex_started.wait(timeout=2)
            for _ in range(5):
                try:
                    idx = index_store.load_index()
                    # Result should be either empty (cache cleared) or valid
                    if idx:
                        search_results.append(idx.get("project"))
                    else:
                        search_results.append(None)
                except Exception as e:
                    errors.append(e)
                time.sleep(0.02)
            search_done.set()

        with patch.object(index_store, "_discover_index_dirs", return_value=[idx_dir]):
            # Pre-load to populate cache
            index_store.load_index()

            t1 = threading.Thread(target=reindex_thread)
            t2 = threading.Thread(target=search_thread)
            t1.start()
            t2.start()
            t1.join(timeout=3)
            t2.join(timeout=3)

        assert not errors, f"Search thread errors: {errors}"
        # At least some searches should return valid data
        valid = [r for r in search_results if r == "test-project"]
        assert len(valid) > 0, f"No valid search results during reindex: {search_results}"


class TestInvalidateDuringLoad:
    """One thread loading index, another invalidating caches."""

    def test_invalidate_during_load(self, tmp_path):
        import index_store

        idx_dir = tmp_path / ".flyto-index"
        idx_dir.mkdir()
        fake_index = _make_fake_index()
        (idx_dir / "index.json").write_text(json.dumps(fake_index))

        load_results = []
        errors = []
        barrier = threading.Barrier(2, timeout=3)

        def load_thread():
            barrier.wait()
            for _ in range(10):
                try:
                    result = index_store.load_index()
                    load_results.append(result)
                except Exception as e:
                    errors.append(("load", e))
                time.sleep(0.01)

        def invalidate_thread():
            barrier.wait()
            for _ in range(10):
                try:
                    index_store.invalidate_caches()
                except Exception as e:
                    errors.append(("invalidate", e))
                time.sleep(0.01)

        with patch.object(index_store, "_discover_index_dirs", return_value=[idx_dir]):
            t1 = threading.Thread(target=load_thread)
            t2 = threading.Thread(target=invalidate_thread)
            t1.start()
            t2.start()
            t1.join(timeout=3)
            t2.join(timeout=3)

        assert not errors, f"Errors during concurrent load/invalidate: {errors}"
        # All load results should be either empty dict or valid index
        for i, r in enumerate(load_results):
            assert isinstance(r, dict), f"load_results[{i}] is not a dict: {type(r)}"


class TestLockNotHeldOnError:
    """If reindex raises, the lock must still be released."""

    def test_lock_released_after_exception_in_perform_live_reindex(self):
        import index_store

        real_index = _make_fake_index()

        def exploding_reindex(project=None):
            raise RuntimeError("Simulated reindex failure")

        with patch("tools.maintenance.load_index", return_value=real_index), \
             patch("tools.maintenance._perform_live_reindex_unlocked", side_effect=exploding_reindex):
            from tools.maintenance import _perform_live_reindex

            # This should raise but the lock should be released
            # _perform_live_reindex catches via try/finally so it propagates
            try:
                _perform_live_reindex(project="test-project")
            except RuntimeError:
                pass

        # Lock should NOT be held after the error
        acquired = index_store._reindex_lock.acquire(blocking=False)
        assert acquired, "Lock was not released after reindex exception"
        index_store._reindex_lock.release()

    def test_lock_released_after_exception_in_auto_reindex(self):
        import index_store

        # Force the timers to allow execution
        index_store._last_reindex_check = 0.0
        index_store._last_full_check = 0.0
        index_store._AUTO_REINDEX_ENABLED = True

        fake_index = _make_fake_index()

        class FakeChange:
            project = "test-project"

        class FakeWatcher:
            def __init__(self, idx):
                pass
            def detect_changes(self):
                return [FakeChange()]

        def exploding_reindex(project=None):
            raise RuntimeError("boom")

        with patch.object(index_store, "load_index", return_value=fake_index), \
             patch("index_store.FileWatcher", FakeWatcher, create=True):
            # Patch the watcher import inside the function
            with patch.dict("sys.modules", {}):
                # We need to mock the import path used by _maybe_auto_reindex
                import importlib
                with patch("index_store._perform_live_reindex_unlocked", exploding_reindex, create=True):
                    # Use a simpler approach: just mock the import
                    mock_module = MagicMock()
                    mock_module._perform_live_reindex_unlocked = exploding_reindex
                    with patch.dict("sys.modules", {"tools.maintenance": mock_module}):
                        with patch("builtins.__import__", side_effect=ImportError("force fallback")):
                            # The function catches OSError/RuntimeError, so it won't propagate
                            # but the lock should still be released
                            pass

        # Simpler test: directly verify lock release in _maybe_auto_reindex
        # by calling it with a watcher that raises
        index_store._last_reindex_check = 0.0
        index_store._last_full_check = 0.0

        with patch.object(index_store, "load_index", side_effect=RuntimeError("boom")):
            index_store._maybe_auto_reindex()

        acquired = index_store._reindex_lock.acquire(blocking=False)
        assert acquired, "Lock was not released after auto-reindex exception"
        index_store._reindex_lock.release()

    def test_lock_released_after_exception_in_maybe_auto_reindex_watcher(self):
        """Verify lock release when watcher.detect_changes() raises."""
        import index_store

        index_store._last_reindex_check = 0.0
        index_store._last_full_check = 0.0
        index_store._AUTO_REINDEX_ENABLED = True

        fake_index = _make_fake_index()

        class ExplodingWatcher:
            def __init__(self, idx):
                pass
            def detect_changes(self):
                raise OSError("disk error")

        # Patch the watcher import used by _maybe_auto_reindex
        with patch.object(index_store, "load_index", return_value=fake_index):
            # Monkey-patch the import resolution
            original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

            def patched_import(name, *args, **kwargs):
                if name == "watcher" or name.endswith(".watcher"):
                    mod = MagicMock()
                    mod.FileWatcher = ExplodingWatcher
                    return mod
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=patched_import):
                index_store._maybe_auto_reindex()

        acquired = index_store._reindex_lock.acquire(blocking=False)
        assert acquired, "Lock was not released after watcher exception"
        index_store._reindex_lock.release()
