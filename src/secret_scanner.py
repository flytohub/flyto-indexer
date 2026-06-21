"""
Secret Scanner — detect hardcoded secrets in source files using regex patterns.

Pure Python stdlib, no external dependencies. Scans for AWS keys, API tokens,
private keys, database URLs, service-specific tokens, and more.
"""

import logging
import os
import re
import subprocess
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
    if any(p in ("tests", "test", "__tests__", "__mocks__", "spec", "specs", "fixtures") for p in parts):
        return True
    if any("mock" in p for p in parts):
        return True
    if (base.startswith("test_") or base.endswith("_test.py")
            or base.endswith(".test.ts") or base.endswith(".test.js")
            or base.endswith(".spec.ts") or base.endswith(".spec.js")
            or base.endswith("_test.go")):
        return True
    return False


def _has_skipped_path_part(rel_path: str) -> bool:
    """Return True when any path segment is generated or dependency-managed."""
    parts = rel_path.replace("\\", "/").split("/")
    for part in parts:
        lower = part.lower()
        if lower in _SKIP_DIRS:
            return True
        if lower.startswith((".venv", "venv")):
            return True
    return False


def _should_skip_file(fname: str, rel_path: str) -> bool:
    """Check if a file should be skipped."""
    if _has_skipped_path_part(rel_path):
        return True
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
    r"(?i)(example|placeholder|TODO|your[_\-]|change[_\-]?me|replace|sample|dummy|fake|mock|template|xxx|fixture|canary|gitleaks|detect-secrets|well-known)",
)

_KNOWN_FAKE_SECRET_VALUES = frozenset({
    # Public AWS canary key used by secret-scanner test suites.
    "AKIAI44QH8DHBR3WZLPQ",
})

_COMMENT_LOW_SIGNAL_PATTERNS = frozenset({
    "api_key",
    "api_token",
    "database_url",
    "generic_secret",
    "password",
    "secret",
})

_DOC_EXAMPLE_SECRET_PATTERNS = frozenset({
    "database_url",
    "password",
    "secret",
    "api_key",
    "api_token",
})

_GIT_ROOT_CACHE: dict[str, Path | None] = {}
_GIT_TRACKED_CACHE: dict[str, bool] = {}


def _git_root_for(path: Path) -> Path | None:
    """Return the containing git root for path, with process-level caching."""
    key = str(path)
    if key in _GIT_ROOT_CACHE:
        return _GIT_ROOT_CACHE[key]
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _GIT_ROOT_CACHE[key] = None
        return None
    if result.returncode != 0:
        _GIT_ROOT_CACHE[key] = None
        return None
    root = Path(result.stdout.strip()).resolve()
    _GIT_ROOT_CACHE[key] = root
    return root


def _is_tracked_by_git(file_path: Path) -> bool:
    """Return True if file_path is tracked by its nearest git repository."""
    root = _git_root_for(file_path.parent.resolve())
    if not root:
        return False
    try:
        rel_path = file_path.resolve().relative_to(root).as_posix()
    except ValueError:
        return False
    cache_key = f"{root}:{rel_path}"
    if cache_key in _GIT_TRACKED_CACHE:
        return _GIT_TRACKED_CACHE[cache_key]
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _GIT_TRACKED_CACHE[cache_key] = False
        return False
    tracked = result.returncode == 0
    _GIT_TRACKED_CACHE[cache_key] = tracked
    return tracked


def _is_untracked_env_file(file_path: Path, fname: str) -> bool:
    """Skip local env files unless they are checked into git."""
    if fname != ".env" and not fname.startswith(".env."):
        return False
    return not _is_tracked_by_git(file_path)


def _is_public_firebase_client_config(pattern_name: str, rel_path: str, line: str) -> bool:
    """Skip public Firebase client identifiers that are not server secrets."""
    if pattern_name not in {"gcp_api_key", "google_api", "firebase_key", "generic_api_key"}:
        return False
    normalized = rel_path.replace("\\", "/").lower()
    basename = normalized.rsplit("/", 1)[-1]
    if basename in {"google-services.json", "googleservice-info.plist", "firebase_options.dart"}:
        return True
    if normalized.endswith("lib/firebase.ts") and "apikey" in line.lower():
        return True
    return False


def _mask_value(match_text: str) -> str:
    """Mask a secret value, showing first 4 chars."""
    if len(match_text) <= 4:
        return match_text + "***"
    return match_text[:4] + "***"


