"""
Project rules engine — loads .flyto-rules.yaml and checks compliance.

Rules are structured, versionable project conventions that:
  - AI auto-generates from user feedback
  - audit tool programmatically checks (glob_deny, grep_deny)
  - Any AI/tool reads as structured context

Three rule layers:
  architecture  — structural constraints (glob_deny, grep_deny)
  style         — code style patterns (grep_deny, anti_pattern examples)
  conventions   — pure text guidance for AI (no automated check)
"""

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Data models ─────────────────────────────────────────────────────────────


@dataclass
class RuleViolation:
    """A single rule violation found during checking."""

    rule: str
    category: str  # architecture, style, conventions
    file_path: str
    line: int
    detail: str
    severity: str = "medium"  # critical, high, medium, low


@dataclass
class RulesReport:
    """Results of a rules compliance check."""

    total_rules: int = 0
    rules_checked: int = 0
    violations: list[RuleViolation] = field(default_factory=list)
    skipped_rules: int = 0  # conventions (text-only, no check)

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def pass_rate(self) -> float:
        if self.rules_checked == 0:
            return 1.0
        violated_rules = len({v.rule for v in self.violations})
        return max(0.0, 1.0 - violated_rules / self.rules_checked)


# ── YAML loading ────────────────────────────────────────────────────────────

