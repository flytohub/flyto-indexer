"""
PR Risk Analysis — analyze a git diff and produce a risk assessment.

Parses git diff output, cross-references with the code index to find affected
symbols and dependents, detects breaking changes, and computes a risk score.

Pure Python stdlib — no external dependencies.
"""

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.pr-analyzer")


# Security: validate git ref names
_SAFE_REF_PATTERN = re.compile(r'^[a-zA-Z0-9_./@^~{}\-]+$')

# Parse unified diff headers
_HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')

# Patterns that indicate API route definitions
_API_PATTERNS = [
    re.compile(r'@(app|router)\.(get|post|put|delete|patch|head|options)\s*\(', re.IGNORECASE),
    re.compile(r'\.(Get|Post|Put|Delete|Patch|Head|Options)\s*\(', re.IGNORECASE),
    re.compile(r'(router|app)\.(use|route|all)\s*\('),
    re.compile(r'@(Get|Post|Put|Delete|Patch|Head|Options|RequestMapping)\s*\('),
]

# Patterns that indicate auth/security code
_AUTH_PATTERNS = [
    re.compile(r'(auth|authenticate|authorization|login|logout|session|token|jwt|oauth)', re.IGNORECASE),
    re.compile(r'(password|credential|secret|apikey|api_key)', re.IGNORECASE),
    re.compile(r'(permission|rbac|role|acl|privilege)', re.IGNORECASE),
]

# Patterns that indicate database/migration code
_DB_PATTERNS = [
    re.compile(r'(migration|migrate|alembic|schema|model)', re.IGNORECASE),
    re.compile(r'(CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE)', re.IGNORECASE),
    re.compile(r'(db\.|database|sqlalchemy|sequelize|prisma|mongoose|typeorm)', re.IGNORECASE),
]

# Config file patterns
_CONFIG_PATTERNS = [
    re.compile(r'^\.env'),
    re.compile(r'(config|settings|configuration)\.(py|ts|js|json|yaml|yml|toml)$', re.IGNORECASE),
    re.compile(r'^docker-compose'),
    re.compile(r'^Dockerfile'),
    re.compile(r'(\.github/workflows|\.gitlab-ci|Jenkinsfile|\.circleci)'),
]

