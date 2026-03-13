"""Coverage Intelligence tools — coverage report, gap analysis, untested changes."""

import json
import os
import re
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    from ..index_store import load_index, get_symbol_content_text
except ImportError:
    from index_store import load_index, get_symbol_content_text


# Parse unified diff headers: @@ -start[,count] +start[,count] @@
_HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


# =============================================================================
# Helpers
# =============================================================================

def _get_project_root(project: Optional[str] = None) -> Tuple[str, str]:
    """Resolve project name and root path from index.

    Returns:
        (project_name, root_path)

    Raises:
        ValueError: If project not found or ambiguous.
    """
    index = load_index()
    project_roots = index.get("project_roots", {})

    if project and project in project_roots:
        return project, project_roots[project]

    if project_roots:
        if len(project_roots) == 1:
            name = next(iter(project_roots))
            return name, project_roots[name]
        if project:
            raise ValueError(
                "Project '{}' not found. Available: {}".format(
                    project, ", ".join(sorted(project_roots.keys()))
                )
            )
        # Multiple projects, no filter — use CWD
        cwd = str(Path.cwd())
        return Path(cwd).name, cwd

    cwd = str(Path.cwd())
    return Path(cwd).name, cwd


def _find_coverage_data(project_root: str) -> Tuple[str, str]:
    """Find coverage data file in project root.

    Checks (in order): .coverage (SQLite), coverage.xml, htmlcov/status.json,
    coverage.json.

    Returns:
        (format, path) — format is one of "sqlite", "xml", "json", "none".
    """
    root = Path(project_root)

    # .coverage (SQLite, coverage.py default)
    cov_db = root / ".coverage"
    if cov_db.is_file():
        return "sqlite", str(cov_db)

    # coverage.xml (Cobertura format)
    cov_xml = root / "coverage.xml"
    if cov_xml.is_file():
        return "xml", str(cov_xml)

    # htmlcov/status.json
    htmlcov_status = root / "htmlcov" / "status.json"
    if htmlcov_status.is_file():
        return "json", str(htmlcov_status)

    # coverage.json
    cov_json = root / "coverage.json"
    if cov_json.is_file():
        return "json", str(cov_json)

    return "none", ""


def _parse_coverage_sqlite(db_path: str) -> Dict[str, Set[int]]:
    """Parse .coverage SQLite file (coverage.py v5+ format).

    Returns:
        {relative_file_path: {set of covered line numbers}}
    """
    project_root = str(Path(db_path).parent)
    result: Dict[str, Set[int]] = {}

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Read file table
        try:
            cursor.execute("SELECT id, path FROM file")
        except sqlite3.OperationalError:
            conn.close()
            return result

        files = {row[0]: row[1] for row in cursor.fetchall()}

        # Read line_bits table
        try:
            cursor.execute("SELECT file_id, numbits FROM line_bits")
        except sqlite3.OperationalError:
            conn.close()
            return result

        for file_id, numbits in cursor.fetchall():
            if file_id not in files:
                continue

            file_path = files[file_id]

            # Make path relative to project root
            if os.path.isabs(file_path):
                try:
                    file_path = os.path.relpath(file_path, project_root)
                except ValueError:
                    pass  # Different drives on Windows

            # Decode numbits bitmap
            lines: Set[int] = set()
            if numbits:
                for byte_idx, byte_val in enumerate(numbits):
                    for bit in range(8):
                        if byte_val & (1 << bit):
                            lines.add(byte_idx * 8 + bit + 1)

            if file_path in result:
                result[file_path].update(lines)
            else:
                result[file_path] = lines

        conn.close()
    except (sqlite3.Error, OSError):
        pass

    return result


def _parse_coverage_xml(xml_path: str) -> Dict[str, Set[int]]:
    """Parse Cobertura XML coverage format.

    Returns:
        {file_path: {set of covered line numbers}}
    """
    result: Dict[str, Set[int]] = {}

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        for cls in root.iter("class"):
            filename = cls.get("filename", "")
            if not filename:
                continue

            lines: Set[int] = set()
            for line in cls.iter("line"):
                hits = int(line.get("hits", "0"))
                if hits > 0:
                    number = int(line.get("number", "0"))
                    if number > 0:
                        lines.add(number)

            if filename in result:
                result[filename].update(lines)
            else:
                result[filename] = lines

    except (ET.ParseError, OSError):
        pass

    return result


