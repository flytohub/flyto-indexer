"""
Documentation Coverage Scanner — analyze documentation completeness.

Pure Python stdlib, no external dependencies. Checks README quality,
API docstrings, module documentation, inline docs, and config docs.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.doc-scanner")

# Directories to skip
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", ".ruff_cache", "target", "out", ".next",
    ".nuxt", ".output", "coverage", ".cache", ".parcel-cache",
    "bower_components", ".eggs", "egg-info",
})

# README section keywords to detect
_README_SECTIONS = {
    "installation": re.compile(r"(?i)^#+\s*(install|installation|getting\s*started|setup|quick\s*start)", re.MULTILINE),
    "usage": re.compile(r"(?i)^#+\s*(usage|how\s*to\s*use|examples?|basic\s*usage)", re.MULTILINE),
    "api": re.compile(r"(?i)^#+\s*(api|api\s*reference|api\s*docs|endpoints?|routes?)", re.MULTILINE),
    "contributing": re.compile(r"(?i)^#+\s*(contribut|development|develop)", re.MULTILINE),
    "license": re.compile(r"(?i)^#+\s*(licen[cs]e|copyright)", re.MULTILINE),
    "configuration": re.compile(r"(?i)^#+\s*(config|configuration|environment|settings)", re.MULTILINE),
    "testing": re.compile(r"(?i)^#+\s*(test|testing|running\s*tests)", re.MULTILINE),
    "architecture": re.compile(r"(?i)^#+\s*(architect|structure|overview|design)", re.MULTILINE),
}


@dataclass
class DocCoverageResult:
    readme_score: int            # 0-100
    readme_sections: list        # ["installation", "usage", "api"]
    api_doc_coverage: float      # 0.0-1.0 (% of API routes with docstrings)
    module_doc_coverage: float   # 0.0-1.0 (% of top dirs with README)
    inline_doc_coverage: float   # 0.0-1.0 (% of functions with summary)
    has_env_example: bool
    has_changelog: bool
    has_contributing: bool
    overall_score: int           # 0-100 weighted average
    suggestions: list            # ["Add README installation section", ...]


def _score_readme(project_path: Path) -> tuple[int, list[str]]:
    """Score README quality. Returns (score, sections_found)."""
    readme_names = [
        "README.md", "README.rst", "README.txt", "README",
        "readme.md", "readme.rst", "readme.txt", "readme",
    ]
    readme_path = None
    for name in readme_names:
        candidate = project_path / name
        if candidate.is_file():
            readme_path = candidate
            break

    if readme_path is None:
        return 0, []

    try:
        content = readme_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0, []

    if not content.strip():
        return 5, []

    # Base score for having a README
    score = 20

    # Length bonus (up to 20 points)
    word_count = len(content.split())
    if word_count > 500:
        score += 20
    elif word_count > 200:
        score += 15
    elif word_count > 50:
        score += 10
    elif word_count > 20:
        score += 5

    # Section detection (up to 60 points)
    sections_found = []
    for section_name, pattern in _README_SECTIONS.items():
        if pattern.search(content):
            sections_found.append(section_name)

    # Key sections worth more
    key_sections = {"installation", "usage", "api"}
    key_found = len(key_sections & set(sections_found))
    score += key_found * 10  # 30 max for key sections

    # Other sections
    other_found = len(set(sections_found) - key_sections)
    score += min(other_found * 6, 30)  # 30 max for other sections

    return min(score, 100), sections_found


def _check_api_doc_coverage(project_path: Path) -> float:
    """Check what percentage of API routes have docstrings."""
    import gzip

    index_dir = project_path / ".flyto-index"
    if not index_dir.exists():
        return 0.0

    # Load index
    index = {}
    try:
        gz_path = index_dir / "index.json.gz"
        if gz_path.exists():
            with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                index = json.load(f)
        else:
            json_path = index_dir / "index.json"
            if json_path.exists():
                index = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0.0

    if not index:
        return 0.0

    symbols = index.get("symbols", {})
    api_symbols = [s for s in symbols.values() if s.get("type") == "api"]

    if not api_symbols:
        return 0.0  # No API routes found — not applicable

    documented = sum(1 for s in api_symbols if s.get("summary", "").strip())
    return documented / len(api_symbols)


def _check_module_doc_coverage(project_path: Path) -> float:
    """Check what percentage of top-level directories have a README or documented __init__.py."""
    top_dirs = []
    try:
        for entry in sorted(os.listdir(project_path)):
            entry_path = project_path / entry
            if entry_path.is_dir() and entry not in _SKIP_DIRS and not entry.startswith("."):
                top_dirs.append(entry)
    except OSError:
        return 0.0

    if not top_dirs:
        return 0.0

    documented = 0
    for dirname in top_dirs:
        dir_path = project_path / dirname
        # Check for README
        has_readme = any(
            (dir_path / name).is_file()
            for name in ("README.md", "README.rst", "README.txt", "README")
        )
        # Check for __init__.py with docstring
        has_init_doc = False
        init_path = dir_path / "__init__.py"
        if init_path.is_file():
            try:
                content = init_path.read_text(encoding="utf-8", errors="ignore")
                # Check for module docstring (starts with triple quotes)
                stripped = content.lstrip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    has_init_doc = True
            except OSError:
                pass

        if has_readme or has_init_doc:
            documented += 1

    return documented / len(top_dirs)


def _check_inline_doc_coverage(project_path: Path) -> float:
    """Check what percentage of functions/classes have docstrings (from index)."""
    import gzip

    index_dir = project_path / ".flyto-index"
    if not index_dir.exists():
        return 0.0

    # Load index
    index = {}
    try:
        gz_path = index_dir / "index.json.gz"
        if gz_path.exists():
            with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                index = json.load(f)
        else:
            json_path = index_dir / "index.json"
            if json_path.exists():
                index = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0.0

    if not index:
        return 0.0

    symbols = index.get("symbols", {})
    # Only count functions, methods, and classes
    documentable_types = {"function", "method", "class", "composable"}
    documentable = [
        s for s in symbols.values()
        if s.get("type") in documentable_types
    ]

    if not documentable:
        return 0.0

    documented = sum(1 for s in documentable if s.get("summary", "").strip())
    return documented / len(documentable)


def _check_env_example(project_path: Path) -> tuple[bool, bool]:
    """Check if .env.example exists and has comments. Returns (exists, has_comments)."""
    env_names = [".env.example", ".env.sample", ".env.template"]
    for name in env_names:
        fpath = project_path / name
        if fpath.is_file():
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                has_comments = any(
                    line.strip().startswith("#") and len(line.strip()) > 1
                    for line in content.splitlines()
                )
                return True, has_comments
            except OSError:
                return True, False
    return False, False


def scan_documentation(project_path: str | Path) -> DocCoverageResult:
    """
    Scan a project for documentation coverage.

    Args:
        project_path: Root directory to scan.

    Returns:
        DocCoverageResult with coverage metrics and suggestions.
    """
    project_path = Path(project_path).resolve()
    suggestions = []

    # 1. README score
    readme_score, readme_sections = _score_readme(project_path)
    if readme_score == 0:
        suggestions.append("Add a README.md file")
    else:
        missing_key = {"installation", "usage"} - set(readme_sections)
        for section in sorted(missing_key):
            suggestions.append(f"Add README '{section}' section")

    # 2. API doc coverage
    api_doc_coverage = _check_api_doc_coverage(project_path)
    if api_doc_coverage < 0.5:
        undocumented_pct = int((1 - api_doc_coverage) * 100)
        if undocumented_pct > 0:
            suggestions.append(f"{undocumented_pct}% of API routes lack docstrings")

    # 3. Module doc coverage
    module_doc_coverage = _check_module_doc_coverage(project_path)
    if module_doc_coverage < 0.5:
        suggestions.append("Add README or docstring to top-level modules")

    # 4. Inline doc coverage
    inline_doc_coverage = _check_inline_doc_coverage(project_path)
    if inline_doc_coverage < 0.3:
        suggestions.append("Add docstrings to functions and classes")

    # 5. Config docs
    has_env, env_has_comments = _check_env_example(project_path)
    if not has_env:
        # Check if project uses env vars
        suggestions.append("Add .env.example with documented environment variables")
    elif not env_has_comments:
        suggestions.append("Add comments to .env.example explaining each variable")

    # 6. Changelog
    has_changelog = any(
        (project_path / name).is_file()
        for name in ("CHANGELOG.md", "CHANGELOG.rst", "CHANGELOG.txt", "CHANGELOG",
                      "CHANGES.md", "CHANGES.rst", "HISTORY.md")
    )
    if not has_changelog:
        suggestions.append("Add a CHANGELOG.md")

    # 7. Contributing guide
    has_contributing = any(
        (project_path / name).is_file()
        for name in ("CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING.txt", "CONTRIBUTING")
    )

    # Calculate overall score (weighted average)
    # README: 30%, API docs: 20%, Module docs: 15%, Inline docs: 25%, Config: 10%
    overall = int(
        readme_score * 0.30
        + api_doc_coverage * 100 * 0.20
        + module_doc_coverage * 100 * 0.15
        + inline_doc_coverage * 100 * 0.25
        + (100 if has_env else 0) * 0.10
    )
    # Bonus for changelog and contributing
    if has_changelog:
        overall = min(100, overall + 3)
    if has_contributing:
        overall = min(100, overall + 2)

    return DocCoverageResult(
        readme_score=readme_score,
        readme_sections=readme_sections,
        api_doc_coverage=round(api_doc_coverage, 3),
        module_doc_coverage=round(module_doc_coverage, 3),
        inline_doc_coverage=round(inline_doc_coverage, 3),
        has_env_example=has_env,
        has_changelog=has_changelog,
        has_contributing=has_contributing,
        overall_score=min(overall, 100),
        suggestions=suggestions,
    )


def format_doc_scan(result: DocCoverageResult) -> str:
    """Format documentation coverage results as human-readable text."""
    lines = []
    lines.append("Documentation Coverage Report")
    lines.append(f"  Overall score: {result.overall_score}/100")
    lines.append("")

    lines.append(f"  README: {result.readme_score}/100")
    if result.readme_sections:
        lines.append(f"    Sections: {', '.join(result.readme_sections)}")
    else:
        lines.append("    Sections: (none detected)")
    lines.append("")

    lines.append(f"  API doc coverage: {result.api_doc_coverage:.0%}")
    lines.append(f"  Module doc coverage: {result.module_doc_coverage:.0%}")
    lines.append(f"  Inline doc coverage: {result.inline_doc_coverage:.0%}")
    lines.append("")

    lines.append(f"  .env.example: {'yes' if result.has_env_example else 'no'}")
    lines.append(f"  CHANGELOG: {'yes' if result.has_changelog else 'no'}")
    lines.append(f"  CONTRIBUTING: {'yes' if result.has_contributing else 'no'}")

    if result.suggestions:
        lines.append("")
        lines.append("  Suggestions:")
        for s in result.suggestions:
            lines.append(f"    - {s}")

    return "\n".join(lines)
