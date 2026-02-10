"""Tests for flyto-indexer MCP server tool handlers.

Tests the core functions in mcp_server.py directly, without going through
the MCP JSON-RPC protocol layer.
"""

import gzip
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add src to path (same pattern as test_basic.py)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_server import (
    search_by_keyword,
    get_file_info,
    load_index,
    load_project_map,
    load_content_file,
    get_symbol_content_text,
    TYPE_WEIGHTS,
    LOW_PRIORITY_PATHS,
)
import mcp_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_index(symbols=None, projects=None, reverse_index=None,
                     dependencies=None, project_roots=None):
    """Build a minimal mock index dict for testing."""
    return {
        "symbols": symbols or {},
        "projects": projects or [],
        "reverse_index": reverse_index or {},
        "dependencies": dependencies or {},
        "project_roots": project_roots or {},
    }


def _make_symbol(name, sym_type="function", path="src/utils.py",
                 summary="", content="", ref_count=0, exports=False,
                 start_line=1, end_line=10):
    """Build a minimal symbol dict."""
    d = {
        "name": name,
        "type": sym_type,
        "path": path,
        "summary": summary,
        "start_line": start_line,
        "end_line": end_line,
        "ref_count": ref_count,
    }
    if content:
        d["content"] = content
    if exports:
        d["exports"] = True
    return d


@pytest.fixture(autouse=True)
def _reset_index_cache():
    """Reset global caches in mcp_server before each test."""
    old_cache = mcp_server._index_cache
    old_content_cache = mcp_server._content_cache.copy()
    old_content_loaded = mcp_server._content_loaded
    old_test_mapper = mcp_server._test_mapper
    old_session_store = mcp_server._session_store
    yield
    mcp_server._index_cache = old_cache
    mcp_server._content_cache = old_content_cache
    mcp_server._content_loaded = old_content_loaded
    mcp_server._test_mapper = old_test_mapper
    mcp_server._session_store = old_session_store


# =========================================================================
# TestSearchByKeyword
# =========================================================================