# Function/method signature patterns for breaking change detection
_SIGNATURE_PATTERNS = {
    "python": re.compile(r'^\s*def\s+(\w+)\s*\((.*?)\)', re.MULTILINE),
    "typescript": re.compile(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\((.*?)\)', re.MULTILINE),
    "go": re.compile(r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\((.*?)\)', re.MULTILINE),
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PRRiskResult:
    """Result of a PR risk analysis."""
    risk_score: int = 0           # 0-100
    risk_level: str = "low"       # "low", "medium", "high", "critical"
    files_changed: int = 0
    lines_added: int = 0
    lines_deleted: int = 0

    # Risk factors
    touches_api: bool = False
    touches_auth: bool = False
    touches_db: bool = False
    touches_config: bool = False
    has_breaking_change: bool = False

    # Impact
    affected_files: list = field(default_factory=list)
    affected_symbols: list = field(default_factory=list)
    suggested_tests: list = field(default_factory=list)

    # Details
    changes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "files_changed": self.files_changed,
            "lines_added": self.lines_added,
            "lines_deleted": self.lines_deleted,
            "touches_api": self.touches_api,
            "touches_auth": self.touches_auth,
            "touches_db": self.touches_db,
            "touches_config": self.touches_config,
            "has_breaking_change": self.has_breaking_change,
            "affected_files": self.affected_files,
            "affected_symbols": self.affected_symbols,
            "suggested_tests": self.suggested_tests,
            "changes": self.changes,
        }


# ---------------------------------------------------------------------------
# Git diff parsing
# ---------------------------------------------------------------------------

def _validate_ref(ref: str) -> bool:
    """Validate a git ref to prevent command injection."""
    if not ref:
        return True
    return bool(_SAFE_REF_PATTERN.match(ref)) and len(ref) <= 256


def _run_git_diff(project_path: str, base: str = "", staged: bool = False) -> str:
    """Run git diff and return the unified diff text."""
    cmd = ["git", "-C", project_path, "diff", "--no-color"]

    if staged:
        cmd.append("--cached")
    elif base:
        cmd.extend([f"{base}...HEAD"])
    # else: unstaged (default)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _run_git_diff_stat(project_path: str, base: str = "", staged: bool = False) -> str:
    """Run git diff --stat for summary stats."""
    cmd = ["git", "-C", project_path, "diff", "--stat", "--no-color"]
    if staged:
        cmd.append("--cached")
    elif base:
        cmd.extend([f"{base}...HEAD"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _parse_diff_files(diff_text: str) -> list[dict]:
    """Parse unified diff to extract changed files with line counts.

    Returns list of {file, lines_added, lines_deleted, hunks: [(start, end)]}.
    """
    files: list[dict] = []
    current: Optional[dict] = None

    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            if current:
                files.append(current)
            current = {"file": "", "lines_added": 0, "lines_deleted": 0, "hunks": [],
                        "added_lines": [], "deleted_lines": []}
        elif line.startswith("+++ b/") and current is not None:
            current["file"] = line[6:]
        elif line.startswith("+++ /dev/null") and current is not None:
            # File deleted — use --- a/ path
            pass
        elif line.startswith("--- a/") and current is not None and not current["file"]:
            current["file"] = line[6:]
        elif line.startswith("@@ ") and current is not None:
            m = _HUNK_HEADER.match(line)
            if m:
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) else 1
                if new_count > 0:
                    current["hunks"].append((new_start, new_start + new_count - 1))
        elif current is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current["lines_added"] += 1
                current["added_lines"].append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                current["lines_deleted"] += 1
                current["deleted_lines"].append(line[1:])

    if current and current["file"]:
        files.append(current)

    return files


# ---------------------------------------------------------------------------
# Risk factor detection
# ---------------------------------------------------------------------------

def _check_touches_api(file_path: str, added_lines: list[str], deleted_lines: list[str]) -> bool:
    """Check if changes touch API route definitions."""
    all_lines = added_lines + deleted_lines
    for line in all_lines:
        for pattern in _API_PATTERNS:
            if pattern.search(line):
                return True
    # Also check file path
    path_lower = file_path.lower()
    if any(seg in path_lower for seg in ("/api/", "/routes/", "/router/", "/endpoints/", "/controllers/")):
        return True
    return False


def _check_touches_auth(file_path: str, added_lines: list[str], deleted_lines: list[str]) -> bool:
    """Check if changes touch auth/security code."""
    path_lower = file_path.lower()
    if any(seg in path_lower for seg in ("auth", "security", "permission", "login", "session")):
        return True
    all_lines = added_lines + deleted_lines
    auth_hits = 0
    for line in all_lines:
        for pattern in _AUTH_PATTERNS:
            if pattern.search(line):
                auth_hits += 1
                if auth_hits >= 2:
                    return True
    return False


def _check_touches_db(file_path: str, added_lines: list[str], deleted_lines: list[str]) -> bool:
    """Check if changes touch database/migration code."""
    path_lower = file_path.lower()
    if any(seg in path_lower for seg in ("migration", "models/", "schema", "alembic", "prisma")):
        return True
    all_lines = added_lines + deleted_lines
    for line in all_lines:
        for pattern in _DB_PATTERNS:
            if pattern.search(line):
                return True
    return False


def _check_touches_config(file_path: str) -> bool:
    """Check if file is a config/env file."""
    basename = os.path.basename(file_path)
    for pattern in _CONFIG_PATTERNS:
        if pattern.search(file_path) or pattern.search(basename):
            return True
    return False


def _detect_breaking_changes(
    diff_files: list[dict],
) -> list[dict]:
    """Detect potential breaking changes from the diff.

    Looks for:
    - Function signature changes (params added/removed)
    - Exported symbol renames or deletions
    - Type/interface field changes
    """
    breaking: list[dict] = []

    for file_info in diff_files:
        file_path = file_info["file"]
        added = file_info.get("added_lines", [])
        deleted = file_info.get("deleted_lines", [])

        # Determine language
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".py":
            lang = "python"
        elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs"):
            lang = "typescript"
        elif ext == ".go":
            lang = "go"
        else:
            continue

        sig_pattern = _SIGNATURE_PATTERNS.get(lang)
        if not sig_pattern:
            continue

        # Extract function signatures from deleted lines and added lines
        deleted_text = "\n".join(deleted)
        added_text = "\n".join(added)

        deleted_sigs = {m.group(1): m.group(2) for m in sig_pattern.finditer(deleted_text)}
        added_sigs = {m.group(1): m.group(2) for m in sig_pattern.finditer(added_text)}

        # Check for signature changes
        for func_name, old_params in deleted_sigs.items():
            if func_name in added_sigs:
                new_params = added_sigs[func_name]
                if _params_changed(old_params, new_params):
                    breaking.append({
                        "file": file_path,
                        "symbol": func_name,
                        "type": "signature_change",
                        "reason": f"Parameter change: ({old_params}) -> ({new_params})",
                    })
            else:
                # Function deleted (might be renamed)
                breaking.append({
                    "file": file_path,
                    "symbol": func_name,
                    "type": "deletion",
                    "reason": f"Function '{func_name}' removed",
                })

        # Check for export changes (JS/TS)
        if lang == "typescript":
            deleted_exports = set(re.findall(r'export\s+(?:default\s+)?(?:function|class|const|let|var|type|interface)\s+(\w+)', deleted_text))
            added_exports = set(re.findall(r'export\s+(?:default\s+)?(?:function|class|const|let|var|type|interface)\s+(\w+)', added_text))
            removed_exports = deleted_exports - added_exports
            for name in removed_exports:
                if not any(b["symbol"] == name for b in breaking):
                    breaking.append({
                        "file": file_path,
                        "symbol": name,
                        "type": "export_removed",
                        "reason": f"Export '{name}' removed",
                    })

    return breaking


def _params_changed(old_params: str, new_params: str) -> bool:
    """Check if function parameters meaningfully changed."""
    # Normalize whitespace
    old_clean = re.sub(r'\s+', ' ', old_params.strip())
    new_clean = re.sub(r'\s+', ' ', new_params.strip())
    if old_clean == new_clean:
        return False
    # Count params (split by comma, ignore default values)
    old_count = len([p for p in old_clean.split(",") if p.strip()]) if old_clean else 0
    new_count = len([p for p in new_clean.split(",") if p.strip()]) if new_clean else 0
    return old_count != new_count


# ---------------------------------------------------------------------------
# Index integration
# ---------------------------------------------------------------------------

def _find_affected_from_index(
    changed_files: list[str],
    project_path: str,
) -> tuple[list[str], list[str], list[str]]:
    """Use the flyto-indexer index to find affected files, symbols, and test files.

    Returns (affected_files, affected_symbols, suggested_tests).
    """
    affected_files: list[str] = []
    affected_symbols: list[str] = []
    suggested_tests: list[str] = []

    try:
        try:
            from .index_store import load_index
        except ImportError:
            from index_store import load_index

        index = load_index()
        symbols = index.get("symbols", {})
        reverse_index = index.get("reverse_index", {})

        # Find symbols in changed files
        changed_sym_ids: list[str] = []
        for sym_id, sym in symbols.items():
            sym_path = sym.get("path", "")
            if sym_path in changed_files:
                changed_sym_ids.append(sym_id)

        # Find dependents via reverse index
        affected_file_set: set[str] = set()
        affected_sym_set: set[str] = set()
        for sym_id in changed_sym_ids:
            callers = reverse_index.get(sym_id, [])
            for caller_id in callers:
                if ":" in caller_id:
                    parts = caller_id.split(":")
                    caller_path = parts[1] if len(parts) >= 2 else ""
                    if caller_path and caller_path not in changed_files:
                        affected_file_set.add(caller_path)
                        affected_sym_set.add(caller_id)

        affected_files = sorted(affected_file_set)
        affected_symbols = sorted(affected_sym_set)

        # Find test files
        test_file_set: set[str] = set()
        all_files_in_index = {sym.get("path", "") for sym in symbols.values() if sym.get("path")}
        for changed_file in changed_files:
            _find_test_for_file(changed_file, all_files_in_index, test_file_set)
        # Also find tests for affected files
        for af in affected_files:
            _find_test_for_file(af, all_files_in_index, test_file_set)

        suggested_tests = sorted(test_file_set)

    except Exception as e:
        logger.debug("Index integration failed: %s", e)

    return affected_files, affected_symbols, suggested_tests


def _find_test_for_file(file_path: str, all_files: set[str], test_set: set[str]):
    """Find test file(s) for a given source file using naming conventions."""
    basename = os.path.basename(file_path)
    name_no_ext = os.path.splitext(basename)[0]
    ext = os.path.splitext(basename)[1]
    dir_path = os.path.dirname(file_path)

    # Common test file naming conventions
    candidates = [
        # Python: test_foo.py, foo_test.py
        os.path.join(dir_path, f"test_{name_no_ext}{ext}"),
        os.path.join(dir_path, f"{name_no_ext}_test{ext}"),
        # tests/ directory
        os.path.join("tests", f"test_{name_no_ext}{ext}"),
        os.path.join("tests", dir_path, f"test_{name_no_ext}{ext}"),
        # JS/TS: foo.test.ts, foo.spec.ts
        os.path.join(dir_path, f"{name_no_ext}.test{ext}"),
        os.path.join(dir_path, f"{name_no_ext}.spec{ext}"),
        # __tests__/ directory
        os.path.join(dir_path, "__tests__", f"{name_no_ext}.test{ext}"),
        os.path.join(dir_path, "__tests__", f"{name_no_ext}{ext}"),
        # Go: foo_test.go
        os.path.join(dir_path, f"{name_no_ext}_test.go"),
    ]

    for candidate in candidates:
        # Normalize separators
        candidate_norm = candidate.replace("\\", "/")
        for indexed_file in all_files:
            indexed_norm = indexed_file.replace("\\", "/")
            if indexed_norm == candidate_norm or indexed_norm.endswith("/" + candidate_norm):
                test_set.add(indexed_file)


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------

def _compute_risk_score(result: PRRiskResult) -> int:
    """Compute the overall risk score (0-100)."""
    score = 0

    # Size risk
    if result.files_changed > 20:
        score += 20
    elif result.files_changed > 10:
        score += 10
    elif result.files_changed > 5:
        score += 5

    # Sensitivity risk
    if result.touches_api:
        score += 15
    if result.touches_auth:
        score += 20
    if result.touches_db:
        score += 15
    if result.touches_config:
        score += 10
    if result.has_breaking_change:
        score += 20

    # Ratio risk (more deletions = more risk)
    if result.lines_added > 0 and result.lines_deleted > result.lines_added * 2:
        score += 10

    return min(score, 100)


def _score_to_level(score: int) -> str:
    """Convert risk score to risk level."""
    if score >= 70:
        return "critical"
    elif score >= 45:
        return "high"
    elif score >= 20:
        return "medium"
    else:
        return "low"


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------

def analyze_pr_risk(
    project_path: str,
    base: str = "",
    staged: bool = False,
) -> PRRiskResult:
    """
    Analyze a PR/changeset and produce a risk assessment.

    Args:
        project_path: Absolute path to the git repository root.
        base: Git ref to compare against (e.g., "main", "HEAD~3").
              If empty and not staged, analyzes uncommitted changes.
        staged: If True, only analyze staged changes.

    Returns:
        PRRiskResult with risk score, risk factors, affected files/symbols, and suggested tests.
    """
    project_path = str(Path(project_path).resolve())

    # Validate inputs
    if base and not _validate_ref(base):
        result = PRRiskResult()
        result.changes = [{"error": f"Invalid base ref: {base}"}]
        return result

    # Run git diff
    diff_text = _run_git_diff(project_path, base=base, staged=staged)
    if not diff_text:
        return PRRiskResult()

    # Parse diff
    diff_files = _parse_diff_files(diff_text)
    if not diff_files:
        return PRRiskResult()

    result = PRRiskResult()
    result.files_changed = len(diff_files)
    result.lines_added = sum(f["lines_added"] for f in diff_files)
    result.lines_deleted = sum(f["lines_deleted"] for f in diff_files)

    # Check risk factors for each file
    change_details: list[dict] = []
    changed_file_paths: list[str] = []

    for file_info in diff_files:
        file_path = file_info["file"]
        changed_file_paths.append(file_path)
        added = file_info.get("added_lines", [])
        deleted = file_info.get("deleted_lines", [])

        detail: dict = {
            "file": file_path,
            "lines_added": file_info["lines_added"],
            "lines_deleted": file_info["lines_deleted"],
            "change_type": _classify_change_type(file_info),
            "risk_contribution": 0,
            "reasons": [],
        }

        if _check_touches_api(file_path, added, deleted):
            result.touches_api = True
            detail["reasons"].append("touches API routes")
            detail["risk_contribution"] += 15

        if _check_touches_auth(file_path, added, deleted):
            result.touches_auth = True
            detail["reasons"].append("touches auth/security code")
            detail["risk_contribution"] += 20

        if _check_touches_db(file_path, added, deleted):
            result.touches_db = True
            detail["reasons"].append("touches database/migration code")
            detail["risk_contribution"] += 15

        if _check_touches_config(file_path):
            result.touches_config = True
            detail["reasons"].append("modifies config/env file")
            detail["risk_contribution"] += 10

        change_details.append(detail)

    # Detect breaking changes
    breaking = _detect_breaking_changes(diff_files)
    if breaking:
        result.has_breaking_change = True
        for b in breaking:
            # Add to relevant change detail
            for detail in change_details:
                if detail["file"] == b["file"]:
                    detail["reasons"].append(f"breaking: {b['reason']}")
                    detail["risk_contribution"] += 20
                    break

    result.changes = change_details

    # Index integration: find affected files, symbols, and tests
    affected_files, affected_symbols, suggested_tests = _find_affected_from_index(
        changed_file_paths, project_path
    )
    result.affected_files = affected_files
    result.affected_symbols = affected_symbols
    result.suggested_tests = suggested_tests

    # Compute risk score
    result.risk_score = _compute_risk_score(result)
    result.risk_level = _score_to_level(result.risk_score)

    return result


def _classify_change_type(file_info: dict) -> str:
    """Classify a file change as add, modify, delete, or rename."""
    if file_info["lines_added"] > 0 and file_info["lines_deleted"] == 0:
        return "add"
    elif file_info["lines_added"] == 0 and file_info["lines_deleted"] > 0:
        return "delete"
    else:
        return "modify"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_pr_risk(result: PRRiskResult) -> str:
    """Format PR risk analysis as human-readable text."""
    lines = [
        f"PR Risk Analysis",
        f"  Score: {result.risk_score}/100 ({result.risk_level})",
        f"  Files changed: {result.files_changed}",
        f"  Lines: +{result.lines_added} / -{result.lines_deleted}",
        "",
    ]

    # Risk factors
    factors = []
    if result.touches_api:
        factors.append("API routes")
    if result.touches_auth:
        factors.append("Auth/security")
    if result.touches_db:
        factors.append("Database/migrations")
    if result.touches_config:
        factors.append("Config/env")
    if result.has_breaking_change:
        factors.append("Breaking changes")

    if factors:
        lines.append(f"  Risk factors: {', '.join(factors)}")
    else:
        lines.append("  Risk factors: none detected")
    lines.append("")

    # Affected files
    if result.affected_files:
        lines.append(f"  Affected files ({len(result.affected_files)}):")
        for f in result.affected_files[:10]:
            lines.append(f"    {f}")
        if len(result.affected_files) > 10:
            lines.append(f"    ... and {len(result.affected_files) - 10} more")
        lines.append("")

    # Suggested tests
    if result.suggested_tests:
        lines.append(f"  Suggested tests ({len(result.suggested_tests)}):")
        for t in result.suggested_tests[:10]:
            lines.append(f"    {t}")
        if len(result.suggested_tests) > 10:
            lines.append(f"    ... and {len(result.suggested_tests) - 10} more")
        lines.append("")

    # Changes detail
    risky_changes = [c for c in result.changes if c.get("reasons")]
    if risky_changes:
        lines.append("  Risky changes:")
        for c in risky_changes[:15]:
            reasons = ", ".join(c["reasons"])
            lines.append(f"    {c['file']}: {reasons}")
        if len(risky_changes) > 15:
            lines.append(f"    ... and {len(risky_changes) - 15} more")

    return "\n".join(lines)
