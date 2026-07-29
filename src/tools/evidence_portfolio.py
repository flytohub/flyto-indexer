"""Bounded, local Git evidence portfolios and deterministic verdicts."""

import os
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Optional

try:
    from .git_intel import (
        _find_git_root,
        _get_project_root,
        _parse_log_with_numstat,
        _run_git,
    )
except ImportError:
    from git_intel import (  # type: ignore
        _find_git_root,
        _get_project_root,
        _parse_log_with_numstat,
        _run_git,
    )


_MAX_COMMITS = 6
_MAX_FILES = 14
_CODE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".dart", ".go", ".h", ".hpp", ".java",
    ".js", ".jsx", ".kt", ".kts", ".php", ".py", ".rb", ".rs", ".swift",
    ".ts", ".tsx", ".vue",
}
_CONFIG_NAMES = {
    "dockerfile", "makefile", "pyproject.toml", "setup.cfg", "tox.ini",
}
_LOCK_NAMES = {
    "bun.lock", "bun.lockb", "cargo.lock", "composer.lock", "gemfile.lock",
    "go.sum", "package-lock.json", "pnpm-lock.yaml", "poetry.lock",
    "uv.lock", "yarn.lock",
}
_NOISE_SEGMENTS = {
    ".flyto-index", ".next", ".nuxt", ".output", "build", "coverage",
    "dist", "generated", "node_modules", "target", "vendor",
}
_BINARY_EXTENSIONS = {
    ".7z", ".a", ".bin", ".bmp", ".class", ".dll", ".dylib", ".exe",
    ".gif", ".gz", ".ico", ".jar", ".jpeg", ".jpg", ".o", ".pdf", ".png",
    ".pyc", ".so", ".tar", ".tgz", ".ttf", ".woff", ".woff2", ".zip",
}


def _resolve_project(project: Optional[str]) -> tuple[str, str, str]:
    if project and os.path.isdir(os.path.expanduser(project)):
        project_root = os.path.abspath(os.path.expanduser(project))
        project_name = os.path.basename(project_root)
    else:
        project_name, project_root = _get_project_root(project)
    git_root = _find_git_root(project_root)
    if not git_root:
        raise ValueError(f"No git repository found for {project_name}")
    prefix = os.path.relpath(project_root, git_root).replace(os.sep, "/")
    return project_name, project_root, "" if prefix == "." else prefix


def _project_path(path: str, prefix: str) -> Optional[str]:
    normalized = path.replace("\\", "/").lstrip("/")
    if not prefix:
        return normalized
    if normalized == prefix:
        return ""
    if normalized.startswith(prefix + "/"):
        return normalized[len(prefix) + 1:]
    return None


def _classify_file(path: str) -> tuple[str, str]:
    pure = PurePosixPath(path.lower())
    name = pure.name
    suffix = pure.suffix
    if (
        name in _LOCK_NAMES
        or any(part in _NOISE_SEGMENTS for part in pure.parts)
        or suffix in _BINARY_EXTENSIONS
        or name.endswith((".min.js", ".min.css", ".map"))
    ):
        return "noise", "generated_or_machine_managed"
    if "test" in pure.parts or "tests" in pure.parts or name.startswith("test_"):
        return "test", "executable_proof"
    if suffix in _CODE_EXTENSIONS:
        return "code", "implementation"
    if suffix in {".md", ".mdx", ".rst"}:
        return "docs", "documentation"
    if name in _CONFIG_NAMES or suffix in {".toml", ".yaml", ".yml", ".ini"}:
        return "config", "configuration"
    return "other", "supporting"


def _working_tree_paths(git_root: str, prefix: str) -> list[str]:
    raw = _run_git(
        ["status", "--porcelain", "--untracked-files=all"],
        cwd=git_root,
    )
    paths: set[str] = set()
    for line in raw.splitlines():
        value = line[3:] if len(line) > 3 else ""
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        relative = _project_path(value.strip('"'), prefix)
        if relative:
            paths.add(relative)
    return sorted(paths)


def _commit_score(entry: dict, prefix: str, changed_paths: set[str]) -> tuple[float, list[dict], int]:
    weights = {"code": 5, "test": 4, "config": 3, "docs": 2, "other": 1}
    signal_files = []
    noise_count = 0
    score = 0.0
    for item in entry.get("files", []):
        path = _project_path(item.get("path", ""), prefix)
        if path is None:
            continue
        signal, reason = _classify_file(path)
        if signal == "noise":
            noise_count += 1
            continue
        enriched = dict(item)
        enriched.update({"path": path, "signal": signal, "reason": reason})
        signal_files.append(enriched)
        churn = int(item.get("insertions", 0)) + int(item.get("deletions", 0))
        score += weights[signal] + min(churn, 100) / 100
        if path in changed_paths:
            score += 8
    return round(score, 2), signal_files, noise_count


