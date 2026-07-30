"""
Code Quality Tools — complexity, duplicates, security, staleness, health score, refactoring.

Extracted from mcp_server.py. Imports index data directly from index_store.
"""

import copy
import hashlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

try:
    from .index_store import load_index, get_symbol_content_text
    from .analyzer.complexity import (
        _line_threshold_for_file,
        _is_test_file,
        summarize_indexed_complexity,
    )
except ImportError:
    from index_store import load_index, get_symbol_content_text
    from analyzer.complexity import (
        _line_threshold_for_file,
        _is_test_file,
        summarize_indexed_complexity,
    )


_HEALTH_CACHE: dict[tuple, dict] = {}


def _health_cache_key(index: dict, project: str | None) -> tuple | None:
    """Return a content-addressed cache identity for the selected project."""
    indexed_at = str(index.get("indexed_at") or "")
    if not indexed_at:
        return None
    symbols = _project_symbols(index, project)
    snapshot_id = _health_snapshot(project, symbols)["id"]
    return (
        str(index.get("root_path") or ""),
        indexed_at,
        project or "",
        str(index.get("generation") or index.get("index_generation") or ""),
        snapshot_id,
    )


def _normalize_complexity_result(result: dict) -> dict:
    """Accept public complexity detail or the equivalent profile summary."""
    return {
        "total_analyzed": result.get(
            "total_analyzed",
            result.get("total_functions", 0),
        ),
        "complex_count": result.get(
            "complex_count",
            result.get("complex_functions", 0),
        ),
        "complexity_burden": result.get("complexity_burden", 0),
        "max_complexity_score": result.get("max_complexity_score", 0),
        "avg_complexity": result.get("avg_complexity", 0.0),
        "functions": result.get("functions", result.get("most_complex", [])),
    }


def _is_production_path(path: str) -> bool:
    """Use the profile classifier so audit and profile analyze one scope."""
    try:
        try:
            from .profile.filesystem import classify_path
        except ImportError:
            from profile.filesystem import classify_path
        return classify_path(path) == "source"
    except ImportError:
        return not _is_test_file(path)


def _project_symbols(index: dict, project: str | None) -> dict:
    symbols = index.get("symbols", {})
    if not project:
        return {
            symbol_id: symbol
            for symbol_id, symbol in symbols.items()
            if _is_production_path(symbol.get("path", ""))
        }
    prefix = project.lower() + ":"
    return {
        symbol_id: symbol
        for symbol_id, symbol in symbols.items()
        if symbol_id.lower().startswith(prefix)
        and _is_production_path(symbol.get("path", ""))
    }


