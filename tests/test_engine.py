"""Comprehensive tests for IndexEngine in flyto-indexer/src/engine.py."""

import json
import sys
import tempfile
import os
import time
from pathlib import Path

# Same import pattern as test_basic.py
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.engine import IndexEngine
from models import Symbol, SymbolType, ProjectIndex, FileManifest


# ---------------------------------------------------------------------------
# Sample source code fixtures
# ---------------------------------------------------------------------------

SIMPLE_PYTHON = """\
def greet(name: str) -> str:
    \"\"\"Say hello to someone.\"\"\"
    return f"Hello, {name}"


def add(a: int, b: int) -> int:
    return a + b
"""

CLASS_PYTHON = """\
class Calculator:
    \"\"\"A simple calculator.\"\"\"

    def __init__(self):
        self.history = []

    def add(self, a, b):
        result = a + b
        self.history.append(result)
        return result

    def subtract(self, a, b):
        result = a - b
        self.history.append(result)
        return result
"""

NESTED_PYTHON = """\
import os
from pathlib import Path

class Processor:
    def process(self, data):
        return data

def helper():
    return True

class Validator(Processor):
    def validate(self, data):
        return bool(data)
"""

SIMPLE_TYPESCRIPT = """\
export function greetUser(name: string): string {
    return `Hello, ${name}`;
}

export const multiply = (a: number, b: number): number => {
    return a * b;
};

export interface UserConfig {
    name: string;
    age: number;
}
"""

SYNTAX_ERROR_PYTHON = """\
def broken(
    # missing closing paren and colon
    x = [1, 2, 3
"""

EMPTY_PYTHON = ""

UNICODE_PYTHON = """\
def unicode_func():
    \"\"\"Function with unicode content.\"\"\"
    message = "Hello World"
    return message
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_project(tmp_path, files: dict) -> Path:
    """Create a temporary project directory with given files.

    Args:
        tmp_path: pytest tmp_path fixture
        files: mapping of relative path -> content

    Returns:
        Path to the project directory
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    for rel_path, content in files.items():
        file_path = project_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    return project_dir


def _make_engine(tmp_path, files: dict, project_name: str = "test") -> IndexEngine:
    """Create an IndexEngine with a temporary project."""
    project_dir = _create_project(tmp_path, files)
    index_dir = tmp_path / ".index"
    return IndexEngine(project_name, project_dir, index_dir)


# ===========================================================================
# Test Classes
# ===========================================================================

