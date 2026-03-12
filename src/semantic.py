"""
Semantic search engine — learned concept graph from code structure.

Zero external dependencies. No manual keyword maps. Learns concept relationships
from the codebase itself:

1. File co-occurrence: symbols in the same file share terms
2. Import graph: connected symbols have related vocabulary
3. Shared callers: symbols called by the same code are semantically similar

At build time, computes term co-occurrence (PMI) from these signals.
At query time, expands query terms using the learned graph, then runs
TF-IDF cosine similarity for ranked results.
"""

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Optional

try:
    from .bm25 import tokenize
except ImportError:
    from bm25 import tokenize


# ---------------------------------------------------------------------------
# Learned concept graph — built from code structure, not manual maps
# ---------------------------------------------------------------------------

class ConceptGraph:
    """Term co-occurrence graph learned from codebase structure.

    For each term, stores the top related terms with association strength.
    Built from three signals:
    - File co-location (symbols in same file)
    - Import edges (dependency graph)
    - Shared callers (reverse index)
    """

    def __init__(self):
        self.related: dict[str, list[tuple[str, float]]] = {}  # term -> [(related_term, weight)]

    @classmethod
    def build_from_index(cls, index_data: dict) -> "ConceptGraph":
        """Build concept graph from a flyto-indexer index dict.

        Args:
            index_data: The full index dict with "symbols", "files",
                        "dependencies", "reverse_index" keys.
        """
        graph = cls()
        symbols = index_data.get("symbols", {})
        files = index_data.get("files", {})
        deps = index_data.get("dependencies", {})
        reverse_index = index_data.get("reverse_index", {})

        # Collect token sets per symbol
        sym_tokens: dict[str, set[str]] = {}
        for sid, sym in symbols.items():
            name = sym.get("name", "") if isinstance(sym, dict) else sym.name
            summary = sym.get("summary", "") if isinstance(sym, dict) else sym.summary
            text = f"{name} {summary}"
            tokens = set(tokenize(text))
            if tokens:
                sym_tokens[sid] = tokens

        # --- Signal 1: File co-location ---
        # Group symbols by file path
        file_to_syms: dict[str, list[str]] = defaultdict(list)
        for sid in sym_tokens:
            # Extract path from symbol_id: "project:path:type:name"
            parts = sid.split(":")
            if len(parts) >= 3:
                path = parts[1]
                file_to_syms[path].append(sid)

        # Also use files manifest if available
        for path, fdata in files.items():
            if isinstance(fdata, dict):
                for sid in fdata.get("symbols", []):
                    if sid in sym_tokens and sid not in file_to_syms.get(path, []):
                        file_to_syms[path].append(sid)

        # Count co-occurrences: terms that appear together in same-file symbols
        cooccur: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

        for path, sids in file_to_syms.items():
            if len(sids) < 2:
                continue
            # Merge all tokens in this file
            file_tokens: list[set[str]] = [sym_tokens[s] for s in sids if s in sym_tokens]
            if len(file_tokens) < 2:
                continue

            # For each pair of symbol token sets, record co-occurrence
            for i in range(len(file_tokens)):
                for j in range(i + 1, len(file_tokens)):
                    for t1 in file_tokens[i]:
                        for t2 in file_tokens[j]:
                            if t1 != t2:
                                cooccur[t1][t2] += 1.0
                                cooccur[t2][t1] += 1.0

        # --- Signal 2: Import edges ---
        for dep_id, dep in deps.items():
            if isinstance(dep, dict):
                src = dep.get("source", "")
                tgt = dep.get("target", "")
            else:
                src = dep.source_id
                tgt = dep.target_id

            src_tokens = sym_tokens.get(src, set())
            tgt_tokens = sym_tokens.get(tgt, set())
            if src_tokens and tgt_tokens:
                for t1 in src_tokens:
                    for t2 in tgt_tokens:
                        if t1 != t2:
                            cooccur[t1][t2] += 2.0  # imports weighted higher
                            cooccur[t2][t1] += 2.0

        # --- Signal 3: Shared callers ---
        # Build caller→callees map, then find symbols that share callers
        caller_to_callees: dict[str, set[str]] = defaultdict(set)
        for callee_sid, callers in reverse_index.items():
            if isinstance(callers, list):
                for caller in callers:
                    caller_id = caller if isinstance(caller, str) else caller.get("id", "")
                    if caller_id:
                        caller_to_callees[caller_id].add(callee_sid)

        for caller_id, callees in caller_to_callees.items():
            callee_list = [c for c in callees if c in sym_tokens]
            if len(callee_list) < 2:
                continue
            for i in range(len(callee_list)):
                for j in range(i + 1, len(callee_list)):
                    for t1 in sym_tokens[callee_list[i]]:
                        for t2 in sym_tokens[callee_list[j]]:
                            if t1 != t2:
                                cooccur[t1][t2] += 1.5  # shared caller weight
                                cooccur[t2][t1] += 1.5

        # --- Compute PMI (Pointwise Mutual Information) ---
        # PMI(x,y) = log(P(x,y) / (P(x) * P(y)))
        term_freq: dict[str, float] = defaultdict(float)
        total_cooccur = 0.0
        for t1, neighbors in cooccur.items():
            for t2, count in neighbors.items():
                term_freq[t1] += count
                total_cooccur += count

        if total_cooccur == 0:
            return graph

        # For each term, keep top-K related terms by PMI
        max_related = 15
        for term, neighbors in cooccur.items():
            if term_freq[term] < 2:  # skip very rare terms
                continue
            scored = []
            for related, count in neighbors.items():
                if term_freq[related] < 2:
                    continue
                # PMI with Laplace smoothing
                p_xy = (count + 0.1) / total_cooccur
                p_x = term_freq[term] / total_cooccur
                p_y = term_freq[related] / total_cooccur
                pmi = math.log(p_xy / (p_x * p_y + 1e-10))
                if pmi > 0:  # only positive associations
                    scored.append((related, round(pmi, 3)))

            scored.sort(key=lambda x: -x[1])
            if scored:
                graph.related[term] = scored[:max_related]

        return graph

    def expand(self, query: str, max_expansion: int = 20) -> list[str]:
        """Expand query terms using learned co-occurrence.

        Returns original tokens + related terms, weighted by association strength.
        """
        tokens = tokenize(query)
        if not tokens:
            return []

        expanded = set(tokens)
        # Collect candidates with their total PMI weight
        candidates: dict[str, float] = {}
        for token in tokens:
            for related, weight in self.related.get(token, []):
                if related not in expanded:
                    candidates[related] = candidates.get(related, 0) + weight

        # Take top-N by weight
        ranked = sorted(candidates.items(), key=lambda x: -x[1])
        for term, _ in ranked[:max_expansion]:
            expanded.add(term)

        return list(expanded)

    def to_dict(self) -> dict:
        return {"related": self.related}

    @classmethod
    def from_dict(cls, data: dict) -> "ConceptGraph":
        graph = cls()
        raw = data.get("related", {})
        # Convert lists back to list of tuples
        for term, neighbors in raw.items():
            graph.related[term] = [(n[0], n[1]) for n in neighbors]
        return graph


