"""Tests for cross-language API call graph detection."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import DependencyType, SymbolType
from scanner.python import PythonScanner
from scanner.typescript import TypeScriptScanner
from scanner.vue import VueScanner


@pytest.fixture
def py_scanner():
    return PythonScanner("test-project")


@pytest.fixture
def ts_scanner():
    return TypeScriptScanner("test-project")


@pytest.fixture
def vue_scanner():
    return VueScanner("test-project")


# =============================================================================
# Python API endpoint detection
# =============================================================================

class TestPythonAPIDetection:
    """Test Python decorator-based API endpoint detection."""

    def test_fastapi_get(self, py_scanner):
        code = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/users")
def list_users():
    return []
'''
        symbols, _ = py_scanner.scan_file(Path("routes.py"), code)
        api_syms = [s for s in symbols if s.symbol_type == SymbolType.API]
        assert len(api_syms) == 1
        assert api_syms[0].name == "GET /users"
        assert api_syms[0].metadata["method"] == "GET"
        assert api_syms[0].metadata["handler"] == "list_users"

    def test_fastapi_post(self, py_scanner):
        code = '''
@router.post("/items")
def create_item(item: dict):
    return item
'''
        symbols, _ = py_scanner.scan_file(Path("routes.py"), code)
        api_syms = [s for s in symbols if s.symbol_type == SymbolType.API]
        assert len(api_syms) == 1
        assert api_syms[0].name == "POST /items"
        assert api_syms[0].metadata["method"] == "POST"

    def test_flask_route_with_methods(self, py_scanner):
        code = '''
@app.route("/login", methods=["GET", "POST"])
def login():
    pass
'''
        symbols, _ = py_scanner.scan_file(Path("routes.py"), code)
        api_syms = [s for s in symbols if s.symbol_type == SymbolType.API]
        assert len(api_syms) == 1
        assert api_syms[0].name == "GET,POST /login"
        assert api_syms[0].metadata["method"] == "GET,POST"

    def test_multiple_endpoints(self, py_scanner):
        code = '''
@app.get("/users")
def list_users():
    return []

@app.get("/users/{id}")
def get_user(id: int):
    return {}

@app.delete("/users/{id}")
def delete_user(id: int):
    pass
'''
        symbols, _ = py_scanner.scan_file(Path("routes.py"), code)
        api_syms = [s for s in symbols if s.symbol_type == SymbolType.API]
        assert len(api_syms) == 3
        names = {s.name for s in api_syms}
        assert "GET /users" in names
        assert "GET /users/{id}" in names
        assert "DELETE /users/{id}" in names

    def test_no_decorator_no_api(self, py_scanner):
        code = '''
def plain_function():
    pass
'''
        symbols, _ = py_scanner.scan_file(Path("utils.py"), code)
        api_syms = [s for s in symbols if s.symbol_type == SymbolType.API]
        assert len(api_syms) == 0

    def test_all_http_methods(self, py_scanner):
        methods = ["get", "post", "put", "delete", "patch"]
        for method in methods:
            code = f'''
@app.{method}("/test")
def handler():
    pass
'''
            symbols, _ = py_scanner.scan_file(Path("routes.py"), code)
            api_syms = [s for s in symbols if s.symbol_type == SymbolType.API]
            assert len(api_syms) == 1, f"Failed for method: {method}"
            assert api_syms[0].metadata["method"] == method.upper()


# =============================================================================
# TypeScript API call detection
# =============================================================================

class TestTypeScriptAPICallDetection:
    """Test TypeScript fetch/axios API call detection."""

    def test_fetch_single_quote(self, ts_scanner):
        code = "const data = fetch('/api/users')\n"
        _, deps = ts_scanner.scan_file(Path("service.ts"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 1
        assert api_deps[0].target_id == "/api/users"
        assert api_deps[0].metadata["method"] == "GET"

    def test_fetch_backtick(self, ts_scanner):
        code = "const data = fetch(`/api/items`)\n"
        _, deps = ts_scanner.scan_file(Path("service.ts"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 1
        assert api_deps[0].target_id == "/api/items"

    def test_axios_get(self, ts_scanner):
        code = "const res = axios.get('/api/users')\n"
        _, deps = ts_scanner.scan_file(Path("service.ts"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 1
        assert api_deps[0].target_id == "/api/users"
        assert api_deps[0].metadata["method"] == "GET"

    def test_axios_post(self, ts_scanner):
        code = "axios.post('/api/items', { name: 'test' })\n"
        _, deps = ts_scanner.scan_file(Path("service.ts"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 1
        assert api_deps[0].metadata["method"] == "POST"

    def test_api_get(self, ts_scanner):
        code = "api.get('/users')\n"
        _, deps = ts_scanner.scan_file(Path("service.ts"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 1
        assert api_deps[0].target_id == "/users"

    def test_http_delete(self, ts_scanner):
        code = "$http.delete('/api/items/123')\n"
        _, deps = ts_scanner.scan_file(Path("service.ts"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 1
        assert api_deps[0].metadata["method"] == "DELETE"

    def test_multiple_api_calls(self, ts_scanner):
        code = """