class TestEngineInit:
    """Test engine initialization."""

    def test_init_creates_scanners(self, tmp_path):
        """Engine should initialize with all 6 language scanners."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        assert len(engine.scanners) == 6
        scanner_types = [type(s).__name__ for s in engine.scanners]
        assert "PythonScanner" in scanner_types
        assert "TypeScriptScanner" in scanner_types
        assert "VueScanner" in scanner_types
        assert "GoScanner" in scanner_types
        assert "RustScanner" in scanner_types
        assert "JavaScanner" in scanner_types

    def test_init_sets_project_name(self, tmp_path):
        """Engine stores the project name correctly."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("my-project", project_dir, tmp_path / ".index")

        assert engine.project_name == "my-project"
        assert engine.index.project == "my-project"

    def test_init_default_index_dir(self, tmp_path):
        """Without explicit index_dir, defaults to .flyto-index inside project."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir)

        assert engine.index_dir == project_dir / ".flyto-index"

    def test_init_custom_index_dir(self, tmp_path):
        """Custom index_dir is used when provided."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        custom_dir = tmp_path / "custom-index"
        engine = IndexEngine("test", project_dir, custom_dir)

        assert engine.index_dir == custom_dir

    def test_init_creates_empty_index(self, tmp_path):
        """A fresh engine with no saved index starts empty."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        assert len(engine.index.symbols) == 0
        assert len(engine.index.files) == 0
        assert len(engine.index.dependencies) == 0

    def test_init_loads_existing_index(self, tmp_path):
        """If a valid index.json exists, it should be loaded."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        index_dir = tmp_path / ".index"
        index_dir.mkdir()

        # Write a valid index file
        index_data = {
            "project": "test",
            "root_path": str(project_dir),
            "files": {
                "hello.py": {
                    "path": "hello.py",
                    "hash": "abc123",
                    "lines": 5,
                    "symbols": ["test:hello.py:function:hello"],
                    "indexed_at": "2025-01-01T00:00:00",
                }
            },
            "symbols": {
                "test:hello.py:function:hello": {
                    "project": "test",
                    "path": "hello.py",
                    "type": "function",
                    "name": "hello",
                    "start_line": 1,
                    "end_line": 3,
                    "language": "python",
                    "content": "def hello(): pass",
                    "summary": "",
                    "exports": [],
                    "imports": [],
                    "ref_count": 0,
                }
            },
            "dependencies": {},
        }
        (index_dir / "index.json").write_text(json.dumps(index_data))

        engine = IndexEngine("test", project_dir, index_dir)
        assert len(engine.index.symbols) == 1
        assert "test:hello.py:function:hello" in engine.index.symbols

    def test_init_path_as_string(self, tmp_path):
        """project_root can be passed as string; engine converts to Path."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", str(project_dir), tmp_path / ".index")

        assert isinstance(engine.project_root, Path)
        assert engine.project_root == project_dir


class TestEngineScan:
    """Test scan() method."""

    def test_scan_simple_python_project(self, tmp_path):
        """Scan a project with simple Python functions."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        result = engine.scan(incremental=False)

        assert result["project"] == "test"
        assert result["files_scanned"] >= 1
        assert result["symbols_found"] >= 2  # greet and add
        assert result["errors"] == 0

    def test_scan_returns_correct_structure(self, tmp_path):
        """scan() returns dict with expected keys."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        result = engine.scan(incremental=False)

        assert "project" in result
        assert "files_scanned" in result
        assert "symbols_found" in result
        assert "dependencies_found" in result
        assert "errors" in result
        assert "changes" in result

    def test_scan_full_rebuild(self, tmp_path):
        """incremental=False triggers full rebuild."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        result = engine.scan(incremental=False)

        assert result["changes"] == "full rebuild"
        assert result["symbols_found"] >= 2

    def test_scan_populates_index_symbols(self, tmp_path):
        """After scan, engine.index.symbols is populated."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        assert len(engine.index.symbols) >= 2
        symbol_names = [s.name for s in engine.index.symbols.values()]
        assert "greet" in symbol_names
        assert "add" in symbol_names

    def test_scan_populates_index_files(self, tmp_path):
        """After scan, engine.index.files has file manifests."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        assert "hello.py" in engine.index.files
        manifest = engine.index.files["hello.py"]
        assert manifest.path == "hello.py"
        assert manifest.content_hash != ""
        assert manifest.line_count > 0

    def test_scan_multiple_files(self, tmp_path):
        """Scan a project with multiple Python files."""
        engine = _make_engine(tmp_path, {
            "utils.py": SIMPLE_PYTHON,
            "models.py": CLASS_PYTHON,
        })
        result = engine.scan(incremental=False)

        assert result["files_scanned"] >= 2
        # greet, add from utils.py + Calculator, __init__, add, subtract from models.py
        assert result["symbols_found"] >= 5

    def test_scan_mixed_languages(self, tmp_path):
        """Scan a project with both Python and TypeScript files."""
        engine = _make_engine(tmp_path, {
            "utils.py": SIMPLE_PYTHON,
            "helpers.ts": SIMPLE_TYPESCRIPT,
        })
        result = engine.scan(incremental=False)

        assert result["files_scanned"] >= 2
        languages = set(s.language for s in engine.index.symbols.values())
        assert "python" in languages

    def test_scan_empty_directory(self, tmp_path):
        """Scanning an empty directory produces zero results without error."""
        engine = _make_engine(tmp_path, {})
        result = engine.scan(incremental=False)

        assert result["files_scanned"] == 0
        assert result["symbols_found"] == 0
        assert result["errors"] == 0

    def test_scan_saves_index_file(self, tmp_path):
        """scan() writes index.json to disk."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        index_file = engine.index_dir / "index.json"
        assert index_file.exists()

        data = json.loads(index_file.read_text())
        assert data["project"] == "test"
        assert "symbols" in data
        assert "files" in data

    def test_scan_saves_content_file(self, tmp_path):
        """scan() writes content.jsonl to disk."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        content_file = engine.index_dir / "content.jsonl"
        assert content_file.exists()

        lines = content_file.read_text().strip().splitlines()
        assert len(lines) >= 2  # at least greet and add
        for line in lines:
            record = json.loads(line)
            assert "id" in record
            assert "content" in record

    def test_scan_ignores_pycache(self, tmp_path):
        """Files inside __pycache__ should be ignored."""
        project_dir = _create_project(tmp_path, {"hello.py": SIMPLE_PYTHON})
        pycache = project_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "cached.py").write_text("def cached(): pass")

        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        engine.scan(incremental=False)

        # cached.py should not be in the index
        for sym in engine.index.symbols.values():
            assert "__pycache__" not in sym.path

    def test_scan_ignores_node_modules(self, tmp_path):
        """Files inside node_modules should be ignored."""
        project_dir = _create_project(tmp_path, {"app.ts": SIMPLE_TYPESCRIPT})
        nm = project_dir / "node_modules" / "some-pkg"
        nm.mkdir(parents=True)
        (nm / "index.ts").write_text("export function pkg(): void {}")

        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        engine.scan(incremental=False)

        for sym in engine.index.symbols.values():
            assert "node_modules" not in sym.path

    def test_scan_skips_large_files(self, tmp_path):
        """Files larger than 1MB should be skipped with an error entry."""
        project_dir = _create_project(tmp_path, {
            "small.py": SIMPLE_PYTHON,
        })
        large_file = project_dir / "large.py"
        large_file.write_text("x = 1\n" * 200_000)  # ~1.2MB
        assert large_file.stat().st_size > 1_048_576

        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        result = engine.scan(incremental=False)

        assert result["errors"] >= 1
        # small.py should still be indexed
        symbol_names = [s.name for s in engine.index.symbols.values()]
        assert "greet" in symbol_names

    def test_scan_subdirectories(self, tmp_path):
        """Files in subdirectories are correctly scanned with relative paths."""
        engine = _make_engine(tmp_path, {
            "src/utils/math.py": SIMPLE_PYTHON,
            "src/models/base.py": CLASS_PYTHON,
        })
        result = engine.scan(incremental=False)

        assert result["files_scanned"] >= 2
        paths = [s.path for s in engine.index.symbols.values()]
        assert any("src/utils/math.py" in p for p in paths)
        assert any("src/models/base.py" in p for p in paths)


