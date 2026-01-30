#!/usr/bin/env python3
"""
Flyto Indexer MCP Server

讓 Claude 可以直接查詢索引和執行語意搜尋。

Usage:
    python -m src.mcp_server

Claude Code 設定 (~/.claude/mcp_servers.json):
{
    "flyto-indexer": {
        "command": "python",
        "args": ["-m", "src.mcp_server"],
        "cwd": "/Library/其他專案/flytohub/flyto-indexer"
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

# 工具實現
def search_by_keyword(query: str, max_results: int = 10) -> dict:
    """
    關鍵字搜尋

    從 PROJECT_MAP 的 keyword_index 和 categories 搜尋相關檔案
    """
    project_map = load_project_map()
    results = []
    query_lower = query.lower()
    query_words = query_lower.split()

    # 搜尋 keyword_index
    keyword_index = project_map.get("keyword_index", {})
    for keyword, paths in keyword_index.items():
        if any(w in keyword or keyword in w for w in query_words):
            for path in paths:
                file_info = project_map.get("files", {}).get(path, {})
                results.append({
                    "path": path,
                    "purpose": file_info.get("purpose", ""),
                    "category": file_info.get("category", ""),
                    "match_type": "keyword",
                    "match_value": keyword,
                })

    # 搜尋 categories
    categories = project_map.get("categories", {})
    for category, paths in categories.items():
        if any(w in category or category in w for w in query_words):
            for path in paths:
                if not any(r["path"] == path for r in results):
                    file_info = project_map.get("files", {}).get(path, {})
                    results.append({
                        "path": path,
                        "purpose": file_info.get("purpose", ""),
                        "category": category,
                        "match_type": "category",
                        "match_value": category,
                    })

    # 去重
    seen = set()
    unique = []
    for r in results:
        if r["path"] not in seen:
            seen.add(r["path"])
            unique.append(r)

    return {
        "query": query,
        "total": len(unique),
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
    """
    index = load_index()
    dependencies = index.get("dependencies", {})
    affected = []

    # 反向查詢
    for dep_id, dep in dependencies.items():
        if dep.get("target") == symbol_id or symbol_id in dep.get("target", ""):
            source_id = dep.get("source", "")
            source_symbol = index.get("symbols", {}).get(source_id, {})
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
    else:
        warning = f"⚠️ 修改會影響 {len(affected)} 個地方！"
        suggestion = "影響範圍較大，建議謹慎修改。"

    return {
        "symbol": symbol_id,
        "affected_count": len(affected),
        "affected": affected,
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


# MCP 工具定義
TOOLS = [
    {
        "name": "search_code",
        "description": "搜尋程式碼。輸入關鍵字（中英文皆可），返回相關檔案列表。例如：搜尋「購物車」會找到 Cart.vue, useCart.ts 等。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜尋關鍵字，例如：購物車、payment、auth"},
                "max_results": {"type": "integer", "default": 10, "description": "最多返回幾筆結果"},
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
                "path": {"type": "string", "description": "檔案路徑，例如：flyto-cloud/src/pages/Cart.vue"},
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
                result = search_by_keyword(arguments.get("query", ""), arguments.get("max_results", 10))
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
