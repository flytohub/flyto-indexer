"""
Secret Scanner — detect hardcoded secrets in source files using regex patterns.

Pure Python stdlib, no external dependencies. Scans for AWS keys, API tokens,
private keys, database URLs, service-specific tokens, and more.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.secret-scanner")

# Directories to skip
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", ".ruff_cache", "target", "out", ".next",
    ".nuxt", ".output", "coverage", ".cache", ".parcel-cache",
    "bower_components", ".eggs", "egg-info",
})

# File patterns to skip
_SKIP_FILES = frozenset({
    ".env.example", ".env.sample", ".env.template",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Gemfile.lock", "Cargo.lock",
    "go.sum", "composer.lock",
})

# Extensions where secrets are likely documentation examples, not real leaks
_DOC_EXTENSIONS = frozenset({".md", ".rst", ".txt", ".adoc", ".wiki"})

# Filenames that are documentation
_DOC_FILES = frozenset({
    "README.md", "README.rst", "README.txt", "README",
    "CONTRIBUTING.md", "CHANGELOG.md", "HISTORY.md",
    "docs.md", "INSTALL.md", "DEPLOYMENT.md",
})

# Extensions to skip (binary, minified, lockfiles)
_SKIP_EXTENSIONS = frozenset({
    ".min.js", ".min.css",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".pyc", ".pyo", ".so", ".dll", ".dylib",
    ".exe", ".bin", ".dat", ".db", ".sqlite",
    ".lock",
})

# Severity mapping for each pattern type
_SEVERITY_MAP = {
    "aws_access_key": "critical",
    "aws_secret_key": "critical",
    "private_key": "critical",
    "database_url": "critical",
    "stripe_key": "critical",
    "github_token": "high",
    "gitlab_token": "high",
    "slack_token": "high",
    "google_api": "high",
    "firebase_key": "high",
    "api_key": "high",
    "api_token": "high",
    "stripe_test": "medium",
    "password": "medium",
    "secret": "medium",
    "jwt": "medium",
}

# Compiled regex patterns
SECRET_PATTERNS = [
    # AWS
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret_key", re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[=:]\s*['\"]([A-Za-z0-9/+=]{40})['\"]")),
    # Generic API keys
    ("api_key", re.compile(r"(?i)(api[_\-]?key|apikey)\s*[=:]\s*['\"]([A-Za-z0-9_\-]{20,})['\"]")),
    ("api_token", re.compile(r"(?i)(api[_\-]?token|access[_\-]?token|auth[_\-]?token)\s*[=:]\s*['\"]([A-Za-z0-9_\-]{20,})['\"]")),
    # Private keys
    ("private_key", re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    # Generic secrets — stricter: value must look like a real secret, not a variable or path
    ("password", re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"](?!form|pass|test|admin|user|demo|example|your|changeme|placeholder|TODO|xxx)([A-Za-z0-9!@#$%^&*_\-]{8,})['\"]")),
    ("secret", re.compile(r"(?i)secret[_\-]?key?\s*[=:]\s*['\"]([A-Za-z0-9_\-]{20,})['\"]")),
    # Database URLs — must have a real-looking password (not "pass", "password", "secret", placeholder)
    ("database_url", re.compile(r"(?i)(postgres|mysql|mongodb|redis)://[^\s'\"]+:(?!pass\b|password\b|secret\b|xxx|changeme|your)([^\s'\"@]{6,})@[^\s'\"]*(?:\.com|\.io|\.net|localhost|\d{1,3}\.\d{1,3})")),
    # GitHub/GitLab tokens
    ("github_token", re.compile(r"gh[ps]_[A-Za-z0-9_]{36,}")),
    ("gitlab_token", re.compile(r"glpat-[A-Za-z0-9_\-]{20,}")),
    # Slack
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    # Stripe
    ("stripe_key", re.compile(r"sk_live_[A-Za-z0-9]{24,}")),
    ("stripe_test", re.compile(r"sk_test_[A-Za-z0-9]{24,}")),
    # JWT
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    # Google
    ("google_api", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    # Firebase
    ("firebase_key", re.compile(r"(?i)firebase[_\-]?api[_\-]?key\s*[=:]\s*['\"]([A-Za-z0-9_\-]{20,})['\"]")),
]


@dataclass
class SecretFinding:
    file: str
    line: int
    pattern: str       # "aws_access_key", "password", etc.
    severity: str      # "critical", "high", "medium"
    masked_value: str  # "AKIA***"


@dataclass
class SecretScanResult:
    total_files_scanned: int
    total_findings: int
    critical: int
    high: int
    medium: int
    findings: list  # list[SecretFinding]


def _is_test_file(rel_path: str) -> bool:
    """Check if a file is a test file."""
    base = os.path.basename(rel_path).lower()
    parts = rel_path.lower().split(os.sep)
    if any(p in ("tests", "test", "__tests__", "spec", "specs", "fixtures") for p in parts):
        return True
    if (base.startswith("test_") or base.endswith("_test.py")
            or base.endswith(".test.ts") or base.endswith(".test.js")
            or base.endswith(".spec.ts") or base.endswith(".spec.js")
            or base.endswith("_test.go")):
        return True
    return False


def _should_skip_file(fname: str, rel_path: str) -> bool:
    """Check if a file should be skipped."""
    if fname in _SKIP_FILES:
        return True
    _, ext = os.path.splitext(fname)
    if ext.lower() in _SKIP_EXTENSIONS:
        return True
    if fname.endswith(".min.js") or fname.endswith(".min.css"):
        return True
    if _is_test_file(rel_path):
        return True
    return False


def _is_doc_file(fname: str, rel_path: str) -> bool:
    """Check if a file is documentation (findings here are likely examples)."""
    if fname in _DOC_FILES:
        return True
    _, ext = os.path.splitext(fname)
    if ext.lower() in _DOC_EXTENSIONS:
        return True
    parts = rel_path.lower().split(os.sep)
    if any(p in ("docs", "doc", "documentation", "examples", "example", "samples") for p in parts):
        return True
    return False


# Patterns to skip in specific contexts — line content indicates example/placeholder
_EXAMPLE_INDICATORS = re.compile(
    r"(?i)(example|placeholder|TODO|your[_\-]|change[_\-]?me|replace|sample|dummy|fake|mock|template|xxx)",
)


def _mask_value(match_text: str) -> str:
    """Mask a secret value, showing first 4 chars."""
    if len(match_text) <= 4:
        return match_text + "***"
    return match_text[:4] + "***"


def scan_secrets(project_path: str | Path) -> SecretScanResult:
    """
    Scan a project directory for hardcoded secrets.

    Args:
        project_path: Root directory to scan.

    Returns:
        SecretScanResult with all findings.
    """
    project_path = Path(project_path).resolve()
    findings: list[SecretFinding] = []
    files_scanned = 0

    for dirpath, dirnames, filenames in os.walk(project_path):
        # Filter skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for fname in filenames:
            file_path = Path(dirpath) / fname
            try:
                rel_path = str(file_path.relative_to(project_path))
            except ValueError:
                rel_path = str(file_path)

            if _should_skip_file(fname, rel_path):
                continue

            # Try to read as text
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            # Skip large files (> 1MB)
            if len(content) > 1_048_576:
                continue

            files_scanned += 1

            is_doc = _is_doc_file(fname, rel_path)

            for line_num, line in enumerate(content.splitlines(), start=1):
                # Skip comment-only lines in source files
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("#") or stripped.startswith("*"):
                    # Still check for actual key patterns (AWS, GitHub token etc.)
                    # but skip generic patterns like password/database_url in comments
                    pass

                for pattern_name, pattern_re in SECRET_PATTERNS:
                    # Skip generic patterns in doc files — they're examples
                    if is_doc and pattern_name in ("database_url", "password", "secret", "api_key", "api_token"):
                        continue

                    match = pattern_re.search(line)
                    if match:
                        matched_text = match.group(0)
                        if match.lastindex:
                            matched_text = match.group(match.lastindex)

                        # Skip if line contains example/placeholder indicators
                        if _EXAMPLE_INDICATORS.search(line):
                            continue

                        # Skip HTML input type="password" and similar false positives
                        if pattern_name == "password":
                            lower_line = line.lower()
                            if any(fp in lower_line for fp in (
                                'type="password"', "type='password'", "type: 'password'",
                                'type: "password"', "inputtype", "password_field",
                                "password_input", "form.password", "v-model", "formdata",
                                "/auth", "/login", "/password", "/reset",
                            )):
                                continue

                        # Skip Dockerfile/CI example connection strings
                        if pattern_name == "database_url":
                            lower_line = line.lower()
                            if any(fp in lower_line for fp in (
                                "user:pass", "user:password", "username:password",
                                "flyto:flyto", "postgres:postgres", "root:root",
                                "example", "localhost:5432/test", "env ",
                            )):
                                continue

                        severity = _SEVERITY_MAP.get(pattern_name, "medium")
                        findings.append(SecretFinding(
                            file=rel_path,
                            line=line_num,
                            pattern=pattern_name,
                            severity=severity,
                            masked_value=_mask_value(matched_text),
                        ))

    # Count by severity
    critical = sum(1 for f in findings if f.severity == "critical")
    high = sum(1 for f in findings if f.severity == "high")
    medium = sum(1 for f in findings if f.severity == "medium")

    return SecretScanResult(
        total_files_scanned=files_scanned,
        total_findings=len(findings),
        critical=critical,
        high=high,
        medium=medium,
        findings=findings,
    )


def format_secret_scan(result: SecretScanResult) -> str:
    """Format secret scan results as human-readable text."""
    lines = []
    lines.append("Secret Scan Report")
    lines.append(f"  Files scanned: {result.total_files_scanned}")
    lines.append(f"  Findings: {result.total_findings}")
    lines.append(f"    Critical: {result.critical}")
    lines.append(f"    High: {result.high}")
    lines.append(f"    Medium: {result.medium}")

    if not result.findings:
        lines.append("")
        lines.append("  No secrets detected.")
        return "\n".join(lines)

    lines.append("")

    # Group by severity
    for severity in ("critical", "high", "medium"):
        severity_findings = [f for f in result.findings if f.severity == severity]
        if not severity_findings:
            continue
        lines.append(f"  [{severity.upper()}]")
        for finding in severity_findings:
            lines.append(
                f"    {finding.file}:{finding.line} — {finding.pattern} — {finding.masked_value}"
            )
        lines.append("")

    return "\n".join(lines)