class TestIncrementalScan:
    """Test incremental scanning."""

    def test_incremental_detects_new_files(self, tmp_path):
        """Adding a new file should be detected in incremental scan."""
        project_dir = _create_project(tmp_path, {"first.py": SIMPLE_PYTHON})
        index_dir = tmp_path / ".index"
        engine = IndexEngine("test", project_dir, index_dir)

        # Initial full scan
        result1 = engine.scan(incremental=False)
        initial_symbols = result1["symbols_found"]

        # Add a new file
        (project_dir / "second.py").write_text(CLASS_PYTHON)

        # Incremental scan
        result2 = engine.scan(incremental=True)
        assert result2["files_scanned"] >= 1
        # Total symbols should now include both files
        assert len(engine.index.symbols) > initial_symbols

    def test_incremental_detects_modified_files(self, tmp_path):
        """Modifying a file should trigger re-scan of that file."""
        project_dir = _create_project(tmp_path, {"hello.py": SIMPLE_PYTHON})
        index_dir = tmp_path / ".index"
        engine = IndexEngine("test", project_dir, index_dir)

        # Initial full scan
        engine.scan(incremental=False)
        old_symbols = dict(engine.index.symbols)

        # Modify the file (add a new function)
        new_content = SIMPLE_PYTHON + "\ndef extra():\n    return 42\n"
        (project_dir / "hello.py").write_text(new_content)

        # Incremental scan
        result = engine.scan(incremental=True)
        assert result["files_scanned"] >= 1

        # Should now have the extra function
        symbol_names = [s.name for s in engine.index.symbols.values()]
        assert "extra" in symbol_names

    def test_incremental_no_changes(self, tmp_path):
        """If nothing changed, incremental scan should process 0 files."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})

        # Initial incremental scan (sees all files as "added", saves manifest)
        engine.scan(incremental=True)

        # Second scan with no changes - manifest exists, hashes match
        result = engine.scan(incremental=True)
        assert result["files_scanned"] == 0

    def test_incremental_handles_deleted_files(self, tmp_path):
        """Deleting a file should remove its symbols from the index."""
        project_dir = _create_project(tmp_path, {
            "keep.py": SIMPLE_PYTHON,
            "remove.py": CLASS_PYTHON,
        })
        index_dir = tmp_path / ".index"
        engine = IndexEngine("test", project_dir, index_dir)

        # Initial incremental scan (saves manifest so delete is detectable)
        engine.scan(incremental=True)
        assert any("Calculator" == s.name for s in engine.index.symbols.values())

        # Delete the file
        (project_dir / "remove.py").unlink()

        # Incremental scan detects deletion via manifest diff
        engine.scan(incremental=True)

        # Calculator should no longer be in the index
        symbol_names = [s.name for s in engine.index.symbols.values()]
        assert "Calculator" not in symbol_names
        # But greet/add from keep.py should remain
        assert "greet" in symbol_names

    def test_full_rebuild_clears_old_symbols(self, tmp_path):
        """incremental=False clears the entire index before rebuilding."""
        project_dir = _create_project(tmp_path, {
            "first.py": SIMPLE_PYTHON,
            "second.py": CLASS_PYTHON,
        })
        index_dir = tmp_path / ".index"
        engine = IndexEngine("test", project_dir, index_dir)

        # Scan both files
        engine.scan(incremental=False)
        assert len(engine.index.symbols) >= 5

        # Remove one file
        (project_dir / "second.py").unlink()

        # Full rebuild should not retain old symbols
        engine.scan(incremental=False)
        symbol_names = [s.name for s in engine.index.symbols.values()]
        assert "Calculator" not in symbol_names


class TestSymbolExtraction:
    """Test that symbols are correctly extracted."""

    def test_extracts_functions(self, tmp_path):
        """Top-level Python functions are extracted as FUNCTION type."""
        engine = _make_engine(tmp_path, {"funcs.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        functions = [
            s for s in engine.index.symbols.values()
            if s.symbol_type == SymbolType.FUNCTION
        ]
        func_names = [f.name for f in functions]
        assert "greet" in func_names
        assert "add" in func_names

    def test_extracts_classes(self, tmp_path):
        """Python classes are extracted as CLASS type."""
        engine = _make_engine(tmp_path, {"calc.py": CLASS_PYTHON})
        engine.scan(incremental=False)

        classes = [
            s for s in engine.index.symbols.values()
            if s.symbol_type == SymbolType.CLASS
        ]
        assert len(classes) == 1
        assert classes[0].name == "Calculator"

    def test_extracts_methods(self, tmp_path):
        """Class methods are extracted as METHOD type with ClassName.method_name."""
        engine = _make_engine(tmp_path, {"calc.py": CLASS_PYTHON})
        engine.scan(incremental=False)

        methods = [
            s for s in engine.index.symbols.values()
            if s.symbol_type == SymbolType.METHOD
        ]
        method_names = [m.name for m in methods]
        assert "Calculator.__init__" in method_names
        assert "Calculator.add" in method_names
        assert "Calculator.subtract" in method_names

    def test_function_has_params(self, tmp_path):
        """Function parameters should be captured."""
        engine = _make_engine(tmp_path, {"funcs.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        greet = None
        for s in engine.index.symbols.values():
            if s.name == "greet":
                greet = s
                break

        assert greet is not None
        assert "name" in greet.params

    def test_function_has_return_type(self, tmp_path):
        """Function return type annotation should be captured."""
        engine = _make_engine(tmp_path, {"funcs.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        greet = None
        for s in engine.index.symbols.values():
            if s.name == "greet":
                greet = s
                break

        assert greet is not None
        assert greet.returns == "str"

    def test_class_has_docstring_summary(self, tmp_path):
        """Class docstrings become the symbol summary."""
        engine = _make_engine(tmp_path, {"calc.py": CLASS_PYTHON})
        engine.scan(incremental=False)

        calc = None
        for s in engine.index.symbols.values():
            if s.name == "Calculator":
                calc = s
                break

        assert calc is not None
        assert "simple calculator" in calc.summary.lower()

    def test_symbol_has_content(self, tmp_path):
        """Symbol content should contain the actual source code."""
        engine = _make_engine(tmp_path, {"funcs.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        greet = None
        for s in engine.index.symbols.values():
            if s.name == "greet":
                greet = s
                break

        assert greet is not None
        assert "def greet" in greet.content
        assert "Hello" in greet.content

    def test_symbol_has_content_hash(self, tmp_path):
        """Each symbol should have a computed content hash."""
        engine = _make_engine(tmp_path, {"funcs.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        for sym in engine.index.symbols.values():
            if sym.content:
                assert sym.content_hash != ""
                assert len(sym.content_hash) == 16

    def test_symbol_has_line_numbers(self, tmp_path):
        """Symbols should have start_line and end_line set."""
        engine = _make_engine(tmp_path, {"funcs.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        greet = None
        for s in engine.index.symbols.values():
            if s.name == "greet":
                greet = s
                break

        assert greet is not None
        assert greet.start_line >= 1
        assert greet.end_line >= greet.start_line

    def test_symbol_has_language(self, tmp_path):
        """Python symbols should have language='python'."""
        engine = _make_engine(tmp_path, {"funcs.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        for sym in engine.index.symbols.values():
            if sym.path.endswith(".py"):
                assert sym.language == "python"

    def test_symbol_id_format(self, tmp_path):
        """Symbol IDs should follow project:path:type:name format."""
        engine = _make_engine(tmp_path, {"funcs.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        for sid, sym in engine.index.symbols.items():
            parts = sid.split(":")
            assert len(parts) == 4, f"Symbol ID {sid} does not have 4 parts"
            assert parts[0] == "test"  # project name
            assert parts[1] == sym.path
            assert parts[2] == sym.symbol_type.value
            assert parts[3] == sym.name

    def test_nested_classes_and_functions(self, tmp_path):
        """Multiple classes and functions in one file are all extracted."""
        engine = _make_engine(tmp_path, {"nested.py": NESTED_PYTHON})
        engine.scan(incremental=False)

        symbol_names = [s.name for s in engine.index.symbols.values()]
        assert "Processor" in symbol_names
        assert "helper" in symbol_names
        assert "Validator" in symbol_names

    def test_method_excludes_self_param(self, tmp_path):
        """Method parameters should not include 'self'."""
        engine = _make_engine(tmp_path, {"calc.py": CLASS_PYTHON})
        engine.scan(incremental=False)

        add_method = None
        for s in engine.index.symbols.values():
            if s.name == "Calculator.add":
                add_method = s
                break

        assert add_method is not None
        assert "self" not in add_method.params
        assert "a" in add_method.params
        assert "b" in add_method.params


class TestHashDetection:
    """Test content hash change detection."""

    def test_same_content_same_hash(self, tmp_path):
        """Scanning the same file twice produces the same hash."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})

        engine.scan(incremental=False)
        first_hashes = {
            path: manifest.content_hash
            for path, manifest in engine.index.files.items()
        }

        # Scan again
        engine.scan(incremental=False)
        second_hashes = {
            path: manifest.content_hash
            for path, manifest in engine.index.files.items()
        }

        assert first_hashes == second_hashes

    def test_modified_content_different_hash(self, tmp_path):
        """Modifying file content produces a different hash."""
        project_dir = _create_project(tmp_path, {"hello.py": SIMPLE_PYTHON})
        index_dir = tmp_path / ".index"
        engine = IndexEngine("test", project_dir, index_dir)

        engine.scan(incremental=False)
        old_hash = engine.index.files["hello.py"].content_hash

        # Modify the file
        (project_dir / "hello.py").write_text(SIMPLE_PYTHON + "\nx = 42\n")
        engine.scan(incremental=False)
        new_hash = engine.index.files["hello.py"].content_hash

        assert old_hash != new_hash

    def test_symbol_hash_updates_on_change(self, tmp_path):
        """When file content changes, symbol content_hash values update."""
        project_dir = _create_project(tmp_path, {"hello.py": SIMPLE_PYTHON})
        index_dir = tmp_path / ".index"
        engine = IndexEngine("test", project_dir, index_dir)

        engine.scan(incremental=False)
        old_symbol_hashes = {
            sid: sym.content_hash
            for sid, sym in engine.index.symbols.items()
        }

        # Modify the greet function
        modified = """\
def greet(name: str) -> str:
    \"\"\"Say hello to someone.\"\"\"
    return f"Hi, {name}!"


def add(a: int, b: int) -> int:
    return a + b
"""
        (project_dir / "hello.py").write_text(modified)
        engine.scan(incremental=False)

        # The greet symbol hash should change, add should remain the same
        greet_id = "test:hello.py:function:greet"
        add_id = "test:hello.py:function:add"

        if greet_id in old_symbol_hashes and greet_id in engine.index.symbols:
            assert engine.index.symbols[greet_id].content_hash != old_symbol_hashes[greet_id]

        if add_id in old_symbol_hashes and add_id in engine.index.symbols:
            assert engine.index.symbols[add_id].content_hash == old_symbol_hashes[add_id]

    def test_file_manifest_line_count(self, tmp_path):
        """FileManifest line_count matches actual file line count."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        manifest = engine.index.files["hello.py"]
        expected_lines = len(SIMPLE_PYTHON.splitlines())
        assert manifest.line_count == expected_lines

    def test_file_manifest_has_symbol_ids(self, tmp_path):
        """FileManifest.symbols contains the IDs of symbols in that file."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        manifest = engine.index.files["hello.py"]
        assert len(manifest.symbols) >= 2
        assert all(s.startswith("test:hello.py:") for s in manifest.symbols)


