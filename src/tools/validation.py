"""
Post-change validation — run ruff (lint) and pytest on a project.

Usage:
    validate_changes(project="flyto-indexer", run_tests=True)
"""

import subprocess
import re
from pathlib import Path

try:
    from ..index_store import load_index
except ImportError:
    from index_store import load_index


def _run_ruff(project_root: str) -> dict:
    """Run ruff check on project root. Returns status dict."""
    result = {
        "status": "skipped",
        "errors": 0,
        "warnings": 0,
        "output": "",
    }

    cmds = [
        ["ruff", "check", "."],
        ["python", "-m", "ruff", "check", "."],
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

    cmd = ["python", "-m", "pytest", test_path or ".", "-x", "--tb=short", "-q"]

    try:
        proc = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
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
        result["output"] = "pytest timed out after 120 seconds"
        return result
    except Exception as e:
        result["status"] = "error"
        result["output"] = str(e)[:3000]
        return result


def validate_changes(
    project: str = None,
    run_tests: bool = True,
    test_path: str = None,
) -> dict:
    """
    Run code quality checks (ruff) and tests (pytest) on a project.

    Args:
        project: Project name from index. If omitted, auto-detect.
        run_tests: Whether to run pytest. Default: True.
        test_path: Specific test file or directory. If omitted, runs all tests.

    Returns:
        Dict with ruff results, pytest results, and overall pass/fail.
    """
    index = load_index()
    project_roots = index.get("project_roots", {})

    # Resolve project root
    project_name = project
    project_root = None

    if project and project in project_roots:
        project_root = project_roots[project]
        project_name = project
    elif project_roots:
        if len(project_roots) == 1:
            project_name = next(iter(project_roots))
            project_root = project_roots[project_name]
        elif project:
            return {"error": "Project '{}' not found. Available: {}".format(
                project, ", ".join(sorted(project_roots.keys()))
            )}
        else:
            # Use CWD as fallback
            project_root = str(Path.cwd())
            project_name = Path(project_root).name
    else:
        project_root = str(Path.cwd())
        project_name = Path(project_root).name

    # Run ruff
    ruff_result = _run_ruff(project_root)

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