def _parse_coverage_json(json_path: str) -> Dict[str, Set[int]]:
    """Parse coverage.json format.

    Returns:
        {file_path: {set of covered line numbers}}
    """
    result: Dict[str, Set[int]] = {}

    try:
        with open(json_path, "r") as f:
            data = json.load(f)

        # coverage.json format: {"files": {"path": {"executed_lines": [...]}}}
        files = data.get("files", {})
        for file_path, file_data in files.items():
            executed = file_data.get("executed_lines", [])
            if executed:
                result[file_path] = set(executed)

    except (json.JSONDecodeError, OSError):
        pass

    return result


def _parse_coverage(fmt: str, path: str) -> Dict[str, Set[int]]:
    """Dispatch to the appropriate parser."""
    if fmt == "sqlite":
        return _parse_coverage_sqlite(path)
    elif fmt == "xml":
        return _parse_coverage_xml(path)
    elif fmt == "json":
        return _parse_coverage_json(path)
    return {}


def _get_data_age_hours(path: str) -> float:
    """Get age of coverage data file in hours."""
    try:
        import time
        mtime = os.path.getmtime(path)
        return (time.time() - mtime) / 3600.0
    except OSError:
        return 0.0


def _uncovered_ranges(total_lines: Set[int], covered_lines: Set[int]) -> List[List[int]]:
    """Compute contiguous uncovered line ranges.

    Returns:
        [[start, end], ...] sorted by start.
    """
    uncovered = sorted(total_lines - covered_lines)
    if not uncovered:
        return []

    ranges: List[List[int]] = []
    start = uncovered[0]
    end = uncovered[0]

    for line in uncovered[1:]:
        if line == end + 1:
            end = line
        else:
            ranges.append([start, end])
            start = line
            end = line

    ranges.append([start, end])
    return ranges


def _map_to_symbols(
    coverage: Dict[str, Set[int]],
    index: dict,
    project: str,
) -> List[dict]:
    """Map coverage data to indexed symbols.

    For each function/method/class symbol in the project, compute coverage
    within its line range.

    Returns:
        List of dicts with symbol coverage info, sorted by coverage ascending.
    """
    symbols = index.get("symbols", {})
    results: List[dict] = []

    for symbol_id, symbol in symbols.items():
        # Filter by project
        sym_project = symbol.get("project", "")
        if project and sym_project != project:
            continue

        sym_type = symbol.get("type", "")
        if sym_type not in ("function", "method", "class"):
            continue

        sym_path = symbol.get("path", "")
        start_line = symbol.get("start_line", 0)
        end_line = symbol.get("end_line", 0)

        if not sym_path or not start_line or not end_line:
            continue

        # Find matching coverage file (try both as-is and without leading prefix)
        covered_lines = None
        for cov_path, cov_lines in coverage.items():
            if cov_path == sym_path or cov_path.endswith("/" + sym_path) or sym_path.endswith("/" + cov_path):
                covered_lines = cov_lines
                break

        if covered_lines is None:
            # No coverage data for this file — skip (don't assume 0%)
            continue

        # Lines in the symbol range
        total_in_range = set(range(start_line, end_line + 1))
        covered_in_range = total_in_range & covered_lines
        total_count = len(total_in_range)
        covered_count = len(covered_in_range)
        pct = (covered_count / total_count * 100.0) if total_count > 0 else 100.0

        uncovered = _uncovered_ranges(total_in_range, covered_lines)

        results.append({
            "symbol_id": symbol_id,
            "name": symbol.get("name", ""),
            "path": sym_path,
            "total_lines": total_count,
            "covered_lines": covered_count,
            "coverage_pct": round(pct, 1),
            "uncovered_ranges": uncovered,
        })

    return results


# =============================================================================
# Tool: coverage_report
# =============================================================================

