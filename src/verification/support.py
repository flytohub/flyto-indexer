"""Shared verification filesystem, workspace, and result helpers.

These utilities are deliberately policy-light so verification checks can compose
them without importing the main verification runner.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from ..finding_identity import stable_finding_id
from .regression import _VERIFY_RESULT_SCHEMA_VERSION
from .reporting import _finding_ids_from_check


_PROJECT_MARKERS = (
    ".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
    "composer.json", "Gemfile", "src", "src-next",
)

_SKIP_WORKSPACE_DIRS = {
    ".git", ".flyto-index", ".venv", "venv", "node_modules", "dist",
    "build", "coverage", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache",
}

_CI_CANDIDATES = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".gitlab-ci.yml",
    "cloudbuild.yaml",
    "cloudbuild.yml",
    "Makefile",
)

def _read_ci_files(root: Path) -> tuple[list[Path], str]:
    files: list[Path] = []
    for pattern in _CI_CANDIDATES:
        files.extend(sorted(root.glob(pattern)))
    readable = []
    chunks = []
    for path in files:
        if not path.is_file():
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
            readable.append(path)
        except OSError:
            continue
    return readable, "\n".join(chunks)

def _decode_process_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output

def _discover_workspace_projects(root: Path) -> list[Path]:
    if _looks_like_project(root):
        return [root]
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        return []
    projects = []
    for child in children:
        if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP_WORKSPACE_DIRS:
            continue
        if _looks_like_project(child):
            projects.append(child)
    return projects

def _looks_like_project(path: Path) -> bool:
    return any((path / marker).exists() for marker in _PROJECT_MARKERS)

def _project_has_changes(project: Path, base: str = "") -> bool:
    if not (project / ".git").exists():
        return True
    commands: list[list[str]]
    if base:
        commands = [
            ["git", "-C", str(project), "diff", "--name-only", f"{base}...HEAD"],
            ["git", "-C", str(project), "diff", "--name-only", f"{base}..HEAD"],
            ["git", "-C", str(project), "diff", "--name-only"],
            ["git", "-C", str(project), "diff", "--cached", "--name-only"],
            ["git", "-C", str(project), "ls-files", "--others", "--exclude-standard"],
        ]
    else:
        commands = [
            ["git", "-C", str(project), "diff", "--name-only"],
            ["git", "-C", str(project), "diff", "--cached", "--name-only"],
            ["git", "-C", str(project), "ls-files", "--others", "--exclude-standard"],
        ]
    saw_valid_git = False
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return True
        if result.returncode != 0:
            continue
        saw_valid_git = True
        if _decode_process_output(result.stdout).strip():
            return True
    return not saw_valid_git

def _load_index_json(root: Path) -> dict[str, Any]:
    index_path = root / ".flyto-index" / "index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}

def _git_changed_paths(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []

    paths: list[str] = []
    for line in _decode_process_output(result.stdout).splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            paths.extend(part.strip().replace("\\", "/") for part in raw_path.split(" -> ") if part.strip())
        elif raw_path:
            paths.append(raw_path.replace("\\", "/"))
    return sorted(set(paths))

def _verification_metadata(root: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": _VERIFY_RESULT_SCHEMA_VERSION,
        "project": root.name,
        "git_head": _git_head(root),
        "git_dirty": bool(_git_changed_paths(root)) if (root / ".git").exists() else None,
        "check_count": len(checks),
        "check_fingerprint": _checks_fingerprint(checks),
    }

def _git_head(root: Path) -> str:
    if not (root / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""

def _checks_fingerprint(checks: list[dict[str, Any]]) -> str:
    payload = [
        {
            "name": check.get("name", ""),
            "status": check.get("status", ""),
            "summary": check.get("summary", ""),
            "finding_ids": sorted(_finding_ids_from_check(check)),
        }
        for check in checks
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def _summarize_checks(checks: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks:
        status = str(check.get("status", "fail"))
        summary[status] = summary.get(status, 0) + 1
    return summary

def _finalize(
    root: Path,
    checks: list[dict[str, Any]],
    *,
    pass_override: bool | None = None,
) -> dict[str, Any]:
    for check in checks:
        check.setdefault(
            "finding_id",
            stable_finding_id(
                f"verify/{check.get('name', 'check')}",
                root.name,
            ),
        )
    summary = _summarize_checks(checks)
    return {
        "project": root.name,
        "path": str(root),
        "pass": pass_override if pass_override is not None else summary.get("fail", 0) == 0,
        "summary": summary,
        "metadata": _verification_metadata(root, checks),
        "checks": checks,
    }

