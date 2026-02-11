"""Tests for mapper/symbol_index module."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from mapper.symbol_index import Symbol, SymbolIndexer


class TestSymbolDataclass:
    """Test the mapper Symbol dataclass (different from models.Symbol)."""

    def test_creation(self):
        sym = Symbol(
            name="login",
            kind="function",
            file="src/auth.py",
            line=10,
        )
        assert sym.name == "login"
        assert sym.kind == "function"
        assert sym.file == "src/auth.py"
        assert sym.line == 10
        assert sym.exported is True  # default

    def test_method_with_parent(self):
        sym = Symbol(
            name="validate",
            kind="method",
            file="src/user.py",
            line=20,
            parent="UserService",
        )
        assert sym.parent == "UserService"


class TestSymbolIndexerPython:
    """Test Python symbol extraction."""

    def test_extract_class(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = "class MyClass:\n    pass\n"
        symbols = indexer.extract_python_symbols("test.py", code)
        assert len(symbols) >= 1
        classes = [s for s in symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "MyClass"

    def test_extract_function(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = "def hello():\n    pass\n"
        symbols = indexer.extract_python_symbols("test.py", code)
        funcs = [s for s in symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "hello"

    def test_extract_method(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = (
            "class Foo:\n"
            "    def bar(self, x):\n"
            "        return x\n"
        )
        symbols = indexer.extract_python_symbols("test.py", code)
        methods = [s for s in symbols if s.kind == "method"]
        assert len(methods) >= 1
        assert methods[0].parent == "Foo"
        assert "x" in methods[0].params

    def test_private_skipped(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = "def _private():\n    pass\n"
        symbols = indexer.extract_python_symbols("test.py", code)
        assert len(symbols) == 0

    def test_syntax_error(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = "def bad(:\n  pass"
        symbols = indexer.extract_python_symbols("bad.py", code)
        assert symbols == []


class TestSymbolIndexerTypeScript:
    """Test TypeScript symbol extraction."""

    def test_extract_function(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = "export function greet(name: string) {\n  return name;\n}\n"
        symbols = indexer.extract_typescript_symbols("test.ts", code)
        funcs = [s for s in symbols if s.kind == "function"]
        assert len(funcs) >= 1
        assert funcs[0].name == "greet"
        assert funcs[0].exported is True

    def test_extract_class(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = "export class UserService {\n  name: string;\n}\n"
        symbols = indexer.extract_typescript_symbols("test.ts", code)
        classes = [s for s in symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "UserService"

    def test_extract_interface(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = "export interface Config {\n  port: number;\n}\n"
        symbols = indexer.extract_typescript_symbols("test.ts", code)
        ifaces = [s for s in symbols if s.kind == "interface"]
        assert len(ifaces) == 1
        assert ifaces[0].name == "Config"

    def test_extract_type(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = "export type Status = 'active' | 'inactive';\n"
        symbols = indexer.extract_typescript_symbols("test.ts", code)
        types = [s for s in symbols if s.kind == "type"]
        assert len(types) == 1
        assert types[0].name == "Status"


class TestSymbolIndexerVue:
    """Test Vue file symbol extraction."""

    def test_component_extracted(self):
        indexer = SymbolIndexer(Path("/tmp"))
        code = (
            "<template><div></div></template>\n"
            "<script setup lang='ts'>\n"
            "function handleClick() { return 1; }\n"
            "</script>\n"
        )
        symbols = indexer.extract_vue_symbols("MyComp.vue", code)
        comp = [s for s in symbols if s.kind == "component"]
        assert len(comp) == 1
        assert comp[0].name == "MyComp"


class TestSymbolIndexerSearch:
    """Test search functionality."""

    def test_exact_match_higher_score(self):
        indexer = SymbolIndexer(Path("/tmp"))
        index = {
            "symbols": {
                "login": [{"file": "a.py", "line": 1, "kind": "function"}],
                "loginPage": [{"file": "b.vue", "line": 1, "kind": "component"}],
            }
        }
        results = indexer.search(index, "login")
        assert len(results) >= 2
        # Exact match should score higher
        assert results[0]["name"] == "login"

    def test_case_insensitive_search(self):
        indexer = SymbolIndexer(Path("/tmp"))
        index = {
            "symbols": {
                "UserService": [{"file": "a.py", "line": 1, "kind": "class"}],
            }
        }
        results = indexer.search(index, "userservice")
        assert len(results) == 1
        assert results[0]["name"] == "UserService"

    def test_search_limit(self):
        indexer = SymbolIndexer(Path("/tmp"))
        index = {
            "symbols": {
                f"fn_{i}": [{"file": "a.py", "line": i, "kind": "function"}]
                for i in range(20)
            }
        }
        results = indexer.search(index, "fn", limit=5)
        assert len(results) <= 5

    def test_no_results(self):
        indexer = SymbolIndexer(Path("/tmp"))
        index = {"symbols": {}}
        results = indexer.search(index, "nonexistent")
        assert results == []