def _health_snapshot(project: str | None, symbols: dict) -> dict:
    """Build a stable evidence identity shared by audit/profile/verify."""
    digest = hashlib.sha256()
    for symbol_id, symbol in sorted(symbols.items()):
        digest.update(symbol_id.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        for field in (
            "path",
            "start_line",
            "end_line",
            "ref_count",
            "content_hash",
            "hash",
            "summary",
        ):
            digest.update(str(symbol.get(field, "")).encode("utf-8", errors="replace"))
            digest.update(b"\0")
    return {
        "schema": "health-snapshot.v2",
        "id": digest.hexdigest(),
        "project": project or "",
        "analysis_scope": "production_source_only",
        "symbol_count": len(symbols),
        "content_addressed": True,
    }


def _find_complex_functions_from_index(
    index: dict,
    *,
    project: str | None,
    max_results: int,
    min_score: int,
    content_loader=None,
) -> dict:
    symbols = _project_symbols(index, project)
    loader = content_loader or get_symbol_content_text
    summary = summarize_indexed_complexity(
        symbols,
        loader,
        min_score=min_score,
        max_results=max_results,
    )
    return {
        "total_analyzed": summary["total_functions"],
        "complex_count": summary["complex_functions"],
        "complexity_burden": summary["complexity_burden"],
        "max_complexity_score": summary["max_complexity_score"],
        "avg_complexity": summary["avg_complexity"],
        "functions": summary["most_complex"],
    }


def find_complex_functions(
    project: str = None,
    max_results: int = 20,
    min_score: int = 1,
) -> dict:
    """Find complex functions by analyzing indexed symbol content.

    Scores each function/method based on: line count, nesting depth,
    parameter count, and branch count. Same thresholds as ComplexityAnalyzer.
    """
    return _find_complex_functions_from_index(
        load_index(),
        project=project,
        max_results=max_results,
        min_score=min_score,
    )


def find_duplicates(
    project: str = None,
    min_lines: int = 6,
    max_results: int = 20,
) -> dict:
    """Find duplicate code blocks by scanning project filesystems."""
    try:
        from .analyzer.duplicates import DuplicateDetector
    except ImportError:
        from analyzer.duplicates import DuplicateDetector

    index = load_index()
    project_roots = index.get("project_roots", {})
    projects = index.get("projects", [])

    if project:
        projects = [p for p in projects if project.lower() in p.lower()]

    all_blocks = []
    total_files = 0
    total_lines = 0

    for proj in projects:
        root = project_roots.get(proj)
        if not root or not Path(root).exists():
            continue

        detector = DuplicateDetector(Path(root), min_lines=min_lines)
        report = detector.analyze()
        total_files += report.total_files
        total_lines += report.total_lines

        for block in report.duplicate_blocks:
            all_blocks.append({
                "project": proj,
                "file1": block.file1,
                "range1": f"{block.start1}-{block.end1}",
                "file2": block.file2,
                "range2": f"{block.start2}-{block.end2}",
                "lines": block.lines,
                "preview": block.code_preview[:200] if block.code_preview else "",
            })

    all_blocks.sort(key=lambda x: x["lines"], reverse=True)
    dup_lines = sum(b["lines"] for b in all_blocks)

    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "duplicate_blocks": len(all_blocks),
        "duplicate_lines": dup_lines,
        "duplicate_rate": f"{dup_lines / max(total_lines, 1) * 100:.1f}%",
        "blocks": all_blocks[:max_results],
    }


def _health_complexity_score(
    *,
    func_count: int,
    complex_count: int,
    complexity_burden: int,
    max_complexity_score: int,
) -> int:
    """Score health complexity using density, total severity, and top hotspot.

    A binary "score >= 5" ratio made a 5-point helper as expensive as a
    100-point god function. The health gate needs to track both how many
    functions crossed the threshold and how severe the crossed functions are.
    """
    if func_count <= 0:
        return 25

    complex_ratio = complex_count / func_count
    severity_density = complexity_burden / max(func_count * 20, 1)
    top_hotspot = min(max_complexity_score, 100) / 100
    penalty = round((complex_ratio * 35) + (severity_density * 15) + (top_hotspot * 5))
    return max(0, 25 - min(25, penalty))


def security_scan(
    project: str = None,
    severity: str = None,
    max_results: int = 50,
) -> dict:
    """Run security scan on indexed project filesystems."""
    try:
        from .analyzer.security import SecurityScanner
    except ImportError:
        from analyzer.security import SecurityScanner

    index = load_index()
    project_roots = index.get("project_roots", {})
    projects = index.get("projects", [])

    if project:
        projects = [p for p in projects if project.lower() in p.lower()]

    all_issues = []
    total_files = 0

    for proj in projects:
        root = project_roots.get(proj)
        if not root or not Path(root).exists():
            continue

        scanner = SecurityScanner(Path(root))
        report = scanner.analyze()
        total_files += report.total_files

        for issue in report.issues:
            if severity and issue.severity != severity:
                continue
            all_issues.append({
                "project": proj,
                "file": issue.file_path,
                "line": issue.line,
                "severity": issue.severity,
                "category": issue.category,
                "description": issue.description,
                "code": issue.code,
                "recommendation": issue.recommendation,
            })

        # Taint analysis (AST-based flow tracking)
        try:
            from .analyzer.taint import TaintAnalyzer
        except ImportError:
            try:
                from analyzer.taint import TaintAnalyzer
            except ImportError:
                TaintAnalyzer = None

        if TaintAnalyzer is not None:
            try:
                taint_analyzer = TaintAnalyzer(Path(root), index=index)
                taint_flows = taint_analyzer.analyze()
                for flow in taint_flows:
                    if severity and flow.severity != severity:
                        continue
                    flow_desc = " → ".join(flow.flow_chain) if flow.flow_chain else ""
                    all_issues.append({
                        "project": proj,
                        "file": flow.file_path,
                        "line": flow.line,
                        "severity": flow.severity,
                        "category": f"taint_flow:{flow.category}",
                        "description": f"{flow.category}: user input from {flow.source_expr} → {flow.sink_expr}",
                        "code": f"Flow: {flow_desc}",
                        "recommendation": flow.recommendation,
                    })
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug("Taint analysis skipped: %s", e)

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_issues.sort(key=lambda x: severity_order.get(x["severity"], 4))

    by_severity = {}
    for issue in all_issues:
        sev = issue["severity"]
        by_severity[sev] = by_severity.get(sev, 0) + 1

    return {
        "total_files": total_files,
        "total_issues": len(all_issues),
        "by_severity": by_severity,
        "issues": all_issues[:max_results],
    }


