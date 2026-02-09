"""Basic tests for flyto-indexer."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# 將 src 加入路徑
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models import Symbol, Dependency, SymbolType, DependencyType, ProjectIndex
from scanner.python import PythonScanner
from scanner.vue import VueScanner

# engine uses relative imports, so import via package
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.engine import IndexEngine


class TestSymbol:
    """Test Symbol model."""

    def test_symbol_id(self):
        """Symbol ID 格式正確"""
        symbol = Symbol(
            project="flyto-cloud",
            path="src/pages/TopUp.vue",
            symbol_type=SymbolType.COMPONENT,
            name="TopUp",
        )
        assert symbol.id == "flyto-cloud:src/pages/TopUp.vue:component:TopUp"

    def test_symbol_hash(self):
        """Symbol hash 計算正確"""
        symbol = Symbol(
            project="test",
            path="test.py",
            symbol_type=SymbolType.FUNCTION,
            name="test_func",
            content="def test_func():\n    pass",
        )
        hash1 = symbol.compute_hash()
        assert len(hash1) == 16

        # 內容不同，hash 不同
        symbol2 = Symbol(
            project="test",
            path="test.py",
            symbol_type=SymbolType.FUNCTION,
            name="test_func",
            content="def test_func():\n    return 1",
        )
        hash2 = symbol2.compute_hash()
        assert hash1 != hash2


class TestProjectIndex:
    """Test ProjectIndex model."""

    def test_impact_chain(self):
        """影響鏈查詢正確"""
        index = ProjectIndex(project="test", root_path="/test")

        # 建立 symbols
        index.symbols["test:a.py:function:a"] = Symbol(
            project="test", path="a.py", symbol_type=SymbolType.FUNCTION, name="a"
        )
        index.symbols["test:b.py:function:b"] = Symbol(
            project="test", path="b.py", symbol_type=SymbolType.FUNCTION, name="b"
        )
        index.symbols["test:c.py:function:c"] = Symbol(
            project="test", path="c.py", symbol_type=SymbolType.FUNCTION, name="c"
        )

        # 建立依賴：c -> b -> a
        index.dependencies["dep1"] = Dependency(
            source_id="test:b.py:function:b",
            target_id="test:a.py:function:a",
            dep_type=DependencyType.CALLS,
        )
        index.dependencies["dep2"] = Dependency(
            source_id="test:c.py:function:c",
            target_id="test:b.py:function:b",
            dep_type=DependencyType.CALLS,
        )

        # 改了 a，影響 b 和 c
        chain = index.get_impact_chain("test:a.py:function:a", max_depth=3)
        assert len(chain["levels"]) >= 1
        assert "test:b.py:function:b" in chain["levels"][0]["symbols"]


class TestPythonScanner:
    """Test Python scanner."""

    def test_scan_function(self):
        """能正確掃描 Python function"""
        scanner = PythonScanner("test")
        content = '''
def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}"
'''
        symbols, deps = scanner.scan_file(Path("test.py"), content)

        assert len(symbols) == 1
        assert symbols[0].name == "hello"
        assert symbols[0].symbol_type == SymbolType.FUNCTION
        assert "name" in symbols[0].params

    def test_scan_class(self):
        """能正確掃描 Python class"""
        scanner = PythonScanner("test")
        content = '''
class MyClass:
    """A test class."""

    def method(self, x):
        return x * 2
'''
        symbols, deps = scanner.scan_file(Path("test.py"), content)

        # 應該有 class 和 method
        class_symbols = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        method_symbols = [s for s in symbols if s.symbol_type == SymbolType.METHOD]

        assert len(class_symbols) == 1
        assert class_symbols[0].name == "MyClass"
        assert len(method_symbols) == 1
        assert method_symbols[0].name == "MyClass.method"


class TestVueScanner:
    """Test Vue scanner."""

    def test_scan_component(self):
        """能正確掃描 Vue component"""
        scanner = VueScanner("test")
        content = '''<template>
  <div>{{ message }}</div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useStore } from '@/stores/main'

const message = ref('Hello')
const store = useStore()

function handleClick() {
  console.log('clicked')
}
</script>
'''
        symbols, deps = scanner.scan_file(Path("Hello.vue"), content)

        # 應該有 component
        comp_symbols = [s for s in symbols if s.symbol_type == SymbolType.COMPONENT]
        assert len(comp_symbols) == 1
        assert comp_symbols[0].name == "Hello"

        # 應該有 import dependencies
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        assert len(import_deps) >= 2  # vue 和 @/stores/main


class TestFileSizeLimit:
    """Test file size limit in scan."""

    def test_file_size_limit(self, tmp_path):
        """Files larger than 1MB should be skipped."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        large_file = project_dir / "large.py"
        large_file.write_text("x = 1\n" * 200_000)  # ~1.2MB
        assert large_file.stat().st_size > 1_048_576

        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        result = engine.scan(incremental=False)
        assert result["errors"] >= 1

    def test_normal_file_scanned(self, tmp_path):
        """Normal-sized files should be scanned."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        normal_file = project_dir / "normal.py"
        normal_file.write_text("def hello():\n    pass\n")

        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        result = engine.scan(incremental=False)
        assert result["errors"] == 0
        assert result["symbols_found"] >= 1


class TestScanFileErrors:
    """Test scan file error handling."""

    def test_scan_file_syntax_error(self, tmp_path):
        """Syntax errors should not crash the engine."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        bad_file = project_dir / "bad.py"
        bad_file.write_text("def broken(\n")  # invalid syntax

        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        result = engine.scan(incremental=False)
        # Should complete without raising
        assert result["files_scanned"] >= 1

    def test_scan_file_unicode_error(self, tmp_path):
        """Binary/encoding errors should not crash the engine."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        bin_file = project_dir / "binary.py"
        # Create content that will cause UnicodeDecodeError with utf-8
        bin_file.write_bytes(b"def foo():\n    x = " + bytes(range(128, 256)) * 10)

        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        # Should complete without raising (file excluded at hash stage)
        result = engine.scan(incremental=False)
        assert isinstance(result, dict)


class TestResolveSymbolId:
    """Test _resolve_symbol_id fuzzy matching fix."""

    def _make_engine_with_symbols(self, tmp_path, symbol_ids):
        """Helper to create an engine with pre-populated symbols."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        for sid in symbol_ids:
            parts = sid.split(":")
            engine.index.symbols[sid] = Symbol(
                project=parts[0],
                path=parts[1],
                symbol_type=SymbolType(parts[2]),
                name=parts[3],
            )
        return engine

    def test_resolve_symbol_exact(self, tmp_path):
        """Exact match should return the symbol."""
        engine = self._make_engine_with_symbols(tmp_path, [
            "test:a.py:function:hello",
        ])
        assert engine._resolve_symbol_id("test:a.py:function:hello") == "test:a.py:function:hello"

    def test_resolve_symbol_short_format(self, tmp_path):
        """Short format (without project) should resolve."""
        engine = self._make_engine_with_symbols(tmp_path, [
            "test:a.py:function:hello",
        ])
        assert engine._resolve_symbol_id("a.py:function:hello") == "test:a.py:function:hello"

    def test_resolve_symbol_name_match(self, tmp_path):
        """Name-only match with colon prefix should resolve."""
        engine = self._make_engine_with_symbols(tmp_path, [
            "test:a.py:function:hello",
        ])
        assert engine._resolve_symbol_id("hello") == "test:a.py:function:hello"

    def test_resolve_symbol_no_partial_match(self, tmp_path):
        """Partial name match should NOT resolve (prevent false positives)."""
        engine = self._make_engine_with_symbols(tmp_path, [
            "test:a.py:function:get_hello",
        ])
        # "hello" should NOT match "get_hello"
        assert engine._resolve_symbol_id("hello") is None

    def test_resolve_symbol_not_found(self, tmp_path):
        """Non-existent symbol should return None."""
        engine = self._make_engine_with_symbols(tmp_path, [
            "test:a.py:function:hello",
        ])
        assert engine._resolve_symbol_id("nonexistent") is None


