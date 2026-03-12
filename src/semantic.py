"""
Semantic search engine — TF-IDF cosine similarity for natural language → code search.

Zero external dependencies. Bridges the gap between human queries ("handle payment
failure") and code symbols (process_refund, charge_customer) by:

1. Building TF-IDF vectors from symbol names + summaries + docstrings
2. Expanding queries with a concept taxonomy (payment → refund, charge, billing)
3. Computing cosine similarity for ranked results

Complements BM25 (exact term matching) with semantic-like fuzzy matching.
"""

import json
import math
import re
from pathlib import Path
from typing import Optional

try:
    from .bm25 import tokenize
except ImportError:
    from bm25 import tokenize


# ---------------------------------------------------------------------------
# Concept taxonomy — maps high-level concepts to code-level terms
# ---------------------------------------------------------------------------

_CONCEPT_MAP: dict[str, list[str]] = {
    # Auth & security
    "authentication": ["auth", "login", "logout", "token", "jwt", "session", "credential", "password", "oauth", "sso"],
    "authorization": ["permission", "role", "acl", "rbac", "guard", "policy", "scope", "privilege"],
    "security": ["encrypt", "decrypt", "hash", "salt", "csrf", "xss", "sanitize", "validate", "secret"],

    # Payment & billing
    "payment": ["pay", "charge", "refund", "invoice", "billing", "subscription", "stripe", "checkout", "price", "amount", "currency"],
    "pricing": ["plan", "tier", "quota", "usage", "metering", "discount", "coupon", "trial"],

    # Data & storage
    "database": ["db", "query", "sql", "table", "migration", "schema", "orm", "model", "record", "row", "column"],
    "cache": ["redis", "memcache", "ttl", "invalidate", "expire", "lru", "memoize"],
    "storage": ["file", "upload", "download", "blob", "s3", "bucket", "object", "stream"],

    # API & networking
    "api": ["endpoint", "route", "handler", "controller", "middleware", "request", "response", "rest", "graphql", "rpc"],
    "http": ["fetch", "axios", "get", "post", "put", "delete", "patch", "url", "header", "cookie"],
    "websocket": ["socket", "ws", "realtime", "subscribe", "publish", "broadcast", "channel"],

    # Error handling
    "error": ["exception", "catch", "throw", "raise", "fault", "failure", "retry", "fallback", "recover"],
    "logging": ["log", "logger", "debug", "info", "warn", "trace", "monitor", "metric", "telemetry"],

    # Testing
    "test": ["spec", "assert", "expect", "mock", "stub", "fixture", "setup", "teardown", "coverage", "pytest", "jest", "vitest"],

    # UI & frontend
    "form": ["input", "field", "validate", "submit", "select", "checkbox", "radio", "textarea", "datepicker"],
    "modal": ["dialog", "popup", "overlay", "drawer", "sheet", "confirm", "alert", "toast", "notification"],
    "table": ["grid", "column", "row", "sort", "filter", "paginate", "pagination", "dataTable"],
    "navigation": ["route", "router", "link", "breadcrumb", "menu", "sidebar", "tab", "navbar"],

    # State management
    "state": ["store", "reducer", "action", "dispatch", "selector", "mutation", "getter", "pinia", "vuex", "redux"],

    # Async & concurrency
    "async": ["await", "promise", "future", "task", "coroutine", "concurrent", "parallel", "queue", "worker", "thread"],

    # Configuration
    "config": ["setting", "option", "preference", "env", "environment", "variable", "flag", "feature"],

    # User management
    "user": ["profile", "account", "registration", "signup", "onboard", "avatar", "preference", "setting"],

    # Email & notifications
    "email": ["mail", "smtp", "template", "send", "notification", "digest", "newsletter", "campaign"],
    "notification": ["notify", "alert", "push", "badge", "bell", "toast", "snackbar"],

    # Deployment & CI/CD
    "deploy": ["release", "build", "pipeline", "ci", "cd", "docker", "container", "kubernetes", "k8s"],

    # Analytics & metrics
    "analytics": ["track", "event", "metric", "dashboard", "chart", "report", "insight", "kpi"],
}

# Build reverse map: term → concepts
_TERM_TO_CONCEPTS: dict[str, set[str]] = {}
for concept, terms in _CONCEPT_MAP.items():
    for term in terms:
        _TERM_TO_CONCEPTS.setdefault(term, set()).add(concept)
    _TERM_TO_CONCEPTS.setdefault(concept, set()).add(concept)


def expand_concepts(query: str) -> list[str]:
    """Expand a query with concept taxonomy terms.

    "handle payment failure" → ["handle", "payment", "failure",
     "pay", "charge", "refund", ..., "error", "exception", ...]
    """
    tokens = tokenize(query)
    expanded = set(tokens)

    for token in tokens:
        # Direct concept expansion
        if token in _CONCEPT_MAP:
            expanded.update(_CONCEPT_MAP[token])
        # Reverse lookup: if token is a specific term, add sibling terms
        if token in _TERM_TO_CONCEPTS:
            for concept in _TERM_TO_CONCEPTS[token]:
                expanded.update(_CONCEPT_MAP.get(concept, []))

    return list(expanded)