def _find_rules_file(project_root: Path) -> Path | None:
    """Find .flyto-rules.yaml in standard locations."""
    candidates = [
        project_root / ".flyto-rules.yaml",
        project_root / ".flyto-index" / "rules.yaml",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_rules(project_root: Path) -> dict | None:
    """Load and parse .flyto-rules.yaml. Returns None if not found or yaml unavailable."""
    rules_path = _find_rules_file(project_root)
    if not rules_path:
        return None

    try:
        import yaml
    except ImportError:
        logger.debug("PyYAML not installed; skipping rules")
        return None

    try:
        with open(rules_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        logger.debug("Failed to load %s: %s", rules_path, e)
        return None


# ── Rule checking engine ───────────────────────────────────────────────────

_SKIP_DIRS = re.compile(
    r"(?:^|/)(?:node_modules|__pycache__|\.git|dist|build|\.venv|venv|"
    r"\.nuxt|\.output|\.flyto-index)(?:/|$)"
)
_SKIP_FILES = {".flyto-rules.yaml"}


def _collect_files(project_root: Path) -> list[str]:
    """Collect all project files as relative paths (skipping ignored dirs)."""
    files = []
    for fpath in project_root.rglob("*"):
        if not fpath.is_file():
            continue
        rel = str(fpath.relative_to(project_root))
        if _SKIP_DIRS.search(rel):
            continue
        if rel in _SKIP_FILES or fpath.name in _SKIP_FILES:
            continue
        files.append(rel)
    return files


def _glob_match(filepath: str, pattern: str) -> bool:
    """Match filepath against glob pattern, supporting ** for directory recursion."""
    if "**" not in pattern:
        return fnmatch.fnmatch(filepath, pattern)

    # Convert glob to regex with placeholders to avoid nested replacement issues
    # Step 1: protect ** sequences with placeholders
    regex = pattern.replace("**/", "\x00STARSTAR_SLASH\x00")
    regex = regex.replace("**", "\x00STARSTAR\x00")
    # Step 2: escape regex special chars (except our placeholders and *)
    regex = regex.replace(".", r"\.")
    regex = regex.replace("(", r"\(")
    regex = regex.replace(")", r"\)")
    regex = regex.replace("[", r"\[")
    regex = regex.replace("]", r"\]")
    # Step 3: convert remaining single * and ?
    regex = regex.replace("*", "[^/]*")
    regex = regex.replace("?", "[^/]")
    # Step 4: restore ** placeholders
    regex = regex.replace("\x00STARSTAR_SLASH\x00", "(.*/)?")
    regex = regex.replace("\x00STARSTAR\x00", ".*")

    return bool(re.fullmatch(regex, filepath))


def _check_glob_deny(
    rule_text: str,
    patterns: list[str],
    all_files: list[str],
    severity: str,
) -> list[RuleViolation]:
    """Check glob_deny: files matching ANY pattern are violations."""
    violations = []
    for pat in patterns:
        for fpath in all_files:
            if _glob_match(fpath, pat):
                violations.append(RuleViolation(
                    rule=rule_text,
                    category="architecture",
                    file_path=fpath,
                    line=0,
                    detail=f"File matches denied pattern: {pat}",
                    severity=severity,
                ))
    return violations


def _check_grep_deny(
    rule_text: str,
    patterns: list[dict],
    project_root: Path,
    all_files: list[str],
    category: str,
    severity: str,
) -> list[RuleViolation]:
    """Check grep_deny: lines matching pattern in matching files are violations."""
    violations = []
    for entry in patterns:
        if isinstance(entry, str):
            regex_str = entry
            file_glob = "*"
        elif isinstance(entry, dict):
            regex_str = entry.get("pattern", "")
            file_glob = entry.get("glob", "*")
        else:
            continue

        if not regex_str:
            continue

        try:
            regex = re.compile(regex_str)
        except re.error:
            logger.debug("Invalid regex in rule %r: %s", rule_text, regex_str)
            continue

        for fpath in all_files:
            if not fnmatch.fnmatch(fpath, file_glob) and not fnmatch.fnmatch(Path(fpath).name, file_glob):
                continue

            full_path = project_root / fpath
            try:
                content = full_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            for i, line in enumerate(content.split("\n"), 1):
                if regex.search(line):
                    violations.append(RuleViolation(
                        rule=rule_text,
                        category=category,
                        file_path=fpath,
                        line=i,
                        detail=f"Line matches denied pattern: {regex_str}",
                        severity=severity,
                    ))
                    # Cap at 5 violations per rule per file
                    file_violations = sum(
                        1 for v in violations
                        if v.file_path == fpath and v.rule == rule_text
                    )
                    if file_violations >= 5:
                        break

    return violations


# ── Main checker ────────────────────────────────────────────────────────────

class RulesChecker:
    """Check project compliance against .flyto-rules.yaml."""

    def __init__(self, project_root: Path, rules: dict | None = None):
        self.project_root = project_root
        self.rules = rules if rules is not None else load_rules(project_root)

    def check(self) -> RulesReport:
        """Run all rule checks and return report."""
        if not self.rules:
            return RulesReport()

        report = RulesReport()
        all_files = _collect_files(self.project_root)

        # Architecture rules
        for entry in self.rules.get("architecture", []):
            report.total_rules += 1
            rule_text = entry.get("rule", "")
            severity = entry.get("severity", "medium")
            has_check = False

            if "glob_deny" in entry:
                has_check = True
                report.rules_checked += 1
                violations = _check_glob_deny(
                    rule_text, entry["glob_deny"], all_files, severity,
                )
                report.violations.extend(violations)

            if "grep_deny" in entry:
                if not has_check:
                    report.rules_checked += 1
                has_check = True
                violations = _check_grep_deny(
                    rule_text, entry["grep_deny"], self.project_root,
                    all_files, "architecture", severity,
                )
                report.violations.extend(violations)

            if not has_check:
                report.skipped_rules += 1

        # Style rules
        for entry in self.rules.get("style", []):
            report.total_rules += 1
            rule_text = entry.get("rule", "")
            severity = entry.get("severity", "low")
            has_check = False

            if "grep_deny" in entry:
                has_check = True
                report.rules_checked += 1
                violations = _check_grep_deny(
                    rule_text, entry["grep_deny"], self.project_root,
                    all_files, "style", severity,
                )
                report.violations.extend(violations)

            if "glob_deny" in entry:
                if not has_check:
                    report.rules_checked += 1
                has_check = True
                violations = _check_glob_deny(
                    rule_text, entry["glob_deny"], all_files, severity,
                )
                report.violations.extend(violations)

            if not has_check:
                report.skipped_rules += 1

        # Conventions — text-only, no automated check
        for entry in self.rules.get("conventions", []):
            report.total_rules += 1
            report.skipped_rules += 1

        return report


# ── Rule writing helpers ────────────────────────────────────────────────────

def add_rule(
    project_root: Path,
    category: str,
    rule: str,
    glob_deny: list[str] | None = None,
    grep_deny: list[dict | str] | None = None,
    example: str | None = None,
    anti_pattern: str | None = None,
    severity: str | None = None,
    source: str | None = None,
) -> dict:
    """Add a rule to .flyto-rules.yaml. Creates the file if needed.

    Returns dict with status and the rule that was added.
    """
    try:
        import yaml
    except ImportError:
        return {"error": "PyYAML not installed"}

    rules_path = project_root / ".flyto-rules.yaml"

    # Load existing or start fresh
    if rules_path.is_file():
        try:
            with open(rules_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            data = {}
    else:
        data = {"version": 1}

    if "version" not in data:
        data["version"] = 1

    # Build rule entry
    entry: dict = {"rule": rule}
    if glob_deny:
        entry["glob_deny"] = glob_deny
    if grep_deny:
        entry["grep_deny"] = grep_deny
    if example:
        entry["example"] = example
    if anti_pattern:
        entry["anti_pattern"] = anti_pattern
    if severity:
        entry["severity"] = severity

    entry["auto_created"] = True
    entry["source"] = source or f"auto {datetime.now().strftime('%Y-%m-%d')}"

    # Append to category
    if category not in data:
        data[category] = []
    if not isinstance(data[category], list):
        data[category] = []

    # Deduplicate: don't add if same rule text exists
    existing_rules = {r.get("rule", "") for r in data[category] if isinstance(r, dict)}
    if rule in existing_rules:
        return {"status": "already_exists", "rule": rule}

    data[category].append(entry)

    # Write back
    with open(rules_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return {"status": "added", "category": category, "rule": rule, "path": str(rules_path)}


def remove_rule(project_root: Path, category: str, rule_text: str) -> dict:
    """Remove a rule by its text from .flyto-rules.yaml."""
    try:
        import yaml
    except ImportError:
        return {"error": "PyYAML not installed"}

    rules_path = project_root / ".flyto-rules.yaml"
    if not rules_path.is_file():
        return {"error": "No .flyto-rules.yaml found"}

    try:
        with open(rules_path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        return {"error": str(e)}

    if category not in data or not isinstance(data[category], list):
        return {"error": f"Category '{category}' not found"}

    before = len(data[category])
    data[category] = [r for r in data[category] if not (isinstance(r, dict) and r.get("rule") == rule_text)]
    after = len(data[category])

    if before == after:
        return {"status": "not_found", "rule": rule_text}

    with open(rules_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return {"status": "removed", "rule": rule_text}


def check_rules(project_root: Path) -> dict:
    """Convenience function: check rules and return dict summary."""
    checker = RulesChecker(project_root)
    report = checker.check()

    violations_list = []
    for v in report.violations[:50]:
        violations_list.append({
            "rule": v.rule,
            "category": v.category,
            "file": v.file_path,
            "line": v.line,
            "detail": v.detail,
            "severity": v.severity,
        })

    return {
        "total_rules": report.total_rules,
        "rules_checked": report.rules_checked,
        "skipped_rules": report.skipped_rules,
        "total_violations": report.violation_count,
        "pass_rate": round(report.pass_rate, 2),
        "violations": violations_list,
    }