class TestIndexLoading:
    """Test index loading with corrupted data."""

    def test_load_corrupted_index(self, tmp_path):
        """Corrupted index file should fallback to empty index."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        index_dir = tmp_path / ".index"
        index_dir.mkdir()
        (index_dir / "index.json").write_text("{invalid json!!")

        engine = IndexEngine("test", project_dir, index_dir)
        assert len(engine.index.symbols) == 0
        assert engine.index.project == "test"

    def test_load_content_invalid_json(self, tmp_path):
        """Invalid JSONL lines in content file should not crash."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        index_dir = tmp_path / ".index"
        index_dir.mkdir()

        # Write a valid index that references content file
        index_data = {
            "project": "test",
            "root_path": str(project_dir),
            "files": {},
            "symbols": {},
            "dependencies": {},
            "has_content_file": True,
        }
        (index_dir / "index.json").write_text(json.dumps(index_data))
        (index_dir / "content.jsonl").write_text("{bad json\n")

        engine = IndexEngine("test", project_dir, index_dir)
        # Should load without crashing, content_map will be empty
        assert engine.index.project == "test"


class TestReverseIndex:
    """Test reverse index building."""

    def test_reverse_index_basic(self, tmp_path):
        """Reverse index should track callers correctly."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        # Add symbols
        engine.index.symbols["test:a.py:function:target_func"] = Symbol(
            project="test", path="a.py", symbol_type=SymbolType.FUNCTION, name="target_func"
        )
        engine.index.symbols["test:b.py:function:caller_func"] = Symbol(
            project="test", path="b.py", symbol_type=SymbolType.FUNCTION, name="caller_func"
        )

        # Add dependency with resolved_target
        engine.index.dependencies["dep1"] = Dependency(
            source_id="test:b.py:function:caller_func",
            target_id="target_func",
            dep_type=DependencyType.CALLS,
            metadata={"resolved_target": "test:a.py:function:target_func"},
        )

        engine._build_reverse_index()
        reverse = engine.index.reverse_index
        assert "test:a.py:function:target_func" in reverse
        assert "test:b.py:function:caller_func" in reverse["test:a.py:function:target_func"]

    def test_reverse_index_reference_count(self, tmp_path):
        """Reference count should match number of unique callers."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        engine.index.symbols["test:a.py:function:target"] = Symbol(
            project="test", path="a.py", symbol_type=SymbolType.FUNCTION, name="target"
        )
        engine.index.symbols["test:b.py:function:caller1"] = Symbol(
            project="test", path="b.py", symbol_type=SymbolType.FUNCTION, name="caller1"
        )
        engine.index.symbols["test:c.py:function:caller2"] = Symbol(
            project="test", path="c.py", symbol_type=SymbolType.FUNCTION, name="caller2"
        )

        engine.index.dependencies["dep1"] = Dependency(
            source_id="test:b.py:function:caller1",
            target_id="target",
            dep_type=DependencyType.CALLS,
            metadata={"resolved_target": "test:a.py:function:target"},
        )
        engine.index.dependencies["dep2"] = Dependency(
            source_id="test:c.py:function:caller2",
            target_id="target",
            dep_type=DependencyType.CALLS,
            metadata={"resolved_target": "test:a.py:function:target"},
        )

        engine._build_reverse_index()
        assert engine.index.symbols["test:a.py:function:target"].reference_count == 2


