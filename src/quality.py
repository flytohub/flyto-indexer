"""
Code Quality Tools — complexity, duplicates, security, staleness, health score, refactoring.

Extracted from mcp_server.py to keep that file focused on MCP protocol.
All functions here receive index data via the `_idx` parameter (a module reference)
to avoid circular imports.
"""

import subprocess
from datetime import datetime, timedelta
from pathlib import Path


def find_complex_functions(
    _idx,
    project: str = None,
    max_results: int = 20,
    min_score: int = 1,
) -> dict:
    """Find complex functions by analyzing indexed symbol content.

    Scores each function/method based on: line count, nesting depth,
    parameter count, and branch count. Same thresholds as ComplexityAnalyzer.
    """
    index = _idx.load_index()
    symbols = index.get("symbols", {})

    complex_fns = []
    total_analyzed = 0

    for sym_id, sym in symbols.items():
        if project and not sym_id.lower().startswith(project.lower() + ":"):
            continue

        sym_type = sym.get("type", "")
        if sym_type not in ("function", "method"):
            continue

        total_analyzed += 1
        content = _idx.get_symbol_content_text(sym_id, sym)
        if not content:
            continue

        lines = content.split("\n")
        line_count = len(lines)
        params_list = sym.get("params", [])
        param_count = len(params_list) if isinstance(params_list, list) else 0

        # Detect language from path
        path = sym.get("path", "")
        is_python = path.endswith(".py")
        indent_unit = 4 if is_python else 2

        max_depth = 0
        branches = 0
        returns = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            indent = len(line) - len(line.lstrip())
            depth = indent // indent_unit
            max_depth = max(max_depth, depth)

            if is_python:
                branch_kws = ("if ", "elif ", "for ", "while ", "try:", "except ", "with ")
            else:
                branch_kws = ("if ", "if(", "else if ", "for ", "for(", "while ", "while(", "switch ", "switch(", "try ", "try{", "catch ", "catch(")
            for kw in branch_kws:
                if stripped.startswith(kw):
                    branches += 1
                    break

            if stripped.startswith("return ") or stripped == "return":
                returns += 1

        score = 0
        issues = []
        if line_count > 50:
            score += (line_count - 50) // 10
            issues.append(f"Too long ({line_count} lines)")
        if max_depth > 3:
            score += (max_depth - 3) * 5
            issues.append(f"Nesting too deep (depth={max_depth})")
        if param_count > 5:
            score += (param_count - 5) * 2
            issues.append(f"Too many parameters ({param_count})")
        if branches > 10:
            score += (branches - 10)
            issues.append(f"Too many branches ({branches})")

        if score >= min_score:
            complex_fns.append({
                "symbol_id": sym_id,
                "name": sym.get("name", ""),
                "path": path,
                "line": sym.get("line", 0),
                "lines": line_count,
                "params": param_count,
                "max_depth": max_depth,
                "branches": branches,
                "returns": returns,
                "score": score,
                "issues": issues,
            })

    complex_fns.sort(key=lambda x: x["score"], reverse=True)

    return {
        "total_analyzed": total_analyzed,
        "complex_count": len(complex_fns),
        "functions": complex_fns[:max_results],
    }


def find_duplicates(
    _idx,
    project: str = None,
    min_lines: int = 6,
    max_results: int = 20,
) -> dict:
    """Find duplicate code blocks by scanning project filesystems."""
    try:
        from .analyzer.duplicates import DuplicateDetector
    except ImportError:
        from analyzer.duplicates import DuplicateDetector

    index = _idx.load_index()
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


def security_scan(
    _idx,
    project: str = None,
    severity: str = None,
    max_results: int = 50,
) -> dict:
    """Run security scan on indexed project filesystems."""
    try:
        from .analyzer.security import SecurityScanner
    except ImportError:
        from analyzer.security import SecurityScanner

    index = _idx.load_index()
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


def find_stale_files(
    _idx,
    project: str = None,
    stale_days: int = 180,
    max_results: int = 30,
) -> dict:
    """Find files untouched for a long time using git history.

    Uses a single git log command per project and cross-references with
    indexed files for efficiency.
    """
    index = _idx.load_index()
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


