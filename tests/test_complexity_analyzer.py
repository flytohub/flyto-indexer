"""Tests for analyzer/complexity module."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from analyzer.complexity import (
    FunctionComplexity, ComplexityReport, ComplexityAnalyzer,
    _line_threshold_for_file, _is_test_file,
)


class TestFileTypeThresholds:
    """Test file-type-aware line thresholds."""

    def test_python_threshold(self):
        assert _line_threshold_for_file("src/foo.py") == 80

    def test_typescript_threshold(self):
        assert _line_threshold_for_file("src/foo.ts") == 80

    def test_javascript_threshold(self):
        assert _line_threshold_for_file("src/foo.js") == 80

    def test_vue_component_threshold(self):
        assert _line_threshold_for_file("src/components/Foo.vue") == 100

    def test_tsx_component_threshold(self):
        assert _line_threshold_for_file("src/components/Foo.tsx") == 100

    def test_jsx_component_threshold(self):
        assert _line_threshold_for_file("src/App.jsx") == 100

    def test_is_test_file(self):
        assert _is_test_file("tests/test_foo.py")
        assert _is_test_file("src/foo.test.ts")
        assert _is_test_file("src/foo.spec.js")
        assert _is_test_file("src/__tests__/foo.ts")
        assert not _is_test_file("src/foo.py")
        assert not _is_test_file("src/testing_utils.py")

    def test_vue_file_not_penalized_at_80(self):
        """A .vue file with 90 lines should NOT be flagged (threshold=100)."""
        fc = FunctionComplexity(
            file_path="src/components/Foo.vue", name="setup",
            line_start=1, line_end=90,
            lines=90, params=2, max_depth=2,
            branches=3, returns=1,
        )
        assert fc.score == 0
        assert fc.issues == []

    def test_py_file_penalized_at_90(self):
        """A .py file with 90 lines should be flagged (threshold=80)."""
        fc = FunctionComplexity(
            file_path="src/foo.py", name="process",
            line_start=1, line_end=90,
            lines=90, params=2, max_depth=2,
            branches=3, returns=1,
        )
        assert fc.score >= 1
        assert any("90" in i for i in fc.issues)


class TestFunctionComplexity:
    """Test FunctionComplexity dataclass."""

    def test_score_simple_function(self):
        fc = FunctionComplexity(
            file_path="a.py", name="simple",
            line_start=1, line_end=10,
            lines=10, params=2, max_depth=1,
            branches=2, returns=1,
        )
        assert fc.score == 0  # All within thresholds

    def test_score_complex_function(self):
        fc = FunctionComplexity(
            file_path="a.py", name="complex",
            line_start=1, line_end=100,
            lines=100, params=8, max_depth=6,
            branches=15, returns=5,
        )
        assert fc.score > 0

    def test_score_long_function(self):
        fc = FunctionComplexity(
            file_path="a.py", name="long",
            line_start=1, line_end=100,
            lines=100, params=2, max_depth=2,
            branches=3, returns=1,
        )
        # (100 - 80) // 10 = 2 (threshold for .py is 80)
        assert fc.score >= 2

    def test_score_deep_nesting(self):
        fc = FunctionComplexity(
            file_path="a.py", name="deep",
            line_start=1, line_end=20,
            lines=20, params=1, max_depth=6,
            branches=3, returns=1,
        )
        # (6 - 3) * 5 = 15
        assert fc.score >= 15

    def test_issues_too_long(self):
        fc = FunctionComplexity(
            file_path="a.py", name="long",
            line_start=1, line_end=100,
            lines=100, params=2, max_depth=2,
            branches=3, returns=1,
        )
        issues = fc.issues
        assert any("100" in i for i in issues)

    def test_issues_too_deep(self):
        fc = FunctionComplexity(
            file_path="a.py", name="deep",
            line_start=1, line_end=20,
            lines=20, params=1, max_depth=5,
            branches=3, returns=1,
        )
        issues = fc.issues
        assert any("depth" in i.lower() for i in issues)

    def test_issues_too_many_params(self):
        fc = FunctionComplexity(
            file_path="a.py", name="many_params",
            line_start=1, line_end=10,
            lines=10, params=8, max_depth=1,
            branches=1, returns=1,
        )
        issues = fc.issues
        assert any("8" in i for i in issues)

    def test_no_issues_for_simple(self):
        fc = FunctionComplexity(
            file_path="a.py", name="simple",
            line_start=1, line_end=10,
            lines=10, params=2, max_depth=1,
            branches=2, returns=1,
        )
        assert fc.issues == []


class TestComplexityReport:
    """Test ComplexityReport dataclass."""

    def test_defaults(self):
        report = ComplexityReport()
        assert report.total_files == 0
        assert report.total_functions == 0
        assert report.complex_functions == []
        assert report.avg_lines == 0
        assert report.max_lines == 0


class TestComplexityAnalyzerPython:
    """Test Python file analysis."""

    def test_simple_python_function(self):
        analyzer = ComplexityAnalyzer(Path("/tmp"))
        code = "def foo(x):\n    return x + 1\n"
        results = analyzer.analyze_python_file("test.py", code)
        assert len(results) == 1
        assert results[0].name == "foo"
        assert results[0].params >= 1

    def test_complex_python_function(self):
        analyzer = ComplexityAnalyzer(Path("/tmp"))
        code = (
            "def complex(a, b, c, d, e, f):\n"
            "    if a > 0:\n"
            "        for i in range(b):\n"
            "            if c:\n"
            "                while d:\n"
            "                    try:\n"
            "                        pass\n"
            "                    except:\n"
            "                        pass\n"
            "    return a\n"
        )
        results = analyzer.analyze_python_file("test.py", code)
        assert len(results) == 1
        assert results[0].branches > 0
        assert results[0].max_depth > 0
        assert results[0].params == 6

    def test_syntax_error_returns_empty(self):
        analyzer = ComplexityAnalyzer(Path("/tmp"))
        code = "def bad(:\n  pass"
        results = analyzer.analyze_python_file("bad.py", code)
        assert results == []

    def test_multiple_functions(self):
        analyzer = ComplexityAnalyzer(Path("/tmp"))
        code = (
            "def foo():\n    pass\n\n"
            "def bar(x):\n    return x\n\n"
            "async def baz():\n    pass\n"
        )
        results = analyzer.analyze_python_file("test.py", code)
        assert len(results) == 3


class TestComplexityAnalyzerTypeScript:
    """Test TypeScript/JavaScript analysis."""

    def test_simple_ts_function(self):
        analyzer = ComplexityAnalyzer(Path("/tmp"))
        code = "function greet(name: string) {\n  return name;\n}\n"
        results = analyzer.analyze_typescript_file("test.ts", code)
        assert len(results) >= 1
        assert results[0].name == "greet"

    def test_arrow_function(self):
        analyzer = ComplexityAnalyzer(Path("/tmp"))
        code = "const add = (a: number, b: number) => {\n  return a + b;\n}\n"
        results = analyzer.analyze_typescript_file("test.ts", code)
        assert len(results) >= 1
        assert results[0].name == "add"


class TestComplexityAnalyzerGo:
    """Test Go analysis."""

    def test_go_function(self):
        analyzer = ComplexityAnalyzer(Path("/tmp"))
        code = "func main() {\n\tfmt.Println(\"hello\")\n}\n"
        results = analyzer.analyze_go_file("main.go", code)
        assert len(results) >= 1
        assert results[0].name == "main"
