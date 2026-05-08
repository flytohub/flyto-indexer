"""
Tech Debt tracker — scan source files for TODO/FIXME/HACK/XXX markers.

Extracts structured debt items from comments, categorizes by tag,
and produces a TechDebtReport with counts, severity breakdown,
and per-file details.

Pure Python stdlib, no external dependencies.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", "target", "out", ".next", ".nuxt",
    ".output", "coverage",
})

_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs",
    ".go", ".rs", ".java", ".kt", ".rb", ".php",
    ".vue", ".svelte", ".yaml", ".yml", ".toml",
    ".sh", ".bash", ".zsh", ".sql", ".tf", ".hcl",
})

# Tags ordered by severity
_TAG_SEVERITY = {
    "FIXME": "high",
    "HACK": "high",
    "BUG": "high",
    "XXX": "high",
    "DEPRECATED": "medium",
    "WARN": "medium",
    "WARNING": "medium",
    "TODO": "medium",
    "NOTE": "low",
    "OPTIMIZE": "low",
    "REFACTOR": "low",
    "REVIEW": "low",
    "TEMP": "medium",
    "TEMPORARY": "medium",
}

# Pattern: comment char + optional space + TAG + optional (author) + : + message
_DEBT_PATTERN = re.compile(
    r"(?:#|//|/\*|\*|--|%%)\s*"          # comment prefix
    r"(" + "|".join(_TAG_SEVERITY) + r")"  # tag
    r"(?:\s*\(([^)]+)\))?"                # optional (author)
    r"\s*:?\s*"                            # optional colon
    r"(.+?)$",                             # message
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class DebtItem:
    """A single tech debt marker."""
    file: str
    line: int
    tag: str             # TODO, FIXME, HACK, etc.
    severity: str        # high, medium, low
    message: str
    author: str = ""     # from (author) annotation, not git blame


@dataclass
class TechDebtReport:
    """Tech debt scan result."""
    total_items: int = 0
    by_tag: dict[str, int] = field(default_factory=dict)
    by_severity: dict[str, int] = field(default_factory=dict)
    by_file: dict[str, int] = field(default_factory=dict)
    items: list[DebtItem] = field(default_factory=list)
    top_files: list[tuple[str, int]] = field(default_factory=list)  # (file, count)

    def to_dict(self) -> dict:
        return {
            "total_items": self.total_items,
            "by_tag": self.by_tag,
            "by_severity": self.by_severity,
            "top_files": self.top_files[:10],
            "high_count": self.by_severity.get("high", 0),
            "medium_count": self.by_severity.get("medium", 0),
            "low_count": self.by_severity.get("low", 0),
        }


def analyze_tech_debt(project_root: str | Path) -> TechDebtReport:
    """Scan project for tech debt markers in comments."""
    project_root = Path(project_root)
    report = TechDebtReport()
    items: list[DebtItem] = []

    for fpath in sorted(project_root.rglob("*")):
        if not fpath.is_file():
            continue
        if any(skip in fpath.parts for skip in _SKIP_DIRS):
            continue
        if fpath.suffix not in _CODE_EXTENSIONS:
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel = str(fpath.relative_to(project_root)).replace("\\", "/")

        for i, line in enumerate(content.splitlines(), 1):
            match = _DEBT_PATTERN.search(line)
            if not match:
                continue

            tag = match.group(1).upper()
            author = (match.group(2) or "").strip()
            message = match.group(3).strip()

            if not message or len(message) < 3:
                continue

            severity = _TAG_SEVERITY.get(tag, "medium")
            items.append(DebtItem(
                file=rel, line=i, tag=tag,
                severity=severity, message=message[:200],
                author=author,
            ))

    # Build report
    report.items = items
    report.total_items = len(items)

    for item in items:
        report.by_tag[item.tag] = report.by_tag.get(item.tag, 0) + 1
        report.by_severity[item.severity] = report.by_severity.get(item.severity, 0) + 1
        report.by_file[item.file] = report.by_file.get(item.file, 0) + 1

    report.top_files = sorted(report.by_file.items(), key=lambda x: -x[1])[:20]

    return report