def code_health_score(_idx, project: str = None) -> dict:
    """Compute an aggregate code health score (0-100) with A-F grade.

    Breakdown: complexity (25), dead code (25), documentation (25), modularity (25).
    """
    index = _idx.load_index()
    symbols = index.get("symbols", {})

    if project:
        symbols = {k: v for k, v in symbols.items() if k.lower().startswith(project.lower() + ":")}

    total_symbols = len(symbols)
    if total_symbols == 0:
        return {"error": "No symbols found", "score": 0, "grade": "N/A"}

    # 1. Complexity score (0-25): penalty for functions > 50 lines
    func_count = 0
    complex_count = 0
    for sym_id, sym in symbols.items():
        if sym.get("type") not in ("function", "method"):
            continue
        func_count += 1
        content = _idx.get_symbol_content_text(sym_id, sym)
        if content and len(content.split("\n")) > 50:
            complex_count += 1

    if func_count > 0:
        complexity_ratio = complex_count / func_count
        complexity_score = max(0, 25 - int(complexity_ratio * 100))
    else:
        complexity_score = 25

    # 2. Dead code score (0-25): penalty for unreferenced symbols
    dead_result = _idx.find_dead_code(project=project, min_lines=5)
    dead_count = dead_result.get("total_dead", 0)
    dead_ratio = dead_count / max(total_symbols, 1)
    dead_score = max(0, 25 - int(dead_ratio * 100))

    # 3. Documentation score (0-25): reward for symbols with summaries
    documented = sum(1 for sym in symbols.values() if sym.get("summary"))
    doc_ratio = documented / max(total_symbols, 1)
    doc_score = min(25, int(doc_ratio * 25))

    # 4. Modularity score (0-25): reward for higher average reference counts
    ref_counts = [sym.get("reference_count", 0) for sym in symbols.values()]
    avg_refs = sum(ref_counts) / max(len(ref_counts), 1)
    modularity_score = min(25, int(avg_refs * 5))

    total_score = complexity_score + dead_score + doc_score + modularity_score

    if total_score >= 90:
        grade = "A"
    elif total_score >= 80:
        grade = "B"
    elif total_score >= 70:
        grade = "C"
    elif total_score >= 60:
        grade = "D"
    else:
        grade = "F"

    # next_action: point to worst dimension
    dimensions = {
        "complexity": (complexity_score, "find_complex_functions", "Use find_complex_functions to identify refactoring targets."),
        "dead_code": (dead_score, "find_dead_code", "Use find_dead_code to find removable symbols."),
        "documentation": (doc_score, "update_description", "Use update_description to document undocumented files."),
        "modularity": (modularity_score, "dependency_graph", "Use dependency_graph to find isolated modules."),
    }
    worst_dim = min(dimensions, key=lambda k: dimensions[k][0])
    worst_score, worst_tool, worst_hint = dimensions[worst_dim]
    if total_score >= 90:
        next_action = "Excellent health. No immediate action needed."
    else:
        next_action = f"Weakest area: {worst_dim} ({worst_score}/25). {worst_hint}"

    return {
        "score": total_score,
        "grade": grade,
        "breakdown": {
            "complexity": {
                "score": complexity_score, "max": 25,
                "detail": f"{complex_count}/{func_count} functions over 50 lines",
            },
            "dead_code": {
                "score": dead_score, "max": 25,
                "detail": f"{dead_count} unreferenced symbols",
            },
            "documentation": {
                "score": doc_score, "max": 25,
                "detail": f"{documented}/{total_symbols} symbols documented",
            },
            "modularity": {
                "score": modularity_score, "max": 25,
                "detail": f"avg {avg_refs:.1f} references per symbol",
            },
        },
        "total_symbols": total_symbols,
        "next_action": next_action,
    }


def _suggest_fix_for_complexity(fn: dict) -> str:
    """Generate a refactoring suggestion for a complex function."""
    parts = []
    if fn.get("lines", 0) > 50:
        parts.append("Extract sub-functions to reduce length")
    if fn.get("max_depth", 0) > 3:
        parts.append("Use early returns or guard clauses to reduce nesting")
    if fn.get("params", 0) > 5:
        parts.append("Group parameters into a config/options object")
    if fn.get("branches", 0) > 10:
        parts.append("Consider strategy pattern or lookup table")
    return "; ".join(parts) if parts else "Review for simplification opportunities"


def suggest_refactoring(_idx, project: str = None, max_results: int = 20) -> dict:
    """Combine complexity + dead code + file size analysis into prioritized refactoring suggestions."""
    index = _idx.load_index()
    symbols = index.get("symbols", {})
    suggestions = []

    # 1. Complex functions → extract/simplify
    complex_result = find_complex_functions(_idx, project=project, max_results=50, min_score=1)
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
    dead_result = _idx.find_dead_code(project=project, min_lines=10)
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
            content = _idx.get_symbol_content_text(sym_id, sym)
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
