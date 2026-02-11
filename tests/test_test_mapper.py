"""Tests for test_mapper module (TestMapper class)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from test_mapper import TestMapper


@pytest.fixture
def index_with_test_pairs():
    """Index with matching source and test files."""
    return {
        "symbols": {
            "proj:src/auth.py:function:login": {
                "path": "src/auth.py",
                "name": "login",
            },
            "proj:src/auth.py:function:logout": {
                "path": "src/auth.py",
                "name": "logout",
            },
            "proj:tests/test_auth.py:function:test_login": {
                "path": "tests/test_auth.py",
                "name": "test_login",
            },
            "proj:src/utils.ts:function:format": {
                "path": "src/utils.ts",
                "name": "format",
            },
            "proj:src/__tests__/utils.test.ts:function:testFormat": {
                "path": "src/__tests__/utils.test.ts",
                "name": "testFormat",
            },
        },
        "dependencies": {},
    }


@pytest.fixture
def empty_index():
    return {"symbols": {}, "dependencies": {}}


class TestTestMapperIsTestFile:
    """Test static _is_test_file method."""

    def test_python_test_file(self):
        assert TestMapper._is_test_file("test_auth.py") is True
        assert TestMapper._is_test_file("auth_test.py") is True

    def test_js_test_file(self):
        assert TestMapper._is_test_file("auth.test.ts") is True
        assert TestMapper._is_test_file("auth.spec.js") is True

    def test_test_directory(self):
        assert TestMapper._is_test_file("tests/foo.py") is True
        assert TestMapper._is_test_file("__tests__/bar.ts") is True

    def test_non_test_file(self):
        assert TestMapper._is_test_file("src/auth.py") is False
        assert TestMapper._is_test_file("src/utils.ts") is False


class TestTestMapperFindTest:
    """Test find_test method."""

    def test_find_python_test(self, index_with_test_pairs):
        mapper = TestMapper(index_with_test_pairs)
        result = mapper.find_test("src/auth.py")
        assert result is not None
        assert "test_auth" in result

    def test_find_ts_test(self, index_with_test_pairs):
        mapper = TestMapper(index_with_test_pairs)
        result = mapper.find_test("src/utils.ts")
        assert result is not None
        assert "utils.test.ts" in result

    def test_find_test_no_match(self, index_with_test_pairs):
        mapper = TestMapper(index_with_test_pairs)
        result = mapper.find_test("src/nonexistent.py")
        assert result is None


class TestTestMapperFindSource:
    """Test find_source method."""

    def test_find_source_from_test(self, index_with_test_pairs):
        mapper = TestMapper(index_with_test_pairs)
        result = mapper.find_source("tests/test_auth.py")
        assert result is not None
        assert result == "src/auth.py"

    def test_find_source_no_match(self, index_with_test_pairs):
        mapper = TestMapper(index_with_test_pairs)
        result = mapper.find_source("tests/test_unknown.py")
        assert result is None


class TestTestMapperBuild:
    """Test build method."""

    def test_build_is_idempotent(self, index_with_test_pairs):
        mapper = TestMapper(index_with_test_pairs)
        mapper.build()
        first_result = mapper.find_test("src/auth.py")
        mapper.build()  # second call should be no-op
        second_result = mapper.find_test("src/auth.py")
        assert first_result == second_result

    def test_empty_index(self, empty_index):
        mapper = TestMapper(empty_index)
        mapper.build()
        assert mapper.find_test("src/foo.py") is None
        assert mapper.find_source("tests/test_foo.py") is None

    def test_lazy_build(self, index_with_test_pairs):
        mapper = TestMapper(index_with_test_pairs)
        assert mapper._built is False
        # find_test triggers build
        mapper.find_test("src/auth.py")
        assert mapper._built is True
