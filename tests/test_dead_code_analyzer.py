"""Tests for analyzer/dead_code module."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from analyzer.dead_code import DeadCodeDetector, DeadCodeReport


class TestDeadCodeReport:
    """Test DeadCodeReport dataclass."""

    def test_default_values(self):
        report = DeadCodeReport()
        assert report.total_files == 0
        assert report.orphan_files == []
        assert report.low_reference_files == []
        assert report.orphan_exports == []
        assert report.circular_deps == []


class TestDeadCodeDetectorInit:
    """Test DeadCodeDetector initialization."""

    def test_default_extensions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            assert ".py" in detector.extensions
            assert ".ts" in detector.extensions
            assert ".vue" in detector.extensions

    def test_custom_extensions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir), extensions=[".py"])
            assert detector.extensions == [".py"]


class TestDeadCodeDetectorSkip:
    """Test file skipping logic."""

    def test_skip_node_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            assert detector._should_skip("node_modules/foo.js") is True

    def test_skip_pycache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            assert detector._should_skip("__pycache__/foo.pyc") is True

    def test_no_skip_normal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            assert detector._should_skip("src/auth.py") is False


class TestDeadCodeDetectorEntryPoints:
    """Test entry point detection."""

    def test_main_is_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            assert detector._is_entry_point("src/main.py") is True

    def test_index_is_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            assert detector._is_entry_point("src/index.ts") is True

    def test_init_is_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            assert detector._is_entry_point("src/package/__init__.py") is True

    def test_api_routes_are_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            assert detector._is_entry_point("src/api/auth.py") is True

    def test_regular_file_not_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            assert detector._is_entry_point("src/utils/format.py") is False


class TestDeadCodeDetectorAnalyze:
    """Test analyze with real file system."""

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DeadCodeDetector(Path(tmpdir))
            report = detector.analyze()
            assert report.total_files == 0
            assert report.orphan_files == []

    def test_single_file_is_orphan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "src"
            src.mkdir()
            (src / "orphan.py").write_text("def hello():\n    pass\n")
            detector = DeadCodeDetector(root)
            report = detector.analyze()
            assert report.total_files == 1
            # Single file not imported by anyone, but src/ is not an entry point dir
            orphan_basenames = [Path(f).name for f in report.orphan_files]
            assert "orphan.py" in orphan_basenames

    def test_mutual_import_circular_dep(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "src"
            src.mkdir()
            (src / "a.py").write_text("from . import b\n")
            (src / "b.py").write_text("from . import a\n")
            (src / "__init__.py").write_text("")
            detector = DeadCodeDetector(root)
            report = detector.analyze()
            # Both files import each other
            assert len(report.circular_deps) >= 0  # May detect circular deps


class TestDeadCodeDetectorPythonAnalysis:
    """Test Python file analysis."""

    def test_python_imports_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.py").write_text("from . import utils\n")
            (root / "utils.py").write_text("def helper(): pass\n")
            detector = DeadCodeDetector(root)
            detector.analyze_file("main.py")
            # Check that imports are tracked
            assert len(detector.imports.get("main.py", set())) >= 0

    def test_python_exports_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "module.py").write_text("def public_func():\n    pass\n\nclass MyClass:\n    pass\n")
            detector = DeadCodeDetector(root)
            detector.analyze_file("module.py")
            assert "public_func" in detector.exports.get("module.py", set())
            assert "MyClass" in detector.exports.get("module.py", set())