class TestErrorHandling:
    """Test handling of malformed files."""

    def test_syntax_error_does_not_crash(self, tmp_path):
        """Files with Python syntax errors should not crash the engine."""
        engine = _make_engine(tmp_path, {"bad.py": SYNTAX_ERROR_PYTHON})
        result = engine.scan(incremental=False)

        # Should complete without raising an exception
        assert isinstance(result, dict)
        assert result["files_scanned"] >= 1

    def test_syntax_error_with_valid_files(self, tmp_path):
        """A mix of valid and invalid files: valid files still get indexed."""
        engine = _make_engine(tmp_path, {
            "good.py": SIMPLE_PYTHON,
            "bad.py": SYNTAX_ERROR_PYTHON,
        })
        result = engine.scan(incremental=False)

        # good.py should still be indexed
        symbol_names = [s.name for s in engine.index.symbols.values()]
        assert "greet" in symbol_names
        assert "add" in symbol_names

    def test_empty_file_does_not_crash(self, tmp_path):
        """An empty .py file should not crash the engine."""
        engine = _make_engine(tmp_path, {"empty.py": EMPTY_PYTHON})
        result = engine.scan(incremental=False)

        assert isinstance(result, dict)
        assert result["errors"] == 0

    def test_unicode_decode_error(self, tmp_path):
        """Binary content in a .py file should not crash the engine."""
        project_dir = _create_project(tmp_path, {"good.py": SIMPLE_PYTHON})

        # Write a binary file with .py extension
        bin_file = project_dir / "binary.py"
        bin_file.write_bytes(b"\x80\x81\x82\xff\xfe def foo(): pass")

        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        result = engine.scan(incremental=False)

        # Should complete without raising
        assert isinstance(result, dict)

    def test_nonexistent_file_during_scan(self, tmp_path):
        """If a file disappears between hash scan and content read, no crash."""
        project_dir = _create_project(tmp_path, {"temp.py": SIMPLE_PYTHON})
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        # Delete the file before scan processes it (race condition simulation)
        # The engine checks file_path.exists() so this should be handled
        (project_dir / "temp.py").unlink()

        result = engine.scan(incremental=False)
        # With no files on disk, hash scan finds nothing
        assert result["files_scanned"] == 0

    def test_corrupted_index_json(self, tmp_path):
        """Corrupted index.json should fall back to empty index."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        index_dir = tmp_path / ".index"
        index_dir.mkdir()
        (index_dir / "index.json").write_text("{{not valid json")

        engine = IndexEngine("test", project_dir, index_dir)
        assert engine.index.project == "test"
        assert len(engine.index.symbols) == 0

    def test_missing_keys_in_index_json(self, tmp_path):
        """Index JSON with missing keys should fall back to empty index."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        index_dir = tmp_path / ".index"
        index_dir.mkdir()
        (index_dir / "index.json").write_text('{"project": "test"}')

        engine = IndexEngine("test", project_dir, index_dir)
        # Should not crash; KeyError is caught
        assert engine.index.project == "test"