# ---------------------------------------------------------------------------
# TF-IDF Vector Engine
# ---------------------------------------------------------------------------

class SemanticIndex:
    """TF-IDF cosine similarity search over code symbols.

    Lighter than BM25 for semantic matching because:
    - Uses concept-expanded document vectors (precomputed at build time)
    - Queries are also concept-expanded
    - Cosine similarity normalizes for document length naturally
    """

    def __init__(self):
        self.doc_ids: list[str] = []
        self.doc_vectors: list[dict[str, float]] = []  # TF-IDF weights per doc
        self.idf: dict[str, float] = {}
        self.N: int = 0

    def build(self, documents: dict[str, str]):
        """Build TF-IDF index from documents.

        Args:
            documents: {symbol_id: text} — text should include name, summary, docstring
        """
        self.doc_ids = list(documents.keys())
        self.N = len(self.doc_ids)
        if self.N == 0:
            return

        # Phase 1: tokenize + compute DF
        doc_tokens: list[list[str]] = []
        df: dict[str, int] = {}

        for doc_id in self.doc_ids:
            tokens = tokenize(documents[doc_id])
            # Also add concept-expanded tokens (so "refund" doc matches "payment" query)
            expanded = set()
            for t in tokens:
                if t in _TERM_TO_CONCEPTS:
                    for concept in _TERM_TO_CONCEPTS[t]:
                        expanded.add(concept)
            all_tokens = tokens + list(expanded)
            doc_tokens.append(all_tokens)

            seen = set(all_tokens)
            for term in seen:
                df[term] = df.get(term, 0) + 1

        # Phase 2: compute IDF
        self.idf = {}
        for term, freq in df.items():
            self.idf[term] = math.log((self.N + 1) / (freq + 1)) + 1  # smoothed IDF

        # Phase 3: compute TF-IDF vectors (L2-normalized)
        self.doc_vectors = []
        for tokens in doc_tokens:
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1

            # TF-IDF weights
            vec: dict[str, float] = {}
            for term, count in tf.items():
                tfidf = (1 + math.log(count)) * self.idf.get(term, 0)
                if tfidf > 0:
                    vec[term] = tfidf

            # L2 normalize
            norm = math.sqrt(sum(v * v for v in vec.values())) if vec else 1.0
            if norm > 0:
                vec = {k: v / norm for k, v in vec.items()}

            self.doc_vectors.append(vec)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Semantic search: expand query with concepts, compute cosine similarity.

        Returns:
            List of (doc_id, similarity_score) tuples, highest first.
        """
        if self.N == 0:
            return []

        # Concept-expand the query
        expanded_tokens = expand_concepts(query)
        if not expanded_tokens:
            return []

        # Build query TF-IDF vector
        qtf: dict[str, int] = {}
        for t in expanded_tokens:
            qtf[t] = qtf.get(t, 0) + 1

        qvec: dict[str, float] = {}
        for term, count in qtf.items():
            tfidf = (1 + math.log(count)) * self.idf.get(term, 0)
            if tfidf > 0:
                qvec[term] = tfidf

        # L2 normalize query vector
        qnorm = math.sqrt(sum(v * v for v in qvec.values())) if qvec else 1.0
        if qnorm > 0:
            qvec = {k: v / qnorm for k, v in qvec.items()}

        # Cosine similarity (dot product of normalized vectors)
        scores: list[tuple[str, float]] = []
        for idx in range(self.N):
            dvec = self.doc_vectors[idx]
            # Dot product — only iterate over smaller set
            if len(qvec) < len(dvec):
                sim = sum(qvec[t] * dvec[t] for t in qvec if t in dvec)
            else:
                sim = sum(dvec[t] * qvec[t] for t in dvec if t in qvec)

            if sim > 0.01:  # threshold noise
                scores.append((self.doc_ids[idx], sim))

        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]

    def save(self, path: Path):
        """Save semantic index to JSON."""
        try:
            from .safe_io import atomic_write_json
        except ImportError:
            from safe_io import atomic_write_json

        data = {
            "doc_ids": self.doc_ids,
            "N": self.N,
            "idf": self.idf,
            "doc_vectors": self.doc_vectors,
        }
        atomic_write_json(path, data, indent=0)

    @classmethod
    def load(cls, path: Path) -> Optional["SemanticIndex"]:
        """Load semantic index from JSON. Returns None if not found."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            idx = cls()
            idx.doc_ids = data["doc_ids"]
            idx.N = data["N"]
            idx.idf = data["idf"]
            idx.doc_vectors = data["doc_vectors"]
            return idx
        except (json.JSONDecodeError, KeyError, OSError):
            return None
