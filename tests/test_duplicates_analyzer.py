"""Tests for analyzer/duplicates module."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from analyzer.duplicates import DuplicateBlock, DuplicateReport, DuplicateDetector


class TestDuplicateBlock:
    """Test DuplicateBlock dataclass."""

    def test_creation(self):
        block = DuplicateBlock(
            file1="a.py", start1=1, end1=10,
            file2="b.py", start2=20, end2=29,
            lines=10, similarity=1.0,
            code_preview="x = 1",
        )
        assert block.file1 == "a.py"
        assert block.lines == 10
        assert block.similarity == 1.0


class TestDuplicateReport:
    """Test DuplicateReport dataclass."""

    def test_defaults(self):
        report = DuplicateReport()
        assert report.total_files == 0
        assert report.total_lines == 0
        assert report.duplicate_blocks == []
        assert report.duplicate_lines == 0

    def test_duplicate_rate_zero(self):
        report = DuplicateReport()
        assert report.duplicate_rate == 0

    def test_duplicate_rate_nonzero(self):
        report = DuplicateReport(total_lines=100, duplicate_lines=25)
        assert report.duplicate_rate == 25.0


class TestDuplicateDetectorInit:
    """Test DuplicateDetector initialization."""

    def test_default_min_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir))
            assert detector.min_lines == 6

    def test_custom_min_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir), min_lines=3)
            assert detector.min_lines == 3


class TestDuplicateDetectorNormalize:
    """Test line normalization."""

    def test_normalize_strips_whitespace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir))
            result = detector._normalize_line("   hello world   ")
            assert result == "hello world"

    def test_normalize_removes_comment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir))
            assert detector._normalize_line("# this is a comment") == ""
            assert detector._normalize_line("// this is a comment") == ""

    def test_normalize_removes_trailing_comment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir))
            result = detector._normalize_line("x = 1 # inline comment")
            assert result == "x = 1"

    def test_normalize_collapses_whitespace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir))
            result = detector._normalize_line("a   =   b   +   c")
            assert result == "a = b + c"


class TestDuplicateDetectorExtractChunks:
    """Test chunk extraction."""

    def test_extract_chunks_from_short_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir), min_lines=3)
            content = "a = 1\nb = 2\nc = 3\nd = 4\n"
            chunks = detector._extract_chunks("test.py", content)
            assert len(chunks) >= 1

    def test_extract_chunks_skips_empty_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir), min_lines=3)
            content = "a = 1\n\n\nb = 2\n\nc = 3\nd = 4\n"
            chunks = detector._extract_chunks("test.py", content)
            # Empty lines (after normalization) are skipped
            for start, hash_val, lines in chunks:
                for line in lines:
                    assert line.strip() != ""

    def test_too_short_file_no_chunks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir), min_lines=6)
            content = "a = 1\nb = 2\n"
            chunks = detector._extract_chunks("test.py", content)
            assert len(chunks) == 0


class TestDuplicateDetectorAnalyze:
    """Test duplicate detection analysis."""

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir))
            report = detector.analyze()
            assert report.total_files == 0
            assert report.duplicate_blocks == []

    def test_identical_files_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            code = "\n".join([f"line_{i} = {i}" for i in range(10)]) + "\n"
            (root / "a.py").write_text(code)
            (root / "b.py").write_text(code)
            detector = DuplicateDetector(root, min_lines=6)
            report = detector.analyze()
            assert report.total_files == 2
            assert len(report.duplicate_blocks) >= 1

    def test_no_duplicates_in_unique_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a.py").write_text("\n".join([f"unique_a_{i} = {i}" for i in range(10)]))
            (root / "b.py").write_text("\n".join([f"unique_b_{i} = {i}" for i in range(10)]))
            detector = DuplicateDetector(root, min_lines=6)
            report = detector.analyze()
            assert len(report.duplicate_blocks) == 0


class TestDuplicateDetectorMerge:
    """Test adjacent block merging."""

    def test_merge_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir))
            result = detector._merge_adjacent([])
            assert result == []

    def test_merge_single(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir))
            dups = [{"file1": "a.py", "start1": 1, "end1": 6,
                      "file2": "b.py", "start2": 1, "end2": 6, "lines": []}]
            result = detector._merge_adjacent(dups)
            assert len(result) == 1

    def test_merge_adjacent_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = DuplicateDetector(Path(tmpdir))
            dups = [
                {"file1": "a.py", "start1": 1, "end1": 6,
                 "file2": "b.py", "start2": 1, "end2": 6, "lines": ["x"]},
                {"file1": "a.py", "start1": 7, "end1": 12,
                 "file2": "b.py", "start2": 7, "end2": 12, "lines": ["y"]},
            ]
            result = detector._merge_adjacent(dups)
            assert len(result) == 1
            assert result[0]["end1"] == 12
