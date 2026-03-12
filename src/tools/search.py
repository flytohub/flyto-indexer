"""Search tools for flyto-indexer MCP server."""

import re

try:
    from ..index_store import (
        load_index,
        get_symbol_content_text,
        _load_bm25,
        _load_semantic,
        _get_session_store,
        TYPE_WEIGHTS,
        LOW_PRIORITY_PATHS,
    )
    from ..synonyms import expand_query
except ImportError:
    from index_store import (
        load_index,
        get_symbol_content_text,
        _load_bm25,
        _load_semantic,
        _get_session_store,
        TYPE_WEIGHTS,
        LOW_PRIORITY_PATHS,
    )
    from synonyms import expand_query

# Pre-compiled regex patterns for TODO/FIXME markers
_TODO_PATTERNS = {
    "FIXME": (re.compile(r'#\s*FIXME[:\s]*(.*)$|//\s*FIXME[:\s]*(.*)$|/\*\s*FIXME[:\s]*(.*?)\*/', re.MULTILINE | re.IGNORECASE), "high"),
    "TODO": (re.compile(r'#\s*TODO[:\s]*(.*)$|//\s*TODO[:\s]*(.*)$|/\*\s*TODO[:\s]*(.*?)\*/', re.MULTILINE | re.IGNORECASE), "medium"),
    "HACK": (re.compile(r'#\s*HACK[:\s]*(.*)$|//\s*HACK[:\s]*(.*)$|/\*\s*HACK[:\s]*(.*?)\*/', re.MULTILINE | re.IGNORECASE), "high"),
    "XXX": (re.compile(r'#\s*XXX[:\s]*(.*)$|//\s*XXX[:\s]*(.*)$|/\*\s*XXX[:\s]*(.*?)\*/', re.MULTILINE | re.IGNORECASE), "medium"),
    "NOTE": (re.compile(r'#\s*NOTE[:\s]*(.*)$|//\s*NOTE[:\s]*(.*)$|/\*\s*NOTE[:\s]*(.*?)\*/', re.MULTILINE | re.IGNORECASE), "low"),
}
_FULLTEXT_TODO_PATTERN = re.compile(r'(?:#|//|/\*|\*)\s*(TODO|FIXME|XXX|HACK|NOTE|BUG)[\s:]+([^\n\r]*)', re.IGNORECASE)
_PY_COMMENT_PATTERN = re.compile(r'#\s*([^\n]*)')
_JS_COMMENT_PATTERN = re.compile(r'//\s*([^\n]*)')
_MULTI_COMMENT_PATTERN = re.compile(r'/\*[\s\S]*?\*/')
_STRING_PATTERNS = [
    re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"'),
    re.compile(r"'([^'\\]*(?:\\.[^'\\]*)*)'"),
    re.compile(r'`([^`]*)`'),
]


def _fuzzy_score(query: str, name: str) -> float:
    """Levenshtein-based similarity between query and symbol name, 0.0 to 1.0.

    Pure Python implementation with early termination.
    Only intended for short strings (symbol names), not content.
    """
    q = query.lower()
    n = name.lower()
    if q == n:
        return 1.0
    len_q, len_n = len(q), len(n)
    if len_q == 0 or len_n == 0:
        return 0.0
    max_len = max(len_q, len_n)
    # Short-circuit: if length difference > 50%, can't be similar enough
    if abs(len_q - len_n) > max_len * 0.5:
        return 0.0
    # Standard Levenshtein via two-row DP
    prev = list(range(len_n + 1))
    curr = [0] * (len_n + 1)
    for i in range(1, len_q + 1):
        curr[0] = i
        for j in range(1, len_n + 1):
            cost = 0 if q[i - 1] == n[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,       # deletion
                curr[j - 1] + 1,   # insertion
                prev[j - 1] + cost  # substitution
            )
        prev, curr = curr, prev
    distance = prev[len_n]
    return 1.0 - (distance / max_len)


