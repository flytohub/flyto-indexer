"""Tests for incremental indexing: BM25 update_docs, reverse_index, semantic stale marker."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from bm25 import BM25Index, tokenize


# =============================================================================
# BM25 incremental update tests
# =============================================================================

class TestBM25UpdateDocs:
    """Test BM25Index.update_docs() for incremental add/remove."""

    @pytest.fixture
    def base_docs(self):
        return {
            "sym1": "useAuth composable authentication login",
            "sym2": "LoginForm component form user login",
            "sym3": "fetchUsers function API users list",
        }

    @pytest.fixture
    def built_index(self, base_docs):
        idx = BM25Index()
        idx.build(base_docs)
        return idx

    def test_remove_doc(self, built_index):
        """Removing a doc should update counts and still return correct results."""
        assert built_index.N == 3
        built_index.update_docs(removed_ids={"sym2"}, added_docs={})
        assert built_index.N == 2
        assert "sym2" not in built_index.doc_ids

        # Search should still work
        results = built_index.search("useAuth")
        assert len(results) > 0
        assert results[0][0] == "sym1"

        # sym2 should not appear in results
        result_ids = [r[0] for r in results]
        assert "sym2" not in result_ids

    def test_add_doc(self, built_index):
        """Adding a new doc should be searchable immediately."""
        built_index.update_docs(
            removed_ids=set(),
            added_docs={"sym4": "validateEmail function validation email format"},
        )
        assert built_index.N == 4
        assert "sym4" in built_index.doc_ids

        results = built_index.search("email validation")
        assert len(results) > 0
        assert results[0][0] == "sym4"

    def test_replace_doc(self, built_index):
        """Remove then add same ID should update content."""
        built_index.update_docs(
            removed_ids={"sym1"},
            added_docs={"sym1": "totally different document about databases SQL"},
        )
        assert built_index.N == 3

        # Old content should not match well
        results = built_index.search("databases SQL")
        assert len(results) > 0
        assert results[0][0] == "sym1"

        # Old terms should rank lower
        results = built_index.search("useAuth authentication")
        result_ids = [r[0] for r in results]
        # sym1 should not be top result for old terms
        if "sym1" in result_ids:
            # It might still match weakly on common terms, but should not be top
            assert result_ids[0] != "sym1" or len(results) == 1

    def test_noop_update(self, built_index):
        """Empty update should not change anything."""
        orig_n = built_index.N
        orig_ids = list(built_index.doc_ids)
        built_index.update_docs(removed_ids=set(), added_docs={})
        assert built_index.N == orig_n
        assert built_index.doc_ids == orig_ids

    def test_remove_all_then_add(self):
        """Removing all docs then adding new ones should work."""
        idx = BM25Index()
        idx.build({"a": "hello world", "b": "goodbye world"})
        idx.update_docs(removed_ids={"a", "b"}, added_docs={"c": "new document testing"})
        assert idx.N == 1
        results = idx.search("testing")
        assert len(results) == 1
        assert results[0][0] == "c"

    def test_df_idf_recomputed(self, base_docs):
        """Verify df/idf are properly recomputed after update."""
        idx = BM25Index()
        idx.build(base_docs)

        # "login" appears in sym1 and sym2 initially
        assert idx.df.get("login", 0) == 2

        # Remove sym2 (which has "login")
        idx.update_docs(removed_ids={"sym2"}, added_docs={})
        assert idx.df.get("login", 0) == 1

    def test_avgdl_updated(self, base_docs):
        """Average document length should be recalculated."""
        idx = BM25Index()
        idx.build(base_docs)
        old_avgdl = idx.avgdl

        # Add a very long document
        idx.update_docs(
            removed_ids=set(),
            added_docs={"long": " ".join(["word"] * 100)},
        )
        assert idx.avgdl > old_avgdl

    def test_search_scores_match_full_rebuild(self):
        """Incremental update should produce same scores as full rebuild."""
        initial = {"a": "auth login user", "b": "form component button", "c": "api fetch data"}
        updated = {"a": "auth login user", "c": "api fetch data", "d": "new module validation"}

        # Method 1: Full rebuild
        full = BM25Index()
        full.build(updated)

        # Method 2: Incremental
        incr = BM25Index()
        incr.build(initial)
        incr.update_docs(removed_ids={"b"}, added_docs={"d": "new module validation"})

        # Compare search results
        for query in ["auth", "api fetch", "validation", "data"]:
            full_results = dict(full.search(query, top_k=10))
            incr_results = dict(incr.search(query, top_k=10))
            assert set(full_results.keys()) == set(incr_results.keys()), f"Mismatch for query '{query}'"
            for doc_id in full_results:
                assert abs(full_results[doc_id] - incr_results[doc_id]) < 0.001, (
                    f"Score mismatch for '{doc_id}' on query '{query}'"
                )


# =============================================================================
# Incremental reverse_index tests
# =============================================================================

class TestIncrementalReverseIndex:
    """Test incremental reverse_index purge + re-add in the engine."""

    def test_incremental_reverse_index_update(self):
        """Verify purge + re-add produces correct reverse index after file change."""
        from src.engine import IndexEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Create initial files
            (root / "caller.py").write_text(
                'from helper import do_work\n\ndef main():\n    do_work()\n'
            )
            (root / "helper.py").write_text(
                'def do_work():\n    """Does work."""\n    pass\n'
            )

            idx_dir = root / ".flyto-index"
            engine = IndexEngine("test", root, index_dir=idx_dir)
            result1 = engine.scan(incremental=False)
            assert result1["errors"] == 0

            # Check reverse index: do_work should have caller
            rev = engine.index.reverse_index
            do_work_callers = []
            for sid, callers in rev.items():
                if "do_work" in sid:
                    do_work_callers = callers
                    break
            assert len(do_work_callers) > 0, "do_work should have at least one caller"

            # Now modify caller.py to call a different function
            (root / "caller.py").write_text(
                'def main():\n    pass  # no longer calls do_work\n'
            )

            result2 = engine.scan(incremental=True)
            # After incremental update, do_work should have no callers from caller.py
            rev2 = engine.index.reverse_index
            do_work_callers2 = []
            for sid, callers in rev2.items():
                if "do_work" in sid:
                    do_work_callers2 = callers
                    break

            # Verify caller.py is no longer referencing do_work
            caller_refs = [c for c in do_work_callers2 if "caller.py" in c]
            assert len(caller_refs) == 0, "caller.py should no longer reference do_work"

    def test_incremental_reverse_index_add_new_ref(self):
        """Adding a new caller in an incremental scan should appear in reverse index."""
        from src.engine import IndexEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            (root / "helper.py").write_text(
                'def helper_func():\n    """A helper."""\n    pass\n'
            )

            idx_dir = root / ".flyto-index"
            engine = IndexEngine("test", root, index_dir=idx_dir)
            engine.scan(incremental=False)

            # Now add a caller
            (root / "new_caller.py").write_text(
                'from helper import helper_func\n\ndef use_it():\n    helper_func()\n'
            )

            engine.scan(incremental=True)

            # helper_func should now have a caller
            rev = engine.index.reverse_index
            helper_callers = []
            for sid, callers in rev.items():
                if "helper_func" in sid:
                    helper_callers = callers
                    break
            caller_from_new = [c for c in helper_callers if "new_caller" in c]
            assert len(caller_from_new) > 0, "helper_func should have caller from new_caller.py"


# =============================================================================
# Semantic stale marker tests
# =============================================================================

class TestSemanticStaleMarker:
    """Test lazy semantic index rebuild via stale marker."""

    def test_stale_marker_triggers_rebuild(self):
        """When .semantic_stale exists, _load_semantic should rebuild."""
        from src.engine import IndexEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            (root / "example.py").write_text(
                'def greet(name):\n    """Greet someone."""\n    return f"Hello {name}"\n'
            )

            idx_dir = root / ".flyto-index"
            engine = IndexEngine("test", root, index_dir=idx_dir)
            engine.scan(incremental=False)

            # Verify semantic.json exists
            semantic_path = idx_dir / "semantic.json"
            assert semantic_path.exists()

            # Delete semantic.json and create stale marker
            semantic_path.unlink()
            stale_marker = idx_dir / ".semantic_stale"
            stale_marker.write_text("1")

            # Now _load_semantic should rebuild
            # We need to set the INDEX_DIR and clear caches
            import src.index_store as store
            old_index_dir = store.INDEX_DIR
            store.INDEX_DIR = idx_dir
            store.invalidate_caches()

            try:
                result = store._load_semantic()
                # After rebuild, semantic.json should exist again
                assert semantic_path.exists(), "semantic.json should be rebuilt"
                # Stale marker should be removed
                assert not stale_marker.exists(), ".semantic_stale should be removed"
                # Result should be a valid semantic index
                if result is not None:
                    assert result.N > 0
            finally:
                store.INDEX_DIR = old_index_dir
                store.invalidate_caches()

    def test_incremental_scan_creates_stale_marker(self):
        """An incremental scan with changes should create .semantic_stale."""
        from src.engine import IndexEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            (root / "mod.py").write_text(
                'def original():\n    """Original function."""\n    pass\n'
            )

            idx_dir = root / ".flyto-index"
            engine = IndexEngine("test", root, index_dir=idx_dir)
            engine.scan(incremental=False)

            # Full scan should NOT leave a stale marker
            stale_marker = idx_dir / ".semantic_stale"
            assert not stale_marker.exists()

            # Modify file and do incremental scan
            (root / "mod.py").write_text(
                'def modified():\n    """Modified function."""\n    pass\n'
            )
            engine.scan(incremental=True)

            # Incremental scan should create .semantic_stale
            assert stale_marker.exists(), ".semantic_stale should be created by incremental scan"


# =============================================================================
# extract_path_from_sid helper tests
# =============================================================================

class TestExtractPathFromSid:
    """Test the _extract_path_from_sid static method."""

    def test_standard_format(self):
        from src.engine import IndexEngine
        assert IndexEngine._extract_path_from_sid("proj:src/foo.py:function:bar") == "src/foo.py"

    def test_short_format(self):
        from src.engine import IndexEngine
        assert IndexEngine._extract_path_from_sid("proj:file.py") == "file.py"

    def test_no_colon(self):
        from src.engine import IndexEngine
        assert IndexEngine._extract_path_from_sid("nocolon") == ""
