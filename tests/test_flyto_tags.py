"""Tests for flyto_tags module."""

import os
import sys
import json
import tempfile
from pathlib import Path

import pytest

# Add project root so 'src' is importable as a package (supports relative imports)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models import Symbol, Dependency, FileManifest, ProjectIndex, SymbolType, DependencyType
from src.flyto_tags import (
    generate_tags, compute_tag_stats, write_tags,
    _is_test_path, _extract_test_stem, _find_dead_code,
    SHOULD_BE_REFERENCED, ENTRY_POINT_PATTERNS, LIFECYCLE_METHODS,
)


def _make_symbol(project, path, sym_type, name, start=1, end=20, content="x", exports=None, ref_count=0):
    """Helper to create a Symbol."""
    s = Symbol(
        project=project, path=path,
        symbol_type=sym_type, name=name,
        start_line=start, end_line=end,
        content=content, language="python",
        exports=exports or [],
        reference_count=ref_count,
    )
    s.compute_hash()
    return s


class TestIsTestPath:
    """Test _is_test_path helper."""

    def test_test_directory(self):
        assert _is_test_path("tests/test_foo.py") is True
        assert _is_test_path("src/__tests__/foo.spec.ts") is True

    def test_test_filename(self):
        assert _is_test_path("src/test_utils.py") is True
        assert _is_test_path("src/utils.test.ts") is True
        assert _is_test_path("src/utils.spec.js") is True

    def test_non_test_path(self):
        assert _is_test_path("src/utils.py") is False
        assert _is_test_path("src/services/auth.ts") is False


class TestExtractTestStem:
    """Test _extract_test_stem helper."""

    def test_python_test(self):
        assert _extract_test_stem("test_auth.py") == "auth"

    def test_js_test(self):
        assert _extract_test_stem("auth.test.js") == "auth"

    def test_spec(self):
        assert _extract_test_stem("auth.spec.ts") == "auth"

    def test_go_test(self):
        assert _extract_test_stem("auth_test.go") == "auth"

    def test_non_test(self):
        assert _extract_test_stem("auth.py") is None


class TestFindDeadCode:
    """Test dead code detection."""

    def test_unreferenced_function_is_dead(self):
        idx = ProjectIndex(project="proj", root_path="/tmp")
        sym = _make_symbol("proj", "src/foo.py", SymbolType.FUNCTION, "orphan_func")
        idx.symbols[sym.id] = sym
        dead = _find_dead_code(idx)
        assert sym.id in dead

    def test_referenced_function_is_not_dead(self):
        idx = ProjectIndex(project="proj", root_path="/tmp")
        sym = _make_symbol("proj", "src/foo.py", SymbolType.FUNCTION, "used_func")
        idx.symbols[sym.id] = sym
        # Add a call dependency referencing this function
        dep = Dependency(
            source_id="proj:src/bar.py:file:bar",
            target_id="used_func",
            dep_type=DependencyType.CALLS,
        )
        idx.dependencies[dep.id] = dep
        dead = _find_dead_code(idx)
        assert sym.id not in dead

    def test_entry_point_not_dead(self):
        idx = ProjectIndex(project="proj", root_path="/tmp")
        sym = _make_symbol("proj", "src/app.py", SymbolType.FUNCTION, "main")
        idx.symbols[sym.id] = sym
        dead = _find_dead_code(idx)
        assert sym.id not in dead

    def test_exported_symbol_not_dead(self):
        idx = ProjectIndex(project="proj", root_path="/tmp")
        sym = _make_symbol("proj", "src/foo.py", SymbolType.FUNCTION, "public_fn", exports=["public_fn"])
        idx.symbols[sym.id] = sym
        dead = _find_dead_code(idx)
        assert sym.id not in dead

    def test_small_function_not_dead(self):
        # Functions < MIN_LINES are skipped
        idx = ProjectIndex(project="proj", root_path="/tmp")
        sym = _make_symbol("proj", "src/foo.py", SymbolType.FUNCTION, "tiny", start=1, end=3)
        idx.symbols[sym.id] = sym
        dead = _find_dead_code(idx)
        assert sym.id not in dead


class TestGenerateTags:
    """Test generate_tags function."""

    def test_generates_dead_code_tags(self):
        idx = ProjectIndex(project="proj", root_path="/tmp")
        sym = _make_symbol("proj", "src/foo.py", SymbolType.FUNCTION, "dead_fn")
        idx.symbols[sym.id] = sym
        tags = generate_tags(idx)
        dead_tags = [t for t in tags if t["kind"] == "dead_code"]
        assert len(dead_tags) >= 1
        assert dead_tags[0]["symbolId"] == sym.id

    def test_tag_schema_version(self):
        idx = ProjectIndex(project="proj", root_path="/tmp")
        sym = _make_symbol("proj", "src/foo.py", SymbolType.FUNCTION, "dead_fn")
        idx.symbols[sym.id] = sym
        tags = generate_tags(idx)
        for tag in tags:
            assert tag["schemaVersion"] == 1


class TestComputeTagStats:
    """Test tag statistics computation."""

    def test_stats_keys(self):
        idx = ProjectIndex(project="proj", root_path="/tmp")
        tags = []
        stats = compute_tag_stats(tags, idx)
        assert "dead_code" in stats
        assert "tdd_covered" in stats
        assert "tdd_testable" in stats


class TestWriteTags:
    """Test writing tags to JSONL."""

    def test_write_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tags_dir = Path(tmpdir)
            tags = [{"kind": "dead_code", "symbolId": "test:a:function:f"}]
            write_tags(tags, tags_dir)
            path = tags_dir / "symbol_tags.jsonl"
            assert path.exists()
            content = path.read_text()
            parsed = json.loads(content.strip())
            assert parsed["kind"] == "dead_code"

    def test_write_empty_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tags_dir = Path(tmpdir)
            write_tags([], tags_dir)
            path = tags_dir / "symbol_tags.jsonl"
            assert path.exists()
            assert path.read_text() == ""
