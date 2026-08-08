"""Tests for incremental indexing: BM25 update_docs, reverse_index, semantic stale marker."""

import json
import os
import shutil
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
        assert orig_n == built_index.N
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


class TestContentAddressedManifest:
    """Incremental reuse requires both file content and parser identity."""

    def test_legacy_manifest_forces_one_safe_rebuild(self, tmp_path):
        from src.indexer.incremental import IncrementalIndexer, compute_file_hash

        index_dir = tmp_path / ".flyto-index"
        index_dir.mkdir()
        content_hash = compute_file_hash("def run():\n    pass\n")
        (index_dir / "manifest.json").write_text(json.dumps({
            "project": "demo",
            "version": 1,
            "files": {
                "app.py": {"hash": content_hash[:16], "symbols": []},
            },
        }))

        indexer = IncrementalIndexer(
            tmp_path,
            index_dir,
            pipeline_fingerprint="a" * 64,
        )
        changes = indexer.detect_changes({"app.py": content_hash})

        assert changes.modified == ["app.py"]

    def test_pipeline_change_invalidates_unchanged_content(self, tmp_path):
        from src.indexer.incremental import IncrementalIndexer, compute_file_hash

        index_dir = tmp_path / ".flyto-index"
        index_dir.mkdir()
        content_hash = compute_file_hash("def run():\n    pass\n")
        (index_dir / "manifest.json").write_text(json.dumps({
            "project": "demo",
            "version": 2,
            "hash_algorithm": "sha256",
            "pipeline_fingerprint": "a" * 64,
            "files": {
                "app.py": {"hash": content_hash, "symbols": []},
            },
        }))

        indexer = IncrementalIndexer(
            tmp_path,
            index_dir,
            pipeline_fingerprint="b" * 64,
        )
        changes = indexer.detect_changes({"app.py": content_hash})

        assert changes.modified == ["app.py"]

    def test_engine_writes_v2_manifest_and_reuses_it(self, tmp_path):
        from src.engine import IndexEngine

        (tmp_path / "app.py").write_text("def run():\n    pass\n")
        index_dir = tmp_path / ".flyto-index"
        engine = IndexEngine("demo", tmp_path, index_dir=index_dir)

        engine.scan(incremental=False)
        manifest = json.loads((index_dir / "manifest.json").read_text())
        second = engine.scan(incremental=True)

        assert manifest["version"] == 2
        assert manifest["hash_algorithm"] == "sha256"
        assert len(manifest["pipeline_fingerprint"]) == 64
        assert len(manifest["files"]["app.py"]["hash"]) == 64
        assert second["changes"] == "+0 ~0 -0"


class TestDriftedManifestEviction:
    """Deleting a file must evict it even when the manifest lost the path."""

    def test_orphan_index_path_is_deleted_but_live_one_is_kept(self, tmp_path):
        from src.indexer.incremental import IncrementalIndexer, compute_file_hash

        index_dir = tmp_path / ".flyto-index"
        index_dir.mkdir()
        (tmp_path / "live.py").write_text("def live():\n    pass\n")
        (index_dir / "index.json").write_text(json.dumps({
            "files": {"gone/removed.py": {}, "live.py": {}},
        }))
        (index_dir / "manifest.json").write_text(json.dumps({
            "project": "demo",
            "version": 2,
            "hash_algorithm": "sha256",
            "pipeline_fingerprint": "a" * 64,
            "files": {},
        }))

        indexer = IncrementalIndexer(
            tmp_path,
            index_dir,
            pipeline_fingerprint="a" * 64,
        )
        # live.py is absent from current_files (an ignore rule, an unreadable
        # file) yet still on disk, so it must never be evicted.
        changes = indexer.detect_changes({})

        assert changes.deleted == ["gone/removed.py"]

    def test_vanished_directory_is_evicted_from_every_store(self, tmp_path):
        from src.engine import IndexEngine

        root = tmp_path / "project"
        (root / "src" / "components").mkdir(parents=True)
        (root / "keep").mkdir()
        (root / "keep" / "alive.py").write_text("def alive():\n    return 1\n")
        (root / "src" / "lonely.py").write_text("def lonely():\n    return 2\n")
        (root / "src" / "components" / "SettingsView.py").write_text(
            "class SettingsView:\n    def render(self):\n        return 3\n"
        )
        idx_dir = root / ".flyto-index"

        IndexEngine("project", root, index_dir=idx_dir).scan(incremental=False)

        # The manifest is the only source of eviction candidates; losing it
        # used to make every indexed path unreachable by ChangeSet.deleted.
        (idx_dir / "manifest.json").write_text("{ truncated")
        shutil.rmtree(root / "src")

        IndexEngine("project", root, index_dir=idx_dir).scan(incremental=True)

        index = json.loads((idx_dir / "index.json").read_text())
        bm25 = json.loads((idx_dir / "bm25.json").read_text())
        content = [
            json.loads(line)
            for line in (idx_dir / "content.jsonl").read_text().splitlines()
            if line.strip()
        ]

        assert set(index["files"]) == {"keep/alive.py"}
        assert not [sid for sid in index["symbols"] if ":src/" in sid]
        assert not [did for did in bm25["doc_ids"] if ":src/" in did]
        assert not [record for record in content if ":src/" in record["id"]]


