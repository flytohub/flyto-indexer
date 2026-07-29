"""Tests for test_mapper module (TestMapper class)."""

import os
import sys
import time
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

    def test_project_scope_disambiguates_identical_relative_paths(self):
        index = {
            "symbols": {
                "alpha:src/auth.py:function:login": {"path": "src/auth.py"},
                "alpha:tests/test_auth.py:function:test_login": {
                    "path": "tests/test_auth.py"
                },
                "beta:src/auth.py:function:login": {"path": "src/auth.py"},
                "beta:spec/auth_test.py:function:test_login": {
                    "path": "spec/auth_test.py"
                },
            },
            "dependencies": {},
        }

        mapper = TestMapper(index)

        assert mapper.find_test("src/auth.py", project="alpha") == "tests/test_auth.py"
        assert mapper.find_test("src/auth.py", project="beta") == "spec/auth_test.py"

    def test_import_fallback_stays_in_source_project(self):
        index = {
            "symbols": {
                "alpha:src/shared.py:function:run": {"path": "src/shared.py"},
                "alpha:tests/test_adapter.py:function:test_run": {
                    "path": "tests/test_adapter.py"
                },
                "beta:src/shared.py:function:run": {"path": "src/shared.py"},
            },
            "dependencies": {
                "import-1": {
                    "type": "imports",
                    "source": "alpha:tests/test_adapter.py:function:test_run",
                    "target": "shared",
                    "metadata": {},
                }
            },
        }

        mapper = TestMapper(index)

        assert (
            mapper.find_source("tests/test_adapter.py", project="alpha")
            == "src/shared.py"
        )
        assert mapper.find_test("src/shared.py", project="beta") is None

    def test_large_import_fallback_is_bounded(self):
        symbols = {}
        dependencies = {}
        source_count = 6000
        import_count = 2000
        for index in range(source_count):
            symbols[f"proj:src/module_{index}.py:function:run"] = {
                "path": f"src/module_{index}.py"
            }
        for index in range(import_count):
            test_path = f"tests/check_{index}.py"
            symbols[f"proj:{test_path}:function:test_run"] = {"path": test_path}
            dependencies[f"import-{index}"] = {
                "type": "imports",
                "source": f"proj:{test_path}:function:test_run",
                "target": f"module_{index}",
                "metadata": {},
            }

        started = time.monotonic()
        mapper = TestMapper(
            {"symbols": symbols, "dependencies": dependencies},
            project="proj",
        )
        mapper.build()
        elapsed = time.monotonic() - started

        assert mapper.find_test("src/module_1999.py") == "tests/check_1999.py"
        assert elapsed < 1.0
