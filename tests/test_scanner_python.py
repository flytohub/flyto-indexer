"""Tests for Python scanner."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import SymbolType, DependencyType
from scanner.python import PythonScanner


@pytest.fixture
def scanner():
    return PythonScanner("test-project")


class TestPythonScannerBasic:
    """Test basic Python scanner functionality."""

    def test_supported_extensions(self, scanner):
        assert ".py" in scanner.supported_extensions

    def test_can_scan_python_file(self, scanner):
        assert scanner.can_scan(Path("test.py")) is True
        assert scanner.can_scan(Path("test.js")) is False

    def test_empty_file(self, scanner):
        symbols, deps = scanner.scan_file(Path("empty.py"), "")
        assert symbols == []
        assert deps == []

    def test_syntax_error_returns_empty(self, scanner):
        symbols, deps = scanner.scan_file(Path("bad.py"), "def foo(:\n  pass")
        assert symbols == []
        assert deps == []


class TestPythonScannerFunctions:
    """Test function extraction."""

    def test_simple_function(self, scanner):
        code = "def hello():\n    pass\n"
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) == 1
        assert funcs[0].name == "hello"
        assert funcs[0].language == "python"
        assert funcs[0].start_line == 1

    def test_function_with_params(self, scanner):
        code = "def greet(name, greeting='hi'):\n    return f'{greeting} {name}'\n"
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) == 1
        assert "name" in funcs[0].params
        assert "greeting" in funcs[0].params

    def test_function_with_return_type(self, scanner):
        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) == 1
        assert funcs[0].returns == "int"

    def test_function_with_docstring(self, scanner):
        code = 'def foo():\n    """This is a docstring."""\n    pass\n'
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) == 1
        assert "docstring" in funcs[0].summary.lower()

    def test_multiple_functions(self, scanner):
        code = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) == 2
        names = {f.name for f in funcs}
        assert names == {"foo", "bar"}


class TestPythonScannerClasses:
    """Test class extraction."""

    def test_simple_class(self, scanner):
        code = "class MyClass:\n    pass\n"
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        classes = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "MyClass"

    def test_class_with_base(self, scanner):
        code = "class Child(Parent):\n    pass\n"
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        classes = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert len(classes) == 1
        assert "Parent" in classes[0].imports

    def test_class_with_methods(self, scanner):
        code = (
            "class MyClass:\n"
            "    def method_one(self):\n"
            "        pass\n"
            "    def method_two(self, x):\n"
            "        pass\n"
        )
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        methods = [s for s in symbols if s.symbol_type == SymbolType.METHOD]
        assert len(methods) == 2
        assert methods[0].name == "MyClass.method_one"
        assert methods[1].name == "MyClass.method_two"
        # self should be excluded from params
        assert "self" not in methods[0].params


class TestPythonScannerImports:
    """Test import extraction."""

    def test_simple_import(self, scanner):
        code = "import os\n\ndef foo():\n    pass\n"
        _, deps = scanner.scan_file(Path("test.py"), code)
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        assert len(import_deps) >= 1
        modules = [d.target_id for d in import_deps]
        assert "os" in modules

    def test_from_import(self, scanner):
        code = "from pathlib import Path\n\ndef foo():\n    pass\n"
        _, deps = scanner.scan_file(Path("test.py"), code)
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        modules = [d.target_id for d in import_deps]
        assert "pathlib" in modules

    def test_call_extraction(self, scanner):
        code = "def foo():\n    bar()\n    obj.method()\n"
        _, deps = scanner.scan_file(Path("test.py"), code)
        call_deps = [d for d in deps if d.dep_type == DependencyType.CALLS]
        call_names = [d.target_id for d in call_deps]
        assert "bar" in call_names
        assert "obj.method" in call_names


class TestPythonScannerSymbolId:
    """Test symbol ID generation."""

    def test_function_symbol_id(self, scanner):
        code = "def hello():\n    pass\n"
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert funcs[0].id == "test-project:test.py:function:hello"

    def test_class_symbol_id(self, scanner):
        code = "class Foo:\n    pass\n"
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        classes = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert classes[0].id == "test-project:test.py:class:Foo"

    def test_content_hash_computed(self, scanner):
        code = "def hello():\n    pass\n"
        symbols, _ = scanner.scan_file(Path("test.py"), code)
        for s in symbols:
            assert s.content_hash != ""
            assert len(s.content_hash) == 16