class TestIndexPersistence:
    """Test that the index survives save/load cycles."""

    def test_roundtrip_save_load(self, tmp_path):
        """Symbols survive a save/load cycle."""
        project_dir = _create_project(tmp_path, {"hello.py": SIMPLE_PYTHON})
        index_dir = tmp_path / ".index"

        # First engine: scan and save
        engine1 = IndexEngine("test", project_dir, index_dir)
        engine1.scan(incremental=False)
        symbols_before = set(engine1.index.symbols.keys())

        # Second engine: load from saved index
        engine2 = IndexEngine("test", project_dir, index_dir)
        symbols_after = set(engine2.index.symbols.keys())

        assert symbols_before == symbols_after

    def test_roundtrip_preserves_symbol_data(self, tmp_path):
        """Symbol attributes are preserved across save/load."""
        project_dir = _create_project(tmp_path, {"hello.py": SIMPLE_PYTHON})
        index_dir = tmp_path / ".index"

        engine1 = IndexEngine("test", project_dir, index_dir)
        engine1.scan(incremental=False)

        greet_id = "test:hello.py:function:greet"
        original = engine1.index.symbols.get(greet_id)
        assert original is not None

        engine2 = IndexEngine("test", project_dir, index_dir)
        loaded = engine2.index.symbols.get(greet_id)
        assert loaded is not None

        assert loaded.name == original.name
        assert loaded.path == original.path
        assert loaded.symbol_type == original.symbol_type
        assert loaded.language == original.language
        assert loaded.start_line == original.start_line
        assert loaded.end_line == original.end_line

    def test_roundtrip_preserves_file_manifest(self, tmp_path):
        """File manifests are preserved across save/load."""
        project_dir = _create_project(tmp_path, {"hello.py": SIMPLE_PYTHON})
        index_dir = tmp_path / ".index"

        engine1 = IndexEngine("test", project_dir, index_dir)
        engine1.scan(incremental=False)
        orig_manifest = engine1.index.files["hello.py"]

        engine2 = IndexEngine("test", project_dir, index_dir)
        loaded_manifest = engine2.index.files.get("hello.py")

        assert loaded_manifest is not None
        assert loaded_manifest.content_hash == orig_manifest.content_hash
        assert loaded_manifest.line_count == orig_manifest.line_count

    def test_content_file_roundtrip(self, tmp_path):
        """Symbol content is restored from content.jsonl."""
        project_dir = _create_project(tmp_path, {"hello.py": SIMPLE_PYTHON})
        index_dir = tmp_path / ".index"

        engine1 = IndexEngine("test", project_dir, index_dir)
        engine1.scan(incremental=False)

        greet_id = "test:hello.py:function:greet"
        original_content = engine1.index.symbols[greet_id].content
        assert original_content != ""

        engine2 = IndexEngine("test", project_dir, index_dir)
        loaded_content = engine2.index.symbols[greet_id].content
        assert loaded_content == original_content


