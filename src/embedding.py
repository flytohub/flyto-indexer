"""
Embedding-based semantic search — optional hybrid layer.

When available, combines BM25 lexical search with dense vector similarity
for better natural language query handling.

Requires: sentence-transformers (optional dependency)
Storage: .flyto-index/embeddings.npz

Status: STUB — not yet implemented. Currently falls back to BM25 + ConceptGraph.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.embedding")

# Flag: is embedding available?
_EMBEDDING_AVAILABLE = False
_embedding_model = None


def is_available() -> bool:
    """Check if embedding search is available (sentence-transformers installed)."""
    return _EMBEDDING_AVAILABLE


def _lazy_init():
    """Try to load sentence-transformers on first use."""
    global _EMBEDDING_AVAILABLE, _embedding_model
    if _embedding_model is not None:
        return
    try:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        _EMBEDDING_AVAILABLE = True
        logger.info("Embedding model loaded: all-MiniLM-L6-v2")
    except ImportError:
        _EMBEDDING_AVAILABLE = False
        logger.debug("sentence-transformers not installed, embedding search disabled")


def build_embeddings(documents: dict[str, str], index_dir: Path) -> bool:
    """Build embedding vectors for all documents. Returns True if successful."""
    _lazy_init()
    if not _EMBEDDING_AVAILABLE:
        return False
    # TODO: implement embedding building
    # 1. Encode all document texts with _embedding_model.encode()
    # 2. Save to index_dir / "embeddings.npz"
    logger.info("Embedding build: stub — not yet implemented")
    return False


def search(query: str, index_dir: Path, top_k: int = 20) -> Optional[list[tuple[str, float]]]:
    """Search using embedding similarity. Returns None if unavailable."""
    _lazy_init()
    if not _EMBEDDING_AVAILABLE:
        return None
    # TODO: implement embedding search
    # 1. Load embeddings from index_dir / "embeddings.npz"
    # 2. Encode query
    # 3. Compute cosine similarity
    # 4. Return top_k results as [(symbol_id, score)]
    return None


def hybrid_search(
    query: str,
    bm25_results: list[tuple[str, float]],
    index_dir: Path,
    alpha: float = 0.7,
    top_k: int = 20,
) -> list[tuple[str, float]]:
    """Combine BM25 and embedding results with weighted fusion.

    alpha: weight for BM25 (1.0 = pure BM25, 0.0 = pure embedding)
    Falls back to pure BM25 if embeddings unavailable.
    """
    emb_results = search(query, index_dir, top_k=top_k * 2)
    if emb_results is None:
        return bm25_results[:top_k]

    # Reciprocal Rank Fusion (RRF)
    k = 60  # RRF constant
    scores: dict[str, float] = {}

    for rank, (sid, _score) in enumerate(bm25_results):
        scores[sid] = scores.get(sid, 0) + alpha / (k + rank + 1)

    for rank, (sid, _score) in enumerate(emb_results):
        scores[sid] = scores.get(sid, 0) + (1 - alpha) / (k + rank + 1)

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return ranked[:top_k]
