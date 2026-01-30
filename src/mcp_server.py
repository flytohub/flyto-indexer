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

import json
import sys
from pathlib import Path
from typing import Any

# MCP Protocol
def send_response(id: Any, result: Any):
    response = {"jsonrpc": "2.0", "id": id, "result": result}
    print(json.dumps(response), flush=True)

def send_error(id: Any, code: int, message: str):
    response = {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}
    print(json.dumps(response), flush=True)

# 載入索引
INDEX_DIR = Path(__file__).parent.parent / ".flyto-index"

# Content cache for lazy loading
_content_cache: dict = {}
_content_loaded: bool = False


def load_project_map() -> dict:
    path = INDEX_DIR / "PROJECT_MAP.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def load_index() -> dict:
    path = INDEX_DIR / "index.json"
    if path.exists():
        return json.loads(path.read_text())
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
    include_content: bool = False
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

    # Collect dependents (what depends on target)
    if direction in ("both", "dependents"):
        seen_dependents = set()
        for path in target_paths:
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
    import re

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
            todo_pattern = r'(?:#|//|/\*|\*)\s*(TODO|FIXME|XXX|HACK|NOTE|BUG)[\s:]+([^\n\r]*)'
            for m in re.finditer(todo_pattern, content, re.IGNORECASE):
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
            py_comment = r'#\s*([^\n]*)'
            for m in re.finditer(py_comment, content):
                if query_lower in m.group(1).lower():
                    line_num = content[:m.start()].count('\n') + 1
                    matches.append({
                        "type": "comment",
                        "text": m.group(1).strip()[:100],
                        "line": sym.get("start_line", 0) + line_num - 1,
                    })

            # JS/TS single-line comments
            js_comment = r'//\s*([^\n]*)'
            for m in re.finditer(js_comment, content):
                if query_lower in m.group(1).lower():
                    line_num = content[:m.start()].count('\n') + 1
                    matches.append({
                        "type": "comment",
                        "text": m.group(1).strip()[:100],
                        "line": sym.get("start_line", 0) + line_num - 1,
                    })

            # Multi-line comments
            multi_comment = r'/\*[\s\S]*?\*/'
            for m in re.finditer(multi_comment, content):
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
            string_patterns = [
                r'"([^"\\]*(?:\\.[^"\\]*)*)"',  # Double-quoted
                r"'([^'\\]*(?:\\.[^'\\]*)*)'",  # Single-quoted
                r'`([^`]*)`',  # Template literals
            ]
            for pattern in string_patterns:
                for m in re.finditer(pattern, content):
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


