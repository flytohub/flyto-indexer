"""
Git History Intelligence — hotspot detection, co-change analysis, churn tracking, and risk scoring.

Uses git log/numstat output to surface files and commits that warrant attention.
All git operations have a 30-second timeout. Gracefully handles missing git repos.
"""

import os
import re
import subprocess
import time
from typing import Dict, List, Optional, Tuple

try:
    from ..index_store import load_index, get_symbol_content_text
except ImportError:
    from index_store import load_index, get_symbol_content_text


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_log_cache = {}        # key: (git_root, args_tuple) -> parsed entries
_log_cache_ts = 0.0    # monotonic timestamp of last cache fill
_LOG_CACHE_TTL = 60.0  # seconds


def _find_git_root(path: str) -> Optional[str]:
    """Walk up from *path* looking for a `.git/` directory. Returns None if not found."""
    current = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _get_project_root(project: Optional[str] = None) -> Tuple[str, str]:
    """Return (project_name, project_root) from the index.

    If *project* is None, picks the first available project.
    Raises ValueError when no suitable project is found.
    """
    index = load_index()
    project_roots = index.get("project_roots", {})
    projects = index.get("projects", [])

    if project:
        matches = [p for p in projects if project.lower() in p.lower()]
        if not matches:
            raise ValueError(f"Project not found: {project}")
        proj_name = matches[0]
    else:
        if not projects:
            raise ValueError("No indexed projects found")
        proj_name = projects[0]

    root = project_roots.get(proj_name)
    if not root or not os.path.isdir(root):
        # Fallback: try to find project root from index directory
        # The index is usually at <project_root>/.flyto-index/
        try:
            from ..index_store import _discover_index_dirs
        except ImportError:
            from index_store import _discover_index_dirs

        for idx_dir in _discover_index_dirs():
            candidate = idx_dir.parent
            if candidate.is_dir():
                git_root = _find_git_root(str(candidate))
                if git_root:
                    return proj_name, str(candidate)

        # Last resort: try CWD
        cwd_git = _find_git_root(os.getcwd())
        if cwd_git:
            return proj_name, cwd_git

        raise ValueError(f"Project root not found on disk: {proj_name}")
    return proj_name, root