# =============================================================================
# Incremental reverse_index tests
# =============================================================================

class TestIncrementalReverseIndex:
    """Test incremental reverse_index purge + re-add in the engine."""

    def test_full_rebuild_replaces_polluted_manifest(self, tmp_path):
        """A full rebuild must leave no sibling paths for the next scan."""
        from src.engine import IndexEngine

        root = tmp_path / "project"
        root.mkdir()
        (root / "app.py").write_text("def run():\n    return True\n")
        idx_dir = root / ".flyto-index"
        idx_dir.mkdir()
        (idx_dir / "manifest.json").write_text(json.dumps({
            "project": "",
            "version": 1,
            "files": {
                "sibling/src/foreign.py": {
                    "path": "sibling/src/foreign.py",
                    "hash": "stale",
                    "lines": 1,
                    "symbols": ["sibling:src/foreign.py:file:foreign"],
                },
            },
        }))

        engine = IndexEngine("project", root, index_dir=idx_dir)
        full = engine.scan(incremental=False)
        manifest = json.loads((idx_dir / "manifest.json").read_text())
        incremental = engine.scan(incremental=True)

        assert full["files_scanned"] == 1
        assert manifest["project"] == "project"
        assert set(manifest["files"]) == {"app.py"}
        assert incremental["changes"] == "+0 ~0 -0"
        assert incremental["files_scanned"] == 0

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

            engine.scan(incremental=True)
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

    def test_semantic_stale_rebuild_indexes_symbol_path_terms(self):
        """Lazy semantic rebuild should use the same searchable document as scan."""
        from src.engine import IndexEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            go_file = root / "ce" / "worker-ce" / "server.go"
            go_file.parent.mkdir(parents=True)
            go_file.write_text(
                "package main\n\n"
                "func newHandler() string {\n"
                "    return runQueueProbe()\n"
                "}\n\n"
                "func runQueueProbe() string {\n"
                "    return \"queue\"\n"
                "}\n",
                encoding="utf-8",
            )

            idx_dir = root / ".flyto-index"
            engine = IndexEngine("flyto-engine", root, index_dir=idx_dir)
            engine.scan(incremental=False)

            semantic_path = idx_dir / "semantic.json"
            semantic_path.unlink()
            stale_marker = idx_dir / ".semantic_stale"
            stale_marker.write_text("1", encoding="utf-8")

            import src.index_store as store
            old_index_dir = store.INDEX_DIR
            old_explicit = store._EXPLICIT_INDEX_DIR
            store.INDEX_DIR = idx_dir
            store._EXPLICIT_INDEX_DIR = str(idx_dir)
            store.invalidate_caches()

            try:
                semantic = store._load_semantic()
            finally:
                store.INDEX_DIR = old_index_dir
                store._EXPLICIT_INDEX_DIR = old_explicit
                store.invalidate_caches()

            result_ids = [sid for sid, _score in semantic.search("worker ce server")]
            assert any("newHandler" in sid for sid in result_ids)
            assert any("runQueueProbe" in sid for sid in result_ids)

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
