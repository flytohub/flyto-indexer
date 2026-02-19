"""
Diff-based Impact Analysis — parse git diff, match changed hunks to indexed symbols,
classify change type (signature vs body), and run impact analysis.

All functions receive `_idx` (mcp_server module) to access index data.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from .signature import ChangeKind, classify_symbol_change
except ImportError:
    from signature import ChangeKind, classify_symbol_change


# Security: validate git ref names to prevent command injection
_SAFE_REF_PATTERN = re.compile(r'^[a-zA-Z0-9_./@^~{}\-]+$')

# Parse unified diff headers: @@ -start[,count] +start[,count] @@
_HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def _validate_ref(ref: str) -> bool:
    """Validate a git ref to prevent command injection."""
    if not ref:
        return True  # empty is ok (means default)
    return bool(_SAFE_REF_PATTERN.match(ref)) and len(ref) <= 256


def _find_git_root(project_roots: dict) -> Optional[str]:
    """Find the first valid git root from project_roots."""
    for root in project_roots.values():
        if root and Path(root).exists() and (Path(root) / ".git").exists():
            return root
    return None


def _run_git_diff(root: str, mode: str, base: str) -> str:
    """Run git diff with the appropriate flags.

    Args:
        root: Git repository root
        mode: "unstaged", "staged", "committed", "branch"
        base: SHA or branch name (for committed/branch modes)

    Returns:
        Raw unified diff text
    """
    cmd = ["git", "-C", root, "diff", "--unified=0", "--no-color"]

    if mode == "staged":
        cmd.append("--cached")
    elif mode == "committed":
        if not base:
            base = "HEAD~1"
        cmd.extend([base, "HEAD"])
    elif mode == "branch":
        if not base:
            base = "main"
        cmd.extend([f"{base}...HEAD"])
    # mode == "unstaged" is the default (no extra args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=root,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _parse_unified_diff(diff_text: str) -> Dict[str, List[Tuple[int, int]]]:
    """Parse unified diff (--unified=0) output to {file: [(start, end), ...]}.

    Returns a dict mapping file paths to lists of changed line ranges (in the new file).
    """
    file_changes: Dict[str, List[Tuple[int, int]]] = {}
    current_file = None

    for line in diff_text.split("\n"):
        # Detect file path from +++ header
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in file_changes:
                file_changes[current_file] = []
        elif line.startswith("+++ /dev/null"):
            current_file = None  # File deleted
        elif line.startswith("--- a/") and not line.startswith("--- /dev/null"):
            # Detect deleted file
            pass
        elif line.startswith("@@ ") and current_file:
            m = _HUNK_HEADER.match(line)
            if m:
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) else 1
                if new_count > 0:
                    file_changes[current_file].append(
                        (new_start, new_start + new_count - 1)
                    )

    return file_changes


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Check if two line ranges overlap."""
    return a_start <= b_end and b_start <= a_end


def _match_symbols_to_changes(
    _idx,
    project_filter: Optional[str],
    file_changes: Dict[str, List[Tuple[int, int]]],
) -> List[dict]:
    """Match changed file hunks to indexed symbols.

    For each symbol that overlaps with a changed hunk, classify the change type
    using signature analysis.
    """
    index = _idx.load_index()
    symbols = index.get("symbols", {})
    project_roots = index.get("project_roots", {})
    matched = []

    for sym_id, sym in symbols.items():
        sym_project = sym_id.split(":")[0] if ":" in sym_id else ""
        if project_filter and project_filter.lower() not in sym_project.lower():
            continue

        sym_path = sym.get("path", "")
        start_line = sym.get("start_line", 0)
        end_line = sym.get("end_line", 0)
        if not sym_path or not start_line:
            continue

        # Check if this file has changes
        hunks = file_changes.get(sym_path)
        if not hunks:
            continue

        # Check if any hunk overlaps with symbol line range
        has_overlap = any(
            _ranges_overlap(start_line, end_line, h_start, h_end)
            for h_start, h_end in hunks
        )
        if not has_overlap:
            continue

        # Classify change type using signature analysis
        change_kind = ChangeKind.BODY_CHANGE
        old_content = _idx.get_symbol_content_text(sym_id, sym)

        # Try to read new content from filesystem
        root = project_roots.get(sym_project, "")
        if root:
            full_path = Path(root) / sym_path
            if full_path.exists():
                try:
                    file_content = full_path.read_text(encoding="utf-8", errors="replace")
                    # Extract the symbol's new content by line range
                    lines = file_content.split("\n")
                    new_content = "\n".join(lines[max(0, start_line - 1):end_line])
                    if old_content and new_content:
                        change_kind = classify_symbol_change(
                            sym.get("name", ""),
                            old_content,
                            new_content,
                            sym_path,
                        )
                except Exception:
                    pass

        matched.append({
            "symbol_id": sym_id,
            "name": sym.get("name", ""),
            "type": sym.get("type", ""),
            "path": sym_path,
            "project": sym_project,
            "line_range": [start_line, end_line],
            "change_type": change_kind.value,
        })

    return matched