def search_by_keyword(
    query: str,
    max_results: int = 20,
    symbol_type: str = None,
    project: str = None,
    include_content: bool = False,
    session_id: str = None,
) -> dict:
    """
    Cross-project search with smart ranking.

    Args:
        query: Search keyword
        max_results: Maximum number of results to return
        symbol_type: Filter by type (function/class/composable/component/method/interface/type)
        project: Filter by project (flyto-core/flyto-cloud/flyto-pro/...)
        include_content: Whether to include code snippets

    Scoring:
        - BM25 base: 0-30 (with synonym-expanded query)
        - Name match: +10 (exact: +20)
        - Fuzzy match: 0-15 (Levenshtein on symbol name, threshold >= 0.6)
        - Synonym match: +5 (name matches expanded concept synonym)
        - Summary match: +5 (original) / +3 (synonym)
        - Content match: +1
        - Type importance: +3~15 (composable > function > method)
        - Reference count: +0.5 per ref (max +10)
        - Path importance: -5 if in tests/
        - Has exports: +3
        - Session boost: +8
    """
    index = load_index()
    results = []
    query_lower = query.lower()
    query_words = query_lower.split()

    # Expand query with concept synonyms
    original_tokens, synonym_tokens = expand_query(query)
    all_search_words = list(original_tokens | synonym_tokens)

    # Session boost paths
    boost_paths: set = set()
    if session_id:
        store = _get_session_store()
        session = store.get(session_id)
        if session:
            boost_paths = session.get_boost_paths()
            session.add_query(query)

    # BM25 pre-scoring (if index available)
    bm25_scores: dict = {}  # symbol_id -> normalized score (0-30)
    bm25 = _load_bm25()
    if bm25:
        # Build expanded BM25 query: original + synonym terms
        bm25_query = query
        if synonym_tokens:
            bm25_query = query + " " + " ".join(synonym_tokens)
        raw_results = bm25.search(bm25_query, top_k=200)
        if raw_results:
            max_score = raw_results[0][1] if raw_results else 1.0
            for doc_id, score in raw_results:
                bm25_scores[doc_id] = (score / max_score) * 30.0 if max_score > 0 else 0

    # Search symbols
    for symbol_id, symbol in index.get("symbols", {}).items():
        # Project filter
        sym_project = symbol_id.split(":")[0] if ":" in symbol_id else ""
        if project and project.lower() not in sym_project.lower():
            continue

        # Type filter
        sym_type = symbol.get("type", "")
        if symbol_type and symbol_type.lower() != sym_type.lower():
            continue

        score = 0
        match_reason = []
        path = symbol.get("path", "").lower()

        # === BM25 base score (if available) ===
        bm25_score = bm25_scores.get(symbol_id, 0)
        if bm25_score > 0:
            score += bm25_score
            match_reason.append("bm25")

        # === Text matching (additive bonuses) ===
        # Name match (high weight) — check original query words
        name = symbol.get("name", "").lower()
        if any(w in name for w in query_words):
            score += 10
            match_reason.append("name")

        # Exact match bonus
        if query_lower == name:
            score += 20

        # Synonym name match — name contains an expanded synonym term
        if synonym_tokens and any(w in name for w in synonym_tokens):
            score += 5
            if "synonym" not in match_reason:
                match_reason.append("synonym")

        # Fuzzy name match (Levenshtein on symbol name vs query)
        fuzzy = _fuzzy_score(query_lower, name)
        if fuzzy >= 0.6:
            fuzzy_pts = fuzzy * 15.0  # scale 0.6-1.0 → 9-15 points
            score += fuzzy_pts
            if "fuzzy" not in match_reason:
                match_reason.append("fuzzy")

        # Summary match — check both original and expanded words
        summary = symbol.get("summary", "").lower()
        if any(w in summary for w in query_words):
            score += 5
            match_reason.append("summary")
        elif synonym_tokens and any(w in summary for w in synonym_tokens):
            score += 3
            match_reason.append("summary_synonym")

        # Content match (only if no BM25 hit — avoid double-loading)
        if bm25_score == 0:
            content = get_symbol_content_text(symbol_id, symbol).lower()
            if any(w in content for w in all_search_words):
                score += 1
                match_reason.append("content")

        # Skip if no match at all
        if score == 0:
            continue

        # === Smart weighting ===
        # 1. Symbol type weight
        type_weight = TYPE_WEIGHTS.get(sym_type, 0)
        score += type_weight

        # 2. Reference count weight (more refs = more important)
        ref_count = symbol.get("ref_count", 0)
        ref_bonus = min(ref_count * 0.5, 10)  # capped at +10
        score += ref_bonus

        # 3. Path weight (demote test files)
        if any(p in path for p in LOW_PRIORITY_PATHS):
            score -= 5

        # 4. Export weight (bonus for public APIs)
        if symbol.get("exports"):
            score += 3

        # 5. Session boost (bonus for recently viewed files)
        if boost_paths and path:
            raw_path = symbol.get("path", "")
            if raw_path in boost_paths:
                score += 8

        result = {
            "project": sym_project,
            "path": symbol.get("path", ""),
            "symbol_id": symbol_id,
            "name": symbol.get("name", ""),
            "type": sym_type,
            "line": symbol.get("start_line", 0),
            "summary": symbol.get("summary", "")[:150],
            "score": round(score, 1),
            "ref_count": ref_count,
            "match": ", ".join(match_reason),
        }
        if include_content:
            # Take first 500 characters of code
            full_content = get_symbol_content_text(symbol_id, symbol)
            result["snippet"] = full_content[:500]
        results.append(result)

    # Sort by score
    results.sort(key=lambda x: -x.get("score", 0))

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        if r["symbol_id"] not in seen:
            seen.add(r["symbol_id"])
            unique.append(r)

    # Group by project
    by_project = {}
    for r in unique[:max_results]:
        proj = r["project"]
        if proj not in by_project:
            by_project[proj] = []
        by_project[proj].append(r)

    return {
        "query": query,
        "filters": {
            "symbol_type": symbol_type,
            "project": project,
        },
        "total": len(unique),
        "showing": min(len(unique), max_results),
        "by_project": by_project,
        "results": unique[:max_results],
    }