# =========================================================================
# Feature 2: Test File Mapping
# =========================================================================

class TestTestMapper:
    """Test TestMapper for source ↔ test file mapping."""

    def _make_index(self, symbols_data, dependencies=None):
        """Helper to create a mock index dict."""
        symbols = {}
        for sid, path in symbols_data:
            parts = sid.split(":")
            symbols[sid] = {
                "path": path,
                "name": parts[-1] if parts else "",
                "type": parts[-2] if len(parts) >= 3 else "function",
                "start_line": 1,
            }
        return {
            "symbols": symbols,
            "dependencies": dependencies or {},
        }

    def test_python_convention(self):
        """src/foo.py → tests/test_foo.py"""
        from test_mapper import TestMapper
        index = self._make_index([
            ("proj:src/foo.py:function:foo", "src/foo.py"),
            ("proj:tests/test_foo.py:function:test_foo", "tests/test_foo.py"),
        ])
        mapper = TestMapper(index)
        assert mapper.find_test("src/foo.py") == "tests/test_foo.py"
        assert mapper.find_source("tests/test_foo.py") == "src/foo.py"

    def test_vue_convention(self):
        """Foo.vue → Foo.test.ts"""
        from test_mapper import TestMapper
        index = self._make_index([
            ("proj:src/Foo.vue:component:Foo", "src/Foo.vue"),
            ("proj:tests/Foo.test.ts:function:test_foo", "tests/Foo.test.ts"),
        ])
        mapper = TestMapper(index)
        assert mapper.find_test("src/Foo.vue") == "tests/Foo.test.ts"

    def test_spec_convention(self):
        """Bar.vue → Bar.spec.ts"""
        from test_mapper import TestMapper
        index = self._make_index([
            ("proj:src/Bar.vue:component:Bar", "src/Bar.vue"),
            ("proj:tests/Bar.spec.ts:function:test_bar", "tests/Bar.spec.ts"),
        ])
        mapper = TestMapper(index)
        assert mapper.find_test("src/Bar.vue") == "tests/Bar.spec.ts"

    def test_no_match(self):
        """No matching test file returns None."""
        from test_mapper import TestMapper
        index = self._make_index([
            ("proj:src/foo.py:function:foo", "src/foo.py"),
            ("proj:tests/test_bar.py:function:test_bar", "tests/test_bar.py"),
        ])
        mapper = TestMapper(index)
        assert mapper.find_test("src/foo.py") is None

    def test_is_test_file(self):
        """_is_test_file correctly identifies test files."""
        from test_mapper import TestMapper
        assert TestMapper._is_test_file("tests/test_foo.py") is True
        assert TestMapper._is_test_file("foo.test.ts") is True
        assert TestMapper._is_test_file("foo.spec.js") is True
        assert TestMapper._is_test_file("__tests__/Foo.js") is True
        assert TestMapper._is_test_file("src/foo.py") is False
        assert TestMapper._is_test_file("src/utils.ts") is False

    def test_import_fallback(self):
        """Import analysis finds test→source mapping when convention fails."""
        from test_mapper import TestMapper
        index = self._make_index(
            [
                ("proj:src/engine.py:class:Engine", "src/engine.py"),
                ("proj:tests/test_engine_integration.py:function:test_it", "tests/test_engine_integration.py"),
            ],
            dependencies={
                "dep1": {
                    "source": "proj:tests/test_engine_integration.py:function:test_it",
                    "target": "Engine",
                    "type": "imports",
                    "metadata": {"resolved_target": "proj:src/engine.py:class:Engine", "names": ["Engine"]},
                },
            }
        )
        mapper = TestMapper(index)
        # Convention won't match (test_engine_integration != test_engine)
        # But import analysis should find it
        assert mapper.find_test("src/engine.py") == "tests/test_engine_integration.py"


