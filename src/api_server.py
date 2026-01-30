#!/usr/bin/env python3
"""
Flyto Indexer HTTP API Server

通用 API 服務，讓任何 AI 工具都能查詢索引。

支援：
- Cursor (HTTP API)
- OpenAI GPTs (OpenAPI spec)
- ChatGPT (HTTP API)
- 任何能發 HTTP 請求的工具

Usage:
    python -m src.api_server [--port 8765]

API Endpoints:
    GET  /health              - 健康檢查
    GET  /openapi.json        - OpenAPI 規格（給 GPTs 用）
    POST /search              - 關鍵字搜尋
    POST /file/info           - 取得檔案資訊
    POST /file/symbols        - 取得檔案 symbols
    POST /impact              - 影響分析
    GET  /categories          - 列出分類
    GET  /apis                - 列出 API
    GET  /stats               - 索引統計
"""

import json
import argparse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 索引目錄
INDEX_DIR = Path(__file__).parent.parent / ".flyto-index"

# OpenAPI 規格
OPENAPI_SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "Flyto Indexer API",
        "description": "程式碼語意索引 API。搜尋程式碼、取得檔案資訊、分析修改影響。",
        "version": "1.0.0",
    },
    "servers": [
        {"url": "http://localhost:8765", "description": "Local server"}
    ],
    "paths": {
        "/search": {
            "post": {
                "operationId": "searchCode",
                "summary": "搜尋程式碼",
                "description": "用關鍵字搜尋相關程式碼檔案。支援中英文。",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "搜尋關鍵字"},
                                    "max_results": {"type": "integer", "default": 10},
                                },
                                "required": ["query"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "搜尋結果",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string"},
                                        "total": {"type": "integer"},
                                        "results": {"type": "array"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/file/info": {
            "post": {
                "operationId": "getFileInfo",
                "summary": "取得檔案資訊",
                "description": "取得檔案的語意資訊：用途、分類、關鍵字、API、依賴等。",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string", "description": "檔案路徑"},
                                },
                                "required": ["path"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "檔案資訊"}},
            }
        },
        "/file/symbols": {
            "post": {
                "operationId": "getFileSymbols",
                "summary": "取得檔案 symbols",
                "description": "列出檔案中的所有函數、類、組件。",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                },
                                "required": ["path"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Symbols 列表"}},
            }
        },
        "/impact": {
            "post": {
                "operationId": "impactAnalysis",
                "summary": "影響分析",
                "description": "分析修改某個函數或組件會影響哪些地方。",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "symbol_id": {"type": "string", "description": "Symbol ID"},
                                },
                                "required": ["symbol_id"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "影響分析結果"}},
            }
        },
        "/categories": {
            "get": {
                "operationId": "listCategories",
                "summary": "列出分類",
                "description": "列出所有程式碼分類及檔案數量。",
                "responses": {"200": {"description": "分類列表"}},
            }
        },
        "/apis": {
            "get": {
                "operationId": "listApis",
                "summary": "列出 API",
                "description": "列出所有 API 端點及使用情況。",
                "responses": {"200": {"description": "API 列表"}},
            }
        },
        "/stats": {
            "get": {
                "operationId": "getStats",
                "summary": "索引統計",
                "description": "取得索引的統計資訊。",
                "responses": {"200": {"description": "統計資訊"}},
            }
        },
    },
}


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


def search_by_keyword(query: str, max_results: int = 10) -> dict:
    """關鍵字搜尋"""
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

    return {"query": query, "total": len(unique), "results": unique[:max_results]}


def get_file_info(path: str) -> dict:
    """取得檔案資訊"""
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
    """取得檔案 symbols"""
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
    return {"path": path, "count": len(symbols), "symbols": symbols}


def impact_analysis(symbol_id: str) -> dict:
    """影響分析"""
    index = load_index()
    dependencies = index.get("dependencies", {})
    affected = []

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
    """列出分類"""
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
    """列出 API"""
    project_map = load_project_map()
    api_map = project_map.get("api_map", {})
    return {
        "total": len(api_map),
        "apis": [
            {"path": api, "used_by_count": len(files)}
            for api, files in sorted(api_map.items(), key=lambda x: -len(x[1]))
        ],
    }


def get_stats() -> dict:
    """索引統計"""
    project_map = load_project_map()
    index = load_index()
    return {
        "total_files": len(project_map.get("files", {})),
        "total_categories": len(project_map.get("categories", {})),
        "total_keywords": len(project_map.get("keyword_index", {})),
        "total_apis": len(project_map.get("api_map", {})),
        "total_symbols": len(index.get("symbols", {})),
        "total_dependencies": len(index.get("dependencies", {})),
        "projects": project_map.get("projects", []),
        "audited_at": project_map.get("audited_at", ""),
    }


class APIHandler(BaseHTTPRequestHandler):
    """HTTP 請求處理器"""

    def _send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def _read_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            body = self.rfile.read(content_length)
            return json.loads(body.decode("utf-8"))
        return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._send_json({"ok": True, "service": "flyto-indexer"})

        elif path == "/openapi.json":
            self._send_json(OPENAPI_SPEC)

        elif path == "/categories":
            self._send_json(list_categories())

        elif path == "/apis":
            self._send_json(list_apis())

        elif path == "/stats":
            self._send_json(get_stats())

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_json()

        if path == "/search":
            query = body.get("query", "")
            max_results = body.get("max_results", 10)
            self._send_json(search_by_keyword(query, max_results))

        elif path == "/file/info":
            file_path = body.get("path", "")
            self._send_json(get_file_info(file_path))

        elif path == "/file/symbols":
            file_path = body.get("path", "")
            self._send_json(get_file_symbols(file_path))

        elif path == "/impact":
            symbol_id = body.get("symbol_id", "")
            self._send_json(impact_analysis(symbol_id))

        else:
            self._send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        logger.info(f"{self.address_string()} - {format % args}")


def main():
    parser = argparse.ArgumentParser(description="Flyto Indexer HTTP API Server")
    parser.add_argument("--port", type=int, default=8765, help="Server port")
    parser.add_argument("--host", default="0.0.0.0", help="Server host")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), APIHandler)
    logger.info(f"Flyto Indexer API Server running at http://{args.host}:{args.port}")
    logger.info(f"OpenAPI spec: http://{args.host}:{args.port}/openapi.json")
    logger.info(f"Health check: http://{args.host}:{args.port}/health")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
