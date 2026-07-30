"""Coverage-context and JUnit evidence for changed-symbol test impact.

The adapter is intentionally dependency-free.  It consumes artifacts that test
runners already emit and never starts a runner, downloads an index, or guesses
that a test covers code when no execution context proves it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

try:
    from .safe_xml import UnsafeXMLError, safe_parse_xml
except ImportError:
    from safe_xml import UnsafeXMLError, safe_parse_xml


TEST_IMPACT_SCHEMA = "test-impact-evidence.v1"
MAX_JUNIT_ARTIFACTS = 20
MAX_IMPACTED_TESTS = 100
DEFAULT_MAX_AGE_HOURS = 24.0


def _relative_path(path: str, project_root: str) -> str:
    """Normalize an artifact path relative to the selected project."""
    candidate = Path(path)
    root = Path(project_root).resolve()
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(root)
        except (OSError, ValueError):
            return candidate.as_posix()
    return candidate.as_posix().lstrip("./")


def _paths_match(left: str, right: str) -> bool:
    """Match equivalent absolute, relative, and suffix-normalized paths."""
    left_norm = left.replace("\\", "/").lstrip("./")
    right_norm = right.replace("\\", "/").lstrip("./")
    return (
        left_norm == right_norm
        or left_norm.endswith("/" + right_norm)
        or right_norm.endswith("/" + left_norm)
    )


def _sha256_file(path: Path) -> str:
    """Hash an artifact in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_provenance(
    path: str,
    project_root: str,
    kind: str,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> dict:
    """Return content-addressed freshness evidence for a local artifact."""
    candidate = Path(path)
    try:
        stat = candidate.stat()
        age_hours = max(0.0, (time.time() - stat.st_mtime) / 3600.0)
        return {
            "kind": kind,
            "path": _relative_path(str(candidate), project_root),
            "sha256": _sha256_file(candidate),
            "size_bytes": stat.st_size,
            "modified_at_unix": round(stat.st_mtime, 3),
            "age_hours": round(age_hours, 3),
            "max_age_hours": max_age_hours,
            "fresh": age_hours <= max_age_hours,
        }
    except OSError as exc:
        return {
            "kind": kind,
            "path": _relative_path(str(candidate), project_root),
            "fresh": False,
            "error": type(exc).__name__,
        }


def _decode_numbits(numbits: bytes) -> Set[int]:
    """Decode coverage.py's compact executed-line bitmap."""
    lines: Set[int] = set()
    for byte_index, byte_value in enumerate(numbits or b""):
        for bit in range(8):
            if byte_value & (1 << bit):
                lines.add(byte_index * 8 + bit + 1)
    return lines


def _normalise_context(context: str) -> str:
    """Normalise coverage.py dynamic contexts to a stable test node id."""
    return context.split("|", 1)[0].strip()


def parse_sqlite_contexts(
    db_path: str,
    project_root: str,
    target_files: Iterable[str] = (),
    target_lines: Optional[Mapping[str, Iterable[int]]] = None,
) -> Dict[str, Dict[int, Set[str]]]:
    """Parse coverage.py line_bits joined to dynamic test contexts."""
    result: Dict[str, Dict[int, Set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    requested_line_sets = {
        target: set(lines)
        for target, lines in (target_lines or {}).items()
    }
    line_rows = []
    arc_rows = []
    try:
        with sqlite3.connect(db_path) as connection:
            selected_ids: list[int] = []
            requested_files = list(target_files) or list(requested_line_sets)
            if requested_files:
                file_rows = connection.execute(
                    "SELECT id, path FROM file"
                ).fetchall()
                selected_ids = [
                    file_id
                    for file_id, raw_path in file_rows
                    if any(
                        _paths_match(
                            _relative_path(raw_path, project_root),
                            target,
                        )
                        for target in requested_files
                    )
                ]
                if not selected_ids:
                    return {}
            where_clause = ""
            parameters: list[int] = []
            if selected_ids:
                placeholders = ",".join("?" for _ in selected_ids)
                where_clause = " WHERE line_bits.file_id IN ({})".format(
                    placeholders
                )
                parameters = selected_ids
            try:
                line_rows = connection.execute(
                    """
                    SELECT file.path, context.context, line_bits.numbits
                    FROM line_bits
                    JOIN file ON file.id = line_bits.file_id
                    JOIN context ON context.id = line_bits.context_id
                    """ + where_clause,
                    parameters,
                ).fetchall()
            except sqlite3.OperationalError:
                pass
            if selected_ids:
                where_clause = " WHERE arc.file_id IN ({})".format(
                    ",".join("?" for _ in selected_ids)
                )
            requested_line_values = sorted({
                line
                for lines in requested_line_sets.values()
                for line in lines
                if isinstance(line, int) and line > 0
            })
            # Bound the SQL expression. Larger requests remain correct through
            # the Python filter below, while ordinary diffs avoid scanning all
            # branch arcs for the selected files.
            if requested_line_values and len(requested_line_values) <= 400:
                prefix = " AND " if where_clause else " WHERE "
                placeholders = ",".join("?" for _ in requested_line_values)
                where_clause += (
                    prefix
                    + "(arc.fromno IN ({0}) OR arc.tono IN ({0}))".format(
                        placeholders
                    )
                )
                parameters = [
                    *parameters,
                    *requested_line_values,
                    *requested_line_values,
                ]
            try:
                arc_rows = connection.execute(
                    """
                    SELECT file.path, context.context, arc.fromno, arc.tono
                    FROM arc
                    JOIN file ON file.id = arc.file_id
                    JOIN context ON context.id = arc.context_id
                    """ + where_clause,
                    parameters,
                ).fetchall()
            except sqlite3.OperationalError:
                pass
    except (sqlite3.Error, OSError):
        return {}

    for raw_path, raw_context, numbits in line_rows:
        context = _normalise_context(raw_context or "")
        if not context:
            continue
        file_path = _relative_path(raw_path, project_root)
        requested_lines = next(
            (
                lines
                for target, lines in requested_line_sets.items()
                if _paths_match(file_path, target)
            ),
            None,
        )
        for line in _decode_numbits(numbits):
            if requested_lines is not None and line not in requested_lines:
                continue
            result[file_path][line].add(context)
    # Branch coverage stores arcs instead of line bitmaps. Negative endpoints
    # represent function entry/exit sentinels; positive endpoints are executed
    # source lines and can be projected back to line evidence losslessly.
    for raw_path, raw_context, from_line, to_line in arc_rows:
        context = _normalise_context(raw_context or "")
        if not context:
            continue
        file_path = _relative_path(raw_path, project_root)
        requested_lines = next(
            (
                lines
                for target, lines in requested_line_sets.items()
                if _paths_match(file_path, target)
            ),
            None,
        )
        for line in (from_line, to_line):
            if isinstance(line, int) and line > 0:
                if requested_lines is not None and line not in requested_lines:
                    continue
                result[file_path][line].add(context)
    return {
        path: {line: set(contexts) for line, contexts in lines.items()}
        for path, lines in result.items()
    }


def parse_lcov(
    lcov_path: str,
    project_root: str,
) -> Tuple[Dict[str, Set[int]], Dict[str, Dict[int, Set[str]]]]:
    """Parse LCOV lines and optional TN (test name) execution contexts."""
    covered: Dict[str, Set[int]] = defaultdict(set)
    contexts: Dict[str, Dict[int, Set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    current_test = ""
    current_file = ""
    try:
        with open(lcov_path, "r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\n")
                if line.startswith("TN:"):
                    current_test = _normalise_context(line[3:])
                elif line.startswith("SF:"):
                    current_file = _relative_path(line[3:].strip(), project_root)
                elif line.startswith("DA:") and current_file:
                    fields = line[3:].split(",")
                    try:
                        line_number = int(fields[0])
                        hit_count = int(fields[1])
                    except (IndexError, ValueError):
                        continue
                    if line_number <= 0 or hit_count <= 0:
                        continue
                    covered[current_file].add(line_number)
                    if current_test:
                        contexts[current_file][line_number].add(current_test)
                elif line == "end_of_record":
                    current_file = ""
    except OSError:
        return {}, {}

    return (
        {path: set(lines) for path, lines in covered.items()},
        {
            path: {line: set(names) for line, names in line_map.items()}
            for path, line_map in contexts.items()
        },
    )


def find_junit_artifacts(project_root: str) -> List[str]:
    """Find common JUnit XML outputs without recursively scanning the project."""
    root = Path(project_root).resolve()
    patterns = (
        "junit.xml",
        "test-results.xml",
        "reports/junit.xml",
        "test-results/*.xml",
        "test-results/**/*.xml",
        "build/test-results/**/*.xml",
        "target/surefire-reports/*.xml",
    )
    found: Set[Path] = set()
    for pattern in patterns:
        try:
            candidates = root.glob(pattern)
            for candidate in candidates:
                resolved = candidate.resolve()
                if resolved.is_file() and root in resolved.parents:
                    found.add(resolved)
                    if len(found) >= MAX_JUNIT_ARTIFACTS:
                        return [str(path) for path in sorted(found)]
        except OSError:
            continue
    return [str(path) for path in sorted(found)]


def _pytest_node_id(classname: str, name: str, file_path: str) -> str:
    """Derive a stable pytest-like node identifier from JUnit metadata."""
    if file_path:
        return "{}::{}".format(file_path.replace("\\", "/"), name)
    if classname:
        components = classname.split(".")
        test_index = next(
            (index for index, value in enumerate(components) if value == "tests"),
            -1,
        )
        if test_index >= 0 and len(components) > test_index + 1:
            module = "/".join(components[test_index:])
            return "{}.py::{}".format(module, name)
        return "{}::{}".format(classname, name)
    return name


def parse_junit(path: str, project_root: str) -> List[dict]:
    """Parse bounded testcase metadata from a JUnit XML artifact."""
    cases: List[dict] = []
    try:
        root = safe_parse_xml(path).getroot()
        for testcase in root.iter("testcase"):
            name = testcase.get("name", "").strip()
            classname = testcase.get("classname", "").strip()
            file_path = _relative_path(testcase.get("file", ""), project_root)
            status = "passed"
            if testcase.find("failure") is not None:
                status = "failed"
            elif testcase.find("error") is not None:
                status = "error"
            elif testcase.find("skipped") is not None:
                status = "skipped"
            try:
                duration_ms = round(float(testcase.get("time", "0")) * 1000, 3)
            except ValueError:
                duration_ms = 0.0
            node_id = _pytest_node_id(classname, name, file_path)
            cases.append({
                "node_id": node_id,
                "name": name,
                "classname": classname,
                "file": file_path,
                "status": status,
                "duration_ms": duration_ms,
            })
    except (ET.ParseError, OSError, UnsafeXMLError):
        return []
    return cases


def _case_aliases(case: Mapping[str, object]) -> Set[str]:
    """Return aliases used to join coverage contexts to JUnit cases."""
    name = str(case.get("name", ""))
    classname = str(case.get("classname", ""))
    file_path = str(case.get("file", ""))
    aliases = {str(case.get("node_id", "")), name}
    if classname:
        aliases.add("{}::{}".format(classname, name))
    if file_path:
        aliases.add("{}::{}".format(file_path, name))
    return {alias for alias in aliases if alias}


def _matching_context_map(
    changed_files: Mapping[str, Iterable[int]],
    line_contexts: Mapping[str, Mapping[int, Set[str]]],
) -> Tuple[Dict[str, Dict[str, Set[int]]], int]:
    """Join changed lines to the execution contexts that covered them."""
    matched: Dict[str, Dict[str, Set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    context_covered_lines = 0
    for changed_path, changed_lines in changed_files.items():
        line_map = next(
            (
                contexts
                for context_path, contexts in line_contexts.items()
                if _paths_match(changed_path, context_path)
            ),
            {},
        )
        for line in set(changed_lines):
            contexts = line_map.get(line, set())
            if contexts:
                context_covered_lines += 1
            for context in contexts:
                matched[context][changed_path].add(line)
    return matched, context_covered_lines


def _symbols_for_test(
    paths_to_lines: Mapping[str, Set[int]],
    changed_symbols: Iterable[Mapping[str, object]],
) -> List[str]:
    """Map one test's covered changed lines to changed symbol identities."""
    symbol_ids: Set[str] = set()
    for symbol in changed_symbols:
        symbol_path = str(symbol.get("path", ""))
        line_range = symbol.get("line_range", [])
        if not symbol_path or not isinstance(line_range, (list, tuple)):
            continue
        if len(line_range) != 2:
            continue
        for changed_path, lines in paths_to_lines.items():
            if not _paths_match(changed_path, symbol_path):
                continue
            if any(int(line_range[0]) <= line <= int(line_range[1]) for line in lines):
                symbol_id = str(symbol.get("symbol_id", ""))
                if symbol_id:
                    symbol_ids.add(symbol_id)
    return sorted(symbol_ids)


def _evidence_fingerprint(payload: Mapping[str, object]) -> str:
    """Hash normalized test-impact evidence for reproducible comparison."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_test_impact_evidence(
    project_root: str,
    coverage_format: str,
    coverage_path: str,
    changed_files: Mapping[str, Iterable[int]],
    changed_symbols: Iterable[Mapping[str, object]] = (),
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> dict:
    """Correlate changed lines with executed-by-test contexts and JUnit status."""
    changed_files = {
        path: sorted(set(lines))
        for path, lines in changed_files.items()
    }
    changed_line_count = sum(len(lines) for lines in changed_files.values())
    if not coverage_path or coverage_format == "none":
        return {
            "schema": TEST_IMPACT_SCHEMA,
            "status": "unavailable",
            "confidence": "none",
            "reason": "coverage_artifact_missing",
            "changed_lines": changed_line_count,
            "impacted_tests": [],
        }

    coverage_artifact = artifact_provenance(
        coverage_path,
        project_root,
        "coverage.{}".format(coverage_format),
        max_age_hours,
    )
    if coverage_format == "sqlite":
        line_contexts = parse_sqlite_contexts(
            coverage_path,
            project_root,
            target_files=changed_files,
            target_lines=changed_files,
        )
    elif coverage_format == "lcov":
        _, line_contexts = parse_lcov(coverage_path, project_root)
    else:
        line_contexts = {}

    junit_paths = find_junit_artifacts(project_root)
    junit_artifacts = [
        artifact_provenance(path, project_root, "junit.xml", max_age_hours)
        for path in junit_paths
    ]
    junit_cases = [
        case
        for path in junit_paths
        for case in parse_junit(path, project_root)
    ]
    cases_by_alias = {
        alias: case
        for case in junit_cases
        for alias in _case_aliases(case)
    }
    matched_contexts, context_covered_lines = _matching_context_map(
        changed_files,
        line_contexts,
    )

    impacted_tests: List[dict] = []
    unmatched_contexts: List[str] = []
    for context, paths_to_lines in sorted(matched_contexts.items()):
        case = cases_by_alias.get(context)
        if case is None:
            unmatched_contexts.append(context)
        impacted_tests.append({
            "node_id": case["node_id"] if case else context,
            "status": case["status"] if case else "unknown",
            "duration_ms": case["duration_ms"] if case else None,
            "changed_files": sorted(paths_to_lines),
            "changed_lines": sum(len(lines) for lines in paths_to_lines.values()),
            "changed_symbol_ids": _symbols_for_test(paths_to_lines, changed_symbols),
            "evidence": "coverage_context+junit" if case else "coverage_context",
        })

    coverage_fresh = bool(coverage_artifact.get("fresh"))
    if not changed_line_count:
        status = "no_changes"
        confidence = "high" if coverage_fresh else "low"
    elif not line_contexts:
        status = "no_contexts"
        confidence = "none"
    elif not coverage_fresh:
        status = "stale"
        confidence = "low"
    elif impacted_tests and not unmatched_contexts:
        status = "ready"
        confidence = "high"
    elif impacted_tests:
        status = "partial"
        confidence = "medium"
    else:
        status = "unmapped"
        confidence = "none"

    result = {
        "schema": TEST_IMPACT_SCHEMA,
        "status": status,
        "confidence": confidence,
        "coverage_artifact": coverage_artifact,
        "junit_artifacts": junit_artifacts,
        "changed_lines": changed_line_count,
        "context_covered_changed_lines": context_covered_lines,
        "impacted_tests": impacted_tests[:MAX_IMPACTED_TESTS],
        "unmatched_contexts": unmatched_contexts[:MAX_IMPACTED_TESTS],
        "truncated": len(impacted_tests) > MAX_IMPACTED_TESTS,
    }
    result["fingerprint"] = _evidence_fingerprint(result)
    return result