def _classify_risk(caller_count: int, change_type: str) -> str:
    """Classify risk based on caller count and change type."""
    if change_type in (ChangeKind.BODY_CHANGE.value,):
        if caller_count == 0:
            return "safe"
        return "low"

    # signature_change, rename, return_type_change, deleted
    if caller_count == 0:
        return "safe"
    elif caller_count <= 3:
        return "low"
    elif caller_count <= 10:
        return "moderate"
    else:
        return "high"


def impact_from_diff(
    _idx,
    mode: str = "unstaged",
    base: str = "",
    project: str = None,
) -> dict:
    """Parse git diff, find affected indexed symbols, and run impact analysis.

    Args:
        _idx: mcp_server module reference
        mode: "unstaged" | "staged" | "committed" | "branch"
        base: SHA or branch name (for committed/branch modes)
        project: Filter to a specific project

    Returns:
        Dict with changed symbols, their impact, and risk summary
    """
    # Validate inputs
    if mode not in ("unstaged", "staged", "committed", "branch"):
        return {"error": f"Invalid mode: {mode}. Use: unstaged, staged, committed, branch"}

    if not _validate_ref(base):
        return {"error": f"Invalid base ref: {base}"}

    # Find git root
    index = _idx.load_index()
    project_roots = index.get("project_roots", {})
    git_root = _find_git_root(project_roots)
    if not git_root:
        return {"error": "No git repository found in indexed project roots"}

    # Run git diff
    diff_text = _run_git_diff(git_root, mode, base)
    if not diff_text:
        return {
            "mode": mode,
            "total_changed_files": 0,
            "total_changed_symbols": 0,
            "symbols": [],
            "summary": {"high_risk": 0, "moderate_risk": 0, "low_risk": 0, "safe": 0},
            "next_action": "No changes detected." if mode == "unstaged" else f"No changes in {mode} mode.",
        }

    # Parse diff into file → hunk ranges
    file_changes = _parse_unified_diff(diff_text)

    # Match hunks to indexed symbols
    changed_symbols = _match_symbols_to_changes(_idx, project, file_changes)

    # For each changed symbol, get impact data
    symbols_with_impact = []
    risk_summary = {"high_risk": 0, "moderate_risk": 0, "low_risk": 0, "safe": 0}

    for sym in changed_symbols:
        impact_result = _idx.impact_analysis(sym["symbol_id"])
        caller_count = impact_result.get("affected_count", 0)
        risk = _classify_risk(caller_count, sym["change_type"])

        # Count affected projects
        affected_projects = set()
        for affected in impact_result.get("affected", []):
            aid = affected.get("id", "")
            if ":" in aid:
                affected_projects.add(aid.split(":")[0])

        sym_entry = {
            "symbol_id": sym["symbol_id"],
            "name": sym["name"],
            "type": sym["type"],
            "path": sym["path"],
            "project": sym["project"],
            "change_type": sym["change_type"],
            "impact": {
                "risk": risk,
                "total_callers": caller_count,
                "affected_projects": sorted(affected_projects),
            },
        }
        symbols_with_impact.append(sym_entry)

        risk_key = f"{risk}_risk" if risk != "safe" else "safe"
        risk_summary[risk_key] = risk_summary.get(risk_key, 0) + 1

    # Sort: high risk first
    risk_order = {"high": 0, "moderate": 1, "low": 2, "safe": 3}
    symbols_with_impact.sort(key=lambda s: risk_order.get(s["impact"]["risk"], 4))

    # Generate next_action hint
    high_risk_symbols = [s for s in symbols_with_impact if s["impact"]["risk"] == "high"]
    if high_risk_symbols:
        names = ", ".join(s["name"] for s in high_risk_symbols[:3])
        callers = sum(s["impact"]["total_callers"] for s in high_risk_symbols)
        projects = set()
        for s in high_risk_symbols:
            projects.update(s["impact"]["affected_projects"])
        next_action = f"Review high-risk symbols: {names} ({callers} callers across {len(projects)} project(s)). Use edit_impact_preview for details."
    elif symbols_with_impact:
        next_action = f"All {len(symbols_with_impact)} changed symbols are low-risk or safe."
    else:
        next_action = "No indexed symbols affected by this diff."

    return {
        "mode": mode,
        "base": base or "(default)",
        "total_changed_files": len(file_changes),
        "total_changed_symbols": len(symbols_with_impact),
        "symbols": symbols_with_impact,
        "summary": risk_summary,
        "next_action": next_action,
    }
