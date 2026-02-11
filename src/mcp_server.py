#!/usr/bin/env python3
"""
Flyto Indexer MCP Server

Code search and analysis for any project.
Reads project list from index.json (no hardcoded paths).

Usage:
    python -m src.mcp_server

Claude Code config (~/.claude/mcp_servers.json):
{
    "flyto-indexer": {
        "command": "python",
        "args": ["-m", "src.mcp_server"],
        "cwd": "/path/to/flyto-indexer"
    }
}
"""

import gzip
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

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

# MCP Protocol
def send_response(id: Any, result: Any):
    response = {"jsonrpc": "2.0", "id": id, "result": result}
    print(json.dumps(response), flush=True)

def send_error(id: Any, code: int, message: str):
    response = {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}
    print(json.dumps(response), flush=True)


# =============================================================================
# Rate Limiting (per-process, sliding window)
# =============================================================================
import time as _time

_RATE_LIMIT_MAX = int(os.environ.get("FLYTO_INDEXER_RATE_LIMIT", "100"))  # requests per window (global)
_RATE_LIMIT_SESSION_MAX = int(os.environ.get("FLYTO_INDEXER_SESSION_RATE_LIMIT", "30"))  # per-session per window
_RATE_LIMIT_WINDOW = 60.0  # seconds
_rate_limit_timestamps: list = []
_session_rate_limits: dict = {}  # session_id -> list of timestamps


def _check_rate_limit(session_id: str = "") -> bool:
    """Return True if request is allowed, False if rate-limited.
    Checks both global and per-session limits."""
    now = _time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW

    # Global rate limit
    while _rate_limit_timestamps and _rate_limit_timestamps[0] < cutoff:
        _rate_limit_timestamps.pop(0)
    if len(_rate_limit_timestamps) >= _RATE_LIMIT_MAX:
        return False
    _rate_limit_timestamps.append(now)

    # Per-session rate limit
    if session_id:
        if session_id not in _session_rate_limits:
            _session_rate_limits[session_id] = []
        session_ts = _session_rate_limits[session_id]
        while session_ts and session_ts[0] < cutoff:
            session_ts.pop(0)
        if len(session_ts) >= _RATE_LIMIT_SESSION_MAX:
            return False
        session_ts.append(now)

        # Evict old session buckets (prevent memory leak)
        if len(_session_rate_limits) > 200:
            oldest_key = min(_session_rate_limits, key=lambda k: _session_rate_limits[k][-1] if _session_rate_limits[k] else 0)
            del _session_rate_limits[oldest_key]

    return True


# 載入索引 (可透過環境變數設定，用於嵌入其他專案時)
INDEX_DIR = Path(os.environ.get(
    "FLYTO_INDEX_DIR",
    str(Path(__file__).parent.parent / ".flyto-index")
))

# Content cache for lazy loading
_content_cache: dict = {}
_content_loaded: bool = False

# Test mapper cache (lazy init)
_test_mapper = None

# Session store (in-memory, survives across MCP calls within same process)
_session_store = None


def load_project_map() -> dict:
    # Try gzip first, fallback to plain JSON
    gz_path = INDEX_DIR / "PROJECT_MAP.json.gz"
    if gz_path.exists():
        with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    path = INDEX_DIR / "PROJECT_MAP.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


# Cache for index (loaded once)
_index_cache: dict = None

def load_index() -> dict:
    global _index_cache
    if _index_cache is not None:
        return _index_cache

    # Try gzip first, fallback to plain JSON
    gz_path = INDEX_DIR / "index.json.gz"
    if gz_path.exists():
        with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
            _index_cache = json.load(f)
            return _index_cache
    path = INDEX_DIR / "index.json"
    if path.exists():
        _index_cache = json.loads(path.read_text())
        return _index_cache
    return {}