# MCP 工具定義
TOOLS = [
    {
        "name": "search_code",
        "description": "跨專案搜尋程式碼。支援關鍵字搜尋、類型篩選、專案篩選。結果按專案分組顯示。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword (function name, class name, etc.)"},
                "max_results": {"type": "integer", "default": 20, "description": "最多返回幾筆結果"},
                "symbol_type": {
                    "type": "string",
                    "enum": ["function", "class", "method", "composable", "component", "interface", "type"],
                    "description": "只搜特定類型"
                },
                "project": {
                    "type": "string",
                    "description": "Filter by project name (use list_projects to see available)"
                },
                "include_content": {"type": "boolean", "default": False, "description": "是否包含程式碼片段"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_file_info",
        "description": "取得檔案的語意資訊，包括用途說明、分類、關鍵字、使用的 API、依賴的模組等。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (e.g., project/src/file.py)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_file_symbols",
        "description": "取得檔案中的所有 symbols（函數、類、組件），包括行號和摘要。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "檔案路徑"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "impact_analysis",
        "description": "影響分析：修改某個函數或組件會影響哪些地方。幫助評估修改風險。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {"type": "string", "description": "Symbol ID，格式：project:path:type:name"},
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "list_categories",
        "description": "列出所有程式碼分類，例如：payment, auth, product, order 等。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_apis",
        "description": "列出所有 API 端點，以及哪些檔案使用它們。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_projects",
        "description": "列出所有已索引的專案，包括檔案數、symbol 數、各類型統計。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_symbol_content",
        "description": "取得 symbol 的完整程式碼。可用於查看函數、類、組件的實作細節。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {"type": "string", "description": "Symbol ID (format: project:path:type:name)"},
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "find_references",
        "description": "找出所有引用此 symbol 的地方。幫助了解函數、組件被哪些地方使用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {"type": "string", "description": "Symbol ID (format: project:path:type:name)"},
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "dependency_graph",
        "description": "取得模組依賴關係圖。顯示檔案/模組之間的依賴關係（imports 和 dependents）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "檔案路徑，例如：src/composables/useToast.js"},
                "symbol_id": {"type": "string", "description": "Symbol ID，會自動提取檔案路徑"},
                "project": {"type": "string", "description": "專案名稱，顯示整個專案的依賴"},
                "direction": {
                    "type": "string",
                    "enum": ["both", "imports", "dependents"],
                    "default": "both",
                    "description": "查詢方向：both=雙向, imports=此檔案依賴什麼, dependents=什麼依賴此檔案"
                },
                "max_depth": {"type": "integer", "default": 2, "description": "最大深度"},
            },
        },
    },
    {
        "name": "fulltext_search",
        "description": "全文搜尋：搜尋註解、字串、TODO/FIXME 標記。可用於找出特定的註解或待辦事項。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜尋關鍵字"},
                "search_type": {
                    "type": "string",
                    "enum": ["all", "todo", "comment", "string"],
                    "default": "all",
                    "description": "搜尋類型：all=全部, todo=TODO/FIXME, comment=註解, string=字串"
                },
                "project": {"type": "string", "description": "只搜特定專案"},
                "max_results": {"type": "integer", "default": 50, "description": "最多返回幾筆"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_index_status",
        "description": "檢查索引是否過期。比較檔案修改時間，判斷是否需要重新索引。",
        "inputSchema": {
            "type": "object",
            "properties": {},
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

        try:
            if tool_name == "search_code":
                result = search_by_keyword(
                    query=arguments.get("query", ""),
                    max_results=arguments.get("max_results", 20),
                    symbol_type=arguments.get("symbol_type"),
                    project=arguments.get("project"),
                    include_content=arguments.get("include_content", False),
                )
            elif tool_name == "get_file_info":
                result = get_file_info(arguments.get("path", ""))
            elif tool_name == "get_file_symbols":
                result = get_file_symbols(arguments.get("path", ""))
            elif tool_name == "impact_analysis":
                result = impact_analysis(arguments.get("symbol_id", ""))
            elif tool_name == "list_categories":
                result = list_categories()
            elif tool_name == "list_apis":
                result = list_apis()
            elif tool_name == "list_projects":
                result = list_projects()
            elif tool_name == "get_symbol_content":
                result = get_symbol_content(arguments.get("symbol_id", ""))
            elif tool_name == "find_references":
                result = find_references(arguments.get("symbol_id", ""))
            elif tool_name == "dependency_graph":
                result = dependency_graph(
                    file_path=arguments.get("file_path"),
                    symbol_id=arguments.get("symbol_id"),
                    project=arguments.get("project"),
                    direction=arguments.get("direction", "both"),
                    max_depth=arguments.get("max_depth", 2),
                )
            elif tool_name == "fulltext_search":
                result = fulltext_search(
                    query=arguments.get("query", ""),
                    search_type=arguments.get("search_type", "all"),
                    project=arguments.get("project"),
                    max_results=arguments.get("max_results", 50),
                )
            elif tool_name == "check_index_status":
                result = check_index_status()
            else:
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
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            handle_request(request)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32000, "message": str(e)}}), flush=True)


if __name__ == "__main__":
    main()