def _iso_timestamp(value: int) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def build_evidence_portfolio(
    project: Optional[str] = None,
    *,
    mode: Optional[str] = None,
    base: str = "",
    changed_paths: Optional[list[str]] = None,
    max_commits: int = 5,
    max_files: int = 12,
) -> dict:
    """Build a compact evidence case file using only the local Git repository."""
    commit_limit = max(1, min(int(max_commits), _MAX_COMMITS))
    file_limit = max(1, min(int(max_files), _MAX_FILES))
    try:
        project_name, project_root, prefix = _resolve_project(project)
        git_root = _find_git_root(project_root)
        if not git_root:
            raise ValueError(f"No git repository found for {project_name}")
        diff_paths = sorted(set(
            changed_paths
            if changed_paths is not None
            else _working_tree_paths(git_root, prefix)
        ))
        history_window = max(12, commit_limit * 3)
        raw_log = _run_git(
            [
                "log",
                "--format=COMMIT:%H|%at|%an|%s",
                "--numstat",
                "--no-renames",
                f"-n{history_window}",
            ],
            cwd=git_root,
        )
        history = _parse_log_with_numstat(raw_log)
    except (RuntimeError, ValueError) as exc:
        return {
            "schema": "evidence-portfolio.v1",
            "status": "unavailable",
            "error": str(exc),
            "limits": {"commits": commit_limit, "files": file_limit},
            "summary": {
                "considered_commits": 0,
                "selected_commits": 0,
                "selected_files": 0,
                "noise_files_excluded": 0,
                "changed_files": 0,
            },
            "commits": [],
            "files": [],
            "diff": None,
        }

    changed_set = set(diff_paths)
    ranked = []
    noise_files: set[str] = set()
    noise_commits = 0
    for entry in history:
        score, signal_files, _noise_count = _commit_score(entry, prefix, changed_set)
        for item in entry.get("files", []):
            path = _project_path(item.get("path", ""), prefix)
            if path and _classify_file(path)[0] == "noise":
                noise_files.add(path)
        if not signal_files:
            noise_commits += 1
            continue
        ranked.append((score, entry, signal_files))
    ranked.sort(
        key=lambda item: (
            -item[0],
            -int(item[1].get("timestamp", 0)),
            item[1].get("hash", ""),
        )
    )
    selected = ranked[:commit_limit]

    file_evidence: dict[str, dict] = {}
    for score, entry, files in selected:
        for item in files:
            path = item["path"]
            evidence = file_evidence.setdefault(
                path,
                {
                    "path": path,
                    "signal": item["signal"],
                    "reason": item["reason"],
                    "insertions": 0,
                    "deletions": 0,
                    "receipts": [],
                    "rank": score + (20 if path in changed_set else 0),
                },
            )
            evidence["insertions"] += int(item.get("insertions", 0))
            evidence["deletions"] += int(item.get("deletions", 0))
            receipt = f"git:{entry.get('hash', '')}:{path}"
            if len(evidence["receipts"]) < 3 and receipt not in evidence["receipts"]:
                evidence["receipts"].append(receipt)

    for path in diff_paths:
        signal, reason = _classify_file(path)
        if signal == "noise":
            noise_files.add(path)
            continue
        evidence = file_evidence.setdefault(
            path,
            {
                "path": path,
                "signal": signal,
                "reason": reason,
                "insertions": 0,
                "deletions": 0,
                "receipts": [],
                "rank": 20,
            },
        )
        evidence["rank"] += 20
        evidence["receipts"].insert(0, f"git:working-tree:{path}")
        evidence["receipts"] = evidence["receipts"][:3]

    ordered_files = sorted(
        file_evidence.values(),
        key=lambda item: (-item.pop("rank"), item["path"]),
    )[:file_limit]
    file_ids = {}
    for index, item in enumerate(ordered_files, 1):
        item["id"] = f"F{index:03d}"
        file_ids[item["path"]] = item["id"]

    commits = []
    for index, (score, entry, files) in enumerate(selected, 1):
        commits.append({
            "id": f"E{index:03d}",
            "sha": entry.get("hash", ""),
            "subject": entry.get("message", ""),
            "committed_at": _iso_timestamp(int(entry.get("timestamp", 0))),
            "signal_score": score,
            "file_refs": [
                file_ids[item["path"]]
                for item in files
                if item["path"] in file_ids
            ],
            "source": f"git:{entry.get('hash', '')}",
        })

    diff_file_refs = [
        file_ids[path]
        for path in diff_paths
        if path in file_ids
    ]
    diff_receipt = {
        "id": "D001",
        "mode": mode or "working_tree",
        "base": base or "(default)",
        "changed_file_count": len(diff_paths),
        "file_refs": diff_file_refs,
        "source": "git:diff" if mode else "git:status",
    }
    return {
        "schema": "evidence-portfolio.v1",
        "status": "captured",
        "limits": {"commits": commit_limit, "files": file_limit},
        "summary": {
            "considered_commits": len(history),
            "selected_commits": len(commits),
            "selected_files": len(ordered_files),
            "noise_files_excluded": len(noise_files),
            "noise_commits_excluded": noise_commits,
            "changed_files": len(diff_paths),
        },
        "commits": commits,
        "files": ordered_files,
        "diff": diff_receipt,
    }


