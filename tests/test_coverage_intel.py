"""Tests for coverage intelligence tools."""

import os
import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project structure."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    auth_py = src_dir / "auth.py"
    auth_py.write_text("# auth module\n" * 30)
    return tmp_path


@pytest.fixture
def coverage_sqlite(tmp_project):
    """Create a synthetic .coverage SQLite file (coverage.py v5+ format)."""
    db_path = tmp_project / ".coverage"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT)")
    conn.execute(
        "CREATE TABLE line_bits (file_id INTEGER, context_id INTEGER, numbits BLOB)"
    )
    # Insert a file with absolute path
    conn.execute(
        "INSERT INTO file VALUES (1, ?)",
        (str(tmp_project / "src/auth.py"),),
    )
    # numbits: byte 0xFF = lines 1-8 covered, 0x00 = lines 9-16 not, 0xFF = lines 17-24 covered
    numbits = bytes([0xFF, 0x00, 0xFF])
    conn.execute("INSERT INTO line_bits VALUES (1, 0, ?)", (numbits,))
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def coverage_xml(tmp_project):
    """Create a synthetic coverage.xml (Cobertura format)."""
    xml_content = textwrap.dedent("""\
        <?xml version="1.0" ?>
        <coverage version="5.5" timestamp="1234567890" lines-valid="20" lines-covered="15">
            <packages>
                <package name="src">
                    <classes>
                        <class name="auth.py" filename="src/auth.py" line-rate="0.75">
                            <lines>
                                <line number="1" hits="1"/>
                                <line number="2" hits="1"/>
                                <line number="3" hits="1"/>
                                <line number="5" hits="0"/>
                                <line number="6" hits="0"/>
                                <line number="10" hits="1"/>
                                <line number="15" hits="0"/>
                            </lines>
                        </class>
                    </classes>
                </package>
            </packages>
        </coverage>
    """)
    xml_path = tmp_project / "coverage.xml"
    xml_path.write_text(xml_content)
    return xml_path


@pytest.fixture
def mock_index(tmp_project):
    """Create a mock index with symbols and project roots."""
    return {
        "project_roots": {"test-project": str(tmp_project)},
        "symbols": {
            "test-project:src/auth.py:function:login": {
                "name": "login",
                "type": "function",
                "path": "src/auth.py",
                "project": "test-project",
                "start_line": 1,
                "end_line": 10,
            },
            "test-project:src/auth.py:function:logout": {
                "name": "logout",
                "type": "function",
                "path": "src/auth.py",
                "project": "test-project",
                "start_line": 12,
                "end_line": 24,
            },
        },
        "reverse_index": {
            "login": [
                {"file": "src/main.py", "line": 5},
                {"file": "src/api.py", "line": 10},
                {"file": "src/test_auth.py", "line": 3},
            ],
            "logout": [
                {"file": "src/main.py", "line": 20},
            ],
        },
    }


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParseCoverageSqlite:

    def test_basic_parsing(self, coverage_sqlite, tmp_project):
        from src.tools.coverage_intel import _parse_coverage_sqlite

        result = _parse_coverage_sqlite(str(coverage_sqlite))

        assert len(result) == 1
        # The key should be a relative path
        key = list(result.keys())[0]
        assert "auth.py" in key

        lines = result[key]
        # Lines 1-8 should be covered (byte 0xFF)
        for i in range(1, 9):
            assert i in lines, f"Line {i} should be covered"

        # Lines 9-16 should NOT be covered (byte 0x00)
        for i in range(9, 17):
            assert i not in lines, f"Line {i} should NOT be covered"

        # Lines 17-24 should be covered (byte 0xFF)
        for i in range(17, 25):
            assert i in lines, f"Line {i} should be covered"

    def test_empty_db(self, tmp_path):
        from src.tools.coverage_intel import _parse_coverage_sqlite

        db_path = tmp_path / ".coverage"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT)")
        conn.execute(
            "CREATE TABLE line_bits (file_id INTEGER, context_id INTEGER, numbits BLOB)"
        )
        conn.commit()
        conn.close()

        result = _parse_coverage_sqlite(str(db_path))
        assert result == {}

    def test_missing_tables(self, tmp_path):
        from src.tools.coverage_intel import _parse_coverage_sqlite

        db_path = tmp_path / ".coverage"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.commit()
        conn.close()

        result = _parse_coverage_sqlite(str(db_path))
        assert result == {}


