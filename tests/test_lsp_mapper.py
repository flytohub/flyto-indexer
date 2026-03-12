"""Tests for LSP mapper — bridging flyto symbols and LSP positions."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from lsp.protocol import (
    Position,
    Range,
    Location,
    uri_to_path,
    path_to_uri,
)
from lsp.mapper import (
    find_symbol_at_line,
    symbol_to_lsp_position,
    lsp_locations_to_references,
)


# ---------------------------------------------------------------------------
# uri_to_path / path_to_uri
# ---------------------------------------------------------------------------


class TestUriConversions:
    def test_path_to_uri_unix(self):
        uri = path_to_uri("/home/user/code/foo.py")
        assert uri.startswith("file://")
        assert "foo.py" in uri

    def test_uri_to_path_unix(self):
        path = uri_to_path("file:///home/user/code/foo.py")
        assert path == "/home/user/code/foo.py"

    def test_roundtrip(self):
        original = "/tmp/test/example.py"
        uri = path_to_uri(original)
        recovered = uri_to_path(uri)
        # Paths should resolve to the same file
        assert os.path.basename(recovered) == os.path.basename(original)

    def test_uri_to_path_no_scheme(self):
        assert uri_to_path("/tmp/foo.py") == "/tmp/foo.py"

    def test_uri_to_path_double_slash(self):
        result = uri_to_path("file:///tmp/foo.py")
        assert result == "/tmp/foo.py"


# ---------------------------------------------------------------------------
# find_symbol_at_line
# ---------------------------------------------------------------------------


class TestFindSymbolAtLine:
    def test_exact_line(self):
        content = "import os\n\ndef hello():\n    pass\n"
        result = find_symbol_at_line(content, "hello", 2)
        assert result is not None
        assert result[0] == 2  # line
        assert result[1] == 4  # column (after 'def ')

    def test_nearby_line(self):
        content = "import os\n\ndef hello():\n    pass\n"
        # Search from line 0, should find at line 2 (within +/-5 range)
        result = find_symbol_at_line(content, "hello", 0)
        assert result is not None
        assert result[0] == 2

    def test_not_found(self):
        content = "import os\n\ndef hello():\n    pass\n"
        result = find_symbol_at_line(content, "nonexistent", 0)
        assert result is None

    def test_empty_content(self):
        result = find_symbol_at_line("", "foo", 0)
        assert result is None

    def test_empty_name(self):
        result = find_symbol_at_line("def foo():\n    pass", "", 0)
        assert result is None

    def test_class_definition(self):
        content = "import typing\n\nclass MyClass:\n    def method(self):\n        pass\n"
        result = find_symbol_at_line(content, "MyClass", 2)
        assert result is not None
        assert result[0] == 2
        assert result[1] == 6  # after 'class '

    def test_method_in_class(self):
        content = "class Foo:\n    def bar(self):\n        pass\n"
        result = find_symbol_at_line(content, "bar", 1)
        assert result is not None
        assert result[0] == 1

    def test_word_boundary(self):
        content = "def foobar():\n    pass\ndef foo():\n    pass\n"
        result = find_symbol_at_line(content, "foo", 2)
        assert result is not None
        assert result[0] == 2  # Should match 'foo', not 'foobar'

    def test_variable_assignment(self):
        content = "x = 1\nmy_var = 42\ny = 3\n"
        result = find_symbol_at_line(content, "my_var", 1)
        assert result is not None
        assert result[0] == 1
        assert result[1] == 0

    def test_out_of_range_target_line(self):
        content = "def foo():\n    pass\n"
        # target_line far out of range, but should still find within +/-5
        result = find_symbol_at_line(content, "foo", 100)
        assert result is None  # 100 is too far from line 0

    def test_negative_target_line(self):
        content = "def foo():\n    pass\n"
        result = find_symbol_at_line(content, "foo", -1)
        assert result is not None
        assert result[0] == 0


# ---------------------------------------------------------------------------
# symbol_to_lsp_position
# ---------------------------------------------------------------------------


class TestSymbolToLspPosition:
    def test_with_real_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("import os\n\ndef hello():\n    pass\n")
            f.flush()
            tmp_path = f.name

        try:
            symbol = {
                "path": tmp_path,
                "name": "hello",
                "start_line": 3,  # 1-based
            }
            result = symbol_to_lsp_position(symbol, "/")
            assert result is not None
            uri, line, col = result
            assert "hello" in uri or tmp_path in uri_to_path(uri)
            assert line == 2  # 0-based
            assert col == 4  # after 'def '
        finally:
            os.unlink(tmp_path)

    def test_missing_file(self):
        symbol = {
            "path": "/nonexistent/path/foo.py",
            "name": "foo",
            "start_line": 1,
        }
        result = symbol_to_lsp_position(symbol, "/nonexistent")
        assert result is None

    def test_empty_name(self):
        symbol = {"path": "/tmp/foo.py", "name": "", "start_line": 1}
        result = symbol_to_lsp_position(symbol, "/tmp")
        assert result is None

    def test_empty_path(self):
        symbol = {"path": "", "name": "foo", "start_line": 1}
        result = symbol_to_lsp_position(symbol, "/tmp")
        assert result is None


# ---------------------------------------------------------------------------
# lsp_locations_to_references
# ---------------------------------------------------------------------------


class TestLspLocationsToReferences:
    def test_empty_locations(self):
        result = lsp_locations_to_references([], {})
        assert result == []

    def test_basic_conversion(self):
        locations = [
            Location(
                uri="file:///home/user/project/src/bar.py",
                range=Range(
                    start=Position(line=10, character=4),
                    end=Position(line=10, character=10),
                ),
            ),
        ]
        index = {
            "symbols": {},
            "files": {"src/bar.py": {}},
        }
        refs = lsp_locations_to_references(locations, index)
        assert len(refs) == 1
        ref = refs[0]
        assert ref["confidence"] == "high"
        assert ref["source"] == "lsp"
        assert ref["type"] == "usage"
        assert ref["line"] == 11  # 0-based -> 1-based

    def test_matches_containing_symbol(self):
        locations = [
            Location(
                uri="file:///project/src/utils.py",
                range=Range(
                    start=Position(line=5, character=0),
                    end=Position(line=5, character=8),
                ),
            ),
        ]
        index = {
            "symbols": {
                "myproj:src/utils.py:function:helper": {
                    "path": "src/utils.py",
                    "name": "helper",
                    "start_line": 3,
                    "end_line": 10,
                },
            },
            "files": {"src/utils.py": {}},
        }
        refs = lsp_locations_to_references(locations, index)
        assert len(refs) == 1
        assert refs[0]["from_symbol"] == "myproj:src/utils.py:function:helper"
        assert refs[0]["from_name"] == "helper"
        assert refs[0]["from_path"] == "src/utils.py"

    def test_multiple_locations(self):
        locations = [
            Location(
                uri="file:///project/a.py",
                range=Range(
                    start=Position(line=1, character=0),
                    end=Position(line=1, character=5),
                ),
            ),
            Location(
                uri="file:///project/b.py",
                range=Range(
                    start=Position(line=20, character=8),
                    end=Position(line=20, character=13),
                ),
            ),
        ]
        refs = lsp_locations_to_references(locations, {"symbols": {}, "files": {}})
        assert len(refs) == 2
        assert refs[0]["line"] == 2
        assert refs[1]["line"] == 21

    def test_no_matching_symbol_still_returns_ref(self):
        locations = [
            Location(
                uri="file:///unknown/file.py",
                range=Range(
                    start=Position(line=0, character=0),
                    end=Position(line=0, character=5),
                ),
            ),
        ]
        refs = lsp_locations_to_references(locations, {"symbols": {}, "files": {}})
        assert len(refs) == 1
        assert refs[0]["from_symbol"] == ""
        assert refs[0]["from_name"] == ""
