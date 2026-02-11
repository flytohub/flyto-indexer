"""Tests for api_server module."""

import os
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api_server import (
    search_by_keyword, get_file_info, get_file_symbols,
    impact_analysis, list_categories, list_apis, get_stats,
    OPENAPI_SPEC, APIHandler,
)


class TestOpenAPISpec:
    """Test OpenAPI specification."""

    def test_spec_version(self):
        assert OPENAPI_SPEC["openapi"] == "3.1.0"

    def test_spec_has_info(self):
        assert "info" in OPENAPI_SPEC
        assert "title" in OPENAPI_SPEC["info"]
        assert "version" in OPENAPI_SPEC["info"]

    def test_spec_has_paths(self):
        paths = OPENAPI_SPEC["paths"]
        assert "/search" in paths
        assert "/file/info" in paths
        assert "/file/symbols" in paths
        assert "/impact" in paths
        assert "/categories" in paths
        assert "/apis" in paths
        assert "/stats" in paths

    def test_search_endpoint_is_post(self):
        assert "post" in OPENAPI_SPEC["paths"]["/search"]

    def test_categories_endpoint_is_get(self):
        assert "get" in OPENAPI_SPEC["paths"]["/categories"]


class TestSearchByKeyword:
    """Test search_by_keyword function."""

    @patch("api_server.load_project_map")
    def test_search_returns_results(self, mock_load):
        mock_load.return_value = {
            "keyword_index": {
                "auth": ["src/auth.py"],
                "login": ["src/auth.py", "src/views/login.vue"],
            },
            "files": {
                "src/auth.py": {"purpose": "Authentication", "category": "api"},
                "src/views/login.vue": {"purpose": "Login page", "category": "view"},
            },
            "categories": {},
        }
        result = search_by_keyword("auth")
        assert result["query"] == "auth"
        assert result["total"] >= 1
        assert any(r["path"] == "src/auth.py" for r in result["results"])

    @patch("api_server.load_project_map")
    def test_search_empty_query(self, mock_load):
        mock_load.return_value = {"keyword_index": {}, "files": {}, "categories": {}}
        result = search_by_keyword("")
        assert result["total"] == 0
        assert result["results"] == []

    @patch("api_server.load_project_map")
    def test_search_respects_max_results(self, mock_load):
        mock_load.return_value = {
            "keyword_index": {
                "x": [f"file_{i}.py" for i in range(20)],
            },
            "files": {f"file_{i}.py": {"purpose": "f", "category": "mod"} for i in range(20)},
            "categories": {},
        }
        result = search_by_keyword("x", max_results=5)
        assert len(result["results"]) <= 5


class TestGetFileInfo:
    """Test get_file_info function."""

    @patch("api_server.load_project_map")
    def test_file_found(self, mock_load):
        mock_load.return_value = {
            "files": {
                "src/auth.py": {
                    "purpose": "Authentication",
                    "category": "api",
                    "keywords": ["auth", "login"],
                    "apis": ["/api/login"],
                    "dependencies": ["jwt"],
                },
            },
        }
        result = get_file_info("src/auth.py")
        assert result["path"] == "src/auth.py"
        assert result["purpose"] == "Authentication"
        assert result["category"] == "api"

    @patch("api_server.load_project_map")
    def test_file_not_found(self, mock_load):
        mock_load.return_value = {"files": {}}
        result = get_file_info("nonexistent.py")
        assert "error" in result


class TestGetFileSymbols:
    """Test get_file_symbols function."""

    @patch("api_server.load_index")
    def test_symbols_found(self, mock_load):
        mock_load.return_value = {
            "symbols": {
                "proj:src/auth.py:function:login": {
                    "path": "src/auth.py",
                    "name": "login",
                    "type": "function",
                    "start_line": 10,
                    "summary": "Login function",
                },
            },
        }
        result = get_file_symbols("src/auth.py")
        assert result["path"] == "src/auth.py"
        assert result["count"] == 1
        assert result["symbols"][0]["name"] == "login"

    @patch("api_server.load_index")
    def test_no_symbols(self, mock_load):
        mock_load.return_value = {"symbols": {}}
        result = get_file_symbols("empty.py")
        assert result["count"] == 0


class TestImpactAnalysis:
    """Test impact_analysis function."""

    @patch("api_server.load_index")
    def test_no_impact(self, mock_load):
        mock_load.return_value = {"symbols": {}, "dependencies": {}}
        result = impact_analysis("nonexistent")
        assert result["affected_count"] == 0
        assert "safe" in result["suggestion"].lower() or len(result["affected"]) == 0

    @patch("api_server.load_index")
    def test_has_impact(self, mock_load):
        mock_load.return_value = {
            "symbols": {
                "proj:src/a.py:function:caller": {
                    "path": "src/a.py",
                    "name": "caller",
                },
            },
            "dependencies": {
                "dep1": {
                    "source": "proj:src/a.py:function:caller",
                    "target": "login",
                    "type": "calls",
                },
            },
        }
        result = impact_analysis("login")
        assert result["affected_count"] >= 1


class TestListCategories:
    """Test list_categories function."""

    @patch("api_server.load_project_map")
    def test_list_categories(self, mock_load):
        mock_load.return_value = {
            "categories": {
                "api": ["a.py", "b.py"],
                "util": ["c.py"],
            },
        }
        result = list_categories()
        assert result["total"] == 2
        assert len(result["categories"]) == 2
        # Sorted by count descending
        assert result["categories"][0]["file_count"] == 2


class TestListApis:
    """Test list_apis function."""

    @patch("api_server.load_project_map")
    def test_list_apis(self, mock_load):
        mock_load.return_value = {
            "api_map": {
                "/api/login": ["auth.py"],
                "/api/users": ["auth.py", "admin.py"],
            },
        }
        result = list_apis()
        assert result["total"] == 2
        assert result["apis"][0]["used_by_count"] == 2  # sorted desc


class TestGetStats:
    """Test get_stats function."""

    @patch("api_server.load_index")
    @patch("api_server.load_project_map")
    def test_stats(self, mock_map, mock_index):
        mock_map.return_value = {
            "files": {"a.py": {}, "b.py": {}},
            "categories": {"api": ["a.py"]},
            "keyword_index": {"auth": ["a.py"]},
            "api_map": {"/api/login": ["a.py"]},
            "projects": ["proj"],
            "audited_at": "2026-01-01",
        }
        mock_index.return_value = {
            "symbols": {"s1": {}, "s2": {}},
            "dependencies": {"d1": {}},
        }
        result = get_stats()
        assert result["total_files"] == 2
        assert result["total_symbols"] == 2
        assert result["total_dependencies"] == 1
