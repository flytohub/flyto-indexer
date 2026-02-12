"""
Pure-Python BM25 (Okapi BM25) index for code search.

Zero external dependencies. Replaces naive "keyword in name" boolean matching
with proper information retrieval scoring that considers:
- Term frequency (TF)
- Inverse document frequency (IDF)
- Document length normalization

Usage:
    idx = BM25Index()
    idx.build({"sym1": "useAuth composable authentication", "sym2": "LoginForm component"})
    results = idx.search("auth login", top_k=10)
    # => [("sym1", 1.23), ("sym2", 0.87)]
"""

import json
import math
import re
from pathlib import Path
from typing import Optional

# camelCase / PascalCase splitter: "useAuthToken" -> ["use", "Auth", "Token"]
_CAMEL_SPLIT = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')

# snake_case / kebab-case splitter
_SEPARATOR_SPLIT = re.compile(r'[_\-./:\\]+')


def tokenize(text: str) -> list[str]:
    """
    Tokenize text for BM25 indexing.

    Splits on whitespace, camelCase boundaries, underscores, hyphens, dots.
    Returns lowercased tokens.
    """
    tokens = []
    # First split on whitespace and separators
    for word in re.findall(r'\w+', text):
        # Split camelCase
        parts = _CAMEL_SPLIT.split(word)
        for part in parts:
            # Further split on separators
            sub_parts = _SEPARATOR_SPLIT.split(part)
            for sp in sub_parts:
                if sp and len(sp) >= 2:  # Skip single chars
                    tokens.append(sp.lower())
    return tokens


class BM25Index:
    """
    Okapi BM25 index for symbol search.

    Parameters:
        k1: Term frequency saturation. Higher = more weight on repeated terms. (default 1.5)
        b: Length normalization. 0 = no normalization, 1 = full. (default 0.75)
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

        # Index state
        self.doc_ids: list[str] = []           # [doc_id, ...]
        self.doc_lens: list[int] = []           # [token_count, ...]
        self.avgdl: float = 0.0                 # Average document length
        self.N: int = 0                         # Total document count
        self.df: dict[str, int] = {}            # term -> document frequency
        self.idf: dict[str, float] = {}         # term -> IDF score
        self.tf: list[dict[str, int]] = []      # [doc_idx -> {term: count}]

    def build(self, documents: dict[str, str]):
        """
        Build BM25 index from documents.

        Args:
            documents: {doc_id: text_content} mapping
        """
        self.doc_ids = list(documents.keys())
        self.N = len(self.doc_ids)
        self.df = {}
        self.tf = []
        self.doc_lens = []

        total_len = 0

        for doc_id in self.doc_ids:
            text = documents[doc_id]
            tokens = tokenize(text)
            self.doc_lens.append(len(tokens))
            total_len += len(tokens)

            # Term frequency for this document
            tf_map: dict[str, int] = {}
            seen_terms: set[str] = set()
            for token in tokens:
                tf_map[token] = tf_map.get(token, 0) + 1
                seen_terms.add(token)
            self.tf.append(tf_map)

            # Document frequency
            for term in seen_terms:
                self.df[term] = self.df.get(term, 0) + 1

        # Average document length
        self.avgdl = total_len / self.N if self.N > 0 else 0

        # Precompute IDF
        self.idf = {}
        for term, df in self.df.items():
            # Standard BM25 IDF: log((N - df + 0.5) / (df + 0.5) + 1)
            self.idf[term] = math.log((self.N - df + 0.5) / (df + 0.5) + 1)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """
        Search for query, return ranked results.

        Args:
            query: Search query string
            top_k: Maximum results to return

        Returns:
            List of (doc_id, score) tuples, highest score first
        """
        if self.N == 0:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores: list[tuple[str, float]] = []

        for idx in range(self.N):
            score = 0.0
            dl = self.doc_lens[idx]
            tf_map = self.tf[idx]

            for term in query_tokens:
                if term not in self.idf:
                    continue

                tf = tf_map.get(term, 0)
                if tf == 0:
                    continue

                idf = self.idf[term]
                # BM25 score for this term
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                score += idf * numerator / denominator

            if score > 0:
                scores.append((self.doc_ids[idx], score))

        # Sort by score descending
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]

    def save(self, path: Path):
        """Save BM25 index to JSON file."""
        data = {
            "k1": self.k1,
            "b": self.b,
            "doc_ids": self.doc_ids,
            "doc_lens": self.doc_lens,
            "avgdl": self.avgdl,
            "N": self.N,
            "df": self.df,
            "idf": self.idf,
            "tf": self.tf,
        }
        path.write_text(json.dumps(data, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> Optional["BM25Index"]:
        """Load BM25 index from JSON file. Returns None if file doesn't exist."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            idx = cls(k1=data.get("k1", 1.5), b=data.get("b", 0.75))
            idx.doc_ids = data["doc_ids"]
            idx.doc_lens = data["doc_lens"]
            idx.avgdl = data["avgdl"]
            idx.N = data["N"]
            idx.df = data["df"]
            idx.idf = data["idf"]
            idx.tf = data["tf"]
            return idx
        except (json.JSONDecodeError, KeyError, OSError):
            return None