def analyze_data_flow(
    project: str = None,
    severity: str = None,
    max_results: int = 50,
) -> dict:
    """Run taint / data flow analysis and return structured results.

    Traces data from untrusted sources (request.args, sys.argv, etc.) to
    dangerous sinks (cursor.execute, eval, os.system, etc.) with cross-function
    flow tracking and sanitizer awareness.

    Returns:
        DataFlowResult as dict with taint_flows, counts, and severity breakdown.
    """
    try:
        from .analyzer.taint import TaintAnalyzer
    except ImportError:
        from analyzer.taint import TaintAnalyzer

    index = load_index()
    project_roots = index.get("project_roots", {})
    projects = index.get("projects", [])

    if project:
        projects = [p for p in projects if project.lower() in p.lower()]

    all_flows = []
    total_sources = 0
    total_sinks = 0
    sanitized_count = 0

    for proj in projects:
        root = project_roots.get(proj)
        if not root or not Path(root).exists():
            continue

        try:
            analyzer = TaintAnalyzer(Path(root), index=index)
            result = analyzer.analyze_full()

            total_sources += result.total_sources
            total_sinks += result.total_sinks
            sanitized_count += result.sanitized_flows

            for flow in result.taint_flows:
                if severity and flow.severity != severity:
                    continue
                if flow.sanitized:
                    continue
                all_flows.append({
                    "project": proj,
                    "source": flow.source_expr,
                    "source_file": flow.source_file or flow.file_path,
                    "source_line": flow.source_line or flow.line,
                    "sink": flow.sink_expr,
                    "sink_file": flow.sink_file or flow.file_path,
                    "sink_line": flow.line,
                    "path": flow.path or flow.flow_chain,
                    "sanitized": flow.sanitized,
                    "severity": flow.severity,
                    "category": flow.category,
                    "recommendation": flow.recommendation,
                })
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Taint analysis failed for %s: %s", proj, e)

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_flows.sort(key=lambda x: severity_order.get(x["severity"], 4))

    by_category = {}
    by_severity = {}
    for flow in all_flows:
        cat = flow["category"]
        sev = flow["severity"]
        by_category[cat] = by_category.get(cat, 0) + 1
        by_severity[sev] = by_severity.get(sev, 0) + 1

    high_risk = sum(1 for f in all_flows if f["severity"] in ("critical", "high"))

    return {
        "total_sources": total_sources,
        "total_sinks": total_sinks,
        "unsanitized_flows": len(all_flows),
        "sanitized_flows": sanitized_count,
        "high_risk_count": high_risk,
        "by_category": by_category,
        "by_severity": by_severity,
        "taint_flows": all_flows[:max_results],
    }


def rules_check(project: str = None) -> dict:
    """Check project compliance against .flyto-rules.yaml."""
    try:
        from .analyzer.rules import check_rules as _check
    except ImportError:
        from analyzer.rules import _check

    index = load_index()
    project_roots = index.get("project_roots", {})
    projects = index.get("projects", [])

    if project:
        projects = [p for p in projects if project.lower() in p.lower()]

    all_violations = []
    total_rules = 0
    rules_checked = 0

    for proj in projects:
        root = project_roots.get(proj)
        if not root or not Path(root).exists():
            continue

        result = _check(Path(root))
        total_rules += result.get("total_rules", 0)
        rules_checked += result.get("rules_checked", 0)

        for v in result.get("violations", []):
            v["project"] = proj
            all_violations.append(v)

    return {
        "total_rules": total_rules,
        "rules_checked": rules_checked,
        "total_violations": len(all_violations),
        "violations": all_violations[:50],
    }


