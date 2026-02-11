"""Tests for SymbolResolver."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from resolver import SymbolResolver


@pytest.fixture
def basic_index():
    """Index with a few symbols across projects."""
    return {
        "symbols": {
            "proj-a:src/auth.py:function:login": {
                "name": "login",
                "path": "src/auth.py",
                "exports": ["login"],
            },
            "proj-a:src/auth.py:function:logout": {
                "name": "logout",
                "path": "src/auth.py",
                "exports": ["logout"],
            },
            "proj-b:src/utils.ts:function:formatDate": {
                "name": "formatDate",
                "path": "src/utils.ts",
                "exports": ["formatDate"],
            },
            "proj-a:src/services/user.py:class:UserService": {
                "name": "UserService",
                "path": "src/services/user.py",
                "exports": ["UserService"],
            },
            "proj-a:src/services/user.py:method:UserService.get_profile": {
                "name": "UserService.get_profile",
                "path": "src/services/user.py",
                "exports": [],
            },
        }
    }


class TestSymbolResolverInit:
    """Test SymbolResolver initialization."""

    def test_builds_export_map(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert "login" in resolver._export_map
        assert "UserService" in resolver._export_map

    def test_empty_index(self):
        resolver = SymbolResolver({})
        assert resolver._export_map == {}

    def test_index_without_symbols(self):
        resolver = SymbolResolver({"symbols": {}})
        assert resolver._export_map == {}


class TestSymbolResolverDetectLanguage:
    """Test language detection."""

    def test_python(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._detect_language("src/foo.py") == "python"

    def test_typescript(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._detect_language("src/foo.ts") == "typescript"

    def test_javascript(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._detect_language("src/foo.js") == "javascript"

    def test_vue(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._detect_language("src/App.vue") == "vue"

    def test_go(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._detect_language("src/main.go") == "go"

    def test_rust(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._detect_language("src/lib.rs") == "rust"

    def test_java(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._detect_language("src/Main.java") == "java"

    def test_unknown(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._detect_language("Makefile") == "unknown"


class TestSymbolResolverNormalize:
    """Test module path normalization."""

    def test_js_alias(self, basic_index):
        resolver = SymbolResolver(basic_index)
        result = resolver._normalize_module_path("@/composables/useAuth", "typescript", "src/foo.ts")
        assert result == "src/composables/useAuth"

    def test_go_external(self, basic_index):
        resolver = SymbolResolver(basic_index)
        result = resolver._normalize_module_path("github.com/user/mux", "go", "main.go")
        assert result == "mux"

    def test_rust_crate(self, basic_index):
        resolver = SymbolResolver(basic_index)
        result = resolver._normalize_module_path("crate::models::user", "rust", "src/lib.rs")
        assert result == "models/user"

    def test_java_package(self, basic_index):
        resolver = SymbolResolver(basic_index)
        result = resolver._normalize_module_path("com.example.service", "java", "Main.java")
        assert result == "com/example/service"


class TestSymbolResolverExtractProject:
    """Test project extraction from IDs."""

    def test_from_symbol_id(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._extract_project("proj-a:src/foo.py:function:bar") == "proj-a"

    def test_from_path(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._extract_project("proj/src/foo.py") == "proj"

    def test_no_separator(self, basic_index):
        resolver = SymbolResolver(basic_index)
        assert resolver._extract_project("standalone") == ""


class TestSymbolResolverResolveMethod:
    """Test method resolution."""

    def test_resolve_method(self, basic_index):
        resolver = SymbolResolver(basic_index)
        result = resolver._resolve_method("user", "get_profile", "proj-a:src/app.py:file:app")
        assert result is not None
        assert "get_profile" in result

    def test_resolve_nonexistent_method(self, basic_index):
        resolver = SymbolResolver(basic_index)
        result = resolver._resolve_method("user", "nonexistent", "proj-a:src/app.py:file:app")
        assert result is None
