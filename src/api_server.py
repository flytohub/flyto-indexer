#!/usr/bin/env python3
"""
Flyto Indexer HTTP API Server

General-purpose API service that allows any AI tool to query the index.

Supports:
- Cursor (HTTP API)
- OpenAI GPTs (OpenAPI spec)
- ChatGPT (HTTP API)
- Any tool that can make HTTP requests

Usage:
    python -m src.api_server [--port 8765]

API Endpoints:
    GET  /health              - Health check
    GET  /openapi.json        - OpenAPI spec (for GPTs)
    POST /search              - Keyword search
    POST /file/info           - Get file info
    POST /file/symbols        - Get file symbols
    POST /impact              - Impact analysis
    GET  /categories          - List categories
    GET  /apis                - List APIs
    GET  /stats               - Index statistics
"""

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Index directory
INDEX_DIR = Path(__file__).parent.parent / ".flyto-index"

# OpenAPI spec
OPENAPI_SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "Flyto Indexer API",
        "description": "Code semantic indexing API. Search code, get file info, analyze change impact.",
        "version": "1.0.0",
    },
    "servers": [
        {"url": "http://localhost:8765", "description": "Local server"}
    ],
    "paths": {
        "/search": {
            "post": {
                "operationId": "searchCode",
                "summary": "Search code",
                "description": "Search for relevant code files by keyword.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Search keyword"},
                                    "max_results": {"type": "integer", "default": 10},
                                },
                                "required": ["query"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Search results",
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
                "summary": "Get file info",
                "description": "Get semantic info for a file: purpose, category, keywords, APIs, dependencies, etc.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string", "description": "File path"},
                                },
                                "required": ["path"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "File info"}},
            }
        },
        "/file/symbols": {
            "post": {
                "operationId": "getFileSymbols",
                "summary": "Get file symbols",
                "description": "List all functions, classes, and components in a file.",
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
                "responses": {"200": {"description": "Symbols list"}},
            }
        },
        "/impact": {
            "post": {
                "operationId": "impactAnalysis",
                "summary": "Impact analysis",
                "description": "Analyze which locations are affected by modifying a function or component.",
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
                "responses": {"200": {"description": "Impact analysis results"}},
            }
        },
        "/categories": {
            "get": {
                "operationId": "listCategories",
                "summary": "List categories",
                "description": "List all code categories and file counts.",
                "responses": {"200": {"description": "Category list"}},
            }
        },
        "/apis": {
            "get": {
                "operationId": "listApis",
                "summary": "List APIs",
                "description": "List all API endpoints and their usage.",
                "responses": {"200": {"description": "API list"}},
            }
        },
        "/stats": {
            "get": {
                "operationId": "getStats",
                "summary": "Index statistics",
                "description": "Get index statistics.",
                "responses": {"200": {"description": "Statistics"}},
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
    """Keyword search"""
    project_map = load_project_map()
    results = []
    query_lower = query.lower()
    query_words = query_lower.split()

    # Search keyword_index
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

    # Search categories
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

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        if r["path"] not in seen:
            seen.add(r["path"])
            unique.append(r)

    return {"query": query, "total": len(unique), "results": unique[:max_results]}


def get_file_info(path: str) -> dict:
    """Get file info"""
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
    """Get file symbols"""
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
    """Impact analysis"""
    index = load_index()
    dependencies = index.get("dependencies", {})
    affected = []

    for _dep_id, dep in dependencies.items():
        if dep.get("target") == symbol_id or symbol_id in dep.get("target", ""):
            source_id = dep.get("source", "")
            source_symbol = index.get("symbols", {}).get(source_id, {})
            affected.append({
                "id": source_id,
                "path": source_symbol.get("path", ""),
                "name": source_symbol.get("name", ""),
                "type": dep.get("type", ""),
                "reason": f"via {dep.get('type', 'unknown')} dependency",
            })

    warning = ""
    if len(affected) == 0:
        suggestion = "This symbol is not referenced elsewhere, safe to modify."
    elif len(affected) <= 3:
        warning = f"Modification affects {len(affected)} locations"
        suggestion = "Impact scope is small, recommend checking each call site."
    else:
        warning = f"Warning: modification affects {len(affected)} locations!"
        suggestion = "Impact scope is large, recommend modifying with caution."

    return {
        "symbol": symbol_id,
        "affected_count": len(affected),
        "affected": affected,
        "warning": warning,
        "suggestion": suggestion,
    }


def list_categories() -> dict:
    """List categories"""
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
    """List APIs"""
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
    """Index statistics"""
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
    """HTTP request handler"""

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