def find_stale_files(
    project: str = None,
    stale_days: int = 180,
    max_results: int = 30,
) -> dict:
    """Find files untouched for a long time using git history.

    Uses a single git log command per project and cross-references with
    indexed files for efficiency.
    """
    index = load_index()
    project_roots = index.get("project_roots", {})
    projects = index.get("projects", [])
    symbols = index.get("symbols", {})

    if project:
        projects = [p for p in projects if project.lower() in p.lower()]

    now = datetime.now()
    threshold = now - timedelta(days=stale_days)
    all_stale = []
    total_files = 0

    for proj in projects:
        root = project_roots.get(proj)
        if not root or not Path(root).exists():
            continue

        # Collect indexed files for this project
        indexed_files = set()
        for sym_id, sym in symbols.items():
            if sym_id.startswith(proj + ":"):
                path = sym.get("path", "")
                if path:
                    indexed_files.add(path)

        total_files += len(indexed_files)
        if not indexed_files:
            continue

        # Single git command: get commit dates with filenames
        try:
            result = subprocess.run(
                ["git", "-C", root, "log", "--format=COMMIT:%ai|%an", "--name-only"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                continue
        except Exception:
            continue

        # Parse: build file → (latest_date_str, author) map
        file_info: dict = {}
        current_date = ""
        current_author = ""
        remaining = set(indexed_files)

        for line in result.stdout.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("COMMIT:"):
                parts = line[7:].split("|", 1)
                if len(parts) == 2:
                    current_date = parts[0].strip()
                    current_author = parts[1].strip()
            elif current_date and line in remaining:
                file_info[line] = (current_date, current_author)
                remaining.discard(line)
                if not remaining:
                    break

        for file_path, (date_str, author) in file_info.items():
            try:
                ds = date_str
                if " +" in ds or " -" in ds:
                    ds = ds.rsplit(" ", 1)[0]
                last_modified = datetime.strptime(ds, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue

            if last_modified < threshold:
                days = (now - last_modified).days
                all_stale.append({
                    "project": proj,
                    "path": file_path,
                    "days_since_modified": days,
                    "last_author": author,
                    "last_modified": last_modified.strftime("%Y-%m-%d"),
                })

    all_stale.sort(key=lambda x: x["days_since_modified"], reverse=True)

    return {
        "total_files": total_files,
        "stale_count": len(all_stale),
        "threshold_days": stale_days,
        "files": all_stale[:max_results],
    }


def _grade_for_health_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _dead_code_from_index(index: dict, project: str | None) -> dict:
    """Run the high-confidence dead-code engine against a supplied snapshot."""
    try:
        try:
            from .tools.maintenance import _find_dead_code_from_index
        except ImportError:
            from tools.maintenance import _find_dead_code_from_index
        return _find_dead_code_from_index(index, project=project, min_lines=5)
    except ImportError:
        return {"total_dead": 0, "dead_symbols": []}


def _code_health_score_from_index(
    index: dict,
    *,
    project: str | None,
    content_loader=None,
    dead_result: dict | None = None,
    complexity_result: dict | None = None,
) -> dict:
    """Compute the canonical health snapshot used by every public surface."""
    cache_key = _health_cache_key(index, project)
    cacheable = dead_result is None
    if cacheable and cache_key in _HEALTH_CACHE:
        return copy.deepcopy(_HEALTH_CACHE[cache_key])

    symbols = _project_symbols(index, project)
    total_symbols = len(symbols)
    if total_symbols == 0:
        return {
            "error": "No symbols found",
            "score": 0,
            "grade": "N/A",
            "snapshot": _health_snapshot(project, symbols),
        }

    complexity = _normalize_complexity_result(
        complexity_result
        or _find_complex_functions_from_index(
            index,
            project=project,
            max_results=20,
            min_score=5,
            content_loader=content_loader,
        )
    )
    func_count = complexity["total_analyzed"]
    complex_count = complexity["complex_count"]
    complexity_burden = complexity["complexity_burden"]
    max_complexity_score = complexity["max_complexity_score"]
    complexity_score = _health_complexity_score(
        func_count=func_count,
        complex_count=complex_count,
        complexity_burden=complexity_burden,
        max_complexity_score=max_complexity_score,
    )

    dead_result = dead_result or _dead_code_from_index(index, project)
    dead_count = dead_result.get("total_dead", 0)
    dead_ratio = dead_count / max(total_symbols, 1)
    dead_score = max(0, 25 - int(dead_ratio * 100))

    documented = sum(1 for symbol in symbols.values() if symbol.get("summary"))
    doc_total = max(total_symbols, 1)
    doc_ratio = documented / doc_total
    doc_score = min(25, round(doc_ratio / 0.7 * 25))

    ref_counts = [
        symbol.get("ref_count", symbol.get("reference_count", 0))
        for symbol in symbols.values()
    ]
    referenced_count = sum(1 for count in ref_counts if count > 0)
    pct_with_refs = referenced_count / max(len(ref_counts), 1)
    entry_points = sum(
        1
        for symbol in symbols.values()
        if symbol.get("type") == "function"
        and not symbol.get("name", "_").startswith("_")
    )
    total_functions = sum(
        1 for symbol in symbols.values() if symbol.get("type") == "function"
    )
    is_toolbox = entry_points / max(total_functions, 1) > 0.4
    modularity_baseline = 0.03 if is_toolbox else 0.08
    archetype = "toolbox" if is_toolbox else "application"
    modularity_score = min(
        25,
        round(pct_with_refs / modularity_baseline * 25),
    )

    scores = {
        "complexity": complexity_score,
        "dead_code": dead_score,
        "documentation": doc_score,
        "modularity": modularity_score,
    }
    total_score = sum(scores.values())
    worst_dimension = min(scores, key=scores.get)
    worst_score = scores[worst_dimension]
    actions = {
        "complexity": {
            "tool": "audit",
            "arguments": {"project": project, "focus": "complexity"},
        },
        "dead_code": {
            "tool": "audit",
            "arguments": {"project": project, "focus": "dead_code"},
        },
        "documentation": {
            "tool": "scan_documentation",
            "arguments": {},
        },
        "modularity": {
            "tool": "structure",
            "arguments": {"project": project, "focus": "profile"},
        },
    }
    next_action = (
        None
        if total_score >= 90
        else {
            "reason": f"Weakest area: {worst_dimension} ({worst_score}/25)",
            **actions[worst_dimension],
        }
    )

    result = {
        "score": total_score,
        "grade": _grade_for_health_score(total_score),
        "breakdown": {
            "complexity": {
                "score": complexity_score,
                "max": 25,
                "detail": (
                    f"{complex_count}/{func_count} functions with high composite complexity "
                    f"(score >= 5, burden {complexity_burden}, top hotspot {max_complexity_score})"
                ),
                "metrics": {
                    "total_functions": func_count,
                    "complex_functions": complex_count,
                    "complexity_burden": complexity_burden,
                    "max_complexity_score": max_complexity_score,
                    "avg_complexity": complexity["avg_complexity"],
                },
                "hotspots": complexity["functions"],
            },
            "dead_code": {
                "score": dead_score,
                "max": 25,
                "detail": f"{dead_count} high-confidence unreferenced symbols",
                "metrics": {
                    "dead_count": dead_count,
                    "dead_lines": dead_result.get("total_dead_lines", 0),
                },
                "symbols": (dead_result.get("dead_symbols") or [])[:20],
            },
            "documentation": {
                "score": doc_score,
                "max": 25,
                "detail": (
                    f"{documented}/{doc_total} production symbols documented "
                    f"({doc_ratio * 100:.0f}%)"
                ),
                "metrics": {
                    "documented_symbols": documented,
                    "total_symbols": doc_total,
                    "coverage": round(doc_ratio, 4),
                },
            },
            "modularity": {
                "score": modularity_score,
                "max": 25,
                "detail": (
                    f"{referenced_count}/{len(ref_counts)} symbols referenced "
                    f"({pct_with_refs * 100:.1f}%, {archetype} baseline "
                    f"{modularity_baseline * 100:.0f}%)"
                ),
                "metrics": {
                    "referenced_symbols": referenced_count,
                    "total_symbols": len(ref_counts),
                    "reference_ratio": round(pct_with_refs, 4),
                    "archetype": archetype,
                },
            },
        },
        "total_symbols": total_symbols,
        "snapshot": _health_snapshot(project, symbols),
        "next_action": next_action,
    }
    if cacheable and cache_key is not None:
        if len(_HEALTH_CACHE) >= 8:
            _HEALTH_CACHE.pop(next(iter(_HEALTH_CACHE)))
        _HEALTH_CACHE[cache_key] = copy.deepcopy(result)
    return result


def code_health_score(project: str = None) -> dict:
    """Compute the canonical aggregate code health score (0-100)."""
    return _code_health_score_from_index(load_index(), project=project)


def _suggest_fix_for_complexity(fn: dict) -> str:
    """Generate a refactoring suggestion for a complex function."""
    parts = []
    line_threshold = _line_threshold_for_file(fn.get("path", ""))
    if fn.get("lines", 0) > line_threshold:
        parts.append("Extract sub-functions to reduce length")
    if fn.get("max_depth", 0) > 3:
        parts.append("Use early returns or guard clauses to reduce nesting")
    if fn.get("params", 0) > 5:
        parts.append("Group parameters into a config/options object")
    if fn.get("branches", 0) > 10:
        parts.append("Consider strategy pattern or lookup table")
    return "; ".join(parts) if parts else "Review for simplification opportunities"


def suggest_refactoring(project: str = None, max_results: int = 20) -> dict:
    """Combine complexity + dead code + file size analysis into prioritized refactoring suggestions."""
    index = load_index()
    symbols = index.get("symbols", {})
    suggestions = []

    # 1. Complex functions → extract/simplify
    complex_result = find_complex_functions(project=project, max_results=50, min_score=1)
    for fn in complex_result.get("functions", []):
        priority = "high" if fn["score"] >= 10 else "medium" if fn["score"] >= 5 else "low"
        suggestions.append({
            "type": "complex_function",
            "priority": priority,
            "symbol_id": fn["symbol_id"],
            "name": fn["name"],
            "path": fn["path"],
            "line": fn["line"],
            "reason": ", ".join(fn["issues"]),
            "suggestion": _suggest_fix_for_complexity(fn),
            "score": fn["score"],
        })

    # 2. Dead code → remove
    try:
        from .tools.maintenance import find_dead_code
    except ImportError:
        try:
            from tools.maintenance import find_dead_code
        except ImportError:
            find_dead_code = None

    if find_dead_code is not None:
        dead_result = find_dead_code(project=project, min_lines=10)
        for sym in dead_result.get("dead_symbols", []):
            lines = sym.get("lines", 0)
            suggestions.append({
                "type": "dead_code",
                "priority": "medium" if lines >= 20 else "low",
                "symbol_id": sym.get("symbol_id", ""),
                "name": sym.get("name", ""),
                "path": sym.get("path", ""),
                "line": sym.get("line", 0),
                "reason": f"Unreferenced ({lines} lines)",
                "suggestion": "Safe to remove — no callers found in indexed code",
                "score": lines // 5,
            })

    # 3. Large files → split
    file_symbol_lines: dict = {}
    for sym_id, sym in symbols.items():
        if project and not sym_id.lower().startswith(project.lower() + ":"):
            continue
        path = sym.get("path", "")
        if path:
            content = get_symbol_content_text(sym_id, sym)
            if content:
                file_symbol_lines[path] = file_symbol_lines.get(path, 0) + len(content.split("\n"))

    for path, lines in sorted(file_symbol_lines.items(), key=lambda x: x[1], reverse=True)[:10]:
        if lines > 500:
            suggestions.append({
                "type": "large_file",
                "priority": "medium" if lines >= 1000 else "low",
                "symbol_id": "",
                "name": path.split("/")[-1],
                "path": path,
                "line": 0,
                "reason": f"Large file ({lines} lines of symbol content)",
                "suggestion": "Consider splitting into smaller, focused modules",
                "score": lines // 100,
            })

    # Sort by priority then score
    priority_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda x: (priority_order.get(x["priority"], 3), -x["score"]))

    by_type = {}
    for s in suggestions:
        by_type[s["type"]] = by_type.get(s["type"], 0) + 1

    return {
        "total_suggestions": len(suggestions),
        "by_type": by_type,
        "suggestions": suggestions[:max_results],
    }