# =========================================================================
# Feature 5: Session
# =========================================================================

class TestSession:
    """Test Session and SessionStore."""

    def test_session_add_file(self):
        """Session tracks opened files."""
        from session import Session
        s = Session(session_id="test", workspace_root="/tmp")
        s.add_file("src/foo.py")
        s.add_file("src/bar.py")
        assert s.open_files == ["src/bar.py", "src/foo.py"]

    def test_session_dedup_file(self):
        """Re-opening a file moves it to front."""
        from session import Session
        s = Session(session_id="test", workspace_root="/tmp")
        s.add_file("src/foo.py")
        s.add_file("src/bar.py")
        s.add_file("src/foo.py")
        assert s.open_files[0] == "src/foo.py"
        assert len(s.open_files) == 2

    def test_session_max_files(self):
        """Session limits open files to MAX_OPEN_FILES."""
        from session import Session
        s = Session(session_id="test", workspace_root="/tmp")
        for i in range(60):
            s.add_file(f"file_{i}.py")
        assert len(s.open_files) == Session.MAX_OPEN_FILES

    def test_session_boost_paths(self):
        """Boost paths include open files and recent edits."""
        from session import Session
        s = Session(session_id="test", workspace_root="/tmp")
        s.add_file("src/a.py")
        s.add_edit("src/b.py")
        boost = s.get_boost_paths()
        assert "src/a.py" in boost
        assert "src/b.py" in boost

    def test_session_to_dict(self):
        """to_dict returns serializable session state."""
        from session import Session
        s = Session(session_id="test", workspace_root="/tmp")
        s.add_file("src/a.py")
        d = s.to_dict()
        assert d["session_id"] == "test"
        assert d["open_files_count"] == 1

    def test_session_store_get_or_create(self):
        """SessionStore creates and retrieves sessions."""
        from session import SessionStore
        store = SessionStore()
        s1 = store.get_or_create("sess1", "/tmp")
        s2 = store.get_or_create("sess1", "/tmp")
        assert s1 is s2

    def test_session_store_eviction(self):
        """SessionStore evicts oldest sessions at capacity."""
        from session import SessionStore
        store = SessionStore()
        store.MAX_SESSIONS = 3
        store.get_or_create("a")
        store.get_or_create("b")
        store.get_or_create("c")
        store.get_or_create("d")  # Should evict "a"
        assert store.get("a") is None
        assert store.get("d") is not None


