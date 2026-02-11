"""Tests for analyzer/stale_files module."""

import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from analyzer.stale_files import StaleFile, StaleReport, StaleFileDetector


class TestStaleFile:
    """Test StaleFile dataclass."""

    def test_creation(self):
        sf = StaleFile(
            path="src/old.py",
            last_modified=datetime(2025, 1, 1),
            last_author="alice",
            days_since_modified=400,
            commit_count=5,
        )
        assert sf.path == "src/old.py"
        assert sf.last_author == "alice"
        assert sf.days_since_modified == 400
        assert sf.commit_count == 5


class TestStaleReport:
    """Test StaleReport dataclass."""

    def test_default_values(self):
        report = StaleReport()
        assert report.total_files == 0
        assert report.stale_files == []
        assert report.stale_dirs == []
        assert report.never_committed == []


class TestStaleFileDetectorInit:
    """Test StaleFileDetector initialization."""

    def test_default_stale_days(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = StaleFileDetector(Path(tmpdir))
            assert detector.stale_days == 180

    def test_custom_stale_days(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = StaleFileDetector(Path(tmpdir), stale_days=90)
            assert detector.stale_days == 90

    def test_default_extensions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = StaleFileDetector(Path(tmpdir))
            assert ".py" in detector.extensions
            assert ".ts" in detector.extensions


class TestStaleFileDetectorSkip:
    """Test file skipping logic."""

    def test_skip_node_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = StaleFileDetector(Path(tmpdir))
            assert detector._should_skip("node_modules/foo.js") is True

    def test_skip_git(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = StaleFileDetector(Path(tmpdir))
            assert detector._should_skip(".git/objects/abc") is True

    def test_no_skip_src(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = StaleFileDetector(Path(tmpdir))
            assert detector._should_skip("src/main.py") is False


class TestStaleFileDetectorScanDirectory:
    """Test directory scanning."""

    def test_finds_python_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.py").write_text("pass")
            (root / "utils.py").write_text("pass")
            (root / "readme.md").write_text("# readme")
            detector = StaleFileDetector(root)
            files = detector.scan_directory()
            assert "main.py" in files
            assert "utils.py" in files
            # .md is not in default extensions
            assert "readme.md" not in files

    def test_skips_ignored_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nm = root / "node_modules"
            nm.mkdir()
            (nm / "pkg.js").write_text("pass")
            (root / "main.py").write_text("pass")
            detector = StaleFileDetector(root)
            files = detector.scan_directory()
            assert "main.py" in files
            assert not any("node_modules" in f for f in files)

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = StaleFileDetector(Path(tmpdir))
            files = detector.scan_directory()
            assert files == []


class TestStaleFileDetectorGetFileHistory:
    """Test git history retrieval (without real git repo)."""

    def test_no_git_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = StaleFileDetector(Path(tmpdir))
            last_mod, author, count = detector.get_file_history("nonexistent.py")
            assert last_mod is None
            assert author == ""
            assert count == 0