def _is_scanner_rule_definition(rel_path: str, line: str) -> bool:
    """Skip regex/rule definitions that describe secrets rather than contain them."""
    normalized = rel_path.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    if normalized.startswith("config/rules/"):
        return True
    if basename in {".gitleaks.toml", ".secrets.baseline"}:
        return True
    if normalized.endswith(("secret_scanner.py", "analyzer/security.py", "rule_loader.py")):
        lower = line.lower()
        if "re.compile" in lower or "re.search" in lower or "pattern" in lower:
            return True
    return False


def _is_secret_pattern_description(pattern_name: str, line: str) -> bool:
    """Skip prose that names a detector pattern without embedding a credential."""
    if pattern_name != "private_key":
        return False
    lower = line.lower()
    if "-----begin" not in lower or "private key-----" not in lower:
        return False
    return any(marker in lower for marker in (
        "_value_patterns",
        "pattern",
        "regex",
        "redact",
        "scanner",
        "detect",
        "misses",
    ))


_TEMPLATE_SECRET_REFERENCE = re.compile(
    r"(\{\{\s*[\w.\-]+\s*\}\}|\$\{\{\s*[\w.\-]+\s*\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\})"
)

_NON_SECRET_LITERAL_VALUES = frozenset({
    "pass",
    "password",
    "passwd",
    "pwd",
    "secret",
    "text",
    "hidden",
})

_UI_SECRET_SHAPED_ASSIGNMENT = re.compile(
    r"""(?ix)
    ^\s*
    (?:export\s+const\s+)?
    [A-Z0-9_]*(?:PASSWORD|PASSWD|PWD|SECRET)[A-Z0-9_]*
    \s*[:=]\s*
    (?P<quote>['"])(?P<value>[^'"]+)(?P=quote)
    \s*,?
    (?:\s*//.*)?$
    """
)


def _is_template_secret_reference(line: str) -> bool:
    """Skip runtime template references such as {{password}} or ${{ secrets.KEY }}."""
    return bool(_TEMPLATE_SECRET_REFERENCE.search(line))


def _is_secret_shaped_metadata_literal(pattern_name: str, line: str) -> bool:
    """Skip UI/schema metadata that names a secret field but stores no secret.

    Many frontend schemas use secret-shaped field labels or password-change
    route names. Those are field/widget identifiers, not credentials. Keep this
    narrow so real credential literals assigned to password-like names still
    fail.
    """
    if pattern_name not in {"generic_secret", "password", "secret"}:
        return False

    lower_line = line.lower()
    if any(fp in lower_line for fp in (
        'type="password"', "type='password'", "type: 'password'",
        'type: "password"', "inputtype", "password_field",
        "password_input", "form.password", "v-model", "formdata",
        "/auth", "/login", "/password", "/reset",
        "bg-", "text-", "border-", "ring-",
        "hardcoded_secret", "move secrets to", "vulnerabilitytype.",
    )):
        return True

    match = _UI_SECRET_SHAPED_ASSIGNMENT.match(line)
    if not match:
        return False
    return match.group("value").strip().lower() in _NON_SECRET_LITERAL_VALUES