def load_content_file() -> dict:
    """Load content from content.jsonl file (lazy load, cached)."""
    global _content_cache, _content_loaded

    if _content_loaded:
        return _content_cache

    content_file = INDEX_DIR / "content.jsonl"
    if content_file.exists():
        try:
            with open(content_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        _content_cache[record["id"]] = record["content"]
        except Exception:
            pass

    _content_loaded = True
    return _content_cache


def get_symbol_content_text(symbol_id: str, symbol_data: dict) -> str:
    """
    Get symbol content, checking both inline and content.jsonl.

    Backward compatible: works with old indexes that have inline content.
    """
    # Try inline content first
    content = symbol_data.get("content", "")
    if content:
        return content

    # Try content file
    content_map = load_content_file()
    return content_map.get(symbol_id, "")

# Symbol type importance weights
TYPE_WEIGHTS = {
    "composable": 15,  # Vue composables are often what you want
    "component": 12,   # Vue/React components
    "function": 10,    # Top-level functions
    "class": 8,        # Classes
    "interface": 6,    # TypeScript interfaces
    "type": 5,         # Type definitions
    "method": 3,       # Methods are usually accessed via class
    "store": 12,       # Pinia/Vuex stores
    "api": 10,         # API endpoints
}

# Path patterns that indicate less important code
LOW_PRIORITY_PATHS = ["test", "tests", "__test__", "spec", "mock", "fixture", "example"]


def search_by_keyword(
    query: str,
    max_results: int = 20,
    symbol_type: str = None,
    project: str = None,
    include_content: bool = False,
    session_id: str = None,
) -> dict:
    """
    跨專案搜尋（智能排序）

    Args:
        query: 搜尋關鍵字
        max_results: 最多返回幾筆
        symbol_type: 只搜特定類型 (function/class/composable/component/method/interface/type)
        project: 只搜特定專案 (flyto-core/flyto-cloud/flyto-pro/...)
        include_content: 是否包含程式碼片段

    Scoring:
        - Name match: +10 (exact: +20)
        - Summary match: +5
        - Content match: +1
        - Type importance: +3~15 (composable > function > method)
        - Reference count: +0.5 per ref (max +10)
        - Path importance: -5 if in tests/
        - Has exports: +3
    """
    index = load_index()
    results = []
    query_lower = query.lower()
    query_words = query_lower.split()

    # Session boost paths
    boost_paths: set = set()
    if session_id:
        store = _get_session_store()
        session = store.get(session_id)
        if session:
            boost_paths = session.get_boost_paths()
            session.add_query(query)

    # 搜尋 symbols
    for symbol_id, symbol in index.get("symbols", {}).items():
        # 專案篩選
        sym_project = symbol_id.split(":")[0] if ":" in symbol_id else ""
        if project and project.lower() not in sym_project.lower():
            continue

        # 類型篩選
        sym_type = symbol.get("type", "")
        if symbol_type and symbol_type.lower() != sym_type.lower():
            continue

        score = 0
        match_reason = []
        path = symbol.get("path", "").lower()

        # === 文字匹配 ===
        # 名稱匹配（高權重）
        name = symbol.get("name", "").lower()
        if any(w in name for w in query_words):
            score += 10
            match_reason.append("name")

        # 精確匹配加分
        if query_lower == name:
            score += 20

        # 摘要匹配
        summary = symbol.get("summary", "").lower()
        if any(w in summary for w in query_words):
            score += 5
            match_reason.append("summary")

        # 內容匹配 (load from content.jsonl if needed)
        content = get_symbol_content_text(symbol_id, symbol).lower()
        if any(w in content for w in query_words):
            score += 1
            match_reason.append("content")

        # 沒有任何匹配就跳過
        if score == 0:
            continue

        # === 智能加權 ===
        # 1. Symbol 類型權重
        type_weight = TYPE_WEIGHTS.get(sym_type, 0)
        score += type_weight

        # 2. 引用次數權重（被引用越多越重要）
        ref_count = symbol.get("ref_count", 0)
        ref_bonus = min(ref_count * 0.5, 10)  # 最多 +10
        score += ref_bonus

        # 3. 路徑權重（tests 降權）
        if any(p in path for p in LOW_PRIORITY_PATHS):
            score -= 5

        # 4. Export 權重（公開 API 加分）
        if symbol.get("exports"):
            score += 3

        # 5. Session boost（最近看過的檔案加分）
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
            # 取前 500 字元的程式碼
            full_content = get_symbol_content_text(symbol_id, symbol)
            result["snippet"] = full_content[:500]
        results.append(result)

    # 排序
    results.sort(key=lambda x: -x.get("score", 0))

    # 去重
    seen = set()
    unique = []
    for r in results:
        if r["symbol_id"] not in seen:
            seen.add(r["symbol_id"])
            unique.append(r)

    # 按專案分組
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


def get_file_info(path: str) -> dict:
    """
    取得檔案資訊

    包括用途、分類、關鍵字、API、依賴等
    """
    project_map = load_project_map()
    file_info = project_map.get("files", {}).get(path, {})

    if not file_info:
        return {"error": f"File not found: {path}"}

    return {
        "path": path,
        "purpose": file_info.get("purpose", ""),
        "category": file_info.get("category", ""),
        "keywords": file_info.get("keywords", []),
        "apis": file_info.get("apis", []),
        "dependencies": file_info.get("dependencies", []),
        "ui_elements": file_info.get("ui_elements", []),
    }


def get_file_symbols(path: str) -> dict:
    """
    取得檔案的 symbols

    列出該檔案中的所有函數、類、組件
    """
    index = load_index()
    symbols = []

    for symbol_id, symbol in index.get("symbols", {}).items():
        if symbol.get("path") == path:
            symbols.append({
                "id": symbol_id,
                "name": symbol.get("name", ""),
                "type": symbol.get("type", ""),
                "line": symbol.get("start_line", 0),
                "summary": symbol.get("summary", ""),
            })

    return {
        "path": path,
        "count": len(symbols),
        "symbols": symbols,
    }


def impact_analysis(symbol_id: str) -> dict:
    """
    影響分析

    修改某個 symbol 會影響哪些地方
    使用 reverse_index 進行準確查詢
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    # Resolve symbol_id if partial
    resolved_id = symbol_id
    if symbol_id not in symbols:
        for sid, sym in symbols.items():
            if sym.get("name") == symbol_id:
                if sym.get("type") in ("composable", "function", "class"):
                    resolved_id = sid
                    break
        else:
            for sid in symbols:
                if symbol_id in sid:
                    resolved_id = sid
                    break

    affected = []
    seen_paths = set()  # Dedup across projects

    def get_basename_key(source_id: str) -> str:
        parts = source_id.split(":")
        if len(parts) >= 4:
            basename = parts[1].rsplit("/", 1)[-1]
            return f"{basename}:{parts[2]}:{parts[3]}"
        return source_id

    # Method 1: Use reverse_index (most accurate)
    if resolved_id in reverse_index:
        for caller_id in reverse_index[resolved_id]:
            dedup_key = get_basename_key(caller_id)
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)

            caller_symbol = symbols.get(caller_id, {})
            affected.append({
                "id": caller_id,
                "path": caller_symbol.get("path", ""),
                "name": caller_symbol.get("name", ""),
                "type": caller_symbol.get("type", ""),
                "reason": "直接調用",
            })

    # Method 2: Check resolved_target in dependencies
    for dep_id, dep in dependencies.items():
        resolved_target = dep.get("metadata", {}).get("resolved_target", "")
        if resolved_target == resolved_id:
            source_id = dep.get("source", "")
            dedup_key = get_basename_key(source_id)
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)

            source_symbol = symbols.get(source_id, {})
            affected.append({
                "id": source_id,
                "path": source_symbol.get("path", ""),
                "name": source_symbol.get("name", ""),
                "type": dep.get("type", ""),
                "reason": f"透過 {dep.get('type', 'unknown')} 依賴",
            })

    warning = ""
    if len(affected) == 0:
        suggestion = "這個 symbol 沒有被其他地方引用，可以安全修改。"
    elif len(affected) <= 3:
        warning = f"修改會影響 {len(affected)} 個地方"
        suggestion = "影響範圍較小，建議逐一檢查這些調用處。"
    elif len(affected) <= 10:
        warning = f"⚠️ 修改會影響 {len(affected)} 個地方"
        suggestion = "影響範圍中等，建議仔細評估。"
    else:
        warning = f"⚠️ 修改會影響 {len(affected)} 個地方！"
        suggestion = "影響範圍較大，建議謹慎修改並做好測試。"

    return {
        "symbol": resolved_id,
        "affected_count": len(affected),
        "affected": affected[:20],  # Limit to top 20
        "has_more": len(affected) > 20,
        "warning": warning,
        "suggestion": suggestion,
    }


def list_categories() -> dict:
    """
    列出所有分類
    """
    project_map = load_project_map()
    categories = project_map.get("categories", {})

    return {
        "total": len(categories),
        "categories": [
            {"name": cat, "file_count": len(paths)}
            for cat, paths in sorted(categories.items(), key=lambda x: -len(x[1]))
        ],
    }


def list_apis() -> dict:
    """
    列出所有 API
    """
    project_map = load_project_map()
    api_map = project_map.get("api_map", {})

    return {
        "total": len(api_map),
        "apis": [
            {"path": api, "used_by_count": len(files)}
            for api, files in sorted(api_map.items(), key=lambda x: -len(x[1]))
        ],
    }


def list_projects() -> dict:
    """
    列出所有已索引的專案和統計
    """
    index = load_index()
    projects = index.get("projects", [])

    # 統計每個專案的 symbols
    stats = {}
    for sid, sym in index.get("symbols", {}).items():
        project = sid.split(":")[0] if ":" in sid else "unknown"
        if project not in stats:
            stats[project] = {"files": set(), "symbols": 0, "by_type": {}}
        stats[project]["files"].add(sym.get("path", ""))
        stats[project]["symbols"] += 1
        sym_type = sym.get("type", "unknown")
        stats[project]["by_type"][sym_type] = stats[project]["by_type"].get(sym_type, 0) + 1

    result = []
    for proj in projects:
        s = stats.get(proj, {"files": set(), "symbols": 0, "by_type": {}})
        result.append({
            "project": proj,
            "files": len(s["files"]),
            "symbols": s["symbols"],
            "by_type": s["by_type"],
        })

    # 按 symbols 數量排序
    result.sort(key=lambda x: -x["symbols"])

    return {
        "total_projects": len(projects),
        "total_symbols": len(index.get("symbols", {})),
        "projects": result,
    }


def get_symbol_content(symbol_id: str) -> dict:
    """
    取得 symbol 的完整程式碼

    Loads content from content.jsonl if not in main index.
    """
    index = load_index()
    symbol = index.get("symbols", {}).get(symbol_id)
    resolved_id = symbol_id

    if not symbol:
        # 嘗試模糊匹配
        for sid, sym in index.get("symbols", {}).items():
            if symbol_id in sid or sid.endswith(symbol_id):
                symbol = sym
                resolved_id = sid
                break

    if not symbol:
        return {"error": f"Symbol not found: {symbol_id}"}

    # Get content (may be in content.jsonl)
    content = get_symbol_content_text(resolved_id, symbol)

    return {
        "symbol_id": resolved_id,
        "project": resolved_id.split(":")[0] if ":" in resolved_id else "",
        "path": symbol.get("path", ""),
        "name": symbol.get("name", ""),
        "type": symbol.get("type", ""),
        "line_start": symbol.get("start_line", 0),
        "line_end": symbol.get("end_line", 0),
        "summary": symbol.get("summary", ""),
        "content": content,
    }


def find_references(symbol_id: str) -> dict:
    """
    Find all places that reference this symbol.

    Uses:
    1. Reverse index (pre-computed during indexing)
    2. Resolved dependencies
    3. Content search as fallback
    """
    import re

    index = load_index()
    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})
    reverse_index = index.get("reverse_index", {})

    # Resolve symbol_id if partial
    resolved_id = symbol_id
    if symbol_id not in symbols:
        # Try exact name match first
        name_matches = []
        partial_matches = []
        for sid, sym in symbols.items():
            sym_name = sym.get("name", "")
            if sym_name == symbol_id:
                name_matches.append(sid)
            elif symbol_id in sid or sid.endswith(symbol_id):
                partial_matches.append(sid)

        if name_matches:
            # Prefer composables/functions over methods
            for sid in name_matches:
                sym = symbols[sid]
                if sym.get("type") in ("composable", "function"):
                    resolved_id = sid
                    break
            else:
                resolved_id = name_matches[0]
        elif partial_matches:
            resolved_id = partial_matches[0]

    target_symbol = symbols.get(resolved_id)
    if not target_symbol:
        return {"error": f"Symbol not found: {symbol_id}"}

    target_name = target_symbol.get("name", "")
    target_path = target_symbol.get("path", "")
    references = []
    seen_keys = set()  # Use (path, line) as key to avoid duplicates
    seen_paths = set()  # Track unique paths for dedup across projects

    def extract_path_from_source_id(source_id: str) -> str:
        """Extract file path from source_id like project:path:type:name"""
        parts = source_id.split(":")
        if len(parts) >= 2:
            return parts[1]
        return ""

    def get_dedup_key(source_id: str) -> str:
        """
        Get dedup key for cross-project deduplication.

        Uses basename + type + name to handle forks with different paths:
        - flyto-cloud: src/ui/web/frontend/src/views/Cart.vue:component:Cart
        - flyto-cloud-dev: frontend/src/views/Cart.vue:component:Cart
        Both become: Cart.vue:component:Cart
        """
        parts = source_id.split(":")
        if len(parts) >= 4:
            # project:path:type:name -> basename(path):type:name
            path = parts[1]
            basename = path.rsplit("/", 1)[-1]  # Get filename only
            return f"{basename}:{parts[2]}:{parts[3]}"
        elif len(parts) >= 2:
            return parts[1]
        return source_id

    # Method 0: Use pre-computed reverse index (fastest & most accurate)
    if resolved_id in reverse_index:
        for caller_id in reverse_index[resolved_id]:
            caller_symbol = symbols.get(caller_id, {})
            from_path = caller_symbol.get("path", "") or extract_path_from_source_id(caller_id)

            # Skip self-references
            if from_path == target_path:
                continue

            # Dedup across projects (flyto-cloud vs flyto-cloud-dev)
            dedup_key = get_dedup_key(caller_id)
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)

            # Find the line from dependencies
            line = 0
            for dep in dependencies.values():
                resolved_target = dep.get("metadata", {}).get("resolved_target", "")
                if resolved_target == resolved_id and dep.get("source", "") == caller_id:
                    line = dep.get("line", 0)
                    break

            key = (from_path, caller_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            references.append({
                "type": "call",
                "from_symbol": caller_id,
                "from_path": from_path,
                "from_name": caller_symbol.get("name", ""),
                "line": line,
                "confidence": "high",  # From reverse index
            })

    # Also check reverse index by name (some deps might not be fully resolved)
    if target_name in reverse_index:
        for caller_id in reverse_index[target_name]:
            caller_symbol = symbols.get(caller_id, {})
            from_path = caller_symbol.get("path", "") or extract_path_from_source_id(caller_id)

            if from_path == target_path:
                continue

            # Dedup across projects
            dedup_key = get_dedup_key(caller_id)
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)

            key = (from_path, caller_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            references.append({
                "type": "call",
                "from_symbol": caller_id,
                "from_path": from_path,
                "from_name": caller_symbol.get("name", ""),
                "line": 0,
                "confidence": "medium",
            })

    # Method 1: Search dependencies (calls, extends, implements, uses)
    for dep_id, dep in dependencies.items():
        dep_type = dep.get("type", "")
        target = dep.get("target", "")
        resolved_target = dep.get("metadata", {}).get("resolved_target", "")

        # Check if this dependency targets our symbol
        if dep_type in ("calls", "extends", "implements", "uses"):
            # Match by resolved_target, symbol_id, or by name
            if resolved_target == resolved_id or target == resolved_id or target == target_name:
                source_id = dep.get("source", "")
                source_symbol = symbols.get(source_id, {})

                # Get path from symbol or extract from source_id
                from_path = source_symbol.get("path", "") or extract_path_from_source_id(source_id)

                # Skip self-references (same file)
                if from_path == target_path:
                    continue

                # Dedup across projects
                dedup_key = get_dedup_key(source_id)
                if dedup_key in seen_paths:
                    continue
                seen_paths.add(dedup_key)

                line = dep.get("line", 0)
                key = (from_path, line)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                references.append({
                    "type": dep_type,
                    "from_symbol": source_id,
                    "from_path": from_path,
                    "from_name": source_symbol.get("name", ""),
                    "line": line,
                    "confidence": "high" if resolved_target else "medium",
                })

    # Method 2: Search content for symbol name usage
    if target_name and len(target_name) >= 3:  # Avoid short names
        pattern = rf'\b{re.escape(target_name)}\s*\('

        for sym_id, sym in symbols.items():
            if sym_id == resolved_id:
                continue

            sym_path = sym.get("path", "")
            # Skip same file (self-references)
            if sym_path == target_path:
                continue

            # Dedup across projects
            dedup_key = get_dedup_key(sym_id)
            if dedup_key in seen_paths:
                continue

            content = get_symbol_content_text(sym_id, sym)
            matches = list(re.finditer(pattern, content))

            if matches:
                seen_paths.add(dedup_key)  # Only add if matches found

                # Find line number of first match
                first_match = matches[0]
                line_offset = content[:first_match.start()].count('\n')
                line = sym.get("start_line", 0) + line_offset

                key = (sym_path, line)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                references.append({
                    "type": "usage",
                    "from_symbol": sym_id,
                    "from_path": sym_path,
                    "from_name": sym.get("name", ""),
                    "line": line,
                    "occurrences": len(matches),
                    "confidence": "low",  # Content regex match
                })

    # Sort by confidence (high first), then by path
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    references.sort(key=lambda x: (
        confidence_order.get(x.get("confidence", "low"), 2),
        x.get("from_path", ""),
        x.get("line", 0)
    ))

    # Group by project
    by_project = {}
    for ref in references:
        project = ref["from_symbol"].split(":")[0] if ":" in ref["from_symbol"] else "unknown"
        if project not in by_project:
            by_project[project] = []
        by_project[project].append(ref)

    # Count by confidence
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    for ref in references:
        conf = ref.get("confidence", "low")
        confidence_counts[conf] = confidence_counts.get(conf, 0) + 1

    return {
        "symbol": resolved_id,
        "name": target_name,
        "total": len(references),  # Unique callers (deduped across projects)
        "confidence_breakdown": confidence_counts,
        "by_project": by_project,
        "references": references,
    }


def dependency_graph(
    file_path: str = None,
    symbol_id: str = None,
    project: str = None,
    direction: str = "both",
    max_depth: int = 2
) -> dict:
    """
    Get dependency graph for a file, symbol, or project.

    Shows what a module depends on (imports) and what depends on it (dependents).
    """
    index = load_index()
    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})

    # Build dependency maps
    imports_map = {}  # source -> [targets]
    dependents_map = {}  # target -> [sources]

    # Also use reverse_index for accurate dependents
    reverse_index = index.get("reverse_index", {})

    for dep_id, dep in dependencies.items():
        source = dep.get("source", "")
        target = dep.get("target", "")
        dep_type = dep.get("type", "")

        # Extract file paths
        source_path = source.split(":")[1] if ":" in source and len(source.split(":")) > 1 else ""

        if source_path:
            if source_path not in imports_map:
                imports_map[source_path] = []
            imports_map[source_path].append({
                "target": target,
                "type": dep_type,
                "line": dep.get("line", 0),
            })

            # Use resolved_target for accurate dependents mapping
            resolved_target = dep.get("metadata", {}).get("resolved_target", "")
            if resolved_target:
                target_path = resolved_target.split(":")[1] if ":" in resolved_target else ""
                if target_path:
                    if target_path not in dependents_map:
                        dependents_map[target_path] = []
                    dependents_map[target_path].append({
                        "source": source_path,
                        "source_id": source,
                        "type": dep_type,
                        "line": dep.get("line", 0),
                    })

    result = {
        "query": {
            "file_path": file_path,
            "symbol_id": symbol_id,
            "project": project,
            "direction": direction,
            "max_depth": max_depth,
        },
        "imports": [],
        "dependents": [],
        "summary": {},
    }

    target_paths = set()

    # Determine target paths based on query
    if file_path:
        target_paths.add(file_path)
    elif symbol_id:
        # Extract path from symbol_id
        if ":" in symbol_id and len(symbol_id.split(":")) > 1:
            target_paths.add(symbol_id.split(":")[1])
    elif project:
        # Get all paths in project
        for sid, sym in symbols.items():
            if sid.startswith(project + ":"):
                target_paths.add(sym.get("path", ""))

    if not target_paths:
        return {"error": "No valid target specified. Provide file_path, symbol_id, or project."}

    # Collect imports (what target depends on)
    if direction in ("both", "imports"):
        seen_imports = set()
        for path in target_paths:
            for imp in imports_map.get(path, []):
                target = imp["target"]
                if target not in seen_imports:
                    seen_imports.add(target)
                    result["imports"].append({
                        "from": path,
                        "to": target,
                        "type": imp["type"],
                    })

    # Collect dependents (what depends on target) - use reverse_index for accuracy
    if direction in ("both", "dependents"):
        seen_dependents = set()
        for path in target_paths:
            # First, use reverse_index (more accurate, includes 'uses' dependencies)
            for sid, callers in reverse_index.items():
                if ":" in sid:
                    sym_path = sid.split(":")[1]
                    if sym_path == path:
                        for caller in callers:
                            if ":" in caller:
                                caller_path = caller.split(":")[1]
                                if caller_path not in seen_dependents and caller_path not in target_paths:
                                    seen_dependents.add(caller_path)
                                    result["dependents"].append({
                                        "from": caller_path,
                                        "to": path,
                                        "type": "calls",  # reverse_index doesn't track dep type
                                    })

            # Fallback: also check dependents_map for additional deps
            for dep in dependents_map.get(path, []):
                source = dep["source"]
                if source not in seen_dependents and source not in target_paths:
                    seen_dependents.add(source)
                    result["dependents"].append({
                        "from": source,
                        "to": path,
                        "type": dep["type"],
                    })

    # Generate summary
    result["summary"] = {
        "target_files": len(target_paths),
        "imports_count": len(result["imports"]),
        "dependents_count": len(result["dependents"]),
        "import_types": {},
        "dependent_types": {},
    }

    for imp in result["imports"]:
        t = imp["type"]
        result["summary"]["import_types"][t] = result["summary"]["import_types"].get(t, 0) + 1

    for dep in result["dependents"]:
        t = dep["type"]
        result["summary"]["dependent_types"][t] = result["summary"]["dependent_types"].get(t, 0) + 1

    return result


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


def check_index_status() -> dict:
    """
    Check if the index is stale and needs to be updated.

    Compares file modification times and hashes with indexed data.
    """
    import os
    import hashlib
    from datetime import datetime

    index = load_index()
    project_map = load_project_map()

    # Get index metadata
    index_file = INDEX_DIR / "index.json"
    if not index_file.exists():
        return {
            "status": "missing",
            "message": "Index does not exist. Run 'python index_all.py' to create it.",
            "indexed_at": None,
        }

    index_mtime = datetime.fromtimestamp(index_file.stat().st_mtime)
    indexed_at = index.get("indexed_at", "")

    # Get project roots from index (set by index_all.py)
    projects = index.get("projects", [])
    project_roots = index.get("project_roots", {})

    # Sample check: look at a few files from each project
    stale_files = []
    checked_count = 0
    total_symbols = len(index.get("symbols", {}))

    # Group symbols by project
    by_project = {}
    for sym_id, sym in index.get("symbols", {}).items():
        proj = sym_id.split(":")[0] if ":" in sym_id else ""
        if proj not in by_project:
            by_project[proj] = []
        by_project[proj].append(sym)

    # Check sample files from each project
    for proj, proj_symbols in by_project.items():
        if proj not in project_roots:
            continue

        root = project_roots[proj]
        if not os.path.exists(root):
            continue

        # Sample up to 10 files per project
        checked_paths = set()
        for sym in proj_symbols[:50]:
            path = sym.get("path", "")
            if path in checked_paths:
                continue
            checked_paths.add(path)

            full_path = os.path.join(root, path)
            if not os.path.exists(full_path):
                stale_files.append({
                    "project": proj,
                    "path": path,
                    "reason": "file_deleted",
                })
                continue

            # Check if file was modified after index
            try:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(full_path))
                if file_mtime > index_mtime:
                    stale_files.append({
                        "project": proj,
                        "path": path,
                        "reason": "modified_after_index",
                        "file_mtime": file_mtime.isoformat(),
                    })
            except OSError:
                pass

            checked_count += 1
            if len(checked_paths) >= 10:
                break

    # Determine status
    if len(stale_files) == 0:
        status = "fresh"
        message = "Index is up to date."
    elif len(stale_files) <= 5:
        status = "slightly_stale"
        message = f"Index has {len(stale_files)} stale files. Consider re-indexing."
    else:
        status = "stale"
        message = f"Index has {len(stale_files)}+ stale files. Re-indexing recommended."

    return {
        "status": status,
        "message": message,
        "indexed_at": indexed_at,
        "index_file_mtime": index_mtime.isoformat(),
        "total_symbols": total_symbols,
        "total_projects": len(projects),
        "files_checked": checked_count,
        "stale_files": stale_files[:20],
        "stale_count": len(stale_files),
        "recommendation": "Run 'python index_all.py' to update the index." if status != "fresh" else None,
    }


def find_dead_code(
    project: str = None,
    symbol_type: str = None,
    min_lines: int = 5
) -> dict:
    """
    找出沒有被任何地方引用的函數/組件（死代碼）。

    這些代碼可以考慮刪除以減少維護負擔。
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    # 建立被引用的名稱集合
    referenced_names = set()
    imported_files = set()  # 被導入的文件
    referenced_classes = set()  # 被引用的類名（其方法不算死代碼）

    for dep_id, dep in dependencies.items():
        dep_type = dep.get("type", "")

        if dep_type == "imports":
            # 收集所有被導入的名稱
            names = dep.get("metadata", {}).get("names", [])
            for name in names:
                referenced_names.add(name)
                # 如果名稱首字母大寫，可能是類名
                if name and name[0].isupper():
                    referenced_classes.add(name)
            # 收集被導入的模塊路徑
            target = dep.get("target", "")
            if target:
                imported_files.add(target)
                basename = target.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                imported_files.add(basename)
                # 對於 Python 模塊路徑（如 src.pro.meta.multi_pass_refiner）
                # 也加入最後一個部分
                if "." in target:
                    last_part = target.rsplit(".", 1)[-1]
                    imported_files.add(last_part)

        elif dep_type == "calls":
            # 收集被調用的名稱（未 resolve 的）
            target = dep.get("target", "")
            if target and not target.startswith("__"):
                referenced_names.add(target)
                # 也加入各個部分（處理 obj.method 的情況）
                parts = target.split(".")
                for part in parts:
                    if part and len(part) > 2:
                        referenced_names.add(part)
                        # 首字母大寫的部分可能是類名
                        if part[0].isupper():
                            referenced_classes.add(part)

    dead_code = []

    # 應該被引用的類型（排除 file, variable 等）
    should_be_referenced = {"function", "method", "composable", "component", "class"}

    # 入口點模式（不需要被引用）
    entry_point_patterns = [
        "main", "index", "app", "App", "Main",
        "__init__", "setup", "teardown",
        "test_", "Test", "_test",
        "register", "init", "configure",
        "handle", "route", "endpoint",
        "do_GET", "do_POST", "do_PUT", "do_DELETE",  # HTTP handlers
        "do_HEAD", "do_OPTIONS", "do_PATCH",
    ]

    # Vue/React 生命週期和特殊方法
    lifecycle_methods = {
        "created", "mounted", "updated", "destroyed",
        "beforeCreate", "beforeMount", "beforeUpdate", "beforeDestroy",
        "onMounted", "onUnmounted", "onUpdated",
        "componentDidMount", "componentWillUnmount", "render",
        "setup", "data", "computed", "methods", "watch",
    }

    for sym_id, sym in symbols.items():
        sym_type = sym.get("type", "")
        sym_name = sym.get("name", "")
        sym_project = sym_id.split(":")[0] if ":" in sym_id else ""
        sym_path = sym.get("path", "")

        # 過濾條件
        if project and project.lower() not in sym_project.lower():
            continue
        if symbol_type and sym_type != symbol_type:
            continue
        if sym_type not in should_be_referenced:
            continue

        # 跳過太短的代碼
        lines = sym.get("end_line", 0) - sym.get("start_line", 0)
        if lines < min_lines:
            continue

        # 跳過入口點
        is_entry_point = any(p in sym_name for p in entry_point_patterns)
        if is_entry_point:
            continue

        # 跳過生命週期方法
        if sym_name in lifecycle_methods:
            continue

        # 跳過 Vue 組件內的函數（可能被模板 @click 等使用，難以追蹤）
        if sym_type == "function" and sym_path.endswith(".vue"):
            continue

        # 跳過導出的符號
        if sym.get("exports"):
            continue

        # 跳過私有方法（以 _ 開頭但不是 __）
        if sym_name.startswith("_") and not sym_name.startswith("__"):
            continue

        # 檢查是否被引用（導入或調用）
        if sym_name in referenced_names:
            continue

        # 對於方法，也檢查方法名本身（不帶類名）
        if sym_type == "method" and "." in sym_name:
            method_only = sym_name.split(".")[-1]
            if method_only in referenced_names:
                continue
            # 如果類名被引用，方法也不算死代碼
            class_name = sym_name.split(".")[0]
            if class_name in referenced_classes or class_name in referenced_names:
                continue

        # 對於類，如果類名被引用則不是死代碼
        if sym_type == "class" and sym_name in referenced_classes:
            continue

        # 檢查文件是否被導入（對於類/組件）
        file_basename = sym_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if file_basename in imported_files or sym_path in imported_files:
            continue

        # 對於 composable，檢查是否被任何文件導入
        # composable 通常以 use 開頭，文件名和函數名相同
        if sym_type == "composable":
            is_imported = False
            for dep in dependencies.values():
                if dep.get("type") == "imports":
                    target = dep.get("target", "")
                    names = dep.get("metadata", {}).get("names", [])
                    # 檢查導入路徑是否包含 composable 名稱
                    if sym_name in target or file_basename in target:
                        is_imported = True
                        break
                    # 檢查具名導入
                    if sym_name in names:
                        is_imported = True
                        break
            if is_imported:
                continue

        # 對於 JS/TS 類和組件，檢查文件名是否與類名匹配並被導入
        # 例如 OutputRendererPlugin.js 定義了 OutputRendererPlugin
        if sym_type in ("class", "component") and file_basename == sym_name:
            # 檢查是否有任何導入指向這個文件路徑
            is_imported = False
            for dep in dependencies.values():
                if dep.get("type") == "imports":
                    target = dep.get("target", "")
                    # 檢查相對路徑導入（如 ./renderers/OutputRendererPlugin）
                    if sym_name in target or file_basename in target:
                        is_imported = True
                        break
            if is_imported:
                continue

        # 檢查 reverse_index
        ref_count = sym.get("ref_count", 0)
        callers = reverse_index.get(sym_id, [])

        if ref_count == 0 and len(callers) == 0:
            dead_code.append({
                "symbol_id": sym_id,
                "name": sym_name,
                "type": sym_type,
                "path": sym_path,
                "project": sym_project,
                "lines": lines,
                "start_line": sym.get("start_line", 0),
            })

    # 按行數排序（大的死代碼優先）
    dead_code.sort(key=lambda x: x["lines"], reverse=True)

    # 按專案分組
    by_project = {}
    for item in dead_code:
        proj = item["project"]
        if proj not in by_project:
            by_project[proj] = []
        by_project[proj].append(item)

    total_dead_lines = sum(item["lines"] for item in dead_code)

    return {
        "total": len(dead_code),
        "total_dead_lines": total_dead_lines,
        "by_project": {k: len(v) for k, v in by_project.items()},
        "top_20": dead_code[:20],
        "suggestion": f"發現 {len(dead_code)} 個未被引用的符號，共 {total_dead_lines} 行代碼可考慮刪除。"
    }


def find_todos(
    project: str = None,
    priority: str = None,
    max_results: int = 100
) -> dict:
    """
    找出所有 TODO、FIXME、HACK、XXX 標記。

    幫助追蹤技術債和待辦事項。
    """
    index = load_index()
    symbols = index.get("symbols", {})

    patterns = _TODO_PATTERNS

    todos = []
    seen_files = set()

    for sym_id, sym in symbols.items():
        sym_project = sym_id.split(":")[0] if ":" in sym_id else ""
        sym_path = sym.get("path", "")

        # 過濾專案
        if project and project.lower() not in sym_project.lower():
            continue

        # 每個檔案只處理一次
        file_key = f"{sym_project}:{sym_path}"
        if file_key in seen_files:
            continue
        seen_files.add(file_key)

        content = get_symbol_content_text(sym_id, sym)
        if not content:
            continue

        for tag, (pattern, tag_priority) in patterns.items():
            if priority and priority != tag_priority:
                continue

            for match in pattern.finditer(content):
                # 取得匹配的文字
                text = match.group(1) or match.group(2) or match.group(3) or ""
                text = text.strip()[:100]  # 限制長度

                # 計算行號
                line_num = content[:match.start()].count('\n') + sym.get("start_line", 1)

                todos.append({
                    "tag": tag,
                    "priority": tag_priority,
                    "text": text,
                    "path": sym_path,
                    "project": sym_project,
                    "line": line_num,
                })

    # 按優先級排序
    priority_order = {"high": 0, "medium": 1, "low": 2}
    todos.sort(key=lambda x: (priority_order.get(x["priority"], 9), x["project"], x["path"]))

    # 統計
    by_priority = {"high": 0, "medium": 0, "low": 0}
    by_project = {}
    by_tag = {}

    for todo in todos:
        by_priority[todo["priority"]] = by_priority.get(todo["priority"], 0) + 1
        by_project[todo["project"]] = by_project.get(todo["project"], 0) + 1
        by_tag[todo["tag"]] = by_tag.get(todo["tag"], 0) + 1

    return {
        "total": len(todos),
        "by_priority": by_priority,
        "by_project": by_project,
        "by_tag": by_tag,
        "todos": todos[:max_results],
        "has_more": len(todos) > max_results,
    }


def cross_project_impact(
    symbol_name: str,
    source_project: str = None
) -> dict:
    """
    跨專案 API 變更追蹤。

    當某個專案的函數/類改變時，找出其他專案哪些地方需要更新。
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    # 找到源符號
    source_symbols = []
    for sym_id, sym in symbols.items():
        if sym.get("name") == symbol_name:
            sym_project = sym_id.split(":")[0] if ":" in sym_id else ""
            if source_project and source_project.lower() not in sym_project.lower():
                continue
            source_symbols.append({
                "id": sym_id,
                "project": sym_project,
                "path": sym.get("path", ""),
                "type": sym.get("type", ""),
            })

    if not source_symbols:
        return {"error": f"Symbol '{symbol_name}' not found"}

    # 找出跨專案的引用
    cross_project_refs = []

    for source in source_symbols:
        source_project = source["project"]
        source_id = source["id"]

        # 從 reverse_index 找引用
        callers = reverse_index.get(source_id, [])

        for caller_id in callers:
            caller_project = caller_id.split(":")[0] if ":" in caller_id else ""

            # 只關心跨專案的引用
            if caller_project == source_project:
                continue

            # 跳過 fork 專案（flyto-cloud-dev 是 flyto-cloud 的 fork）
            if (source_project == "flyto-cloud" and caller_project == "flyto-cloud-dev") or \
               (source_project == "flyto-cloud-dev" and caller_project == "flyto-cloud"):
                continue

            caller_sym = symbols.get(caller_id, {})
            cross_project_refs.append({
                "caller_id": caller_id,
                "caller_project": caller_project,
                "caller_path": caller_sym.get("path", ""),
                "caller_name": caller_sym.get("name", ""),
                "caller_type": caller_sym.get("type", ""),
                "source_project": source_project,
                "source_id": source_id,
            })

    # 按專案分組
    by_affected_project = {}
    for ref in cross_project_refs:
        proj = ref["caller_project"]
        if proj not in by_affected_project:
            by_affected_project[proj] = []
        by_affected_project[proj].append(ref)

    # 生成建議
    if len(cross_project_refs) == 0:
        suggestion = f"'{symbol_name}' 沒有跨專案的引用，可以安全修改。"
        risk = "low"
    elif len(by_affected_project) == 1:
        suggestion = f"修改 '{symbol_name}' 會影響 1 個其他專案的 {len(cross_project_refs)} 處調用。"
        risk = "medium"
    else:
        suggestion = f"⚠️ 修改 '{symbol_name}' 會影響 {len(by_affected_project)} 個其他專案！"
        risk = "high"

    return {
        "symbol_name": symbol_name,
        "source_symbols": source_symbols,
        "cross_project_refs": cross_project_refs,
        "by_affected_project": {k: len(v) for k, v in by_affected_project.items()},
        "affected_projects": list(by_affected_project.keys()),
        "total_cross_refs": len(cross_project_refs),
        "risk": risk,
        "suggestion": suggestion,
    }


def get_description(path: str, project: str = None) -> dict:
    """
    Get the latest description for a file path.

    Searches all indexed projects' .flyto/descriptions.jsonl files.
    Returns the latest entry matching the path (bottom-up, last wins).
    """
    import hashlib

    # Determine which project roots to search
    index = load_index()
    project_roots = index.get("project_roots", {})

    if project and project in project_roots:
        roots_to_search = {project: project_roots[project]}
    else:
        roots_to_search = project_roots

    # Search each project's descriptions.jsonl
    for proj_name, root in roots_to_search.items():
        desc_path = Path(root) / ".flyto" / "descriptions.jsonl"
        if not desc_path.exists():
            continue

        latest = None
        for line in desc_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if entry.get("path") == path:
                    latest = entry
            except json.JSONDecodeError:
                pass

        if latest:
            # Check staleness
            full_path = Path(root) / path
            stale = False
            if full_path.exists() and latest.get("hash"):
                import hashlib
                current_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()[:16]
                if current_hash != latest["hash"]:
                    stale = True

            return {
                "project": proj_name,
                "path": path,
                "one_liner": latest.get("one_liner", ""),
                "source": latest.get("source", "unknown"),
                "updatedAt": latest.get("updatedAt", ""),
                "stale": stale,
                "category": latest.get("category", ""),
                "refs": latest.get("refs", 0),
                "hotspot": latest.get("hotspot", False),
            }

    return {"error": f"No description found for: {path}"}


def update_description(path: str, summary: str, project: str = None) -> dict:
    """
    Write or update a file description.

    Appends a new entry to the project's .flyto/descriptions.jsonl.
    Hash is computed from the current file content for staleness tracking.
    """
    import hashlib
    from datetime import datetime, timezone

    index = load_index()
    project_roots = index.get("project_roots", {})

    # Find the right project root
    target_root = None
    target_project = None

    if project and project in project_roots:
        target_root = project_roots[project]
        target_project = project
    else:
        # Try to find which project contains this path
        for proj_name, root in project_roots.items():
            if (Path(root) / path).exists():
                target_root = root
                target_project = proj_name
                break

    if not target_root:
        return {"error": f"Cannot find project containing: {path}"}

    desc_path = Path(target_root) / ".flyto" / "descriptions.jsonl"
    if not desc_path.parent.exists():
        return {"error": f"No .flyto/ found in {target_project}. Run 'flyto-index init' first."}

    # Compute file hash
    full_path = Path(target_root) / path
    file_hash = ""
    if full_path.exists():
        file_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()[:16]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "path": path,
        "hash": file_hash,
        "one_liner": summary,
        "source": "ai",
        "updatedAt": now,
    }
    line = json.dumps(entry, ensure_ascii=False)

    # Append
    with open(desc_path, "a", encoding="utf-8") as f:
        if desc_path.exists() and desc_path.stat().st_size > 0:
            with open(desc_path, "rb") as check:
                check.seek(-1, 2)
                if check.read(1) != b"\n":
                    f.write("\n")
        f.write(line + "\n")

    return {
        "ok": True,
        "project": target_project,
        "path": path,
        "one_liner": summary,
        "hash": file_hash,
        "updatedAt": now,
    }


def _get_test_mapper():
    """Get or create the TestMapper singleton."""
    global _test_mapper
    if _test_mapper is None:
        try:
            from .test_mapper import TestMapper
        except ImportError:
            from test_mapper import TestMapper
        _test_mapper = TestMapper(load_index())
    return _test_mapper


def _get_session_store():
    """Get or create the SessionStore singleton."""
    global _session_store
    if _session_store is None:
        try:
            from .session import SessionStore
        except ImportError:
            from session import SessionStore
        _session_store = SessionStore()
    return _session_store


def find_test_file(path: str) -> dict:
    """
    Find the corresponding test file for a source file, or vice versa.

    Uses naming convention (primary) and import analysis (fallback).
    """
    mapper = _get_test_mapper()

    try:
        from .test_mapper import TestMapper
    except ImportError:
        from test_mapper import TestMapper
    if TestMapper._is_test_file(path):
        source = mapper.find_source(path)
        return {
            "query_path": path,
            "is_test_file": True,
            "source_file": source,
            "test_file": path,
        }
    else:
        test = mapper.find_test(path)
        return {
            "query_path": path,
            "is_test_file": False,
            "source_file": path,
            "test_file": test,
        }


def get_file_context(path: str, include_content: bool = False) -> dict:
    """
    One-call context package: returns everything an agent needs about a file.

    Aggregates: file info, symbols, imports, dependents, test file, related files.
    All from cached data, zero I/O.
    """
    index = load_index()
    symbols_map = index.get("symbols", {})
    dependencies = index.get("dependencies", {})
    reverse_index = index.get("reverse_index", {})

    # 1. File info
    info = get_file_info(path)
    if "error" in info:
        info = {"purpose": "", "category": "", "keywords": []}

    # 2. Symbols in this file
    file_symbols = get_file_symbols(path)
    symbols_list = file_symbols.get("symbols", [])

    # 3. Imports (dependencies where source is in this file)
    imports = []
    seen_imports = set()
    for dep_id, dep in dependencies.items():
        source_id = dep.get("source", "")
        if ":" in source_id:
            source_path = source_id.split(":")[1] if len(source_id.split(":")) >= 2 else ""
            if source_path == path and dep.get("type") == "imports":
                target = dep.get("target", "")
                if target not in seen_imports:
                    seen_imports.add(target)
                    names = dep.get("metadata", {}).get("names", [])
                    imports.append({"target": target, "names": names})

    # 4. Dependents (who references symbols in this file)
    dependents = []
    seen_deps = set()
    for sym_id, callers in reverse_index.items():
        if ":" not in sym_id:
            continue
        sym_path = sym_id.split(":")[1] if len(sym_id.split(":")) >= 2 else ""
        if sym_path != path:
            continue
        sym_name = sym_id.split(":")[-1] if ":" in sym_id else sym_id
        for caller_id in callers:
            if ":" in caller_id:
                caller_path = caller_id.split(":")[1] if len(caller_id.split(":")) >= 2 else ""
                if caller_path != path and caller_id not in seen_deps:
                    seen_deps.add(caller_id)
                    caller_sym = symbols_map.get(caller_id, {})
                    dependents.append({
                        "from_path": caller_path,
                        "symbol_used": sym_name,
                        "confidence": "high",
                    })

    # 5. Test file
    mapper = _get_test_mapper()
    test_file = mapper.find_test(path)

    # 6. Related files (1-hop import graph neighbors)
    related_files = []
    related_seen = set()
    # Import targets
    for imp in imports:
        target = imp["target"]
        # Try to resolve to a file path
        for sid, sym in symbols_map.items():
            if sym.get("path", "") and target in sid:
                rpath = sym["path"]
                if rpath != path and rpath not in related_seen:
                    related_seen.add(rpath)
                    related_files.append({"path": rpath, "relation": "imports"})
                break
    # Dependents as related
    for dep in dependents[:10]:
        rpath = dep["from_path"]
        if rpath not in related_seen:
            related_seen.add(rpath)
            related_files.append({"path": rpath, "relation": "imported_by"})

    # 7. Optional: include content
    if include_content:
        for sym_entry in symbols_list:
            sid = sym_entry.get("id", "")
            sym_data = symbols_map.get(sid, {})
            content = get_symbol_content_text(sid, sym_data)
            sym_entry["content"] = content[:500] if content else ""

    return {
        "path": path,
        "info": info,
        "symbols": symbols_list,
        "imports": imports,
        "dependents": dependents[:30],
        "test_file": test_file,
        "related_files": related_files[:20],
        "summary": {
            "total_symbols": len(symbols_list),
            "total_imports": len(imports),
            "total_dependents": len(dependents),
        },
    }


def edit_impact_preview(symbol_id: str, change_type: str = "modify") -> dict:
    """
    Preview the impact of editing a symbol before making changes.

    Shows all call sites with actual code lines, risk assessment, and suggestions.
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    # Resolve symbol_id (same pattern as find_references)
    resolved_id = symbol_id
    if symbol_id not in symbols:
        for sid, sym in symbols.items():
            if sym.get("name") == symbol_id:
                if sym.get("type") in ("composable", "function", "class", "component"):
                    resolved_id = sid
                    break
        else:
            for sid in symbols:
                if symbol_id in sid:
                    resolved_id = sid
                    break

    target_symbol = symbols.get(resolved_id)
    if not target_symbol:
        return {"error": f"Symbol not found: {symbol_id}"}

    target_name = target_symbol.get("name", "")
    target_path = target_symbol.get("path", "")

    # Collect call sites
    call_sites = []
    seen_keys = set()

    def get_dedup_key(source_id: str) -> str:
        parts = source_id.split(":")
        if len(parts) >= 4:
            basename = parts[1].rsplit("/", 1)[-1]
            return f"{basename}:{parts[2]}:{parts[3]}"
        return source_id

    # From reverse_index
    for caller_id in reverse_index.get(resolved_id, []):
        dedup_key = get_dedup_key(caller_id)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        caller_sym = symbols.get(caller_id, {})
        caller_path = caller_sym.get("path", "")
        if caller_path == target_path:
            continue

        # Get the actual code line
        content = get_symbol_content_text(caller_id, caller_sym)
        code_line = ""
        line_num = 0
        if content and target_name:
            for i, line in enumerate(content.split("\n")):
                if target_name in line:
                    code_line = line.strip()[:120]
                    line_num = caller_sym.get("start_line", 0) + i
                    break

        call_sites.append({
            "file": caller_path,
            "line": line_num,
            "code": code_line,
            "caller_name": caller_sym.get("name", ""),
            "confidence": "high",
        })

    # Also check by name in reverse_index
    for caller_id in reverse_index.get(target_name, []):
        dedup_key = get_dedup_key(caller_id)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        caller_sym = symbols.get(caller_id, {})
        caller_path = caller_sym.get("path", "")
        if caller_path == target_path:
            continue

        content = get_symbol_content_text(caller_id, caller_sym)
        code_line = ""
        line_num = 0
        if content and target_name:
            for i, line in enumerate(content.split("\n")):
                if target_name in line:
                    code_line = line.strip()[:120]
                    line_num = caller_sym.get("start_line", 0) + i
                    break

        call_sites.append({
            "file": caller_path,
            "line": line_num,
            "code": code_line,
            "caller_name": caller_sym.get("name", ""),
            "confidence": "medium",
        })

    # Limit to 30
    call_sites = call_sites[:30]

    # Group by project
    by_project: dict[str, int] = {}
    for cs in call_sites:
        # Try to determine project from file path
        for sid, sym in symbols.items():
            if sym.get("path") == cs["file"]:
                proj = sid.split(":")[0] if ":" in sid else "unknown"
                by_project[proj] = by_project.get(proj, 0) + 1
                break

    total = len(call_sites)

    # Risk assessment
    change_risk_map = {
        "rename": ("All call sites must update the name.", ["Update all call sites in a single commit", "Use find-and-replace across all files"]),
        "delete": ("All call sites will break.", ["Ensure no code depends on this before deleting", "Consider deprecation first"]),
        "signature_change": ("Call sites may need parameter updates.", ["Review each call site for compatibility", "Consider adding default parameters for backward compatibility"]),
        "add_param": ("Call sites may need to pass the new argument.", ["Add default value to new parameter if possible", "Update call sites that need the new parameter"]),
        "modify": ("Internal logic change only.", ["Run tests to verify behavior", "Check if return type/shape changed"]),
    }
    risk_info = change_risk_map.get(change_type, ("Unknown change type.", []))

    if total == 0:
        risk = "safe"
        risk_reason = "No call sites found, safe to change."
    elif total <= 3 and len(by_project) <= 1:
        risk = "low"
        risk_reason = f"{total} call site(s) in {len(by_project)} project(s)"
    elif total <= 10:
        risk = "moderate"
        risk_reason = f"{total} call sites across {len(by_project)} project(s)"
    else:
        risk = "high"
        risk_reason = f"{total} call sites across {len(by_project)} project(s)"

    # For delete/rename, risk is always elevated
    if change_type in ("delete", "rename") and total > 0:
        risk = "high" if total > 3 else "moderate"

    return {
        "symbol": resolved_id,
        "symbol_name": target_name,
        "change_type": change_type,
        "total_call_sites": total,
        "call_sites": call_sites,
        "by_project": by_project,
        "risk": risk,
        "risk_reason": risk_reason,
        "change_description": risk_info[0],
        "suggestions": risk_info[1],
    }


def check_and_reindex(dry_run: bool = True, project: str = None) -> dict:
    """
    Detect file changes and optionally clear caches for re-indexing.

    dry_run=True: only report changes
    dry_run=False: clear caches + report changes (user should run index_all.py after)
    """
    global _index_cache, _content_cache, _content_loaded, _test_mapper

    try:
        from .watcher import FileWatcher
    except ImportError:
        from watcher import FileWatcher

    index = load_index()
    watcher = FileWatcher(index)
    changes = watcher.detect_changes(project=project)
    summary = watcher.get_summary(changes)

    result = {
        "dry_run": dry_run,
        "total_changes": summary["total"],
        "by_type": summary["by_type"],
        "by_project": summary["by_project"],
        "changes": [
            {"path": c.path, "project": c.project, "type": c.change_type}
            for c in changes[:50]
        ],
        "has_more": len(changes) > 50,
    }

    if not dry_run and changes:
        _index_cache = None
        _content_cache = {}
        _content_loaded = False
        _test_mapper = None
        result["caches_cleared"] = True
        result["recommendation"] = "Run 'python index_all.py' to rebuild the index."
    elif changes:
        result["recommendation"] = "Run with dry_run=false to clear caches, then 'python index_all.py' to rebuild."
    else:
        result["recommendation"] = "Index is up to date."

    return result


def session_track(session_id: str, event_type: str, target: str, workspace_root: str = "") -> dict:
    """
    Track a workspace event (file_open, query, edit).

    Creates session if it doesn't exist.
    """
    store = _get_session_store()
    session = store.get_or_create(session_id, workspace_root)

    if event_type == "file_open":
        session.add_file(target)
    elif event_type == "query":
        session.add_query(target)
    elif event_type == "edit":
        session.add_edit(target)
    else:
        return {"error": f"Unknown event_type: {event_type}. Use: file_open, query, edit"}

    return {
        "ok": True,
        "session_id": session_id,
        "event_type": event_type,
        "target": target,
        "boost_paths_count": len(session.get_boost_paths()),
    }


def session_get(session_id: str) -> dict:
    """Get the current state of a session."""
    store = _get_session_store()
    session = store.get(session_id)
    if not session:
        return {"error": f"Session not found or expired: {session_id}"}
    return session.to_dict()


# MCP 工具定義
TOOLS = [
    # =========================================================================
    # Code Search & Discovery
    # =========================================================================
    {
        "name": "search_code",
        "description": (
            "Search for functions, classes, components, and composables across all indexed projects. "
            "Use this as the FIRST step when you need to find code by name or keyword. "
            "Results are ranked by relevance (name match > summary match > content match) "
            "and grouped by project. "
            "Returns: symbol_id, path, line number, type, summary, score. "
            "Use the symbol_id in follow-up calls to get_symbol_content, find_references, or impact_analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword to search (function name, class name, etc.). Example: 'useAuth', 'LoginForm', 'validate'"},
                "max_results": {"type": "integer", "default": 20, "description": "Max results to return (default 20)"},
                "symbol_type": {
                    "type": "string",
                    "enum": ["function", "class", "method", "composable", "component", "interface", "type"],
                    "description": "Filter by symbol type. Omit to search all types.",
                },
                "project": {
                    "type": "string",
                    "description": "Filter by project name. Use list_projects to see available projects.",
                },
                "include_content": {"type": "boolean", "default": False, "description": "Include first 500 chars of source code in results"},
                "session_id": {"type": "string", "description": "Optional session ID for search boost. Files recently opened/edited in the session get +8 score boost."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_symbol_content",
        "description": (
            "Get the full source code of a specific symbol (function, class, component). "
            "Use this AFTER search_code to read the actual implementation. "
            "Supports fuzzy matching: you can pass a partial symbol_id and it will find the best match. "
            "Returns: full source code, file path, line range, summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID from search_code results. Format: project:path:type:name. Example: 'flyto-core:src/modules/string/uppercase.py:class:StringUppercase'",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "get_file_symbols",
        "description": (
            "List all symbols (functions, classes, methods, components) defined in a specific file. "
            "Use this to get an overview of what a file contains before diving deeper. "
            "Returns: symbol id, name, type, line number, and summary for each symbol."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root. Example: 'src/composables/useAuth.js'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_file_info",
        "description": (
            "Get semantic metadata for a file: purpose, category, keywords, APIs used, and dependencies. "
            "Use this to quickly understand what a file does without reading its source code. "
            "Returns: purpose description, category (e.g. 'auth', 'payment'), keywords, API endpoints, dependencies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. Example: 'src/api/auth.py' or 'frontend/src/views/Login.vue'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "fulltext_search",
        "description": (
            "Full-text search across all indexed source code. Searches inside comments, strings, and TODO/FIXME markers. "
            "Use this when search_code doesn't find what you need (search_code matches symbol names; this searches content). "
            "Use search_type='todo' to find all TODO/FIXME items, 'comment' for comments only, 'string' for string literals. "
            "Returns: matching symbols with context snippets, grouped by project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for. Example: 'deprecated', 'workaround', 'api/v2'"},
                "search_type": {
                    "type": "string",
                    "enum": ["all", "todo", "comment", "string"],
                    "default": "all",
                    "description": "What to search: 'all' = everything, 'todo' = TODO/FIXME/HACK/XXX markers, 'comment' = code comments, 'string' = string literals",
                },
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 50, "description": "Max results to return"},
            },
            "required": ["query"],
        },
    },
    # =========================================================================
    # Reference & Dependency Analysis
    # =========================================================================
    {
        "name": "find_references",
        "description": (
            "Find all places that call or import a specific symbol. "
            "Use this BEFORE modifying a function/component to understand who depends on it. "
            "Uses pre-computed reverse index for fast, accurate results. "
            "Each reference includes confidence level: high (from dependency analysis), medium (from imports), low (from content regex). "
            "Returns: list of callers with file path, line number, and confidence, grouped by project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or just the symbol name. Examples: 'flyto-pro:src/agent/browser_agent.py:class:BrowserAgent' or just 'useToast'",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "impact_analysis",
        "description": (
            "Analyze the blast radius of modifying a symbol. "
            "Use this to assess risk BEFORE making changes to shared code. "
            "Returns: count of affected locations, list of affected symbols with paths, "
            "and a risk assessment (safe / moderate / high risk) with suggestions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or name to analyze. Format: project:path:type:name",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "dependency_graph",
        "description": (
            "Get the dependency graph for a file, symbol, or entire project. "
            "Shows what a module imports (dependencies) and what imports it (dependents). "
            "Use direction='imports' to see what a file depends on, 'dependents' to see what depends on it, 'both' for full picture. "
            "Returns: lists of import and dependent relationships with file paths and dependency types."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File path to analyze. Example: 'src/composables/useToast.js'"},
                "symbol_id": {"type": "string", "description": "Symbol ID (file path is auto-extracted from it)"},
                "project": {"type": "string", "description": "Project name to show all dependencies for the entire project"},
                "direction": {
                    "type": "string",
                    "enum": ["both", "imports", "dependents"],
                    "default": "both",
                    "description": "'imports' = what this file depends on, 'dependents' = what depends on this file, 'both' = full graph",
                },
                "max_depth": {"type": "integer", "default": 2, "description": "Max traversal depth"},
            },
        },
    },
    {
        "name": "cross_project_impact",
        "description": (
            "Track cross-project API usage. When a function/class in one project changes, "
            "find all other projects that need to be updated. "
            "Use this before changing shared APIs (e.g. a function in flyto-core used by flyto-pro and flyto-cloud). "
            "Returns: list of cross-project references, affected projects, and risk level (low/medium/high)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_name": {
                    "type": "string",
                    "description": "Symbol name to track. Example: 'useModuleSchema', 'ValidationError', 'BaseModule'",
                },
                "source_project": {
                    "type": "string",
                    "description": "Limit search to symbols defined in this project (optional)",
                },
            },
            "required": ["symbol_name"],
        },
    },
    # =========================================================================
    # Project Overview & Status
    # =========================================================================
    {
        "name": "list_projects",
        "description": (
            "List all indexed projects with statistics. "
            "Use this FIRST to discover available projects and their sizes. "
            "Returns: project names, file counts, symbol counts, and breakdown by symbol type (function/class/component/etc)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_categories",
        "description": (
            "List all code categories (e.g. auth, payment, product, order) and how many files belong to each. "
            "Use this to understand the high-level structure of indexed projects. "
            "Returns: category names sorted by file count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_apis",
        "description": (
            "List all API endpoints found in indexed code, along with which files use them. "
            "Use this to discover available backend endpoints or see API usage patterns. "
            "Returns: API paths sorted by usage count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "check_index_status",
        "description": (
            "Check if the code index is up-to-date or stale. "
            "Compares file modification times against the last index time. "
            "Returns: status (fresh/slightly_stale/stale), list of changed files, and recommendation to re-index if needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    # =========================================================================
    # Code Quality
    # =========================================================================
    {
        "name": "find_dead_code",
        "description": (
            "Find unreferenced functions, classes, and components (dead code). "
            "These symbols are never imported or called by any other code and can likely be removed. "
            "Automatically excludes entry points, lifecycle hooks, private methods, and test files. "
            "Returns: list of dead symbols sorted by line count (largest first), with total dead lines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Filter to a specific project",
                },
                "symbol_type": {
                    "type": "string",
                    "description": "Filter to a specific symbol type",
                    "enum": ["function", "method", "composable", "component", "class"],
                },
                "min_lines": {
                    "type": "integer",
                    "description": "Minimum line count to report (filters out tiny functions). Default: 5",
                    "default": 5,
                },
            },
        },
    },
    {
        "name": "find_todos",
        "description": (
            "Find all TODO, FIXME, HACK, and XXX markers across indexed code. "
            "Use this to track technical debt and pending work items. "
            "Priority: FIXME/HACK = high, TODO/XXX = medium, NOTE = low. "
            "Returns: list of markers with text, file path, line number, grouped by priority and project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Filter to a specific project",
                },
                "priority": {
                    "type": "string",
                    "description": "Filter by priority level",
                    "enum": ["high", "medium", "low"],
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return. Default: 100",
                    "default": 100,
                },
            },
        },
    },
    # =========================================================================
    # File Descriptions
    # =========================================================================
    {
        "name": "get_description",
        "description": (
            "Get the semantic one-liner description for a file. "
            "Returns the latest human or AI-written summary, staleness status (whether the file changed since description was written), and metadata. "
            "Use this to quickly understand what a file does."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root. Example: 'src/api/auth.py'"},
                "project": {"type": "string", "description": "Project name (optional, auto-detected if omitted)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "update_description",
        "description": (
            "Write or update a semantic description for a file. "
            "Call this after reading or modifying a file to record what it does. "
            "The description is stored in .flyto/descriptions.jsonl with a content hash for staleness tracking. "
            "Side effects: appends one line to .flyto/descriptions.jsonl."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root. Example: 'src/api/auth.py'"},
                "summary": {"type": "string", "description": "One-liner description. Example: 'User auth core: login, register, rate limiting, JWT token management'"},
                "project": {"type": "string", "description": "Project name (optional, auto-detected if omitted)"},
            },
            "required": ["path", "summary"],
        },
    },
    # =========================================================================
    # VSCode Agent Helpers
    # =========================================================================
    {
        "name": "get_file_context",
        "description": (
            "Get a complete context package for a file in one call. "
            "Returns file info (purpose, category), symbols, imports, dependents, "
            "test file mapping, and related files. "
            "Use this instead of calling get_file_info + get_file_symbols + find_references separately. "
            "All data comes from cached index, zero I/O."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root. Example: 'src/composables/useAuth.js'"},
                "include_content": {"type": "boolean", "default": False, "description": "Include first 500 chars of each symbol's source code"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_test_file",
        "description": (
            "Find the corresponding test file for a source file, or the source file for a test file. "
            "Uses naming conventions (test_foo.py, Foo.spec.ts) and import analysis as fallback. "
            "Returns the matched file path or null if no match found."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Source or test file path. Example: 'src/engine.py' or 'tests/test_engine.py'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_impact_preview",
        "description": (
            "Preview the impact of editing a symbol before making changes. "
            "Shows all call sites with actual code lines, risk assessment, and suggestions. "
            "Use this BEFORE renaming, deleting, or changing a function/class signature. "
            "change_type: rename, delete, signature_change, add_param, modify."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or name. Example: 'useAuth' or 'flyto-cloud:src/composables/useAuth.js:composable:useAuth'",
                },
                "change_type": {
                    "type": "string",
                    "enum": ["rename", "delete", "signature_change", "add_param", "modify"],
                    "default": "modify",
                    "description": "Type of change: rename, delete, signature_change, add_param, modify",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "check_and_reindex",
        "description": (
            "Detect file changes since last index and optionally clear caches. "
            "dry_run=true (default): only report which files changed. "
            "dry_run=false: clear all caches (must run 'python index_all.py' after). "
            "Returns: changed files grouped by type (modified/added/deleted) and project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean", "default": True, "description": "If true, only report changes. If false, also clear caches."},
                "project": {"type": "string", "description": "Filter to a specific project"},
            },
        },
    },
    {
        "name": "session_track",
        "description": (
            "Track a workspace event for search boosting. "
            "Events: file_open (opened a file), query (searched), edit (edited a file/symbol). "
            "Tracked files get +8 score boost in search_code results. "
            "Sessions are in-memory and expire after 24h."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Unique session identifier (e.g. workspace folder name or UUID)"},
                "event_type": {
                    "type": "string",
                    "enum": ["file_open", "query", "edit"],
                    "description": "Type of event: file_open, query, edit",
                },
                "target": {"type": "string", "description": "Target of the event (file path for file_open/edit, query string for query)"},
                "workspace_root": {"type": "string", "description": "Workspace root path (optional, used when creating new session)"},
            },
            "required": ["session_id", "event_type", "target"],
        },
    },
    {
        "name": "session_get",
        "description": (
            "Get the current state of a workspace session. "
            "Returns: open files, recent queries, recent edits, and boost path count. "
            "Use this to inspect what the session is tracking."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session identifier"},
            },
            "required": ["session_id"],
        },
    },
]


def handle_request(request: dict):
    """處理 MCP 請求"""
    method = request.get("method", "")
    id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        send_response(id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "flyto-indexer",
                "version": "1.0.0",
            },
        })

    elif method == "tools/list":
        send_response(id, {"tools": TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Extract session_id from arguments if available (for per-session rate limiting)
        _session_id = str(arguments.get("session_id", ""))[:64] if isinstance(arguments.get("session_id"), str) else ""

        # Rate limiting: reject if too many requests in window
        if not _check_rate_limit(session_id=_session_id):
            send_error(id, -32000, f"Rate limit exceeded ({_RATE_LIMIT_MAX} req/{int(_RATE_LIMIT_WINDOW)}s). Please slow down.")
            return

        try:
            # All tools dispatched via tool_registry
            try:
                from .tool_registry import execute_tool as _registry_execute
            except ImportError:
                from tool_registry import execute_tool as _registry_execute  # type: ignore[no-redef]
            try:
                result = _registry_execute(tool_name, arguments)
            except KeyError:
                send_error(id, -32601, f"Unknown tool: {tool_name}")
                return

            send_response(id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            })
        except Exception as e:
            send_error(id, -32000, str(e))

    elif method == "notifications/initialized":
        pass  # No response needed

    else:
        send_error(id, -32601, f"Method not found: {method}")


def main():
    """MCP Server 主程式"""
    import sys
    # Debug logging to stderr
    sys.stderr.write(f"[flyto-indexer] Starting MCP server (pid={os.getpid()})\n")
    sys.stderr.flush()

    for line in sys.stdin:
        try:
            sys.stderr.write(f"[flyto-indexer] Received: {line[:100]}...\n")
            sys.stderr.flush()
            request = json.loads(line.strip())
            handle_request(request)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"[flyto-indexer] JSON decode error: {e}\n")
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"[flyto-indexer] Error: {e}\n")
            sys.stderr.flush()
            print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32000, "message": str(e)}}), flush=True)


if __name__ == "__main__":
    main()
