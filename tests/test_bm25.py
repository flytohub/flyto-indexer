"""Tests for BM25 search index."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from bm25 import BM25Index, tokenize


# =============================================================================
# Tokenizer tests
# =============================================================================

class TestTokenize:
    """Test the tokenize function."""

    def test_simple_words(self):
        tokens = tokenize("hello world")
        assert "hello" in tokens
        assert "world" in tokens

    def test_camel_case_split(self):
        tokens = tokenize("useAuthToken")
        assert "use" in tokens
        assert "auth" in tokens
        assert "token" in tokens

    def test_pascal_case_split(self):
        tokens = tokenize("LoginFormComponent")
        assert "login" in tokens
        assert "form" in tokens
        assert "component" in tokens

    def test_snake_case_split(self):
        tokens = tokenize("get_user_by_id")
        assert "get" in tokens
        assert "user" in tokens
        assert "id" in tokens

    def test_mixed_format(self):
        tokens = tokenize("src/composables/useAuth.ts")
        assert "src" in tokens
        assert "composables" in tokens
        assert "use" in tokens
        assert "auth" in tokens
        assert "ts" in tokens

    def test_single_char_filtered(self):
        tokens = tokenize("a b c hello")
        assert "hello" in tokens
        # Single chars should be filtered
        assert "a" not in tokens
        assert "b" not in tokens

    def test_lowercase(self):
        tokens = tokenize("HTTP Request GET")
        assert all(t.islower() for t in tokens)

    def test_empty_string(self):
        tokens = tokenize("")
        assert tokens == []

    def test_abbreviations(self):
        tokens = tokenize("HTMLParser")
        # Should split HTML and Parser
        assert "html" in tokens or "htmlparser" in tokens
        assert "parser" in tokens


# =============================================================================
# BM25 Index tests
# =============================================================================

class TestBM25Index:
    """Test BM25 index build and search."""

    @pytest.fixture
    def sample_docs(self):
        return {
            "sym1": "useAuth composable authentication login token JWT",
            "sym2": "LoginForm component form user login password",
            "sym3": "fetchUsers function API users list pagination",
            "sym4": "UserService class service user CRUD operations",
            "sym5": "validateEmail function validation email format regex",
        }

    @pytest.fixture
    def built_index(self, sample_docs):
        idx = BM25Index()
        idx.build(sample_docs)
        return idx

    def test_build_basic(self, built_index, sample_docs):
        assert built_index.N == 5
        assert len(built_index.doc_ids) == 5
        assert built_index.avgdl > 0

    def test_search_exact_name(self, built_index):
        results = built_index.search("useAuth")
        assert len(results) > 0
        # sym1 should be the top result (contains "useAuth" directly)
        assert results[0][0] == "sym1"

    def test_search_related_terms(self, built_index):
        results = built_index.search("authentication login")
        assert len(results) > 0
        # sym1 and sym2 both have "login"
        result_ids = [r[0] for r in results]
        assert "sym1" in result_ids
        assert "sym2" in result_ids

    def test_search_email(self, built_index):
        results = built_index.search("email validation")
        assert len(results) > 0
        assert results[0][0] == "sym5"

    def test_search_user(self, built_index):
        results = built_index.search("user")
        assert len(results) > 0
        # Multiple docs mention "user"
        result_ids = [r[0] for r in results]
        assert len(result_ids) >= 2

    def test_search_no_match(self, built_index):
        results = built_index.search("zzzznonexistent")
        assert len(results) == 0

    def test_search_empty_query(self, built_index):
        results = built_index.search("")
        assert len(results) == 0

    def test_top_k_limit(self, built_index):
        results = built_index.search("user", top_k=2)
        assert len(results) <= 2

    def test_scores_are_positive(self, built_index):
        results = built_index.search("login")
        for _, score in results:
            assert score > 0

    def test_scores_are_ordered(self, built_index):
        results = built_index.search("user login")
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_empty_index(self):
        idx = BM25Index()
        idx.build({})
        results = idx.search("anything")
        assert results == []

    def test_single_doc(self):
        idx = BM25Index()
        idx.build({"only": "the only document about testing"})
        results = idx.search("testing")
        assert len(results) == 1
        assert results[0][0] == "only"

    def test_idf_effect(self):
        """Rare terms should score higher than common terms."""
        docs = {
            "doc1": "common word rare_unique_xyz",
            "doc2": "common word another",
            "doc3": "common word yet_another",
        }
        idx = BM25Index()
        idx.build(docs)

        # "rare_unique_xyz" only in doc1 — should get higher IDF
        results = idx.search("rare_unique_xyz")
        assert len(results) == 1
        assert results[0][0] == "doc1"

        # Searching "common" should return all 3
        results = idx.search("common")
        assert len(results) == 3


# =============================================================================
# Persistence tests
# =============================================================================

class TestBM25Persistence:
    """Test save/load of BM25 index."""

    def test_save_and_load(self):
        docs = {
            "a": "hello world function",
            "b": "goodbye world class",
        }
        idx = BM25Index()
        idx.build(docs)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)

        try:
            idx.save(tmp_path)
            loaded = BM25Index.load(tmp_path)

            assert loaded is not None
            assert loaded.N == idx.N
            assert loaded.doc_ids == idx.doc_ids
            assert loaded.avgdl == idx.avgdl

            # Search should produce same results
            r1 = idx.search("hello")
            r2 = loaded.search("hello")
            assert len(r1) == len(r2)
            assert r1[0][0] == r2[0][0]
            assert abs(r1[0][1] - r2[0][1]) < 0.001
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_load_missing_file(self):
        result = BM25Index.load(Path("/nonexistent/path/bm25.json"))
        assert result is None

    def test_load_corrupted_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("not valid json {{{")
            tmp_path = Path(f.name)

        try:
            result = BM25Index.load(tmp_path)
            assert result is None
        finally:
            tmp_path.unlink(missing_ok=True)


# =============================================================================
# Integration with engine
# =============================================================================

class TestBM25EngineIntegration:
    """Test BM25 index is built during engine scan."""

    def test_bm25_file_created_after_scan(self):
        from src.engine import IndexEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Create a simple Python file
            py_file = root / "example.py"
            py_file.write_text('''
def calculate_total(items):
    """Calculate the total price of items."""
    return sum(item.price for item in items)

class ShoppingCart:
    """Shopping cart for e-commerce."""
    def add_item(self, item):
        self.items.append(item)
''')

            idx_dir = root / ".flyto-index"
            engine = IndexEngine("test", root, index_dir=idx_dir)
            engine.scan(incremental=False)

            bm25_path = idx_dir / "bm25.json"
            assert bm25_path.exists(), "bm25.json should be created after scan"

            # Load and verify
            loaded = BM25Index.load(bm25_path)
            assert loaded is not None
            assert loaded.N > 0

            # Search should work
            results = loaded.search("shopping cart")
            assert len(results) > 0

    def test_bm25_search_ranks_correctly(self):
        from src.engine import IndexEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            py_file = root / "auth.py"
            py_file.write_text('''
def authenticate_user(username, password):
    """Authenticate user with credentials."""
    pass

def list_products():
    """List all products in catalog."""
    pass
''')

            idx_dir = root / ".flyto-index"
            engine = IndexEngine("test", root, index_dir=idx_dir)
            engine.scan(incremental=False)

            loaded = BM25Index.load(idx_dir / "bm25.json")
            results = loaded.search("authenticate user")

            # "authenticate_user" should rank higher than "list_products"
            result_ids = [r[0] for r in results]
            assert len(result_ids) >= 1
            # The first result should be the auth function
            first_sym = result_ids[0]
            assert "authenticate" in first_sym.lower() or "auth" in first_sym.lower()