# ---------------------------------------------------------------------------
# TF-IDF Vector Engine (uses learned concepts instead of manual map)
# ---------------------------------------------------------------------------

class SemanticIndex:
    """TF-IDF cosine similarity search with code-derived concept expansion.

    At build time:
    - Learns concept relationships from file co-location, imports, shared callers
    - Builds TF-IDF vectors with concept-expanded terms

    At query time:
    - Expands query using learned concept graph (not manual taxonomy)
    - Computes cosine similarity for ranked results
    """

    def __init__(self):
        self.doc_ids: list[str] = []
        self.doc_vectors: list[dict[str, float]] = []
        self.idf: dict[str, float] = {}
        self.N: int = 0
        self.concept_graph: ConceptGraph = ConceptGraph()

    def build(self, documents: dict[str, str], index_data: dict = None):
        """Build semantic index from documents + optional index structure.

        Args:
            documents: {symbol_id: text} mapping
            index_data: Full index dict for building concept graph.
                        If None, concept expansion is disabled (plain TF-IDF).
        """
        self.doc_ids = list(documents.keys())
        self.N = len(self.doc_ids)
        if self.N == 0:
            return

        # Build concept graph from code structure
        if index_data:
            self.concept_graph = ConceptGraph.build_from_index(index_data)

        # Phase 1: tokenize + concept-expand + compute DF
        doc_tokens: list[list[str]] = []
        df: dict[str, int] = {}

        for doc_id in self.doc_ids:
            tokens = tokenize(documents[doc_id])
            # Expand with learned concepts (not manual map)
            if self.concept_graph.related:
                expanded = set()
                for t in tokens:
                    for related, weight in self.concept_graph.related.get(t, []):
                        if weight > 0.5:  # only strong associations
                            expanded.add(related)
                all_tokens = tokens + list(expanded)
            else:
                all_tokens = tokens
            doc_tokens.append(all_tokens)

            seen = set(all_tokens)
            for term in seen:
                df[term] = df.get(term, 0) + 1

        # Phase 2: compute IDF
        self.idf = {}
        for term, freq in df.items():
            self.idf[term] = math.log((self.N + 1) / (freq + 1)) + 1

        # Phase 3: compute TF-IDF vectors (L2-normalized)
        self.doc_vectors = []
        for tokens in doc_tokens:
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1

            vec: dict[str, float] = {}
            for term, count in tf.items():
                tfidf = (1 + math.log(count)) * self.idf.get(term, 0)
                if tfidf > 0:
                    vec[term] = tfidf

            norm = math.sqrt(sum(v * v for v in vec.values())) if vec else 1.0
            if norm > 0:
                vec = {k: v / norm for k, v in vec.items()}

            self.doc_vectors.append(vec)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Search using learned concept expansion + cosine similarity."""
        if self.N == 0:
            return []

        # Expand query with learned concepts
        if self.concept_graph.related:
            expanded_tokens = self.concept_graph.expand(query)
        else:
            expanded_tokens = tokenize(query)

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

        qnorm = math.sqrt(sum(v * v for v in qvec.values())) if qvec else 1.0
        if qnorm > 0:
            qvec = {k: v / qnorm for k, v in qvec.items()}

        # Cosine similarity
        scores: list[tuple[str, float]] = []
        for idx in range(self.N):
            dvec = self.doc_vectors[idx]
            if len(qvec) < len(dvec):
                sim = sum(qvec[t] * dvec[t] for t in qvec if t in dvec)
            else:
                sim = sum(dvec[t] * qvec[t] for t in dvec if t in qvec)

            if sim > 0.01:
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
            "version": 2,
            "doc_ids": self.doc_ids,
            "N": self.N,
            "idf": self.idf,
            "doc_vectors": self.doc_vectors,
            "concept_graph": self.concept_graph.to_dict(),
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
            if "concept_graph" in data:
                idx.concept_graph = ConceptGraph.from_dict(data["concept_graph"])
            return idx
        except (json.JSONDecodeError, KeyError, OSError):
            return None


# Backward compat: expose expand_concepts using learned graph
def expand_concepts(query: str) -> list[str]:
    """Expand query — stub for backward compat. Returns tokenized query."""
    return tokenize(query)