def fulltext_search(
    query: str,
    search_type: str = "all",
    project: str = None,
    max_results: int = 50
) -> dict:
    """
    Full-text search across all indexed code.

    Searches in comments, strings, TODOs, and general content.
    """
    index = load_index()
    symbols = index.get("symbols", {})
    results = []

    query_lower = query.lower()
    query_pattern = re.compile(re.escape(query), re.IGNORECASE)

    for sym_id, sym in symbols.items():
        # Project filter
        sym_project = sym_id.split(":")[0] if ":" in sym_id else ""
        if project and project.lower() not in sym_project.lower():
            continue

        content = get_symbol_content_text(sym_id, sym)
        if not content:
            continue

        matches = []

        # Search based on type
        if search_type in ("all", "todo"):
            # Find TODOs, FIXMEs, XXX, HACK, NOTE
            for m in _FULLTEXT_TODO_PATTERN.finditer(content):
                if query_lower in m.group(0).lower():
                    line_num = content[:m.start()].count('\n') + 1
                    matches.append({
                        "type": "todo",
                        "tag": m.group(1).upper(),
                        "text": m.group(2).strip()[:100],
                        "line": sym.get("start_line", 0) + line_num - 1,
                    })

        if search_type in ("all", "comment"):
            # Find comments containing query
            # Python comments
            for m in _PY_COMMENT_PATTERN.finditer(content):
                if query_lower in m.group(1).lower():
                    line_num = content[:m.start()].count('\n') + 1
                    matches.append({
                        "type": "comment",
                        "text": m.group(1).strip()[:100],
                        "line": sym.get("start_line", 0) + line_num - 1,
                    })

            # JS/TS single-line comments
            for m in _JS_COMMENT_PATTERN.finditer(content):
                if query_lower in m.group(1).lower():
                    line_num = content[:m.start()].count('\n') + 1
                    matches.append({
                        "type": "comment",
                        "text": m.group(1).strip()[:100],
                        "line": sym.get("start_line", 0) + line_num - 1,
                    })

            # Multi-line comments
            for m in _MULTI_COMMENT_PATTERN.finditer(content):
                if query_lower in m.group(0).lower():
                    line_num = content[:m.start()].count('\n') + 1
                    text = m.group(0).replace('/*', '').replace('*/', '').strip()
                    matches.append({
                        "type": "comment",
                        "text": text[:100],
                        "line": sym.get("start_line", 0) + line_num - 1,
                    })

        if search_type in ("all", "string"):
            # Find strings containing query
            for pattern in _STRING_PATTERNS:
                for m in pattern.finditer(content):
                    if query_lower in m.group(1).lower():
                        line_num = content[:m.start()].count('\n') + 1
                        matches.append({
                            "type": "string",
                            "text": m.group(1)[:100],
                            "line": sym.get("start_line", 0) + line_num - 1,
                        })

        if search_type == "all" and not matches:
            # General content search if no specific matches
            for m in query_pattern.finditer(content):
                line_num = content[:m.start()].count('\n') + 1
                # Get context around match
                start = max(0, m.start() - 30)
                end = min(len(content), m.end() + 30)
                context = content[start:end].replace('\n', ' ').strip()
                matches.append({
                    "type": "content",
                    "text": context[:100],
                    "line": sym.get("start_line", 0) + line_num - 1,
                })
                break  # Only first match per symbol for general search

        if matches:
            results.append({
                "symbol_id": sym_id,
                "project": sym_project,
                "path": sym.get("path", ""),
                "name": sym.get("name", ""),
                "matches": matches[:5],  # Limit matches per symbol
            })

    # Sort by project and path
    results.sort(key=lambda x: (x["project"], x["path"]))

    # Group by project
    by_project = {}
    for r in results[:max_results]:
        proj = r["project"]
        if proj not in by_project:
            by_project[proj] = []
        by_project[proj].append(r)

    # Count match types
    type_counts = {}
    for r in results:
        for m in r.get("matches", []):
            t = m.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

    return {
        "query": query,
        "search_type": search_type,
        "project_filter": project,
        "total": len(results),
        "showing": min(len(results), max_results),
        "type_counts": type_counts,
        "by_project": by_project,
        "results": results[:max_results],
    }