def coverage_report(project: Optional[str] = None, min_coverage: Optional[float] = None) -> dict:
    """Generate a coverage report mapped to indexed symbols.

    Args:
        project: Project name. If omitted, auto-detect.
        min_coverage: If set (0.0-1.0), filter to functions below this threshold.

    Returns:
        Coverage report with overall stats and per-function breakdown.
    """
    try:
        project_name, project_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    fmt, cov_path = _find_coverage_data(project_root)
    if fmt == "none":
        return {
            "error": (
                "No coverage data found in '{}'. "
                "Run your test suite with coverage enabled first:\n"
                "  pytest --cov=src --cov-report=xml\n"
                "  # or: coverage run -m pytest && coverage xml"
            ).format(project_root),
        }

    coverage = _parse_coverage(fmt, cov_path)
    if not coverage:
        return {"error": "Coverage file found but could not be parsed: {}".format(cov_path)}

    age_hours = _get_data_age_hours(cov_path)
    stale_warning = None
    if age_hours > 24:
        stale_warning = "Coverage data is {:.0f} hours old. Consider re-running tests.".format(age_hours)

    index = load_index()
    functions = _map_to_symbols(coverage, index, project_name)

    # Overall stats
    total_lines = sum(f["total_lines"] for f in functions)
    covered_lines = sum(f["covered_lines"] for f in functions)
    overall_pct = (covered_lines / total_lines * 100.0) if total_lines > 0 else 0.0

    # Filter by min_coverage threshold
    if min_coverage is not None:
        threshold_pct = min_coverage * 100.0
        functions = [f for f in functions if f["coverage_pct"] < threshold_pct]

    # Sort by coverage ascending (worst first)
    functions.sort(key=lambda f: f["coverage_pct"])

    # Truncate to avoid huge output
    total_functions = len(functions)
    functions = functions[:100]

    return {
        "project": project_name,
        "data_source": fmt,
        "data_age_hours": round(age_hours, 1),
        "stale_warning": stale_warning,
        "overall": {
            "total_lines": total_lines,
            "covered_lines": covered_lines,
            "coverage_pct": round(overall_pct, 1),
        },
        "functions": functions,
        "total_functions": total_functions,
    }


# =============================================================================
# Tool: coverage_gaps
# =============================================================================

def coverage_gaps(project: Optional[str] = None, max_results: int = 20) -> dict:
    """Find high-impact coverage gaps: low-coverage functions with many references.

    Args:
        project: Project name. If omitted, auto-detect.
        max_results: Maximum results. Default: 20.

    Returns:
        Gaps sorted by gap_score (descending), with summary stats.
    """
    try:
        project_name, project_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    fmt, cov_path = _find_coverage_data(project_root)
    if fmt == "none":
        return {
            "error": (
                "No coverage data found in '{}'. "
                "Run your test suite with coverage enabled first."
            ).format(project_root),
        }

    coverage = _parse_coverage(fmt, cov_path)
    if not coverage:
        return {"error": "Coverage file found but could not be parsed: {}".format(cov_path)}

    index = load_index()
    functions = _map_to_symbols(coverage, index, project_name)
    reverse_index = index.get("reverse_index", {})

    gaps: List[dict] = []
    for func in functions:
        cov_pct = func["coverage_pct"]
        if cov_pct >= 100.0:
            continue  # Fully covered — not a gap

        # Count references to this symbol
        symbol_name = func["name"]
        ref_count = len(reverse_index.get(symbol_name, []))

        gap_score = (1.0 - cov_pct / 100.0) * (1 + ref_count)

        gaps.append({
            "symbol_id": func["symbol_id"],
            "name": symbol_name,
            "path": func["path"],
            "coverage_pct": cov_pct,
            "reference_count": ref_count,
            "gap_score": round(gap_score, 2),
            "uncovered_ranges": func["uncovered_ranges"],
        })

    # Sort by gap_score descending
    gaps.sort(key=lambda g: g["gap_score"], reverse=True)
    total_gaps = len(gaps)
    gaps = gaps[:max_results]

    avg_gap_coverage = 0.0
    if gaps:
        avg_gap_coverage = sum(g["coverage_pct"] for g in gaps) / len(gaps)

    return {
        "gaps": gaps,
        "summary": {
            "total_gaps": total_gaps,
            "avg_gap_coverage": round(avg_gap_coverage, 1),
        },
    }


# =============================================================================
# Tool: untested_changes
# =============================================================================

def _match_coverage_path(file_path: str, coverage: Dict[str, Set[int]]) -> Set[int]:
    """Find matching coverage data for a file path."""
    for cov_path_key, cov_lines in coverage.items():
        if (cov_path_key == file_path
                or cov_path_key.endswith("/" + file_path)
                or file_path.endswith("/" + cov_path_key)):
            return cov_lines
    return set()