# =========================================================================
# Feature 3: File Change Watcher
# =========================================================================

class TestFileWatcher:
    """Test FileWatcher change detection."""

    def test_detect_deleted_file(self, tmp_path):
        """Detect files that exist in index but not on disk."""
        from watcher import FileWatcher
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        index = {
            "symbols": {
                "proj:missing.py:function:foo": {
                    "path": "missing.py",
                    "name": "foo",
                    "type": "function",
                    "start_line": 1,
                },
            },
            "project_roots": {"proj": str(project_dir)},
        }
        watcher = FileWatcher(index)
        # Manually set index mtime to past
        watcher._get_index_mtime = lambda: 0.0  # Override to skip file check

        # The file doesn't exist → should detect as deleted
        # But _get_index_mtime returns 0 so detect_changes will return empty
        # Let's set it to a real value
        import time
        watcher._get_index_mtime = lambda: time.time() - 3600  # 1 hour ago

        changes = watcher.detect_changes()
        assert len(changes) == 1
        assert changes[0].change_type == "deleted"
        assert changes[0].path == "missing.py"

    def test_detect_modified_file(self, tmp_path):
        """Detect files modified after index."""
        from watcher import FileWatcher
        import time

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "modified.py").write_text("x = 1")

        index = {
            "symbols": {
                "proj:modified.py:function:foo": {
                    "path": "modified.py",
                    "name": "foo",
                    "type": "function",
                    "start_line": 1,
                },
            },
            "project_roots": {"proj": str(project_dir)},
        }
        watcher = FileWatcher(index)
        # Set index mtime to 1 hour ago (file was just created, so it's "newer")
        watcher._get_index_mtime = lambda: time.time() - 3600

        changes = watcher.detect_changes()
        assert len(changes) == 1
        assert changes[0].change_type == "modified"

    def test_summary(self):
        """get_summary counts correctly."""
        from watcher import FileWatcher, FileChange
        changes = [
            FileChange(path="a.py", project="proj", change_type="modified"),
            FileChange(path="b.py", project="proj", change_type="deleted"),
            FileChange(path="c.py", project="other", change_type="modified"),
        ]
        watcher = FileWatcher({})
        summary = watcher.get_summary(changes)
        assert summary["total"] == 3
        assert summary["by_type"]["modified"] == 2
        assert summary["by_type"]["deleted"] == 1
        assert summary["by_project"]["proj"] == 2


