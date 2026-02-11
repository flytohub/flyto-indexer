"""Tests for scanner base classes."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import Symbol, Dependency, FileManifest, SymbolType, DependencyType
from scanner.base import BaseScanner, ScanResult


class ConcreteScanner(BaseScanner):
    """Concrete implementation of BaseScanner for testing."""
    supported_extensions = [".py", ".txt"]

    def scan_file(self, file_path, content):
        return [], []


class TestBaseScannerInit:
    """Test BaseScanner initialization."""

    def test_init_stores_project_name(self):
        scanner = ConcreteScanner("my-project")
        assert scanner.project == "my-project"

    def test_init_empty_project_name(self):
        scanner = ConcreteScanner("")
        assert scanner.project == ""


class TestBaseScannerCanScan:
    """Test can_scan method."""

    def test_can_scan_supported_extension(self):
        scanner = ConcreteScanner("proj")
        assert scanner.can_scan(Path("foo.py")) is True
        assert scanner.can_scan(Path("bar.txt")) is True

    def test_can_scan_unsupported_extension(self):
        scanner = ConcreteScanner("proj")
        assert scanner.can_scan(Path("foo.rs")) is False
        assert scanner.can_scan(Path("bar.java")) is False

    def test_can_scan_no_extension(self):
        scanner = ConcreteScanner("proj")
        assert scanner.can_scan(Path("Makefile")) is False


class TestBaseScannerHash:
    """Test compute_file_hash method."""

    def test_hash_deterministic(self):
        scanner = ConcreteScanner("proj")
        h1 = scanner.compute_file_hash("hello world")
        h2 = scanner.compute_file_hash("hello world")
        assert h1 == h2

    def test_hash_different_for_different_content(self):
        scanner = ConcreteScanner("proj")
        h1 = scanner.compute_file_hash("hello world")
        h2 = scanner.compute_file_hash("hello worlds")
        assert h1 != h2

    def test_hash_length(self):
        scanner = ConcreteScanner("proj")
        h = scanner.compute_file_hash("test content")
        assert len(h) == 16


class TestBaseScannerFileManifest:
    """Test create_file_manifest method."""

    def test_manifest_creation(self):
        scanner = ConcreteScanner("proj")
        symbols = [
            Symbol(
                project="proj",
                path="test.py",
                symbol_type=SymbolType.FUNCTION,
                name="foo",
                content="def foo(): pass",
            )
        ]
        manifest = scanner.create_file_manifest(
            Path("test.py"),
            "def foo(): pass\n",
            symbols,
        )
        assert manifest.path == "test.py"
        assert manifest.line_count == 1
        assert len(manifest.symbols) == 1
        assert manifest.content_hash == scanner.compute_file_hash("def foo(): pass\n")

    def test_manifest_empty_symbols(self):
        scanner = ConcreteScanner("proj")
        manifest = scanner.create_file_manifest(Path("empty.py"), "", [])
        assert manifest.line_count == 0
        assert manifest.symbols == []


class TestBaseScannerExtractImports:
    """Test default extract_imports."""

    def test_default_returns_empty(self):
        scanner = ConcreteScanner("proj")
        assert scanner.extract_imports("import os") == []


class TestScanResult:
    """Test ScanResult container."""

    def test_empty_scan_result(self):
        result = ScanResult()
        assert result.symbols == []
        assert result.dependencies == []
        assert result.manifests == []
        assert result.errors == []

    def test_add_file_result(self):
        result = ScanResult()
        sym = Symbol(
            project="proj", path="a.py",
            symbol_type=SymbolType.FUNCTION, name="f",
        )
        dep = Dependency(
            source_id="a", target_id="b",
            dep_type=DependencyType.IMPORTS,
        )
        manifest = FileManifest(path="a.py", content_hash="abc", line_count=10)
        result.add_file_result([sym], [dep], manifest)
        assert len(result.symbols) == 1
        assert len(result.dependencies) == 1
        assert len(result.manifests) == 1

    def test_add_error(self):
        result = ScanResult()
        result.add_error("bad.py", "syntax error")
        assert len(result.errors) == 1
        assert result.errors[0]["file"] == "bad.py"
        assert result.errors[0]["error"] == "syntax error"

    def test_summary(self):
        result = ScanResult()
        manifest = FileManifest(path="a.py", content_hash="abc", line_count=10)
        result.add_file_result([], [], manifest)
        result.add_error("bad.py", "error")
        s = result.summary()
        assert s["files_scanned"] == 1
        assert s["symbols_found"] == 0
        assert s["errors"] == 1