def _find_affected_symbols(
    file_path: str, uncovered_lines: List[int],
    symbols: dict, project_name: str,
) -> List[dict]:
    """Find symbols that contain uncovered changed lines."""
    affected: List[dict] = []
    for symbol_id, symbol in symbols.items():
        sym_project = symbol.get("project", "")
        if project_name and sym_project != project_name:
            continue
        sym_path = symbol.get("path", "")
        if sym_path != file_path and not file_path.endswith("/" + sym_path) and not sym_path.endswith("/" + file_path):
            continue
        start_line = symbol.get("start_line", 0)
        end_line = symbol.get("end_line", 0)
        if not start_line or not end_line:
            continue
        for line in uncovered_lines:
            if start_line <= line <= end_line:
                affected.append({"name": symbol.get("name", ""), "symbol_id": symbol_id})
                break
    return affected


def untested_changes(project: Optional[str] = None, mode: str = "unstaged") -> dict:
    """Find changed lines that lack test coverage.

    Cross-references git diff with coverage data to identify untested changes.
    """
    try:
        project_name, project_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    fmt, cov_path = _find_coverage_data(project_root)
    if fmt == "none":
        return {
            "error": (
                "No coverage data found in '{}'. "
                "Run your test suite with coverage enabled first."
            ).format(project_root),
        }

    coverage = _parse_coverage(fmt, cov_path)
    if not coverage:
        return {"error": "Coverage file found but could not be parsed: {}".format(cov_path)}

    diff_text = _run_git_diff(project_root, mode)
    if not diff_text:
        return {
            "mode": mode,
            "summary": {"total_changed_lines": 0, "uncovered_changed_lines": 0, "change_coverage_pct": 100.0},
            "files": [],
        }

    file_changes = _parse_diff_lines(diff_text)
    index = load_index()
    symbols = index.get("symbols", {})
    files_result: List[dict] = []
    total_changed = 0
    total_uncovered = 0

    for file_path, changed_lines in file_changes.items():
        if not changed_lines:
            continue

        covered_lines = _match_coverage_path(file_path, coverage)
        uncovered_lines = sorted(set(changed_lines) - covered_lines)
        changed_count = len(changed_lines)
        uncovered_count = len(uncovered_lines)
        change_pct = ((changed_count - uncovered_count) / changed_count * 100.0) if changed_count > 0 else 100.0

        total_changed += changed_count
        total_uncovered += uncovered_count

        affected_symbols = _find_affected_symbols(file_path, uncovered_lines, symbols, project_name)

        if uncovered_lines or changed_lines:
            files_result.append({
                "path": file_path,
                "changed_lines": sorted(changed_lines),
                "uncovered_lines": uncovered_lines,
                "change_coverage_pct": round(change_pct, 1),
                "affected_symbols": affected_symbols,
            })

    overall_pct = ((total_changed - total_uncovered) / total_changed * 100.0) if total_changed > 0 else 100.0
    files_result.sort(key=lambda f: f["change_coverage_pct"])

    return {
        "mode": mode,
        "summary": {
            "total_changed_lines": total_changed,
            "uncovered_changed_lines": total_uncovered,
            "change_coverage_pct": round(overall_pct, 1),
        },
        "files": files_result[:50],
    }


# =============================================================================
# Diff helpers (internal)
# =============================================================================

def _run_git_diff(project_root: str, mode: str) -> str:
    """Run git diff and return raw unified diff text."""
    cmd = ["git", "-C", project_root, "diff", "--unified=0", "--no-color"]

    if mode == "staged":
        cmd.append("--cached")
    elif mode == "committed":
        cmd.extend(["HEAD~1", "HEAD"])
    # "unstaged" is the default

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _parse_diff_lines(diff_text: str) -> Dict[str, List[int]]:
    """Parse unified diff to extract changed line numbers per file.

    Returns:
        {file_path: [line_numbers]} for added/modified lines.
    """
    file_changes: Dict[str, List[int]] = {}
    current_file: Optional[str] = None

    for line in diff_text.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in file_changes:
                file_changes[current_file] = []
        elif line.startswith("+++ /dev/null"):
            current_file = None
        elif line.startswith("@@ ") and current_file:
            m = _HUNK_HEADER.match(line)
            if m:
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) else 1
                for i in range(new_count):
                    file_changes[current_file].append(new_start + i)

    return file_changes