fetch('/api/users')
axios.post('/api/items')
api.delete('/api/orders')
"""
        _, deps = ts_scanner.scan_file(Path("service.ts"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 3

    def test_no_api_calls(self, ts_scanner):
        code = "const x = 1 + 2\n"
        _, deps = ts_scanner.scan_file(Path("util.ts"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 0


# =============================================================================
# Vue API call detection
# =============================================================================

class TestVueAPICallDetection:
    """Test Vue component API call detection in <script> blocks."""

    def test_fetch_in_script_setup(self, vue_scanner):
        code = """<template><div>test</div></template>
<script setup>
const data = await fetch('/api/products')
</script>
"""
        _, deps = vue_scanner.scan_file(Path("Products.vue"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 1
        assert api_deps[0].target_id == "/api/products"

    def test_axios_in_script(self, vue_scanner):
        code = """<template><div>test</div></template>
<script>
export default {
  methods: {
    async loadData() {
      const res = axios.get('/api/data')
    }
  }
}
</script>
"""
        _, deps = vue_scanner.scan_file(Path("Data.vue"), code)
        api_deps = [d for d in deps if d.dep_type == DependencyType.API_CALLS]
        assert len(api_deps) == 1
        assert api_deps[0].metadata["method"] == "GET"


# =============================================================================
# E2E: Cross-reference engine test
# =============================================================================

class TestCrossLanguageEngine:
    """Test that engine correctly cross-references Python API → TS/Vue callers."""

    def test_api_cross_reference(self):
        """End-to-end: Python defines API, TypeScript calls it."""
        from src.engine import IndexEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Create Python backend file
            backend = root / "backend" / "routes.py"
            backend.parent.mkdir(parents=True)
            backend.write_text('''
from fastapi import FastAPI
app = FastAPI()

@app.get("/api/users")
def list_users():
    return []

@app.post("/api/items")
def create_item(data: dict):
    return data
''')

            # Create TypeScript frontend file
            frontend = root / "frontend" / "api.ts"
            frontend.parent.mkdir(parents=True)
            frontend.write_text('''
export async function getUsers() {
  return fetch('/api/users')
}

export async function createItem(data: any) {
  return axios.post('/api/items')
}
''')

            # Run engine
            engine = IndexEngine("test", root)
            engine.scan(incremental=False)

            # Verify API symbols were created
            api_syms = {
                sid: sym for sid, sym in engine.index.symbols.items()
                if sym.symbol_type == SymbolType.API
            }
            assert len(api_syms) >= 2, f"Expected at least 2 API symbols, got {len(api_syms)}"

            # Verify API_CALLS dependencies were created
            api_call_deps = {
                did: dep for did, dep in engine.index.dependencies.items()
                if dep.dep_type == DependencyType.API_CALLS
            }
            assert len(api_call_deps) >= 2, f"Expected at least 2 API_CALLS deps, got {len(api_call_deps)}"

            # Verify cross-reference resolution
            resolved_count = sum(
                1 for dep in api_call_deps.values()
                if dep.metadata.get("resolved_target")
            )
            assert resolved_count >= 1, "Expected at least 1 resolved API cross-reference"