def semantic_search(
    query: str,
    project: str = None,
    max_results: int = 20,
    include_content: bool = False,
) -> dict:
    """
    Natural language → code search using TF-IDF cosine similarity with concept expansion.

    Unlike search_by_keyword (BM25), this expands the query through a concept taxonomy
    so "handle payment failure" matches process_refund(), charge_customer(), etc.

    Scoring:
        - Semantic similarity: 0-50 (TF-IDF cosine with concept expansion)
        - Type importance: +3~15 (composable > function > method)
        - Reference count: +0.5 per ref (max +10)
        - Path importance: -5 if in tests/
    """
    index = load_index()
    semantic = _load_semantic()

    if not semantic:
        # Fallback: if no semantic index exists, delegate to keyword search
        return search_by_keyword(
            query=query,
            max_results=max_results,
            project=project,
            include_content=include_content,
        )

    # Get semantic similarity scores
    raw_scores = semantic.search(query, top_k=200)
    if not raw_scores:
        return {
            "query": query,
            "search_mode": "semantic",
            "total": 0,
            "showing": 0,
            "results": [],
            "hint": "No semantic matches. Try search_code for exact keyword matching.",
        }

    # Normalize to 0-50 range
    max_sim = raw_scores[0][1] if raw_scores else 1.0
    sim_scores = {sid: (sim / max_sim) * 50.0 for sid, sim in raw_scores}

    results = []
    symbols = index.get("symbols", {})

    for symbol_id, base_score in sim_scores.items():
        symbol = symbols.get(symbol_id)
        if not symbol:
            continue

        # Project filter
        sym_project = symbol_id.split(":")[0] if ":" in symbol_id else ""
        if project and project.lower() not in sym_project.lower():
            continue

        score = base_score
        path = symbol.get("path", "").lower()

        # Type importance
        sym_type = symbol.get("type", "")
        score += TYPE_WEIGHTS.get(sym_type, 0)

        # Reference count bonus
        ref_count = symbol.get("ref_count", 0)
        score += min(ref_count * 0.5, 10)

        # Demote test files
        if any(p in path for p in LOW_PRIORITY_PATHS):
            score -= 5

        result = {
            "project": sym_project,
            "path": symbol.get("path", ""),
            "symbol_id": symbol_id,
            "name": symbol.get("name", ""),
            "type": sym_type,
            "line": symbol.get("start_line", 0),
            "summary": symbol.get("summary", "")[:150],
            "score": round(score, 1),
            "similarity": round(base_score, 1),
            "ref_count": ref_count,
        }
        if include_content:
            full_content = get_symbol_content_text(symbol_id, symbol)
            result["snippet"] = full_content[:500]
        results.append(result)

    # Sort by score
    results.sort(key=lambda x: -x["score"])

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        if r["symbol_id"] not in seen:
            seen.add(r["symbol_id"])
            unique.append(r)

    # Group by project
    by_project = {}
    for r in unique[:max_results]:
        proj = r["project"]
        if proj not in by_project:
            by_project[proj] = []
        by_project[proj].append(r)

    # Show learned concept expansion for transparency
    expanded = []
    if semantic and semantic.concept_graph.related:
        expanded = semantic.concept_graph.expand(query, max_expansion=15)

    return {
        "query": query,
        "search_mode": "semantic",
        "concepts_expanded": sorted(expanded)[:30],
        "total": len(unique),
        "showing": min(len(unique), max_results),
        "by_project": by_project,
        "results": unique[:max_results],
    }