def _portfolio_ref(portfolio: dict) -> list[str]:
    commits = portfolio.get("commits") or []
    return (
        [f"evidence_portfolio.commits.{commits[0]['id']}"]
        if commits
        else []
    )


def build_audit_verdict(audit: dict, portfolio: dict) -> dict:
    """Return a concise audit verdict whose claims point to source receipts."""
    health = audit.get("health") if isinstance(audit.get("health"), dict) else {}
    score = int(health.get("score", 0))
    grade = str(health.get("grade", "unknown"))
    breakdown = health.get("breakdown") or {}
    weakest_name = ""
    weakest_ratio = 1.0
    for name, dimension in breakdown.items():
        if not isinstance(dimension, dict):
            continue
        maximum = dimension.get("max", 25) or 25
        ratio = dimension.get("score", maximum) / maximum
        if ratio < weakest_ratio:
            weakest_name, weakest_ratio = name, ratio
    security = (
        audit.get("security_findings")
        if isinstance(audit.get("security_findings"), dict)
        else {}
    )
    severities = security.get("by_severity") or {}
    severe_findings = int(severities.get("critical", 0)) + int(
        severities.get("high", 0)
    )
    status = "clear" if score >= 80 and severe_findings == 0 else "attention"
    refs = ["audit.health"]
    findings = [{
        "message": f"Overall health is {score}/100 ({grade}).",
        "refs": refs,
    }]
    if weakest_name:
        findings.append({
            "message": f"Weakest measured dimension: {weakest_name}.",
            "refs": [f"audit.health.breakdown.{weakest_name}"],
        })
    if severe_findings:
        findings.append({
            "message": f"Security scan has {severe_findings} high-severity finding(s).",
            "refs": ["audit.security_findings.by_severity"],
        })
    evidence_refs = _portfolio_ref(portfolio)
    if evidence_refs and len(findings) < 3:
        findings.append({
            "message": (
                f"Git case file retained "
                f"{portfolio['summary']['selected_commits']} high-signal commit(s)."
            ),
            "refs": evidence_refs,
        })
    return {
        "schema": "evidence-verdict.v1",
        "status": status,
        "grade": grade,
        "headline": f"{grade} audit: {score}/100",
        "summary": (
            f"{'Healthy' if status == 'clear' else 'Review recommended'}"
            + (
                f"; {severe_findings} high-severity security finding(s)."
                if severe_findings
                else (f"; weakest area is {weakest_name}." if weakest_name else ".")
            )
        ),
        "confidence": "high" if portfolio.get("status") == "captured" else "medium",
        "findings": findings[:3],
    }


def build_impact_verdict(impact: dict, portfolio: dict) -> dict:
    """Return a concise diff verdict linked to impact and Git evidence."""
    summary = impact.get("summary") or {}
    high = int(summary.get("high_risk", 0))
    moderate = int(summary.get("moderate_risk", 0))
    changed = int(impact.get("total_changed_files", 0))
    if high:
        status, grade = "attention", "high"
    elif moderate:
        status, grade = "guarded", "moderate"
    else:
        status, grade = "clear", "low"
    refs = ["impact.summary", "evidence_portfolio.diff.D001"]
    findings = [{
        "message": (
            f"{changed} changed file(s); {high} high-risk and "
            f"{moderate} moderate-risk symbol(s)."
        ),
        "refs": refs,
    }]
    symbols = impact.get("symbols") or []
    if symbols:
        findings.append({
            "message": f"Highest-ranked affected symbol: {symbols[0].get('name', '')}.",
            "refs": ["impact.symbols.0"],
        })
    evidence_refs = _portfolio_ref(portfolio)
    if evidence_refs:
        findings.append({
            "message": "Recent high-signal history is attached for comparison.",
            "refs": evidence_refs,
        })
    return {
        "schema": "evidence-verdict.v1",
        "status": status,
        "grade": grade,
        "headline": f"{grade} diff risk",
        "summary": impact.get("next_action", "Review the attached impact evidence."),
        "confidence": "high" if portfolio.get("status") == "captured" else "medium",
        "findings": findings[:3],
    }