def _load_git_ignored_paths(project_path: Path) -> set[str]:
    """Return gitignored, untracked paths so local secrets do not fail repo scans."""
    try:
        result = subprocess.run(
            [
                "git", "-C", str(project_path),
                "ls-files", "--others", "--ignored", "--exclude-standard", "-z",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return set()
    if result.returncode != 0 or not result.stdout:
        return set()
    return {path for path in result.stdout.split("\0") if path}


def _load_secret_patterns_to_use():
    """Load configured secret patterns with the built-in fallback."""
    try:
        from .rule_loader import get_secret_patterns
    except ImportError:
        from rule_loader import get_secret_patterns

    yaml_patterns = get_secret_patterns()
    if yaml_patterns:
        return [(pid, regex, sev) for pid, regex, sev in yaml_patterns]
    return [(pid, regex, _SEVERITY_MAP.get(pid, "medium")) for pid, regex in SECRET_PATTERNS]


def _iter_secret_scan_inputs(project_path: Path, git_ignored_paths: set[str]):
    """Yield text files that should participate in secret scanning."""
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for fname in filenames:
            file_path = Path(dirpath) / fname
            try:
                rel_path = str(file_path.relative_to(project_path))
            except ValueError:
                rel_path = str(file_path)

            if _should_skip_file(fname, rel_path):
                continue
            if _is_untracked_env_file(file_path, fname):
                continue
            if rel_path in git_ignored_paths:
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            if len(content) > 1_048_576:
                continue

            yield fname, rel_path, content


def _matched_secret_text(match: re.Match) -> str:
    """Return the captured secret value when a pattern exposes one."""
    if match.lastindex:
        return match.group(match.lastindex)
    return match.group(0)


def _is_database_url_example(pattern_name: str, line: str) -> bool:
    """Return True for Dockerfile/CI example connection strings."""
    if pattern_name != "database_url":
        return False
    lower_line = line.lower()
    return any(fp in lower_line for fp in (
        "user:pass", "user:password", "username:password",
        "flyto:flyto", "postgres:postgres", "root:root",
        "example", "localhost:5432/test", "env ",
    ))


def _should_skip_secret_match(pattern_name: str, rel_path: str, line: str, matched_text: str) -> bool:
    """Apply contextual false-positive filters after a secret regex matched."""
    if matched_text in _KNOWN_FAKE_SECRET_VALUES:
        return True
    if _EXAMPLE_INDICATORS.search(line):
        return True
    if _is_template_secret_reference(line):
        return True
    if _is_scanner_rule_definition(rel_path, line):
        return True
    if _is_secret_pattern_description(pattern_name, line):
        return True
    if re.search(r"\$\{[A-Z0-9_]*(?:SECRET|TOKEN|KEY)[A-Z0-9_]*:-\}", line):
        return True
    if _is_secret_shaped_metadata_literal(pattern_name, line):
        return True
    if _is_public_firebase_client_config(pattern_name, rel_path, line):
        return True
    return _is_database_url_example(pattern_name, line)


def _line_secret_findings(rel_path: str, line_num: int, line: str, is_doc: bool, patterns_to_use):
    """Yield secret findings detected on one line."""
    stripped = line.strip()
    is_comment_only = (
        stripped.startswith("//")
        or stripped.startswith("#")
        or stripped.startswith("*")
    )

    for pattern_name, pattern_re, pattern_severity in patterns_to_use:
        if is_comment_only and pattern_name in _COMMENT_LOW_SIGNAL_PATTERNS:
            continue
        if is_doc and pattern_name in _DOC_EXAMPLE_SECRET_PATTERNS:
            continue

        match = pattern_re.search(line)
        if not match:
            continue

        matched_text = _matched_secret_text(match)
        if _should_skip_secret_match(pattern_name, rel_path, line, matched_text):
            continue

        yield SecretFinding(
            file=rel_path,
            line=line_num,
            pattern=pattern_name,
            severity=pattern_severity,
            masked_value=_mask_value(matched_text),
        )


def scan_secrets(project_path: str | Path) -> SecretScanResult:
    """
    Scan a project directory for hardcoded secrets.

    Args:
        project_path: Root directory to scan.

    Returns:
        SecretScanResult with all findings.
    """
    patterns_to_use = _load_secret_patterns_to_use()
    project_path = Path(project_path).resolve()
    findings: list[SecretFinding] = []
    files_scanned = 0
    git_ignored_paths = _load_git_ignored_paths(project_path)

    for fname, rel_path, content in _iter_secret_scan_inputs(project_path, git_ignored_paths):
        files_scanned += 1
        is_doc = _is_doc_file(fname, rel_path)
        for line_num, line in enumerate(content.splitlines(), start=1):
            findings.extend(_line_secret_findings(rel_path, line_num, line, is_doc, patterns_to_use))

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


# ---------------------------------------------------------------------------
# Code Vulnerability Scanner (SAST rules)
# ---------------------------------------------------------------------------

# Directories and files to skip for vulnerability scanning
_VULN_SKIP_DIRS = _SKIP_DIRS  # reuse same set

_VULN_SCANNABLE_EXTENSIONS = frozenset({
    ".py", ".go", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".php", ".rb", ".java", ".vue", ".html", ".htm",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".conf",
})

@dataclass
class VulnerabilityRule:
    id: str
    title: str
    description: str
    severity: str          # CRITICAL, HIGH, MEDIUM
    pattern: "re.Pattern"
    languages: list        # file extensions like [".py", ".go"]
    cwe: str               # CWE ID like "CWE-89"


@dataclass
class VulnerabilityFinding:
    rule_id: str
    title: str
    severity: str
    file: str
    line: int
    snippet: str
    cwe: str


# 15 SAST vulnerability rules
VULNERABILITY_RULES: list[VulnerabilityRule] = [
    # --- SQL Injection (4 rules) ---
    VulnerabilityRule(
        id="SQLI-PY", title="SQL Injection (Python)",
        description="String interpolation or concatenation in SQL query execution",
        severity="CRITICAL",
        pattern=re.compile(r"""(?:f["']SELECT\s.*\{|["']SELECT\s.*["']\s*\+|\.execute\(f["']|\.execute\(["'][^"']*%s["']\s*%)"""),
        languages=[".py"],
        cwe="CWE-89",
    ),
    VulnerabilityRule(
        id="SQLI-GO", title="SQL Injection (Go)",
        description="String formatting or concatenation in SQL queries",
        severity="CRITICAL",
        pattern=re.compile(r"""(?:fmt\.Sprintf\(["']SELECT\s.*%s|db\.Query\(["']SELECT\s.*["']\s*\+)"""),
        languages=[".go"],
        cwe="CWE-89",
    ),
    VulnerabilityRule(
        id="SQLI-JS", title="SQL Injection (JavaScript/TypeScript)",
        description="Template literal or concatenation in SQL queries",
        severity="CRITICAL",
        pattern=re.compile(r"""(?:`SELECT\s.*\$\{|\.query\(["']SELECT\s.*["']\s*\+)"""),
        languages=[".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"],
        cwe="CWE-89",
    ),
    VulnerabilityRule(
        id="SQLI-PHP", title="SQL Injection (PHP)",
        description="Variable interpolation in SQL queries",
        severity="CRITICAL",
        pattern=re.compile(r"""(?:mysql_query\(["']SELECT\s.*\$|mysqli_query\(\$.*,\s*["']SELECT\s.*\$)"""),
        languages=[".php"],
        cwe="CWE-89",
    ),

    # --- XSS (3 rules) ---
    VulnerabilityRule(
        id="XSS-JINJA", title="XSS via unsafe template rendering (Python/Jinja)",
        description="Using |safe filter or Markup() bypasses HTML escaping",
        severity="HIGH",
        pattern=re.compile(r"""(?:\|\s*safe\b|Markup\()"""),
        languages=[".py", ".html", ".htm"],
        cwe="CWE-79",
    ),
    VulnerabilityRule(
        id="XSS-JS", title="XSS via unsafe DOM manipulation (JavaScript)",
        description="dangerouslySetInnerHTML, document.write, or innerHTML assignment",
        severity="HIGH",
        pattern=re.compile(r"""(?:dangerouslySetInnerHTML|document\.write\(|\.innerHTML\s*=)"""),
        languages=[".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue"],
        cwe="CWE-79",
    ),
    VulnerabilityRule(
        id="XSS-GO", title="XSS via template.HTML (Go)",
        description="template.HTML() bypasses Go template escaping",
        severity="HIGH",
        pattern=re.compile(r"""template\.HTML\("""),
        languages=[".go"],
        cwe="CWE-79",
    ),

    # --- Path Traversal (2 rules) ---
    VulnerabilityRule(
        id="PATH-PY", title="Path Traversal (Python)",
        description="Opening files or joining paths with user-controlled input",
        severity="HIGH",
        pattern=re.compile(r"""(?:open\(request\.|os\.path\.join\(request\.)"""),
        languages=[".py"],
        cwe="CWE-22",
    ),
    VulnerabilityRule(
        id="PATH-JS-GO", title="Path Traversal (Go/JavaScript)",
        description="Joining paths with user-controlled request parameters",
        severity="HIGH",
        pattern=re.compile(r"""(?:filepath\.Join\(r\.URL|path\.join\(req\.params|fs\.readFile\(req\.)"""),
        languages=[".go", ".js", ".ts", ".mjs", ".cjs"],
        cwe="CWE-22",
    ),

    # --- Command Injection (2 rules) ---
    VulnerabilityRule(
        id="CMDI-PY", title="Command Injection (Python)",
        description="os.system() or subprocess with shell=True can execute arbitrary commands",
        severity="CRITICAL",
        pattern=re.compile(r"""(?:os\.system\(|subprocess\.call\(.*shell\s*=\s*True)"""),
        languages=[".py"],
        cwe="CWE-78",
    ),
    VulnerabilityRule(
        id="CMDI-GO", title="Command Injection (Go)",
        description="exec.Command with concatenated user input",
        severity="HIGH",
        pattern=re.compile(r"""exec\.Command\([^)]*\+"""),
        languages=[".go"],
        cwe="CWE-78",
    ),

    # --- Hardcoded Credentials (2 rules) ---
    VulnerabilityRule(
        id="CRED-PASS", title="Hardcoded Password",
        description="Password assigned as a string literal (not empty or placeholder)",
        severity="HIGH",
        pattern=re.compile(r"""(?i)password\s*=\s*["'][^"']{8,}["']"""),
        languages=[".py", ".go", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".php", ".rb", ".java"],
        cwe="CWE-798",
    ),
    VulnerabilityRule(
        id="CRED-SECRET", title="Hardcoded Secret Key",
        description="Secret key assigned as a string literal",
        severity="HIGH",
        pattern=re.compile(r"""(?i)secret_key\s*=\s*["'][^"']{8,}["']"""),
        languages=[".py", ".go", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".php", ".rb", ".java"],
        cwe="CWE-798",
    ),

    # --- Insecure Config (2 rules) ---
    VulnerabilityRule(
        id="CFG-DEBUG", title="Debug Mode Enabled",
        description="DEBUG=True or debug:true in non-test files",
        severity="MEDIUM",
        pattern=re.compile(r"""(?i)(?:DEBUG\s*=\s*True|debug:\s*true)"""),
        languages=[".py", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".conf", ".js", ".ts"],
        cwe="CWE-489",
    ),
    VulnerabilityRule(
        id="CFG-NOVERIFY", title="SSL Verification Disabled",
        description="Disabling SSL/TLS certificate verification",
        severity="HIGH",
        pattern=re.compile(r"""(?:verify\s*=\s*False|InsecureSkipVerify:\s*true)"""),
        languages=[".py", ".go", ".js", ".ts", ".yaml", ".yml"],
        cwe="CWE-295",
    ),
]


def scan_code_vulnerabilities(project_path: str | Path) -> dict:
    """
    Scan source files for dangerous code patterns (SAST rules).

    Args:
        project_path: Root directory to scan.

    Returns:
        Dict with total_findings and list of findings.
    """
    project_path = Path(project_path).resolve()
    findings: list[VulnerabilityFinding] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _VULN_SKIP_DIRS]

        for fname in filenames:
            file_path = Path(dirpath) / fname
            try:
                rel_path = str(file_path.relative_to(project_path))
            except ValueError:
                rel_path = str(file_path)

            # Skip test files
            if _is_test_file(rel_path):
                continue

            # Check extension
            _, ext = os.path.splitext(fname)
            ext_lower = ext.lower()
            if ext_lower not in _VULN_SCANNABLE_EXTENSIONS:
                continue

            # Read file
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            # Skip large files
            if len(content) > 1_048_576:
                continue

            # Find applicable rules for this file extension
            applicable_rules = [r for r in VULNERABILITY_RULES if ext_lower in r.languages]
            if not applicable_rules:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                stripped = line.strip()
                # Skip empty lines
                if not stripped:
                    continue

                for rule in applicable_rules:
                    match = rule.pattern.search(line)
                    if match:
                        # Skip lines with example/placeholder indicators
                        if _EXAMPLE_INDICATORS.search(line):
                            continue

                        # For hardcoded creds, skip common false positives
                        if rule.id in ("CRED-PASS", "CRED-SECRET"):
                            lower_line = line.lower()
                            if any(fp in lower_line for fp in (
                                'type="password"', "type='password'",
                                "password_field", "password_input",
                                "v-model", "formdata", "/auth", "/login",
                                "os.environ", "os.getenv", "process.env",
                                "config.", "settings.",
                            )):
                                continue

                        # For debug mode, skip if it looks like a test/dev config check
                        if rule.id == "CFG-DEBUG":
                            lower_line = line.lower()
                            if any(fp in lower_line for fp in (
                                "if ", "assert", "# ", "// ", "not debug",
                                "!debug", "debug ==", "debug !=",
                            )):
                                continue

                        # Truncate snippet to 120 chars
                        snippet = stripped[:120]

                        findings.append(VulnerabilityFinding(
                            rule_id=rule.id,
                            title=rule.title,
                            severity=rule.severity,
                            file=rel_path,
                            line=line_num,
                            snippet=snippet,
                            cwe=rule.cwe,
                        ))

    return {
        "total_findings": len(findings),
        "findings": [
            {
                "rule_id": f.rule_id,
                "title": f.title,
                "severity": f.severity,
                "file": f.file,
                "line": f.line,
                "snippet": f.snippet,
                "cwe": f.cwe,
            }
            for f in findings
        ],
    }


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