class TestParseCoverageXml:

    def test_basic_parsing(self, coverage_xml):
        from src.tools.coverage_intel import _parse_coverage_xml

        result = _parse_coverage_xml(str(coverage_xml))

        assert "src/auth.py" in result
        lines = result["src/auth.py"]

        # Covered lines (hits > 0)
        assert 1 in lines
        assert 2 in lines
        assert 3 in lines
        assert 10 in lines

        # Uncovered lines (hits == 0)
        assert 5 not in lines
        assert 6 not in lines
        assert 15 not in lines

    def test_malformed_xml(self, tmp_path):
        from src.tools.coverage_intel import _parse_coverage_xml

        bad_xml = tmp_path / "bad.xml"
        bad_xml.write_text("<not valid xml>>>")

        result = _parse_coverage_xml(str(bad_xml))
        assert result == {}

    def test_missing_file(self):
        from src.tools.coverage_intel import _parse_coverage_xml

        result = _parse_coverage_xml("/nonexistent/coverage.xml")
        assert result == {}


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestFindCoverageData:

    def test_finds_sqlite(self, tmp_project, coverage_sqlite):
        from src.tools.coverage_intel import _find_coverage_data

        fmt, path = _find_coverage_data(str(tmp_project))
        assert fmt == "sqlite"
        assert path == str(coverage_sqlite)

    def test_finds_xml(self, tmp_project, coverage_xml):
        from src.tools.coverage_intel import _find_coverage_data

        fmt, path = _find_coverage_data(str(tmp_project))
        assert fmt == "xml"
        assert path == str(coverage_xml)

    def test_sqlite_preferred_over_xml(self, tmp_project, coverage_sqlite, coverage_xml):
        from src.tools.coverage_intel import _find_coverage_data

        fmt, path = _find_coverage_data(str(tmp_project))
        assert fmt == "sqlite"

    def test_no_coverage(self, tmp_path):
        from src.tools.coverage_intel import _find_coverage_data

        fmt, path = _find_coverage_data(str(tmp_path))
        assert fmt == "none"
        assert path == ""


class TestUncoveredRanges:

    def test_contiguous(self):
        from src.tools.coverage_intel import _uncovered_ranges

        total = set(range(1, 11))
        covered = {1, 2, 3, 7, 8, 9, 10}
        result = _uncovered_ranges(total, covered)
        assert result == [[4, 6]]

    def test_multiple_ranges(self):
        from src.tools.coverage_intel import _uncovered_ranges

        total = set(range(1, 21))
        covered = {1, 2, 3, 8, 9, 10, 15, 16}
        result = _uncovered_ranges(total, covered)
        assert [4, 7] in result
        assert [11, 14] in result
        assert [17, 20] in result

    def test_fully_covered(self):
        from src.tools.coverage_intel import _uncovered_ranges

        total = {1, 2, 3}
        covered = {1, 2, 3, 4, 5}
        result = _uncovered_ranges(total, covered)
        assert result == []


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------

class TestCoverageReport:

    @patch("src.tools.coverage_intel.load_index")
    def test_with_sqlite(self, mock_load, tmp_project, coverage_sqlite, mock_index):
        from src.tools.coverage_intel import coverage_report

        mock_load.return_value = mock_index
        result = coverage_report(project="test-project")

        assert "error" not in result
        assert result["project"] == "test-project"
        assert result["data_source"] == "sqlite"
        assert "overall" in result
        assert result["overall"]["total_lines"] > 0
        assert "functions" in result
        assert len(result["functions"]) > 0

    @patch("src.tools.coverage_intel.load_index")
    def test_no_coverage_data(self, mock_load, tmp_path, mock_index):
        from src.tools.coverage_intel import coverage_report

        mock_index["project_roots"] = {"test-project": str(tmp_path)}
        mock_load.return_value = mock_index

        result = coverage_report(project="test-project")
        assert "error" in result
        assert "No coverage data found" in result["error"]

    @patch("src.tools.coverage_intel.load_index")
    def test_min_coverage_filter(self, mock_load, tmp_project, coverage_sqlite, mock_index):
        from src.tools.coverage_intel import coverage_report

        mock_load.return_value = mock_index
        result = coverage_report(project="test-project", min_coverage=0.5)

        assert "error" not in result
        # All returned functions should be below 50%
        for func in result["functions"]:
            assert func["coverage_pct"] < 50.0

    @patch("src.tools.coverage_intel.load_index")
    def test_stale_warning(self, mock_load, tmp_project, coverage_sqlite, mock_index):
        from src.tools.coverage_intel import coverage_report

        # Make the file appear old
        old_time = os.path.getmtime(str(coverage_sqlite)) - (48 * 3600)
        os.utime(str(coverage_sqlite), (old_time, old_time))

        mock_load.return_value = mock_index
        result = coverage_report(project="test-project")

        assert result.get("stale_warning") is not None
        assert "hours old" in result["stale_warning"]


