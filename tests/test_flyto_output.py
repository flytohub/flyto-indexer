"""Tests for flyto_output module."""

import os
import sys
import json
import tempfile
from pathlib import Path

import pytest

# Add project root so 'src' is importable as a package (supports relative imports)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models import Symbol, Dependency, FileManifest, ProjectIndex, SymbolType, DependencyType
from src.flyto_output import (
    _categorize_file, _build_symbols, _build_one_liner,
    _count_languages, _count_folders,
    CATEGORY_LABELS, TOP_LEVEL_KINDS, GENERATOR_VERSION, SCHEMA_VERSION,
)


def _make_symbol(path, sym_type, name, start=1, end=20, ref_count=0):
    s = Symbol(
        project="proj", path=path,
        symbol_type=sym_type, name=name,
        start_line=start, end_line=end,
        language="python", reference_count=ref_count,
    )
    s.compute_hash()
    return s


class TestCategorizeFile:
    """Test _categorize_file path-based categorization."""

    def test_api_path(self):
        assert _categorize_file("src/api/auth.py") == "api"

    def test_composable_path(self):
        assert _categorize_file("src/composables/useAuth.ts") == "composable"

    def test_component_path(self):
        assert _categorize_file("src/components/Button.vue") == "component"

    def test_service_path(self):
        assert _categorize_file("src/services/payment.py") == "service"

    def test_store_path(self):
        assert _categorize_file("src/stores/userStore.ts") == "store"

    def test_view_path(self):
        assert _categorize_file("src/views/Login.vue") == "view"

    def test_util_path(self):
        assert _categorize_file("src/utils/format.ts") == "util"

    def test_test_path(self):
        assert _categorize_file("tests/test_auth.py") == "test"

    def test_model_path(self):
        assert _categorize_file("src/models/user.py") == "model"

    def test_default_module(self):
        assert _categorize_file("src/engine.py") == "module"


class TestBuildSymbols:
    """Test _build_symbols compact symbol list builder."""

    def test_builds_symbol_entries(self):
        symbols = [
            _make_symbol("a.py", SymbolType.FUNCTION, "foo", start=1, end=20),
            _make_symbol("a.py", SymbolType.CLASS, "Bar", start=25, end=50),
        ]
        result = _build_symbols(symbols)
        assert len(result) == 2
        assert result[0]["name"] == "foo"
        assert result[0]["kind"] == "function"
        assert result[0]["line"] == 1

    def test_limits_to_max_symbols(self):
        symbols = [
            _make_symbol("a.py", SymbolType.FUNCTION, f"fn_{i}", start=i*10, end=i*10+9)
            for i in range(20)
        ]
        result = _build_symbols(symbols)
        assert len(result) <= 10

    def test_includes_refs_when_nonzero(self):
        symbols = [
            _make_symbol("a.py", SymbolType.FUNCTION, "popular", ref_count=5),
        ]
        result = _build_symbols(symbols)
        assert result[0]["refs"] == 5


class TestBuildOneLiner:
    """Test _build_one_liner mechanical description builder."""

    def test_basic_one_liner(self):
        info = {
            "category": "api",
            "refs": 3,
            "symbols": [
                {"name": "login", "kind": "function"},
                {"name": "logout", "kind": "function"},
            ],
        }
        result = _build_one_liner("src/api/auth.py", info, set())
        assert "API Endpoints" in result
        assert "login" in result
        assert "3 refs" in result

    def test_hotspot_marker(self):
        info = {
            "category": "service",
            "refs": 10,
            "symbols": [{"name": "process", "kind": "function"}],
        }
        hotspots = {"src/core/engine.py"}
        result = _build_one_liner("src/core/engine.py", info, hotspots)
        assert "[hotspot]" in result

    def test_no_refs(self):
        info = {
            "category": "util",
            "refs": 0,
            "symbols": [{"name": "format", "kind": "function"}],
        }
        result = _build_one_liner("src/utils/format.py", info, set())
        assert "refs" not in result


class TestCountLanguages:
    """Test language counting."""

    def test_counts_unique_file_languages(self):
        idx = ProjectIndex(project="p", root_path="/tmp")
        s1 = Symbol(project="p", path="a.py", symbol_type=SymbolType.FUNCTION, name="f", language="python")
        s2 = Symbol(project="p", path="b.ts", symbol_type=SymbolType.FUNCTION, name="g", language="typescript")
        s3 = Symbol(project="p", path="a.py", symbol_type=SymbolType.CLASS, name="C", language="python")
        idx.symbols = {s1.id: s1, s2.id: s2, s3.id: s3}
        langs = _count_languages(idx)
        assert langs["python"] == 1  # a.py counted once
        assert langs["typescript"] == 1


class TestCountFolders:
    """Test folder counting."""

    def test_counts_nested_folders(self):
        idx = ProjectIndex(project="p", root_path="/tmp")
        idx.files = {
            "src/api/auth.py": FileManifest(path="src/api/auth.py", content_hash="a", line_count=10),
            "src/api/user.py": FileManifest(path="src/api/user.py", content_hash="b", line_count=10),
            "src/models/base.py": FileManifest(path="src/models/base.py", content_hash="c", line_count=10),
        }
        count = _count_folders(idx)
        # src, src/api, src/models = 3 unique folders
        assert count >= 3


class TestConstants:
    """Test module constants."""

    def test_schema_version(self):
        assert SCHEMA_VERSION == 1

    def test_generator_version(self):
        assert isinstance(GENERATOR_VERSION, str)
        assert len(GENERATOR_VERSION) > 0

    def test_top_level_kinds_include_expected(self):
        assert SymbolType.CLASS in TOP_LEVEL_KINDS
        assert SymbolType.FUNCTION in TOP_LEVEL_KINDS
        assert SymbolType.COMPONENT in TOP_LEVEL_KINDS

    def test_category_labels_are_strings(self):
        for key, label in CATEGORY_LABELS.items():
            assert isinstance(key, str)
            assert isinstance(label, str)
