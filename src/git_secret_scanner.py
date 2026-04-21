"""
Git History Secret Scanner — detect leaked secrets in git commit history.

Scans added lines in git history using the same regex patterns as secret_scanner.
Pure Python stdlib, no external dependencies.
"""

import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("flyto-indexer.git-secret-scanner")


def scan_git_history(project_path: str | Path, max_commits: int = 100) -> dict:
    """
    Scan git commit history for leaked secrets.

    Applies the same 18 secret patterns from secret_scanner to added lines
    in git history (not current files — those are handled by scan_secrets).

    Args:
        project_path: Root directory of the git repository.
        max_commits: Maximum number of commits to scan (default: 100).

    Returns:
        Dict with total_leaked, commits_scanned, and leaked_secrets list.
    """
    project_path = Path(project_path).resolve()

    # Import secret patterns from secret_scanner
    try:
        from .secret_scanner import SECRET_PATTERNS, _SEVERITY_MAP, _EXAMPLE_INDICATORS
    except ImportError:
        from secret_scanner import SECRET_PATTERNS, _SEVERITY_MAP, _EXAMPLE_INDICATORS

    # Check if this is a git repo
    git_dir = project_path / ".git"
    if not git_dir.exists():
        return {
            "total_leaked": 0,
            "commits_scanned": 0,
            "leaked_secrets": [],
            "error": "not a git repository",
        }

    # Run git log with patches showing only added files/lines
    try:
        proc = subprocess.run(
            [
                "git", "-C", str(project_path),
                "log", "-p", "--diff-filter=A",
                f"-n{max_commits}",
                "--no-merges",
                "--format=COMMIT:%H%n%an%n%aI",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            logger.warning("git log failed: %s", proc.stderr.strip())
            return {
                "total_leaked": 0,
                "commits_scanned": 0,
                "leaked_secrets": [],
                "error": proc.stderr.strip(),
            }
    except FileNotFoundError:
        return {
            "total_leaked": 0,
            "commits_scanned": 0,
            "leaked_secrets": [],
            "error": "git not found",
        }
    except subprocess.TimeoutExpired:
        return {
            "total_leaked": 0,
            "commits_scanned": 0,
            "leaked_secrets": [],
            "error": "git log timed out",
        }

    output = proc.stdout
    if not output.strip():
        return {
            "total_leaked": 0,
            "commits_scanned": 0,
            "leaked_secrets": [],
        }

    # Parse git log output
    leaked_secrets = []
    commits_scanned = set()
    current_commit = ""
    current_author = ""
    current_date = ""
    current_file = ""

    # Skip binary/lock/doc files in history too
    _SKIP_FILE_PATTERNS = re.compile(
        r"(?:\.min\.js|\.min\.css|\.lock|\.png|\.jpg|\.jpeg|\.gif|\.ico|\.svg|"
        r"\.woff|\.woff2|\.ttf|\.pdf|\.zip|\.gz|\.pyc|\.exe|\.bin|"
        r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|"
        r"go\.sum|Cargo\.lock|Gemfile\.lock|composer\.lock)$",
        re.IGNORECASE,
    )

    # State machine to track whether we are in a test file
    _TEST_PATH_PATTERNS = re.compile(
        r"(?:^|/)(?:tests?|__tests__|specs?|fixtures)/|"
        r"(?:^|/)test_|_test\.py$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$|_test\.go$",
        re.IGNORECASE,
    )

    lines = output.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Parse commit header
        if line.startswith("COMMIT:"):
            current_commit = line[7:].strip()
            commits_scanned.add(current_commit)
            # Next two lines are author and date
            if i + 1 < len(lines):
                current_author = lines[i + 1].strip()
            if i + 2 < len(lines):
                current_date = lines[i + 2].strip()
            i += 3
            continue

        # Parse diff file header
        if line.startswith("diff --git"):
            # Extract filename from "diff --git a/path b/path"
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                current_file = parts[1].strip()
            i += 1
            continue

        # Only scan added lines (lines starting with +, but not +++ header)
        if line.startswith("+") and not line.startswith("+++"):
            # Skip if file should be skipped
            if current_file and (
                _SKIP_FILE_PATTERNS.search(current_file)
                or _TEST_PATH_PATTERNS.search(current_file)
            ):
                i += 1
                continue

            added_line = line[1:]  # Strip the leading +

            # Skip example/placeholder lines
            if _EXAMPLE_INDICATORS.search(added_line):
                i += 1
                continue

            for pattern_name, pattern_re in SECRET_PATTERNS:
                match = pattern_re.search(added_line)
                if match:
                    # Mask the snippet
                    snippet = added_line.strip()[:120]

                    leaked_secrets.append({
                        "commit": current_commit[:12] if current_commit else "",
                        "author": current_author,
                        "date": current_date,
                        "file": current_file,
                        "pattern": pattern_name,
                        "snippet": snippet,
                    })
                    break  # One match per line is enough

        i += 1

    # Signal truncation so callers can warn about incomplete coverage instead
    # of treating a clean result as proof of no historical leaks.
    try:
        total = int(subprocess.run(
            ["git", "-C", str(project_path), "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip() or "0")
    except (subprocess.TimeoutExpired, ValueError):
        total = 0

    truncated = total > len(commits_scanned)
    return {
        "total_leaked": len(leaked_secrets),
        "commits_scanned": len(commits_scanned),
        "leaked_secrets": leaked_secrets,
        "scan_limit": max_commits,
        "total_commits": total,
        "truncated": truncated,
    }