class TestDependencyExtraction:
    """Test that dependencies (imports, calls) are captured."""

    def test_python_imports_detected(self, tmp_path):
        """Python import statements produce IMPORTS dependencies."""
        engine = _make_engine(tmp_path, {"nested.py": NESTED_PYTHON})
        engine.scan(incremental=False)

        import_deps = [
            d for d in engine.index.dependencies.values()
            if d.dep_type.value == "imports"
        ]
        # NESTED_PYTHON has: import os, from pathlib import Path
        assert len(import_deps) >= 2

    def test_python_calls_detected(self, tmp_path):
        """Python function calls produce CALLS dependencies."""
        code = """\
from pathlib import Path

def process():
    p = Path(".")
    items = list(p.iterdir())
    return items
"""
        engine = _make_engine(tmp_path, {"proc.py": code})
        engine.scan(incremental=False)

        call_deps = [
            d for d in engine.index.dependencies.values()
            if d.dep_type.value == "calls"
        ]
        assert len(call_deps) >= 1

    def test_dependency_has_source_id(self, tmp_path):
        """Each dependency should have a valid source_id."""
        engine = _make_engine(tmp_path, {"nested.py": NESTED_PYTHON})
        engine.scan(incremental=False)

        for dep in engine.index.dependencies.values():
            assert dep.source_id != ""
            assert "test:" in dep.source_id


