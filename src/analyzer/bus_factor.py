"""
Bus Factor analyzer — identify files with dangerously low contributor count.

Uses git shortlog to count unique authors per file. Files with bus_factor=1
that are heavily referenced (high fan-in) are critical risks — if that person
leaves, nobody understands the code.

Pure Python stdlib (shells out to git, same pattern as stale_files.py).
"""

import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", "target", "out", ".next", ".nuxt",
})

_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs",
    ".go", ".rs", ".java", ".kt", ".rb", ".php",
    ".vue", ".svelte", ".sql",
})


@dataclass
class FileOwnership:
    """Ownership info for a single file."""
    file: str
    authors: list[str]
    bus_factor: int          # unique author count
    primary_author: str      # most commits
    primary_pct: float       # % of commits by primary
    total_commits: int = 0


@dataclass
class BusFactorReport:
    """Bus factor analysis result."""
    total_files_analyzed: int = 0
    bus_factor_1_count: int = 0
    bus_factor_1_pct: float = 0.0
    avg_bus_factor: float = 0.0
    risk_files: list[FileOwnership] = field(default_factory=list)  # bus_factor=1 files
    all_files: list[FileOwnership] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_files_analyzed": self.total_files_analyzed,
            "bus_factor_1_count": self.bus_factor_1_count,
            "bus_factor_1_pct": round(self.bus_factor_1_pct, 1),
            "avg_bus_factor": round(self.avg_bus_factor, 1),
            "risk_files": [
                {"file": f.file, "bus_factor": f.bus_factor,
                 "primary_author": f.primary_author,
                 "primary_pct": round(f.primary_pct, 1)}
                for f in self.risk_files[:20]
            ],
        }


def _run_git(project_root: Path, args: list[str]) -> str | None:
    """Run a git command, return stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root)] + args,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def analyze_bus_factor(project_root: str | Path) -> BusFactorReport:
    """Analyze bus factor (contributor concentration) per file."""
    project_root = Path(project_root)
    report = BusFactorReport()

    # Get all tracked files with their author stats via a single git log call
    # This is much faster than per-file git blame
    log_output = _run_git(project_root, [
        "log", "--format=%aN", "--name-only", "--no-merges",
        "--diff-filter=ACMR",  # added, copied, modified, renamed
    ])

    if not log_output:
        return report

    # Parse: author line, then file lines, then blank line
    file_authors: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    current_author = ""

    for line in log_output.splitlines():
        line = line.strip()
        if not line:
            current_author = ""
            continue

        if not current_author:
            current_author = line
            continue

        # It's a file path
        file_path = line
        # Filter to code files only
        suffix = Path(file_path).suffix
        if suffix not in _CODE_EXTENSIONS:
            continue
        if any(skip in file_path.split("/") for skip in _SKIP_DIRS):
            continue

        file_authors[file_path][current_author] += 1

    # Build ownership data
    all_files: list[FileOwnership] = []
    total_bus = 0

    for file_path, authors in file_authors.items():
        total_commits = sum(authors.values())
        sorted_authors = sorted(authors.items(), key=lambda x: -x[1])
        primary = sorted_authors[0]

        ownership = FileOwnership(
            file=file_path,
            authors=[a for a, _ in sorted_authors],
            bus_factor=len(authors),
            primary_author=primary[0],
            primary_pct=(primary[1] / total_commits * 100) if total_commits > 0 else 0,
            total_commits=total_commits,
        )
        all_files.append(ownership)
        total_bus += ownership.bus_factor

    report.total_files_analyzed = len(all_files)
    report.all_files = sorted(all_files, key=lambda f: f.bus_factor)
    report.bus_factor_1_count = sum(1 for f in all_files if f.bus_factor == 1)
    report.avg_bus_factor = (total_bus / len(all_files)) if all_files else 0

    if report.total_files_analyzed > 0:
        report.bus_factor_1_pct = (report.bus_factor_1_count / report.total_files_analyzed) * 100

    # Risk files: bus_factor=1, sorted by total_commits (more commits = more knowledge concentration)
    report.risk_files = sorted(
        [f for f in all_files if f.bus_factor == 1],
        key=lambda f: -f.total_commits,
    )

    return report