# =========================================================================
# Feature 1: Context Package (get_file_context)
# =========================================================================

class TestGetFileContext:
    """Test get_file_context aggregation."""

    def test_basic_context(self):
        """get_file_context returns expected structure."""
        from mcp_server import get_file_context, _index_cache, load_index
        import mcp_server

        # Create a mock index
        mock_index = {
            "symbols": {
                "proj:src/auth.py:function:login": {
                    "path": "src/auth.py",
                    "name": "login",
                    "type": "function",
                    "start_line": 10,
                    "summary": "Login function",
                },
                "proj:src/views.py:function:login_view": {
                    "path": "src/views.py",
                    "name": "login_view",
                    "type": "function",
                    "start_line": 1,
                    "summary": "Login view",
                },
            },
            "dependencies": {},
            "reverse_index": {
                "proj:src/auth.py:function:login": ["proj:src/views.py:function:login_view"],
            },
            "projects": ["proj"],
            "project_roots": {},
        }

        old_cache = mcp_server._index_cache
        old_mapper = mcp_server._test_mapper
        mcp_server._index_cache = mock_index
        mcp_server._test_mapper = None  # Force rebuild with mock index
        try:
            result = get_file_context("src/auth.py")
            assert result["path"] == "src/auth.py"
            assert len(result["symbols"]) == 1
            assert result["symbols"][0]["name"] == "login"
            assert len(result["dependents"]) == 1
            assert result["dependents"][0]["from_path"] == "src/views.py"
            assert "summary" in result
            assert result["summary"]["total_symbols"] == 1
        finally:
            mcp_server._index_cache = old_cache
            mcp_server._test_mapper = old_mapper


# =========================================================================
# Feature 4: Edit Impact Preview
# =========================================================================

class TestEditImpactPreview:
    """Test edit_impact_preview."""

    def test_rename_impact(self):
        """edit_impact_preview shows call sites for rename."""
        import mcp_server

        mock_index = {
            "symbols": {
                "proj:src/utils.py:function:helper": {
                    "path": "src/utils.py",
                    "name": "helper",
                    "type": "function",
                    "start_line": 1,
                },
                "proj:src/main.py:function:main": {
                    "path": "src/main.py",
                    "name": "main",
                    "type": "function",
                    "start_line": 1,
                },
            },
            "dependencies": {},
            "reverse_index": {
                "proj:src/utils.py:function:helper": ["proj:src/main.py:function:main"],
            },
            "projects": ["proj"],
            "project_roots": {},
        }

        old_cache = mcp_server._index_cache
        mcp_server._index_cache = mock_index
        try:
            result = mcp_server.edit_impact_preview("helper", change_type="rename")
            assert result["change_type"] == "rename"
            assert result["total_call_sites"] >= 1
            assert result["risk"] in ("moderate", "high")
            assert "suggestions" in result
        finally:
            mcp_server._index_cache = old_cache

    def test_no_callers(self):
        """Symbol with no callers is safe to change."""
        import mcp_server

        mock_index = {
            "symbols": {
                "proj:src/isolated.py:function:lonely": {
                    "path": "src/isolated.py",
                    "name": "lonely",
                    "type": "function",
                    "start_line": 1,
                },
            },
            "dependencies": {},
            "reverse_index": {},
            "projects": ["proj"],
            "project_roots": {},
        }

        old_cache = mcp_server._index_cache
        mcp_server._index_cache = mock_index
        try:
            result = mcp_server.edit_impact_preview("lonely", change_type="delete")
            assert result["total_call_sites"] == 0
            assert result["risk"] == "safe"
        finally:
            mcp_server._index_cache = old_cache


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
