"""
Post-change validation — run ruff (lint) and pytest on a project.

Usage:
    validate_changes(project="flyto-indexer", run_tests=True)
"""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

try:
    from ..index_store import load_index
except ImportError:
    from index_store import load_index


DEFAULT_PYTEST_TIMEOUT_SECONDS = 900
MIN_PYTEST_TIMEOUT_SECONDS = 30
MAX_PYTEST_TIMEOUT_SECONDS = 3600


def _pytest_timeout_seconds() -> int:
    """Return a bounded timeout suitable for full stress/subprocess suites."""
    raw = os.environ.get("FLYTO_INDEXER_PYTEST_TIMEOUT", "").strip()
    try:
        configured = int(raw) if raw else DEFAULT_PYTEST_TIMEOUT_SECONDS
    except ValueError:
        configured = DEFAULT_PYTEST_TIMEOUT_SECONDS
    return max(MIN_PYTEST_TIMEOUT_SECONDS, min(configured, MAX_PYTEST_TIMEOUT_SECONDS))


def _lint_targets(project_root: str, lint_paths: list[str]) -> list[str]:
    """Return existing in-project Python files/directories for a task lint."""
    root = Path(project_root).resolve()
    targets: set[str] = set()
    for raw in lint_paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        requested = Path(raw.strip())
        candidate = (
            requested.resolve()
            if requested.is_absolute()
            else (root / requested).resolve()
        )
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if not candidate.exists():
            continue
        if candidate.is_file() and candidate.suffix != ".py":
            continue
        if not (candidate.is_file() or candidate.is_dir()):
            continue
        targets.add(relative.as_posix() or ".")
    return sorted(targets)


def _run_ruff(
    project_root: str,
    lint_paths: list[str] | None = None,
) -> dict:
    """Run Ruff repository-wide or on one frozen task's Python targets."""
    targets = ["."] if lint_paths is None else _lint_targets(
        project_root,
        lint_paths,
    )
    result = {
        "status": "skipped",
        "errors": 0,
        "warnings": 0,
        "output": "",
        "scope": "repository" if lint_paths is None else "task_targets",
        "targets": targets,
    }

    if not targets:
        result["output"] = "No existing Python targets declared by task contract"
        return result

    cmds = [
        ["ruff", "check", *targets],
        [sys.executable, "-m", "ruff", "check", *targets],
    ]

    for cmd in cmds:
        try:
            proc = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            result["output"] = output[:2000]

            # Count errors and warnings from ruff output
            # Ruff lines look like: path.py:10:1: E501 ...
            error_count = 0
            warning_count = 0
            for line in output.splitlines():
                if re.match(r"^.+:\d+:\d+:\s+(E|F)\d+", line):
                    error_count += 1
                elif re.match(r"^.+:\d+:\d+:\s+(W|C|D)\d+", line):
                    warning_count += 1

            result["errors"] = error_count
            result["warnings"] = warning_count
            result["status"] = "pass" if proc.returncode == 0 else "fail"
            return result

        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            result["status"] = "fail"
            result["output"] = "ruff timed out after 30 seconds"
            return result
        except Exception as e:
            result["status"] = "fail"
            result["output"] = str(e)[:2000]
            return result

    result["status"] = "skipped"
    result["output"] = "ruff not found. Install with: pip install ruff"
    return result


def _run_pytest(project_root: str, test_path: str = None) -> dict:
    """Run pytest on project root. Returns status dict."""
    result = {
        "status": "skipped",
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "output": "",
    }

    cmd = [sys.executable, "-m", "pytest"]
    if test_path:
        cmd.extend(shlex.split(test_path))
    # With no explicit target, let pytest honor testpaths from pyproject.toml,
    # pytest.ini, tox.ini, or setup.cfg. Passing "." overrides that contract
    # and can accidentally collect example scripts named test_*.py.
    cmd.extend(["-x", "--tb=short", "-q"])

    try:
        timeout_seconds = _pytest_timeout_seconds()
        proc = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        result["output"] = output[:3000]

        # Parse pytest summary line, e.g.:
        # "5 passed", "3 failed, 2 passed", "1 error"
        summary_match = re.search(
            r"(\d+)\s+passed", output
        )
        if summary_match:
            result["passed"] = int(summary_match.group(1))

        failed_match = re.search(r"(\d+)\s+failed", output)
        if failed_match:
            result["failed"] = int(failed_match.group(1))

        error_match = re.search(r"(\d+)\s+error", output)
        if error_match:
            result["errors"] = int(error_match.group(1))

        if proc.returncode == 0:
            result["status"] = "pass"
        elif result["errors"] > 0:
            result["status"] = "error"
        else:
            result["status"] = "fail"

        return result

    except FileNotFoundError:
        result["status"] = "skipped"
        result["output"] = "pytest not found. Install with: pip install pytest"
        return result
    except subprocess.TimeoutExpired:
        result["status"] = "error"
        result["output"] = (
            f"pytest timed out after {_pytest_timeout_seconds()} seconds"
        )
        return result
    except Exception as e:
        result["status"] = "error"
        result["output"] = str(e)[:3000]
        return result


def validate_changes(
    project: str = None,
    run_tests: bool = True,
    test_path: str = None,
    lint_paths: list[str] | None = None,
) -> dict:
    """
    Run code quality checks (ruff) and tests (pytest) on a project.

    Args:
        project: Project name from index. If omitted, auto-detect.
        run_tests: Whether to run pytest. Default: True.
        test_path: Specific test file or directory. If omitted, runs all tests.
        lint_paths: Exact task-owned paths to lint. ``None`` preserves the
            repository-wide validation contract; an empty list is a docs-only
            task and does not fall back to linting unrelated legacy files.

    Returns:
        Dict with ruff results, pytest results, and overall pass/fail.
    """
    index = load_index()
    project_roots = index.get("project_roots", {})

    # Resolve project root
    project_name = project
    project_root = None

    project_path = Path(project).expanduser() if project else None
    cwd_project_path = (Path.cwd() / project).resolve() if project and not project_path.is_absolute() else None

    if project and project in project_roots:
        project_root = project_roots[project]
        project_name = project
    elif project_path and project_path.exists():
        project_root = str(project_path.resolve())
        project_name = project_path.name
    elif cwd_project_path and cwd_project_path.exists():
        project_root = str(cwd_project_path)
        project_name = cwd_project_path.name
    elif project_roots:
        if project:
            return {"error": "Project '{}' not found. Available: {}".format(
                project, ", ".join(sorted(project_roots.keys()))
            )}
        if len(project_roots) == 1:
            project_name = next(iter(project_roots))
            project_root = project_roots[project_name]
        else:
            # Use CWD as fallback
            project_root = str(Path.cwd())
            project_name = Path(project_root).name
    else:
        project_root = str(Path.cwd())
        project_name = Path(project_root).name

    # Run ruff
    if lint_paths is None:
        ruff_result = _run_ruff(project_root)
    else:
        ruff_result = _run_ruff(project_root, lint_paths)

    # Run pytest
    if run_tests:
        pytest_result = _run_pytest(project_root, test_path)
    else:
        pytest_result = {
            "status": "skipped",
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "output": "Tests skipped (run_tests=False)",
        }

    # Determine overall status
    ruff_ok = ruff_result["status"] in ("pass", "skipped")
    pytest_ok = pytest_result["status"] in ("pass", "skipped")
    overall = "pass" if (ruff_ok and pytest_ok) else "fail"

    return {
        "project": project_name,
        "project_root": str(project_root),
        "ruff": ruff_result,
        "pytest": pytest_result,
        "overall": overall,
    }