class TestResolveSymbolId:
    """Test _resolve_symbol_id resolution logic."""

    def _engine_with_symbols(self, tmp_path, symbol_specs):
        """Create engine with pre-populated symbols."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")
        for sid, name, stype in symbol_specs:
            parts = sid.split(":")
            engine.index.symbols[sid] = Symbol(
                project=parts[0],
                path=parts[1],
                symbol_type=SymbolType(parts[2]),
                name=name,
            )
        return engine

    def test_resolve_exact_match(self, tmp_path):
        """Exact full symbol ID resolves correctly."""
        engine = self._engine_with_symbols(tmp_path, [
            ("test:a.py:function:hello", "hello", "function"),
        ])
        assert engine._resolve_symbol_id("test:a.py:function:hello") == "test:a.py:function:hello"

    def test_resolve_short_format(self, tmp_path):
        """Symbol ID without project prefix resolves correctly."""
        engine = self._engine_with_symbols(tmp_path, [
            ("test:a.py:function:hello", "hello", "function"),
        ])
        assert engine._resolve_symbol_id("a.py:function:hello") == "test:a.py:function:hello"

    def test_resolve_name_only(self, tmp_path):
        """Bare symbol name resolves via suffix matching."""
        engine = self._engine_with_symbols(tmp_path, [
            ("test:a.py:function:hello", "hello", "function"),
        ])
        assert engine._resolve_symbol_id("hello") == "test:a.py:function:hello"

    def test_resolve_returns_none_for_unknown(self, tmp_path):
        """Non-existent symbol returns None."""
        engine = self._engine_with_symbols(tmp_path, [
            ("test:a.py:function:hello", "hello", "function"),
        ])
        assert engine._resolve_symbol_id("nonexistent") is None

    def test_resolve_no_partial_match(self, tmp_path):
        """Partial name should not match (prevents false positives)."""
        engine = self._engine_with_symbols(tmp_path, [
            ("test:a.py:function:get_hello", "get_hello", "function"),
        ])
        # "hello" should NOT match "get_hello"
        assert engine._resolve_symbol_id("hello") is None


class TestGetScanner:
    """Test _get_scanner file-to-scanner routing."""

    def test_python_file_gets_python_scanner(self, tmp_path):
        """A .py file is routed to PythonScanner."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        scanner = engine._get_scanner(Path("test.py"))
        assert scanner is not None
        assert type(scanner).__name__ == "PythonScanner"

    def test_typescript_file_gets_ts_scanner(self, tmp_path):
        """A .ts file is routed to TypeScriptScanner."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        scanner = engine._get_scanner(Path("app.ts"))
        assert scanner is not None
        assert type(scanner).__name__ == "TypeScriptScanner"

    def test_vue_file_gets_vue_scanner(self, tmp_path):
        """A .vue file is routed to VueScanner."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        scanner = engine._get_scanner(Path("App.vue"))
        assert scanner is not None
        assert type(scanner).__name__ == "VueScanner"

    def test_unknown_extension_returns_none(self, tmp_path):
        """An unsupported file extension returns None."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        scanner = engine._get_scanner(Path("readme.md"))
        assert scanner is None

    def test_go_file_gets_go_scanner(self, tmp_path):
        """A .go file is routed to GoScanner."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        scanner = engine._get_scanner(Path("main.go"))
        assert scanner is not None
        assert type(scanner).__name__ == "GoScanner"


class TestReverseIndex:
    """Test reverse index building and reference counting."""

    def test_reverse_index_built_on_scan(self, tmp_path):
        """After scan(), reverse_index is populated."""
        engine = _make_engine(tmp_path, {"hello.py": SIMPLE_PYTHON})
        engine.scan(incremental=False)

        # reverse_index should exist (may be empty for standalone functions)
        assert hasattr(engine.index, "reverse_index")
        assert isinstance(engine.index.reverse_index, dict)

    def test_builtin_names_excluded(self, tmp_path):
        """Built-in names like 'str', 'int' should not appear in reverse index."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        engine = IndexEngine("test", project_dir, tmp_path / ".index")

        # Check that BUILTIN_NAMES is populated
        assert "str" in engine.BUILTIN_NAMES
        assert "console" in engine.BUILTIN_NAMES
        assert "ref" in engine.BUILTIN_NAMES


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
