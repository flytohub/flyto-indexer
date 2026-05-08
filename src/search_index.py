"""
Search-index mixin for IndexEngine.

Builds and incrementally updates BM25 and semantic search indexes.
Extracted from engine.py.
"""

try:
    from .bm25 import BM25Index
    from .semantic import SemanticIndex
except ImportError:
    from bm25 import BM25Index
    from semantic import SemanticIndex


class SearchIndexMixin:
    """Mixin that provides all search-index methods.

    Expects the host class to expose (via self or other mixins):
        self.index              – ProjectIndex
        self.index_dir          – Path
        self._extract_path_from_sid()  (from DependencyResolverMixin)
    """

    def _build_symbol_doc(self, symbol) -> str:
        """Build BM25/semantic document text for a symbol."""
        parts = [symbol.name]
        if symbol.summary:
            parts.append(symbol.summary)
        if symbol.content:
            parts.append(symbol.content[:300])
        return " ".join(parts)

    def _build_bm25_index(self):
        """Build BM25 search index from symbols and save to disk (full rebuild)."""
        documents = {}
        for sid, symbol in self.index.symbols.items():
            documents[sid] = self._build_symbol_doc(symbol)

        if not documents:
            return

        bm25 = BM25Index()
        bm25.build(documents)
        bm25.save(self.index_dir / "bm25.json")

        # Build semantic (TF-IDF) index with code-derived concept graph
        index_data = {
            "symbols": {sid: s.to_dict() for sid, s in self.index.symbols.items()},
            "files": {k: v.to_dict() for k, v in self.index.files.items()},
            "dependencies": {k: v.to_dict() for k, v in self.index.dependencies.items()},
            "reverse_index": self.index.reverse_index,
        }
        semantic = SemanticIndex()
        semantic.build(documents, index_data=index_data)
        semantic.save(self.index_dir / "semantic.json")

    def _update_search_indexes(self, changed_paths: set = None):
        """Update BM25 and semantic search indexes.

        When changed_paths is provided:
        - BM25: incremental update (remove old docs, add new docs)
        - Semantic: mark as stale for lazy rebuild (concept graph needs full data)

        When changed_paths is None: full rebuild of both.
        """
        if changed_paths is None:
            self._build_bm25_index()
            return

        # Incremental BM25 update
        bm25_path = self.index_dir / "bm25.json"
        bm25 = BM25Index.load(bm25_path)
        if bm25 is None:
            # No existing index, do full build
            self._build_bm25_index()
            return

        # Compute which symbol IDs to remove and add
        removed_ids = set()
        added_docs = {}
        for sid, symbol in self.index.symbols.items():
            if symbol.path in changed_paths:
                added_docs[sid] = self._build_symbol_doc(symbol)

        # Also remove any docs from BM25 whose path is in changed_paths
        # (they may have been deleted or renamed)
        for doc_id in bm25.doc_ids:
            doc_path = self._extract_path_from_sid(doc_id)
            if doc_path in changed_paths:
                removed_ids.add(doc_id)

        bm25.update_docs(removed_ids, added_docs)
        bm25.save(bm25_path)

        # Mark semantic index as stale (lazy rebuild on next load)
        stale_marker = self.index_dir / ".semantic_stale"
        try:
            stale_marker.write_text("1")
        except OSError:
            pass
