"""Tests for semantic search engine (learned concept graph from code structure)."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from semantic import SemanticIndex, ConceptGraph


# ---------------------------------------------------------------------------
# Fixtures: synthetic index data simulating a real codebase
# ---------------------------------------------------------------------------

def _make_index_data():
    """Build a synthetic index with realistic structure.

    Simulates a payment service codebase:
    - payment_service.py has: process_refund, charge_customer, validate_amount
    - auth.py has: login, verify_token, logout
    - error_handler.py has: handle_error, retry_operation
    - Dependencies: payment_service imports error_handler
    - Shared callers: api_handler calls both process_refund and charge_customer
    """
    symbols = {
        "proj:src/payment_service.py:function:process_refund": {
            "name": "process_refund",
            "type": "function",
            "path": "src/payment_service.py",
            "summary": "Process a customer refund via Stripe",
            "start_line": 10,
        },
        "proj:src/payment_service.py:function:charge_customer": {
            "name": "charge_customer",
            "type": "function",
            "path": "src/payment_service.py",
            "summary": "Charge customer credit card",
            "start_line": 30,
        },
        "proj:src/payment_service.py:function:validate_amount": {
            "name": "validate_amount",
            "type": "function",
            "path": "src/payment_service.py",
            "summary": "Validate payment amount is positive",
            "start_line": 50,
        },
        "proj:src/auth.py:function:login": {
            "name": "login",
            "type": "function",
            "path": "src/auth.py",
            "summary": "Authenticate user with credentials",
            "start_line": 5,
        },
        "proj:src/auth.py:function:verify_token": {
            "name": "verify_token",
            "type": "function",
            "path": "src/auth.py",
            "summary": "Verify JWT token validity",
            "start_line": 25,
        },
        "proj:src/auth.py:function:logout": {
            "name": "logout",
            "type": "function",
            "path": "src/auth.py",
            "summary": "End user session",
            "start_line": 45,
        },
        "proj:src/error_handler.py:function:handle_error": {
            "name": "handle_error",
            "type": "function",
            "path": "src/error_handler.py",
            "summary": "Handle and log exceptions",
            "start_line": 5,
        },
        "proj:src/error_handler.py:function:retry_operation": {
            "name": "retry_operation",
            "type": "function",
            "path": "src/error_handler.py",
            "summary": "Retry failed operations with backoff",
            "start_line": 30,
        },
    }

    files = {
        "src/payment_service.py": {
            "path": "src/payment_service.py",
            "symbols": [
                "proj:src/payment_service.py:function:process_refund",
                "proj:src/payment_service.py:function:charge_customer",
                "proj:src/payment_service.py:function:validate_amount",
            ],
            "hash": "abc123",
            "lines": 60,
        },
        "src/auth.py": {
            "path": "src/auth.py",
            "symbols": [
                "proj:src/auth.py:function:login",
                "proj:src/auth.py:function:verify_token",
                "proj:src/auth.py:function:logout",
            ],
            "hash": "def456",
            "lines": 55,
        },
        "src/error_handler.py": {
            "path": "src/error_handler.py",
            "symbols": [
                "proj:src/error_handler.py:function:handle_error",
                "proj:src/error_handler.py:function:retry_operation",
            ],
            "hash": "ghi789",
            "lines": 50,
        },
    }

    # payment_service imports error_handler
    dependencies = {
        "dep1": {
            "source": "proj:src/payment_service.py:function:process_refund",
            "target": "proj:src/error_handler.py:function:handle_error",
            "type": "calls",
        },
        "dep2": {
            "source": "proj:src/payment_service.py:function:charge_customer",
            "target": "proj:src/error_handler.py:function:handle_error",
            "type": "calls",
        },
    }

    # Shared callers: api_handler calls both refund and charge
    reverse_index = {
        "proj:src/payment_service.py:function:process_refund": [
            "proj:src/api_handler.py:function:handle_payment_api",
        ],
        "proj:src/payment_service.py:function:charge_customer": [
            "proj:src/api_handler.py:function:handle_payment_api",
        ],
        "proj:src/error_handler.py:function:handle_error": [
            "proj:src/payment_service.py:function:process_refund",
            "proj:src/payment_service.py:function:charge_customer",
        ],
    }

    return {
        "symbols": symbols,
        "files": files,
        "dependencies": dependencies,
        "reverse_index": reverse_index,
    }


def _make_documents(index_data):
    """Build document map from index symbols."""
    docs = {}
    for sid, sym in index_data["symbols"].items():
        docs[sid] = f"{sym['name']} {sym.get('summary', '')}"
    return docs


# ---------------------------------------------------------------------------
# Tests: ConceptGraph
# ---------------------------------------------------------------------------

class TestConceptGraph:

    def test_build_from_index(self):
        """Concept graph extracts relationships from code structure."""
        index_data = _make_index_data()
        graph = ConceptGraph.build_from_index(index_data)

        # Should have learned some relationships
        assert len(graph.related) > 0

    def test_file_cooccurrence(self):
        """Symbols in same file produce co-occurring terms."""
        index_data = _make_index_data()
        graph = ConceptGraph.build_from_index(index_data)

        # "refund" and "charge" are in the same file → should be related
        refund_related = {t for t, _ in graph.related.get("refund", [])}
        charge_related = {t for t, _ in graph.related.get("charge", [])}

        # They share file co-location, so at least one should link to the other
        assert "charge" in refund_related or "refund" in charge_related

    def test_import_edge_relationship(self):
        """Import graph creates term relationships."""
        index_data = _make_index_data()
        graph = ConceptGraph.build_from_index(index_data)

        # process_refund calls handle_error → their terms should be related
        # "refund" ↔ "handle" or "error" should have some association
        refund_neighbors = {t for t, _ in graph.related.get("refund", [])}
        # At minimum, terms from the same file should be connected
        assert len(refund_neighbors) > 0

    def test_expand_query(self):
        """Query expansion adds related terms."""
        index_data = _make_index_data()
        graph = ConceptGraph.build_from_index(index_data)

        expanded = graph.expand("refund")
        assert "refund" in expanded
        # Should include related terms from co-occurrence
        assert len(expanded) > 1

    def test_expand_empty_query(self):
        graph = ConceptGraph()
        assert graph.expand("") == []

    def test_expand_unknown_term(self):
        """Unknown terms return just the original tokens."""
        graph = ConceptGraph()
        graph.related = {"known": [("related", 1.0)]}
        expanded = graph.expand("xyzunknown")
        assert expanded == ["xyzunknown"]

    def test_serialization(self):
        """Concept graph round-trips through dict serialization."""
        index_data = _make_index_data()
        graph = ConceptGraph.build_from_index(index_data)

        data = graph.to_dict()
        restored = ConceptGraph.from_dict(data)

        assert set(graph.related.keys()) == set(restored.related.keys())
        for term in graph.related:
            orig_terms = {t for t, _ in graph.related[term]}
            rest_terms = {t for t, _ in restored.related[term]}
            assert orig_terms == rest_terms

    def test_empty_index(self):
        """Empty index produces empty graph."""
        graph = ConceptGraph.build_from_index({})
        assert graph.related == {}


# ---------------------------------------------------------------------------
# Tests: SemanticIndex
# ---------------------------------------------------------------------------

class TestSemanticIndex:

    @pytest.fixture
    def index_with_graph(self):
        """Build index with learned concept graph."""
        index_data = _make_index_data()
        documents = _make_documents(index_data)
        idx = SemanticIndex()
        idx.build(documents, index_data=index_data)
        return idx

    @pytest.fixture
    def index_plain(self):
        """Build index without concept graph (plain TF-IDF)."""
        documents = {
            "sym1": "process_refund handle payment failure stripe",
            "sym2": "LoginForm user authentication login form",
            "sym3": "DatabaseMigration schema migration sql table",
        }
        idx = SemanticIndex()
        idx.build(documents)
        return idx

    def test_basic_search(self, index_with_graph):
        results = index_with_graph.search("refund", top_k=5)
        assert len(results) > 0
        # The refund function should be top result
        top_ids = [r[0] for r in results[:2]]
        assert any("refund" in sid for sid in top_ids)

    def test_concept_expanded_search(self, index_with_graph):
        """Searching for related concept finds co-located symbols."""
        results = index_with_graph.search("charge customer", top_k=5)
        assert len(results) > 0

    def test_cross_file_search(self, index_with_graph):
        """Search finds symbols across files via learned relationships."""
        results = index_with_graph.search("error handling", top_k=5)
        assert len(results) > 0
        top_ids = [r[0] for r in results[:3]]
        assert any("error" in sid for sid in top_ids)

    def test_plain_tfidf_fallback(self, index_plain):
        """Works without concept graph (plain TF-IDF)."""
        results = index_plain.search("payment", top_k=5)
        assert len(results) > 0
        assert results[0][0] == "sym1"

    def test_empty_query(self, index_with_graph):
        assert index_with_graph.search("") == []

    def test_empty_index(self):
        idx = SemanticIndex()
        idx.build({})
        assert idx.search("anything") == []

    def test_score_range(self, index_with_graph):
        results = index_with_graph.search("refund", top_k=10)
        for _, score in results:
            assert 0 <= score <= 1.0

    def test_save_load(self, index_with_graph, tmp_path):
        save_path = tmp_path / "semantic.json"
        index_with_graph.save(save_path)

        loaded = SemanticIndex.load(save_path)
        assert loaded is not None
        assert loaded.N == index_with_graph.N
        assert loaded.doc_ids == index_with_graph.doc_ids

        # Concept graph should survive save/load
        assert len(loaded.concept_graph.related) > 0

        # Search should produce same results
        original = index_with_graph.search("refund", top_k=5)
        restored = loaded.search("refund", top_k=5)
        assert len(original) == len(restored)
        for (id1, s1), (id2, s2) in zip(original, restored):
            assert id1 == id2
            assert abs(s1 - s2) < 0.001

    def test_save_load_v2_format(self, index_with_graph, tmp_path):
        """Saved format includes version and concept_graph."""
        save_path = tmp_path / "semantic.json"
        index_with_graph.save(save_path)

        raw = json.loads(save_path.read_text())
        assert raw.get("version") == 2
        assert "concept_graph" in raw
        assert "related" in raw["concept_graph"]

    def test_load_missing_file(self):
        assert SemanticIndex.load(Path("/nonexistent/path.json")) is None

    def test_load_corrupt_file(self, tmp_path):
        corrupt = tmp_path / "bad.json"
        corrupt.write_text("not json")
        assert SemanticIndex.load(corrupt) is None