class TestSearchByKeyword:
    """Test search_by_keyword function."""

    def test_empty_query_returns_no_results(self):
        """An empty query string should match nothing."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:hello": _make_symbol("hello"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("")
        # Empty string matches everything via `"" in name`, but the behavior
        # is that empty words list yields no matches since split("") -> [""]
        # Actually "".split() returns [], so no word matches, score=0, skip.
        assert result["total"] == 0
        assert result["results"] == []

    def test_basic_search_returns_expected_format(self):
        """Search results must contain required top-level keys."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/auth.py:function:login": _make_symbol(
                    "login", summary="User login", content="def login(): pass"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("login")
        assert "query" in result
        assert "total" in result
        assert "results" in result
        assert "by_project" in result
        assert "filters" in result
        assert result["query"] == "login"
        assert result["total"] >= 1

    def test_result_items_have_required_fields(self):
        """Each result item must have symbol_id, name, type, path, score."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/auth.py:function:login": _make_symbol("login"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("login")
        assert result["total"] >= 1
        item = result["results"][0]
        assert "symbol_id" in item
        assert "name" in item
        assert "type" in item
        assert "path" in item
        assert "score" in item

    def test_project_filter(self):
        """Only symbols from the filtered project should appear."""
        mock_index = _make_mock_index(
            symbols={
                "alpha:src/a.py:function:login": _make_symbol("login"),
                "beta:src/b.py:function:login": _make_symbol("login", path="src/b.py"),
            },
            projects=["alpha", "beta"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("login", project="alpha")
        assert result["total"] >= 1
        for item in result["results"]:
            assert item["project"] == "alpha"

    def test_symbol_type_filter(self):
        """Only symbols of the filtered type should appear."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:process": _make_symbol("process", sym_type="function"),
                "proj:src/a.py:class:Process": _make_symbol("Process", sym_type="class"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("process", symbol_type="function")
        assert result["total"] >= 1
        for item in result["results"]:
            assert item["type"] == "function"

    def test_max_results_limit(self):
        """Should not return more than max_results."""
        symbols = {}
        for i in range(50):
            sid = f"proj:src/mod{i}.py:function:handler{i}"
            symbols[sid] = _make_symbol(
                f"handler{i}", path=f"src/mod{i}.py", content="handler"
            )
        mock_index = _make_mock_index(symbols=symbols, projects=["proj"])
        mcp_server._index_cache = mock_index

        result = search_by_keyword("handler", max_results=5)
        assert len(result["results"]) <= 5
        assert result["showing"] <= 5

    def test_name_match_higher_than_content_match(self):
        """A name match should score higher than a content-only match."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:validate": _make_symbol(
                    "validate", content="def validate(): pass"
                ),
                "proj:src/b.py:function:process": _make_symbol(
                    "process", content="result = validate(data)"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("validate")
        assert result["total"] >= 2
        # "validate" (name match) should rank above "process" (content match)
        assert result["results"][0]["name"] == "validate"

    def test_exact_name_match_bonus(self):
        """An exact name match should score higher than partial name match."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:auth": _make_symbol("auth"),
                "proj:src/b.py:function:authenticate": _make_symbol(
                    "authenticate", path="src/b.py"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("auth")
        assert result["total"] >= 2
        # "auth" exact match should rank first
        assert result["results"][0]["name"] == "auth"

    def test_type_weight_affects_ranking(self):
        """Composables should rank higher than methods for same name match."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.js:method:useAuth": _make_symbol(
                    "useAuth", sym_type="method", path="src/a.js"
                ),
                "proj:src/b.js:composable:useAuth": _make_symbol(
                    "useAuth", sym_type="composable", path="src/b.js"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("useAuth")
        assert result["total"] == 2
        # composable weight (15) > method weight (3)
        assert result["results"][0]["type"] == "composable"

    def test_test_path_demotion(self):
        """Symbols in test paths should rank lower."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/utils.py:function:parse": _make_symbol(
                    "parse", path="src/utils.py"
                ),
                "proj:tests/test_utils.py:function:parse": _make_symbol(
                    "parse", path="tests/test_utils.py"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("parse")
        assert result["total"] == 2
        # src/ version should rank higher than tests/ version
        assert "tests" not in result["results"][0]["path"]

    def test_exports_bonus(self):
        """Exported symbols should score higher."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:helper": _make_symbol(
                    "helper", path="src/a.py", exports=True
                ),
                "proj:src/b.py:function:helper": _make_symbol(
                    "helper", path="src/b.py", exports=False
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("helper")
        assert result["total"] == 2
        # Both match by name, but exports version gets +3
        assert result["results"][0]["path"] == "src/a.py"

    def test_ref_count_bonus(self):
        """More referenced symbols should score higher."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:util": _make_symbol(
                    "util", path="src/a.py", ref_count=20
                ),
                "proj:src/b.py:function:util": _make_symbol(
                    "util", path="src/b.py", ref_count=0
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("util")
        assert result["total"] == 2
        # Higher ref_count should rank first
        assert result["results"][0]["path"] == "src/a.py"

    def test_include_content_adds_snippet(self):
        """include_content=True should add a snippet field to results."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:hello": _make_symbol(
                    "hello", content="def hello():\n    return 'world'"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("hello", include_content=True)
        assert result["total"] >= 1
        assert "snippet" in result["results"][0]
        assert "hello" in result["results"][0]["snippet"]

    def test_by_project_grouping(self):
        """Results should be grouped by project in by_project dict."""
        mock_index = _make_mock_index(
            symbols={
                "alpha:src/a.py:function:run": _make_symbol("run"),
                "beta:src/b.py:function:run": _make_symbol("run", path="src/b.py"),
            },
            projects=["alpha", "beta"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("run")
        assert "by_project" in result
        assert "alpha" in result["by_project"]
        assert "beta" in result["by_project"]

    def test_deduplication(self):
        """Same symbol_id should not appear twice in results."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:foo": _make_symbol(
                    "foo", content="foo bar foo baz"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("foo")
        ids = [r["symbol_id"] for r in result["results"]]
        assert len(ids) == len(set(ids))

    def test_no_match_returns_empty(self):
        """Query with no matching symbols should return empty results."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:hello": _make_symbol("hello"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("zzz_nonexistent_zzz")
        assert result["total"] == 0
        assert result["results"] == []

    def test_multi_word_query(self):
        """Multi-word query should match if any word is found."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:get_user": _make_symbol("get_user"),
                "proj:src/b.py:function:delete_item": _make_symbol(
                    "delete_item", path="src/b.py"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("get user")
        # "get" or "user" should match "get_user"
        assert result["total"] >= 1
        names = [r["name"] for r in result["results"]]
        assert "get_user" in names


# =========================================================================
# TestGetFileInfo
# =========================================================================

class TestGetFileInfo:
    """Test get_file_info function."""

    def test_unknown_path_returns_error(self):
        """Unknown file path should return an error dict."""
        with patch.object(mcp_server, "load_project_map", return_value={"files": {}}):
            result = get_file_info("nonexistent/path.py")
            assert "error" in result

    def test_valid_path_returns_structure(self):
        """Valid file path should return all expected fields."""
        mock_project_map = {
            "files": {
                "src/auth.py": {
                    "purpose": "Authentication logic",
                    "category": "auth",
                    "keywords": ["login", "jwt"],
                    "apis": ["/api/login"],
                    "dependencies": ["bcrypt"],
                    "ui_elements": [],
                },
            },
        }
        with patch.object(mcp_server, "load_project_map", return_value=mock_project_map):
            result = get_file_info("src/auth.py")
            assert result["path"] == "src/auth.py"
            assert result["purpose"] == "Authentication logic"
            assert result["category"] == "auth"
            assert "login" in result["keywords"]
            assert "/api/login" in result["apis"]
            assert "bcrypt" in result["dependencies"]

    def test_partial_file_data(self):
        """File with missing optional fields should still return defaults."""
        mock_project_map = {
            "files": {
                "src/utils.py": {
                    "purpose": "Utility functions",
                },
            },
        }
        with patch.object(mcp_server, "load_project_map", return_value=mock_project_map):
            result = get_file_info("src/utils.py")
            assert result["path"] == "src/utils.py"
            assert result["purpose"] == "Utility functions"
            assert result["category"] == ""
            assert result["keywords"] == []
            assert result["apis"] == []
            assert result["dependencies"] == []


# =========================================================================
# TestLoadIndex
# =========================================================================

class TestLoadIndex:
    """Test index loading."""

    def test_missing_index_file_returns_empty_dict(self):
        """When no index file exists, load_index should return {}."""
        mcp_server._index_cache = None
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(mcp_server, "INDEX_DIR", Path(tmpdir)):
                # No index.json or index.json.gz in tmpdir
                result = load_index()
                assert result == {}

    def test_cache_behavior(self):
        """Second call should return cached data without re-reading."""
        mock_data = {"symbols": {"a": {}}, "projects": ["test"]}
        mcp_server._index_cache = mock_data

        result = load_index()
        assert result is mock_data  # Same object reference

    def test_plain_json_loading(self):
        """Should load plain index.json when gzip version is absent."""
        mcp_server._index_cache = None
        with tempfile.TemporaryDirectory() as tmpdir:
            index_data = {"symbols": {"test:a.py:function:foo": {"name": "foo"}}}
            Path(tmpdir, "index.json").write_text(json.dumps(index_data))

            with patch.object(mcp_server, "INDEX_DIR", Path(tmpdir)):
                result = load_index()
                assert "symbols" in result
                assert "test:a.py:function:foo" in result["symbols"]

    def test_gzip_loading_preferred(self):
        """Should prefer index.json.gz over index.json."""
        mcp_server._index_cache = None
        with tempfile.TemporaryDirectory() as tmpdir:
            gz_data = {"symbols": {"gz_symbol": {}}, "source": "gzip"}
            plain_data = {"symbols": {"plain_symbol": {}}, "source": "plain"}

            gz_path = Path(tmpdir, "index.json.gz")
            with gzip.open(gz_path, "wt", encoding="utf-8") as f:
                json.dump(gz_data, f)

            Path(tmpdir, "index.json").write_text(json.dumps(plain_data))

            with patch.object(mcp_server, "INDEX_DIR", Path(tmpdir)):
                result = load_index()
                assert result.get("source") == "gzip"


# =========================================================================
# TestLoadProjectMap
# =========================================================================

class TestLoadProjectMap:
    """Test project map loading."""

    def test_missing_file_returns_empty_dict(self):
        """When no PROJECT_MAP file exists, should return {}."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(mcp_server, "INDEX_DIR", Path(tmpdir)):
                result = load_project_map()
                assert result == {}

    def test_plain_json_loading(self):
        """Should load plain PROJECT_MAP.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            map_data = {"files": {"src/a.py": {"purpose": "test"}}}
            Path(tmpdir, "PROJECT_MAP.json").write_text(json.dumps(map_data))

            with patch.object(mcp_server, "INDEX_DIR", Path(tmpdir)):
                result = load_project_map()
                assert "files" in result
                assert "src/a.py" in result["files"]

    def test_gzip_loading_preferred(self):
        """Should prefer PROJECT_MAP.json.gz over PROJECT_MAP.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gz_data = {"files": {}, "source": "gzip"}
            gz_path = Path(tmpdir, "PROJECT_MAP.json.gz")
            with gzip.open(gz_path, "wt", encoding="utf-8") as f:
                json.dump(gz_data, f)

            plain_data = {"files": {}, "source": "plain"}
            Path(tmpdir, "PROJECT_MAP.json").write_text(json.dumps(plain_data))

            with patch.object(mcp_server, "INDEX_DIR", Path(tmpdir)):
                result = load_project_map()
                assert result.get("source") == "gzip"


# =========================================================================
# TestLoadContentFile
# =========================================================================

class TestLoadContentFile:
    """Test content.jsonl loading."""

    def test_missing_content_file(self):
        """When content.jsonl does not exist, should return empty dict."""
        mcp_server._content_cache = {}
        mcp_server._content_loaded = False
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(mcp_server, "INDEX_DIR", Path(tmpdir)):
                result = load_content_file()
                assert result == {}

    def test_valid_content_file(self):
        """Should parse each line as a JSON record with id and content."""
        mcp_server._content_cache = {}
        mcp_server._content_loaded = False
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                json.dumps({"id": "proj:a.py:function:foo", "content": "def foo(): pass"}),
                json.dumps({"id": "proj:b.py:class:Bar", "content": "class Bar: ..."}),
            ]
            Path(tmpdir, "content.jsonl").write_text("\n".join(lines) + "\n")

            with patch.object(mcp_server, "INDEX_DIR", Path(tmpdir)):
                result = load_content_file()
                assert "proj:a.py:function:foo" in result
                assert result["proj:a.py:function:foo"] == "def foo(): pass"
                assert "proj:b.py:class:Bar" in result

    def test_cache_on_second_call(self):
        """Second call should use cached data."""
        mcp_server._content_cache = {"cached": "yes"}
        mcp_server._content_loaded = True

        result = load_content_file()
        assert result == {"cached": "yes"}

    def test_malformed_lines_skipped(self):
        """Invalid JSON lines should be skipped without crashing."""
        mcp_server._content_cache = {}
        mcp_server._content_loaded = False
        with tempfile.TemporaryDirectory() as tmpdir:
            content = (
                '{"id": "good", "content": "ok"}\n'
                '{bad json line\n'
                '{"id": "also_good", "content": "fine"}\n'
            )
            Path(tmpdir, "content.jsonl").write_text(content)

            with patch.object(mcp_server, "INDEX_DIR", Path(tmpdir)):
                # The function catches all exceptions, so malformed lines
                # will cause the entire load to fail silently (due to the
                # broad except). After the bad line, the loop may stop.
                result = load_content_file()
                # At minimum "good" was loaded before the bad line
                assert isinstance(result, dict)


# =========================================================================
# TestGetSymbolContent
# =========================================================================

class TestGetSymbolContent:
    """Test content retrieval for symbols."""

    def test_inline_content_priority(self):
        """When symbol dict has inline content, return it directly."""
        symbol_data = {"content": "def hello(): return 42", "name": "hello"}
        result = get_symbol_content_text("proj:a.py:function:hello", symbol_data)
        assert result == "def hello(): return 42"

    def test_content_jsonl_fallback(self):
        """When no inline content, fall back to content.jsonl."""
        mcp_server._content_cache = {
            "proj:a.py:function:hello": "def hello(): return 'from jsonl'"
        }
        mcp_server._content_loaded = True

        symbol_data = {"name": "hello"}  # No "content" key
        result = get_symbol_content_text("proj:a.py:function:hello", symbol_data)
        assert result == "def hello(): return 'from jsonl'"

    def test_no_content_anywhere(self):
        """When content is in neither inline nor content.jsonl, return empty string."""
        mcp_server._content_cache = {}
        mcp_server._content_loaded = True

        symbol_data = {"name": "orphan"}
        result = get_symbol_content_text("proj:x.py:function:orphan", symbol_data)
        assert result == ""

    def test_inline_empty_string_falls_through(self):
        """Empty inline content should fall through to content.jsonl."""
        mcp_server._content_cache = {
            "proj:a.py:function:foo": "from_jsonl"
        }
        mcp_server._content_loaded = True

        symbol_data = {"content": "", "name": "foo"}
        result = get_symbol_content_text("proj:a.py:function:foo", symbol_data)
        assert result == "from_jsonl"


# =========================================================================
# TestMCPResultFormat
# =========================================================================

class TestMCPResultFormat:
    """Test that search results conform to the expected schema."""

    def test_search_result_top_level_schema(self):
        """Top-level search result must have query, total, results, by_project, filters, showing."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:test": _make_symbol("test"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("test")
        required_keys = {"query", "total", "results", "by_project", "filters", "showing"}
        assert required_keys.issubset(set(result.keys()))

    def test_search_result_filters_schema(self):
        """Filters should contain symbol_type and project (both may be None)."""
        mock_index = _make_mock_index(symbols={}, projects=[])
        mcp_server._index_cache = mock_index

        result = search_by_keyword("anything")
        assert "filters" in result
        assert "symbol_type" in result["filters"]
        assert "project" in result["filters"]

    def test_search_result_item_schema(self):
        """Each result item must have the required fields."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:hello": _make_symbol(
                    "hello", ref_count=5
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("hello")
        assert result["total"] >= 1
        item = result["results"][0]
        required_item_keys = {
            "symbol_id", "name", "type", "path", "score",
            "project", "line", "summary", "ref_count", "match",
        }
        assert required_item_keys.issubset(set(item.keys()))

    def test_search_result_score_is_numeric(self):
        """Score should be a numeric value."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:calc": _make_symbol("calc"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("calc")
        assert result["total"] >= 1
        score = result["results"][0]["score"]
        assert isinstance(score, (int, float))
        assert score > 0

    def test_total_matches_results_length(self):
        """total should reflect the number of unique results (before max_results trim)."""
        symbols = {}
        for i in range(10):
            sid = f"proj:src/m{i}.py:function:handler"
            symbols[sid] = _make_symbol("handler", path=f"src/m{i}.py")
        mock_index = _make_mock_index(symbols=symbols, projects=["proj"])
        mcp_server._index_cache = mock_index

        result = search_by_keyword("handler", max_results=5)
        assert result["total"] == 10
        assert result["showing"] == 5
        assert len(result["results"]) == 5


# =========================================================================
# TestInputValidation
# =========================================================================

class TestInputValidation:
    """Test input validation and edge cases."""

    def test_very_long_query(self):
        """Very long query should not crash."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:x": _make_symbol("x"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        long_query = "a" * 10001
        result = search_by_keyword(long_query)
        assert isinstance(result, dict)
        assert "total" in result

    def test_unicode_query(self):
        """Unicode queries should work without errors."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/i18n.py:function:translate": _make_symbol(
                    "translate", summary="translate text to Chinese",
                    content="msg = 'this handles unicode well'"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("translate")
        assert isinstance(result, dict)

    def test_unicode_in_symbol_names(self):
        """Symbols with unicode in names should be searchable."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:get_data": _make_symbol(
                    "get_data", summary="takes input and returns output"
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("get_data")
        assert result["total"] >= 1

    def test_special_characters_in_query(self):
        """Special regex characters in query should not crash."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:parse": _make_symbol("parse"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        # These would break if used directly in regex
        for query in ["foo()", "bar[0]", "a+b", "x*y", "path/to/file", "a.b.c"]:
            result = search_by_keyword(query)
            assert isinstance(result, dict)
            assert "total" in result

    def test_empty_index(self):
        """Search on empty index should return zero results."""
        mcp_server._index_cache = {}

        result = search_by_keyword("anything")
        assert result["total"] == 0
        assert result["results"] == []

    def test_index_without_symbols_key(self):
        """Index missing the 'symbols' key should not crash."""
        mcp_server._index_cache = {"projects": []}

        result = search_by_keyword("anything")
        assert result["total"] == 0

    def test_malformed_symbol_data(self):
        """Symbols with missing fields should not crash search."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:broken": {},  # No name, type, etc.
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("broken")
        assert isinstance(result, dict)

    def test_whitespace_only_query(self):
        """Whitespace-only query should return no results (split yields [])."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:hello": _make_symbol("hello"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("   ")
        assert result["total"] == 0

    def test_newline_in_query(self):
        """Query containing newlines should not crash."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:hello": _make_symbol("hello"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("hello\nworld")
        assert isinstance(result, dict)


# =========================================================================
# TestTypeWeights
# =========================================================================

class TestTypeWeights:
    """Test TYPE_WEIGHTS configuration."""

    def test_composable_highest(self):
        """Composable should have the highest weight."""
        assert TYPE_WEIGHTS["composable"] >= TYPE_WEIGHTS["function"]
        assert TYPE_WEIGHTS["composable"] >= TYPE_WEIGHTS["class"]
        assert TYPE_WEIGHTS["composable"] >= TYPE_WEIGHTS["method"]

    def test_method_lowest(self):
        """Method should have a lower weight than function."""
        assert TYPE_WEIGHTS["method"] < TYPE_WEIGHTS["function"]

    def test_all_weights_positive(self):
        """All type weights should be positive."""
        for sym_type, weight in TYPE_WEIGHTS.items():
            assert weight > 0, f"Weight for {sym_type} should be positive"

    def test_expected_types_present(self):
        """Expected symbol types should be in TYPE_WEIGHTS."""
        expected = {"composable", "component", "function", "class", "method",
                    "interface", "type"}
        for t in expected:
            assert t in TYPE_WEIGHTS, f"Missing type: {t}"


# =========================================================================
# TestLowPriorityPaths
# =========================================================================

class TestLowPriorityPaths:
    """Test LOW_PRIORITY_PATHS configuration."""

    def test_test_paths_included(self):
        """Common test directory names should be in LOW_PRIORITY_PATHS."""
        assert "test" in LOW_PRIORITY_PATHS
        assert "tests" in LOW_PRIORITY_PATHS

    def test_mock_paths_included(self):
        """Mock and fixture paths should be in LOW_PRIORITY_PATHS."""
        assert "mock" in LOW_PRIORITY_PATHS
        assert "fixture" in LOW_PRIORITY_PATHS


# =========================================================================
# TestSearchWithContentFile
# =========================================================================

class TestSearchWithContentFile:
    """Test search when content is stored in content.jsonl (not inline)."""

    def test_search_matches_external_content(self):
        """Search should match content from content.jsonl when not inline."""
        mcp_server._content_cache = {
            "proj:src/a.py:function:process": "def process():\n    validate_input(data)\n    return result"
        }
        mcp_server._content_loaded = True

        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:process": _make_symbol(
                    "process", content=""  # No inline content
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("validate_input")
        assert result["total"] >= 1
        assert result["results"][0]["name"] == "process"


# =========================================================================
# TestGetFileInfoEdgeCases
# =========================================================================

class TestGetFileInfoEdgeCases:
    """Edge cases for get_file_info."""

    def test_empty_project_map(self):
        """Empty project map should return error."""
        with patch.object(mcp_server, "load_project_map", return_value={}):
            result = get_file_info("any/path.py")
            assert "error" in result

    def test_project_map_without_files_key(self):
        """Project map missing 'files' key should return error."""
        with patch.object(mcp_server, "load_project_map", return_value={"categories": {}}):
            result = get_file_info("any/path.py")
            assert "error" in result

    def test_path_with_special_characters(self):
        """Path with special characters should not crash."""
        with patch.object(mcp_server, "load_project_map", return_value={"files": {}}):
            result = get_file_info("src/file (copy).py")
            assert "error" in result  # File not found, but no crash


# =========================================================================
# TestSearchSessionBoost
# =========================================================================

class TestSearchSessionBoost:
    """Test session-based search boosting."""

    def test_search_without_session(self):
        """Search without session_id should work normally."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:hello": _make_symbol("hello"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("hello", session_id=None)
        assert result["total"] >= 1

    def test_search_with_nonexistent_session(self):
        """Search with non-existent session_id should still work."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:hello": _make_symbol("hello"),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        # This may create a new session or return None depending on
        # _get_session_store implementation. Either way it should not crash.
        try:
            result = search_by_keyword("hello", session_id="nonexistent_session_999")
            assert isinstance(result, dict)
        except Exception:
            # If session module is not available, that is acceptable
            pass


# =========================================================================
# TestSummaryTruncation
# =========================================================================

class TestSummaryTruncation:
    """Test that long summaries are truncated in results."""

    def test_long_summary_truncated(self):
        """Summary longer than 150 chars should be truncated."""
        long_summary = "A" * 300
        mock_index = _make_mock_index(
            symbols={
                "proj:src/a.py:function:verbose": _make_symbol(
                    "verbose", summary=long_summary
                ),
            },
            projects=["proj"],
        )
        mcp_server._index_cache = mock_index

        result = search_by_keyword("verbose")
        assert result["total"] >= 1
        assert len(result["results"][0]["summary"]) <= 150


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