class TestCoverageGaps:

    @patch("src.tools.coverage_intel.load_index")
    def test_gap_scoring(self, mock_load, tmp_project, coverage_sqlite, mock_index):
        from src.tools.coverage_intel import coverage_gaps

        mock_load.return_value = mock_index
        result = coverage_gaps(project="test-project")

        assert "error" not in result
        assert "gaps" in result
        assert "summary" in result

        # Gaps should be sorted by gap_score descending
        if len(result["gaps"]) >= 2:
            for i in range(len(result["gaps"]) - 1):
                assert result["gaps"][i]["gap_score"] >= result["gaps"][i + 1]["gap_score"]

    @patch("src.tools.coverage_intel.load_index")
    def test_gap_score_formula(self, mock_load, tmp_project, coverage_sqlite, mock_index):
        from src.tools.coverage_intel import coverage_gaps

        mock_load.return_value = mock_index
        result = coverage_gaps(project="test-project")

        for gap in result.get("gaps", []):
            # gap_score = (1 - coverage_pct/100) * (1 + ref_count)
            expected = round(
                (1.0 - gap["coverage_pct"] / 100.0) * (1 + gap["reference_count"]),
                2,
            )
            assert gap["gap_score"] == expected, (
                f"Gap score mismatch for {gap['name']}: "
                f"expected {expected}, got {gap['gap_score']}"
            )

    @patch("src.tools.coverage_intel.load_index")
    def test_no_coverage(self, mock_load, tmp_path, mock_index):
        from src.tools.coverage_intel import coverage_gaps

        mock_index["project_roots"] = {"test-project": str(tmp_path)}
        mock_load.return_value = mock_index

        result = coverage_gaps(project="test-project")
        assert "error" in result


class TestUntestedChanges:

    @patch("src.tools.coverage_intel._run_git_diff")
    @patch("src.tools.coverage_intel.load_index")
    def test_with_mock_diff(self, mock_load, mock_diff, tmp_project, coverage_sqlite, mock_index):
        from src.tools.coverage_intel import untested_changes

        mock_load.return_value = mock_index

        # Simulate a diff that changes lines 10-12 in src/auth.py
        mock_diff.return_value = textwrap.dedent("""\
            diff --git a/src/auth.py b/src/auth.py
            --- a/src/auth.py
            +++ b/src/auth.py
            @@ -10,3 +10,3 @@
            +new line 10
            +new line 11
            +new line 12
        """)

        result = untested_changes(project="test-project", mode="unstaged")

        assert "error" not in result
        assert result["mode"] == "unstaged"
        assert "summary" in result
        assert "files" in result
        assert result["summary"]["total_changed_lines"] == 3

    @patch("src.tools.coverage_intel._run_git_diff")
    @patch("src.tools.coverage_intel.load_index")
    def test_no_diff(self, mock_load, mock_diff, tmp_project, coverage_sqlite, mock_index):
        from src.tools.coverage_intel import untested_changes

        mock_load.return_value = mock_index
        mock_diff.return_value = ""

        result = untested_changes(project="test-project")

        assert "error" not in result
        assert result["summary"]["total_changed_lines"] == 0
        assert result["summary"]["change_coverage_pct"] == 100.0

    @patch("src.tools.coverage_intel._run_git_diff")
    @patch("src.tools.coverage_intel.load_index")
    def test_uncovered_changes_detected(self, mock_load, mock_diff, tmp_project, coverage_sqlite, mock_index):
        from src.tools.coverage_intel import untested_changes

        mock_load.return_value = mock_index

        # Lines 9-16 are NOT covered in our fixture (byte 0x00)
        mock_diff.return_value = textwrap.dedent("""\
            diff --git a/src/auth.py b/src/auth.py
            --- a/src/auth.py
            +++ b/src/auth.py
            @@ -9,4 +9,4 @@
            +changed line 9
            +changed line 10
            +changed line 11
            +changed line 12
        """)

        result = untested_changes(project="test-project", mode="unstaged")

        assert "error" not in result
        assert result["summary"]["total_changed_lines"] == 4
        assert result["summary"]["uncovered_changed_lines"] == 4
        assert result["summary"]["change_coverage_pct"] == 0.0


class TestParseDiffLines:

    def test_basic_diff(self):
        from src.tools.coverage_intel import _parse_diff_lines

        diff = textwrap.dedent("""\
            diff --git a/src/auth.py b/src/auth.py
            --- a/src/auth.py
            +++ b/src/auth.py
            @@ -10,0 +10,3 @@
            +line1
            +line2
            +line3
        """)

        result = _parse_diff_lines(diff)
        assert "src/auth.py" in result
        assert result["src/auth.py"] == [10, 11, 12]

    def test_multiple_hunks(self):
        from src.tools.coverage_intel import _parse_diff_lines

        diff = textwrap.dedent("""\
            diff --git a/src/auth.py b/src/auth.py
            --- a/src/auth.py
            +++ b/src/auth.py
            @@ -5,0 +5,2 @@
            +a
            +b
            @@ -20,0 +22,1 @@
            +c
        """)

        result = _parse_diff_lines(diff)
        assert "src/auth.py" in result
        assert 5 in result["src/auth.py"]
        assert 6 in result["src/auth.py"]
        assert 22 in result["src/auth.py"]

    def test_deleted_file(self):
        from src.tools.coverage_intel import _parse_diff_lines

        diff = textwrap.dedent("""\
            diff --git a/src/old.py b/src/old.py
            --- a/src/old.py
            +++ /dev/null
            @@ -1,5 +0,0 @@
            -line1
        """)

        result = _parse_diff_lines(diff)
        # Deleted files should not appear (file is /dev/null)
        assert "src/old.py" not in result
