"""Tests for artifact-backed changed-symbol to test impact evidence."""

import os
import sqlite3
import textwrap

from src.test_evidence import (
    artifact_provenance,
    build_test_impact_evidence,
    parse_lcov,
)


def _context_coverage(project_root):
    db_path = project_root / ".coverage"
    connection = sqlite3.connect(str(db_path))
    connection.execute("CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT)")
    connection.execute(
        "CREATE TABLE context (id INTEGER PRIMARY KEY, context TEXT)"
    )
    connection.execute(
        "CREATE TABLE line_bits (file_id INTEGER, context_id INTEGER, numbits BLOB)"
    )
    connection.execute(
        "INSERT INTO file VALUES (1, ?)",
        (str(project_root / "src/auth.py"),),
    )
    connection.execute(
        "INSERT INTO context VALUES (1, ?)",
        ("tests/test_auth.py::test_login|run",),
    )
    # Line 10 is byte index 1, bit index 1.
    connection.execute(
        "INSERT INTO line_bits VALUES (1, 1, ?)",
        (bytes([0x00, 0x02]),),
    )
    connection.commit()
    connection.close()
    return db_path


def _junit(project_root):
    junit_path = project_root / "junit.xml"
    junit_path.write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0"?>
            <testsuite tests="1">
              <testcase classname="tests.test_auth" name="test_login" time="0.125"/>
            </testsuite>
            """
        ),
        encoding="utf-8",
    )
    return junit_path


def test_context_and_junit_map_changed_symbol_to_precise_test(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/auth.py").write_text("pass\n" * 20, encoding="utf-8")
    coverage_path = _context_coverage(tmp_path)
    _junit(tmp_path)

    evidence = build_test_impact_evidence(
        str(tmp_path),
        "sqlite",
        str(coverage_path),
        {"src/auth.py": [10]},
        [{
            "symbol_id": "demo:src/auth.py:function:login",
            "path": "src/auth.py",
            "line_range": [5, 15],
        }],
    )

    assert evidence["schema"] == "test-impact-evidence.v1"
    assert evidence["status"] == "ready"
    assert evidence["confidence"] == "high"
    assert evidence["context_covered_changed_lines"] == 1
    assert evidence["impacted_tests"] == [{
        "node_id": "tests/test_auth.py::test_login",
        "status": "passed",
        "duration_ms": 125.0,
        "changed_files": ["src/auth.py"],
        "changed_lines": 1,
        "changed_symbol_ids": ["demo:src/auth.py:function:login"],
        "evidence": "coverage_context+junit",
    }]
    assert len(evidence["coverage_artifact"]["sha256"]) == 64
    assert len(evidence["fingerprint"]) == 64


def test_stale_coverage_downgrades_confidence(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/auth.py").write_text("pass\n" * 20, encoding="utf-8")
    coverage_path = _context_coverage(tmp_path)
    old_timestamp = coverage_path.stat().st_mtime - 48 * 3600
    os.utime(coverage_path, (old_timestamp, old_timestamp))

    evidence = build_test_impact_evidence(
        str(tmp_path),
        "sqlite",
        str(coverage_path),
        {"src/auth.py": [10]},
    )

    assert evidence["status"] == "stale"
    assert evidence["confidence"] == "low"
    assert evidence["coverage_artifact"]["fresh"] is False


def test_branch_coverage_arcs_map_back_to_line_contexts(tmp_path):
    from src.test_evidence import parse_sqlite_contexts

    db_path = tmp_path / ".coverage"
    connection = sqlite3.connect(str(db_path))
    connection.execute("CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT)")
    connection.execute(
        "CREATE TABLE context (id INTEGER PRIMARY KEY, context TEXT)"
    )
    connection.execute(
        "CREATE TABLE arc "
        "(file_id INTEGER, context_id INTEGER, fromno INTEGER, tono INTEGER)"
    )
    connection.execute(
        "INSERT INTO file VALUES (1, ?)",
        (str(tmp_path / "src/auth.py"),),
    )
    connection.execute(
        "INSERT INTO context VALUES (1, ?)",
        ("tests/test_auth.py::test_login|run",),
    )
    connection.execute("INSERT INTO arc VALUES (1, 1, -10, 10)")
    connection.execute("INSERT INTO arc VALUES (1, 1, 10, 11)")
    connection.execute("INSERT INTO arc VALUES (1, 1, 11, -10)")
    connection.commit()
    connection.close()

    contexts = parse_sqlite_contexts(str(db_path), str(tmp_path))
    targeted = parse_sqlite_contexts(
        str(db_path),
        str(tmp_path),
        target_files=["src/auth.py"],
        target_lines={"src/auth.py": [11]},
    )

    assert contexts == {
        "src/auth.py": {
            10: {"tests/test_auth.py::test_login"},
            11: {"tests/test_auth.py::test_login"},
        }
    }
    assert targeted == {
        "src/auth.py": {11: {"tests/test_auth.py::test_login"}}
    }


def test_missing_sqlite_context_artifact_is_read_only(tmp_path):
    from src.test_evidence import parse_sqlite_contexts

    missing = tmp_path / ".coverage"

    assert parse_sqlite_contexts(str(missing), str(tmp_path)) == {}
    assert not missing.exists()


def test_lcov_tn_context_is_preserved(tmp_path):
    lcov_path = tmp_path / "lcov.info"
    lcov_path.write_text(
        textwrap.dedent(
            """\
            TN:tests/test_api.py::test_request
            SF:src/api.py
            DA:7,1
            DA:8,0
            end_of_record
            """
        ),
        encoding="utf-8",
    )

    covered, contexts = parse_lcov(str(lcov_path), str(tmp_path))

    assert covered == {"src/api.py": {7}}
    assert contexts == {
        "src/api.py": {7: {"tests/test_api.py::test_request"}}
    }


def test_artifact_provenance_is_content_addressed(tmp_path):
    artifact = tmp_path / "coverage.json"
    artifact.write_text('{"files": {}}', encoding="utf-8")

    first = artifact_provenance(
        str(artifact), str(tmp_path), "coverage.json"
    )
    artifact.write_text('{"files": {"src/a.py": {}}}', encoding="utf-8")
    second = artifact_provenance(
        str(artifact), str(tmp_path), "coverage.json"
    )

    assert first["sha256"] != second["sha256"]
    assert first["path"] == "coverage.json"