def _run_git(args: List[str], cwd: str, timeout: int = 30) -> str:
    """Run a git command and return stdout. Raises RuntimeError on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except FileNotFoundError:
        raise RuntimeError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git command timed out after {timeout}s: git {' '.join(args)}")

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"git failed (rc={result.returncode}): {stderr}")
    return result.stdout


def _parse_log_with_files(log_text: str) -> List[dict]:
    """Parse output of ``git log --format='COMMIT:%H|%at|%an|%s' --name-only``.

    Returns a list of dicts:
        {hash, timestamp, author, message, files: [str]}
    """
    entries = []  # type: List[dict]
    current = None  # type: Optional[dict]

    for line in log_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("COMMIT:"):
            if current is not None:
                entries.append(current)
            parts = line[len("COMMIT:"):].split("|", 3)
            if len(parts) < 4:
                current = None
                continue
            current = {
                "hash": parts[0],
                "timestamp": int(parts[1]) if parts[1].isdigit() else 0,
                "author": parts[2],
                "message": parts[3],
                "files": [],
            }
        elif current is not None:
            current["files"].append(line)

    if current is not None:
        entries.append(current)

    return entries


def _parse_log_with_numstat(log_text: str) -> List[dict]:
    """Parse output of ``git log --format='COMMIT:%H|%at|%an|%s' --numstat``.

    Returns a list of dicts:
        {hash, timestamp, author, message, files: [{path, insertions, deletions}]}
    """
    entries = []  # type: List[dict]
    current = None  # type: Optional[dict]

    for line in log_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("COMMIT:"):
            if current is not None:
                entries.append(current)
            parts = line[len("COMMIT:"):].split("|", 3)
            if len(parts) < 4:
                current = None
                continue
            current = {
                "hash": parts[0],
                "timestamp": int(parts[1]) if parts[1].isdigit() else 0,
                "author": parts[2],
                "message": parts[3],
                "files": [],
            }
        elif current is not None:
            # numstat lines: "insertions\tdeletions\tpath"
            numstat_match = re.match(r"^(\d+|-)\t(\d+|-)\t(.+)$", line)
            if numstat_match:
                ins = numstat_match.group(1)
                dels = numstat_match.group(2)
                current["files"].append({
                    "path": numstat_match.group(3),
                    "insertions": int(ins) if ins != "-" else 0,
                    "deletions": int(dels) if dels != "-" else 0,
                })

    if current is not None:
        entries.append(current)

    return entries


def _get_cached_log(git_root: str, extra_args: tuple) -> List[dict]:
    """Return parsed log entries, using a module-level TTL cache."""
    global _log_cache, _log_cache_ts

    now = time.monotonic()
    if now - _log_cache_ts > _LOG_CACHE_TTL:
        _log_cache.clear()
        _log_cache_ts = now

    key = (git_root, extra_args)
    if key in _log_cache:
        return _log_cache[key]

    args = ["log", '--format=COMMIT:%H|%at|%an|%s', "--name-only"] + list(extra_args)
    raw = _run_git(args, cwd=git_root)
    entries = _parse_log_with_files(raw)
    _log_cache[key] = entries
    return entries


def _lazy_quality():
    """Lazy import for quality module (find_complex_functions)."""
    try:
        from .. import quality
    except ImportError:
        import quality
    return quality


# ---------------------------------------------------------------------------
# Tool 1: git_hotspots
# ---------------------------------------------------------------------------

def git_hotspots(project: Optional[str] = None, max_results: int = 20) -> dict:
    """Find files that change most frequently and cross-reference with complexity.

    Hotspot score = commit_count * (1 + complexity / 10).
    """
    try:
        proj_name, proj_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    git_root = _find_git_root(proj_root)
    if not git_root:
        return {"error": f"No git repository found for {proj_name}"}

    try:
        entries = _get_cached_log(git_root, ("--since=1 year ago",))
    except RuntimeError as e:
        return {"error": str(e)}

    # Relativize file paths to project root for matching
    git_root_abs = os.path.abspath(git_root)
    proj_root_abs = os.path.abspath(proj_root)
    proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
    if proj_prefix == ".":
        proj_prefix = ""

    # Count commits per file and track per-author commit counts
    file_commits = {}   # type: Dict[str, int]
    file_authors = {}   # type: Dict[str, Dict[str, int]]

    for entry in entries:
        for f in entry["files"]:
            # Filter to project files
            if proj_prefix and not f.startswith(proj_prefix + "/") and f != proj_prefix:
                continue
            rel = f[len(proj_prefix):].lstrip("/") if proj_prefix else f
            if not rel:
                continue
            file_commits[rel] = file_commits.get(rel, 0) + 1
            if rel not in file_authors:
                file_authors[rel] = {}
            author = entry["author"]
            file_authors[rel][author] = file_authors[rel].get(author, 0) + 1

    # Cross-reference with complexity
    complexity_lookup = {}  # type: Dict[str, float]
    try:
        cx_result = _lazy_quality().find_complex_functions(project=proj_name, max_results=500, min_score=0)
        for item in cx_result.get("results", []):
            path = item.get("path", "")
            score = item.get("score", 0)
            if path in complexity_lookup:
                complexity_lookup[path] = max(complexity_lookup[path], score)
            else:
                complexity_lookup[path] = score
    except Exception:
        pass  # Complexity data is optional enrichment

    # Build hotspot list
    hotspots = []
    for path, count in file_commits.items():
        cx = complexity_lookup.get(path, 0)
        hotspot_score = count * (1 + cx / 10.0)
        author_counts = file_authors.get(path, {})
        hotspots.append({
            "path": path,
            "project": proj_name,
            "commit_count": count,
            "complexity_score": cx,
            "hotspot_score": round(hotspot_score, 2),
            "recent_authors": sorted(author_counts.keys()),
            "primary_author": max(author_counts, key=author_counts.get) if author_counts else "",
        })

    hotspots.sort(key=lambda h: h["hotspot_score"], reverse=True)
    hotspots = hotspots[:max_results]

    return {
        "hotspots": hotspots,
        "total_files_analyzed": len(file_commits),
    }


# ---------------------------------------------------------------------------
# Tool 2: git_cochange
# ---------------------------------------------------------------------------

def git_cochange(path: str, project: Optional[str] = None, max_results: int = 10) -> dict:
    """Find files that frequently change together with *path*."""
    try:
        proj_name, proj_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    git_root = _find_git_root(proj_root)
    if not git_root:
        return {"error": f"No git repository found for {proj_name}"}

    try:
        entries = _get_cached_log(git_root, ("--since=1 year ago",))
    except RuntimeError as e:
        return {"error": str(e)}

    git_root_abs = os.path.abspath(git_root)
    proj_root_abs = os.path.abspath(proj_root)
    proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
    if proj_prefix == ".":
        proj_prefix = ""

    # Normalize path
    norm_path = path.replace("\\", "/").strip("/")

    # Find commits touching the target path
    target_commits = []
    for entry in entries:
        rel_files = []
        for f in entry["files"]:
            if proj_prefix and not f.startswith(proj_prefix + "/") and f != proj_prefix:
                continue
            rel = f[len(proj_prefix):].lstrip("/") if proj_prefix else f
            rel_files.append(rel)

        if norm_path in rel_files:
            target_commits.append((entry, rel_files))

    total_commits = len(target_commits)
    if total_commits == 0:
        return {
            "target_path": norm_path,
            "total_commits": 0,
            "cochanges": [],
        }

    # Count co-occurrences
    cochange_count = {}   # type: Dict[str, int]
    cochange_samples = {}  # type: Dict[str, List[str]]

    # Determine test-file counterpart to exclude obvious pairs
    base_name = os.path.splitext(os.path.basename(norm_path))[0]
    test_variants = {
        "test_" + base_name,
        base_name + "_test",
        base_name + ".test",
        base_name + ".spec",
    }

    for entry, rel_files in target_commits:
        for f in rel_files:
            if f == norm_path:
                continue
            f_base = os.path.splitext(os.path.basename(f))[0]
            if f_base in test_variants:
                continue
            cochange_count[f] = cochange_count.get(f, 0) + 1
            if f not in cochange_samples:
                cochange_samples[f] = []
            if len(cochange_samples[f]) < 5:
                cochange_samples[f].append(entry["hash"][:8])

    # Filter: min 2 co-changes
    results = []
    for f, count in cochange_count.items():
        if count < 2:
            continue
        ratio = round(count / total_commits, 3)
        results.append({
            "path": f,
            "frequency": count,
            "ratio": ratio,
            "sample_commits": cochange_samples.get(f, []),
        })

    results.sort(key=lambda r: r["frequency"], reverse=True)
    results = results[:max_results]

    return {
        "target_path": norm_path,
        "total_commits": total_commits,
        "cochanges": results,
    }


# ---------------------------------------------------------------------------
# Tool 3: git_churn
# ---------------------------------------------------------------------------

def git_churn(path: Optional[str] = None, project: Optional[str] = None, days: int = 90) -> dict:
    """Measure code churn (insertions/deletions) for a file or project."""
    try:
        proj_name, proj_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    git_root = _find_git_root(proj_root)
    if not git_root:
        return {"error": f"No git repository found for {proj_name}"}

    since = "%d days ago" % days

    try:
        if path:
            # Resolve to git-relative path
            git_root_abs = os.path.abspath(git_root)
            proj_root_abs = os.path.abspath(proj_root)
            proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
            if proj_prefix == ".":
                git_path = path.replace("\\", "/").strip("/")
            else:
                git_path = os.path.join(proj_prefix, path.replace("\\", "/").strip("/"))

            raw = _run_git(
                ["log", "--follow", '--format=COMMIT:%H|%at|%an|%s', "--numstat",
                 "--since=%s" % since, "--", git_path],
                cwd=git_root,
            )
        else:
            raw = _run_git(
                ["log", '--format=COMMIT:%H|%at|%an|%s', "--numstat",
                 "--since=%s" % since],
                cwd=git_root,
            )
    except RuntimeError as e:
        return {"error": str(e)}

    entries = _parse_log_with_numstat(raw)

    git_root_abs = os.path.abspath(git_root)
    proj_root_abs = os.path.abspath(proj_root)
    proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
    if proj_prefix == ".":
        proj_prefix = ""

    total_ins = 0
    total_dels = 0
    authors = set()
    recent_commits = []

    for entry in entries:
        commit_ins = 0
        commit_dels = 0
        for finfo in entry["files"]:
            fp = finfo["path"]
            # Filter to project files when no specific path
            if not path:
                if proj_prefix and not fp.startswith(proj_prefix + "/") and fp != proj_prefix:
                    continue
            commit_ins += finfo["insertions"]
            commit_dels += finfo["deletions"]
        total_ins += commit_ins
        total_dels += commit_dels
        authors.add(entry["author"])
        if len(recent_commits) < 20:
            recent_commits.append({
                "hash": entry["hash"][:8],
                "date": time.strftime("%Y-%m-%d", time.gmtime(entry["timestamp"])) if entry["timestamp"] else "unknown",
                "author": entry["author"],
                "message": entry["message"],
                "lines_changed": commit_ins + commit_dels,
            })

    # Map to symbols if path is given
    symbols_churn = []
    if path:
        norm_path = path.replace("\\", "/").strip("/")
        index = load_index()
        all_symbols = index.get("symbols", {})
        for sym_id, sym in all_symbols.items():
            sym_path = sym.get("path", "")
            if sym_path != norm_path:
                continue
            start = sym.get("start_line", 0)
            end = sym.get("end_line", start)
            line_span = max(end - start, 1)
            # Approximate: churn proportional to symbol's share of file lines
            # This is a rough estimate since we don't have per-line git blame
            total_lines = total_ins + total_dels
            if total_lines > 0:
                est_churn = round(total_lines * (line_span / max(line_span, 1)), 1)
            else:
                est_churn = 0
            symbols_churn.append({
                "name": sym.get("name", ""),
                "symbol_id": sym_id,
                "estimated_churn": est_churn,
            })

    result = {
        "path": path,
        "project": proj_name,
        "total_commits": len(entries),
        "unique_authors": len(authors),
        "total_insertions": total_ins,
        "total_deletions": total_dels,
        "recent_commits": recent_commits,
    }
    if path:
        result["symbols"] = symbols_churn
    return result


# ---------------------------------------------------------------------------
# Tool 4: git_risk_commits
# ---------------------------------------------------------------------------

_RISK_MESSAGE_RE = re.compile(r"\b(fix|hotfix|workaround|hack|revert)\b", re.IGNORECASE)


def git_risk_commits(
    project: Optional[str] = None,
    days: int = 30,
    max_results: int = 15,
) -> dict:
    """Score recent commits by risk heuristics."""
    try:
        proj_name, proj_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    git_root = _find_git_root(proj_root)
    if not git_root:
        return {"error": f"No git repository found for {proj_name}"}

    since = "%d days ago" % days

    try:
        raw = _run_git(
            ["log", '--format=COMMIT:%H|%at|%an|%s', "--numstat",
             "--since=%s" % since],
            cwd=git_root,
        )
    except RuntimeError as e:
        return {"error": str(e)}

    entries = _parse_log_with_numstat(raw)

    git_root_abs = os.path.abspath(git_root)
    proj_root_abs = os.path.abspath(proj_root)
    proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
    if proj_prefix == ".":
        proj_prefix = ""

    # Build complexity lookup
    complexity_lookup = {}  # type: Dict[str, float]
    try:
        cx_result = _lazy_quality().find_complex_functions(project=proj_name, max_results=500, min_score=0)
        for item in cx_result.get("results", []):
            p = item.get("path", "")
            s = item.get("score", 0)
            if p in complexity_lookup:
                complexity_lookup[p] = max(complexity_lookup[p], s)
            else:
                complexity_lookup[p] = s
    except Exception:
        pass

    scored_commits = []

    for entry in entries:
        files_in_proj = []
        total_ins = 0
        total_dels = 0

        for finfo in entry["files"]:
            fp = finfo["path"]
            if proj_prefix and not fp.startswith(proj_prefix + "/") and fp != proj_prefix:
                continue
            rel = fp[len(proj_prefix):].lstrip("/") if proj_prefix else fp
            files_in_proj.append(rel)
            total_ins += finfo["insertions"]
            total_dels += finfo["deletions"]

        if not files_in_proj:
            continue

        n_files = len(files_in_proj)
        lines_changed = total_ins + total_dels
        risk_score = 0
        risk_factors = []

        # +2 per file over 5
        if n_files > 5:
            extra = (n_files - 5) * 2
            risk_score += extra
            risk_factors.append("%d files changed (+%d)" % (n_files, extra))

        # +3 if message matches risky keywords
        if _RISK_MESSAGE_RE.search(entry["message"]):
            risk_score += 3
            risk_factors.append("risky keyword in message (+3)")

        # +1 per 100 lines changed
        if lines_changed > 0:
            line_risk = lines_changed // 100
            if line_risk > 0:
                risk_score += line_risk
                risk_factors.append("%d lines changed (+%d)" % (lines_changed, line_risk))

        # +2 if touches file with complexity > 5
        touched_complex = False
        for rel in files_in_proj:
            if complexity_lookup.get(rel, 0) > 5:
                touched_complex = True
                break
        if touched_complex:
            risk_score += 2
            risk_factors.append("touches complex file (+2)")

        scored_commits.append({
            "hash": entry["hash"][:8],
            "message": entry["message"],
            "author": entry["author"],
            "date": time.strftime("%Y-%m-%d", time.gmtime(entry["timestamp"])) if entry["timestamp"] else "unknown",
            "risk_score": risk_score,
            "risk_factors": risk_factors,
            "stats": {
                "files": n_files,
                "insertions": total_ins,
                "deletions": total_dels,
            },
        })

    scored_commits.sort(key=lambda c: c["risk_score"], reverse=True)
    scored_commits = scored_commits[:max_results]

    return {
        "commits": scored_commits,
    }
