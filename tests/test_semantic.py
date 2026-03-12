"""Tests for semantic search engine (TF-IDF + concept expansion)."""

import json
import tempfile
from pathlib import Path

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from semantic import SemanticIndex, expand_concepts, _CONCEPT_MAP


class TestConceptExpansion:
    """Test concept taxonomy expansion."""

    def test_direct_concept(self):
        """Query containing a concept name expands to its terms."""
        expanded = expand_concepts("payment")
        assert "refund" in expanded
        assert "charge" in expanded
        assert "billing" in expanded
        assert "payment" in expanded

    def test_reverse_lookup(self):
        """Query containing a specific term finds sibling terms."""
        expanded = expand_concepts("refund")
        assert "charge" in expanded
        assert "billing" in expanded
        assert "stripe" in expanded

    def test_multi_concept(self):
        """Query spanning multiple concepts expands all."""
        expanded = expand_concepts("payment error")
        # Payment terms
        assert "refund" in expanded
        assert "charge" in expanded
        # Error terms
        assert "exception" in expanded
        assert "retry" in expanded

    def test_no_expansion(self):
        """Unknown terms are kept as-is without expansion."""
        expanded = expand_concepts("foobar")
        assert "foobar" in expanded
        assert len(expanded) == 1

    def test_empty_query(self):
        expanded = expand_concepts("")
        assert expanded == []


class TestSemanticIndex:
    """Test TF-IDF cosine similarity search."""

    @pytest.fixture
    def index(self):
        """Build a small test index."""
        idx = SemanticIndex()
        idx.build({
            "sym1": "process_refund handle payment failure stripe",
            "sym2": "LoginForm user authentication login form",
            "sym3": "DatabaseMigration schema migration sql table",
            "sym4": "handleError exception retry fallback recover",
            "sym5": "renderChart analytics dashboard visualization",
        })
        return idx

    def test_basic_search(self, index):
        """Search returns relevant results."""
        results = index.search("payment failure", top_k=5)
        assert len(results) > 0
        # sym1 (payment/refund) should be top result
        assert results[0][0] == "sym1"

    def test_concept_expansion_in_search(self, index):
        """Searching for a concept finds related code."""
        results = index.search("authentication", top_k=5)
        assert len(results) > 0
        # sym2 (login/auth) should rank high
        top_ids = [r[0] for r in results[:3]]
        assert "sym2" in top_ids

    def test_cross_concept_search(self, index):
        """Searching 'billing problem' should find payment + error code."""
        results = index.search("billing problem", top_k=5)
        top_ids = [r[0] for r in results[:3]]
        # Payment-related symbol should appear
        assert "sym1" in top_ids

    def test_empty_query(self, index):
        results = index.search("", top_k=5)
        assert results == []

    def test_empty_index(self):
        idx = SemanticIndex()
        idx.build({})
        assert idx.search("anything") == []

    def test_score_range(self, index):
        """All scores should be between 0 and 1 (cosine similarity)."""
        results = index.search("payment", top_k=10)
        for _, score in results:
            assert 0 <= score <= 1.0

    def test_save_load(self, index, tmp_path):
        """Index can be saved and loaded without loss."""
        save_path = tmp_path / "semantic.json"
        index.save(save_path)

        loaded = SemanticIndex.load(save_path)
        assert loaded is not None
        assert loaded.N == index.N
        assert loaded.doc_ids == index.doc_ids

        # Search should produce same results
        original = index.search("payment", top_k=5)
        restored = loaded.search("payment", top_k=5)
        assert len(original) == len(restored)
        for (id1, s1), (id2, s2) in zip(original, restored):
            assert id1 == id2
            assert abs(s1 - s2) < 0.001

    def test_load_missing_file(self):
        result = SemanticIndex.load(Path("/nonexistent/path.json"))
        assert result is None

    def test_load_corrupt_file(self, tmp_path):
        corrupt = tmp_path / "bad.json"
        corrupt.write_text("not json")
        result = SemanticIndex.load(corrupt)
        assert result is None


class TestConceptMapCoverage:
    """Verify concept map structure."""

    def test_all_concepts_have_terms(self):
        for concept, terms in _CONCEPT_MAP.items():
            assert len(terms) >= 3, f"Concept '{concept}' has too few terms: {terms}"

    def test_no_duplicate_concepts(self):
        concepts = list(_CONCEPT_MAP.keys())
        assert len(concepts) == len(set(concepts))
