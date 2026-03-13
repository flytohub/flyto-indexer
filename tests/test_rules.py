"""Tests for project rules engine (.flyto-rules.yaml)."""

import tempfile
import textwrap
from pathlib import Path

import pytest

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from src.analyzer.rules import (
    RulesChecker,
    add_rule,
    check_rules,
    load_rules,
    remove_rule,
)

pytestmark = pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_project(tmpdir: str, rules: dict, files: dict[str, str] | None = None) -> Path:
    """Create a temp project with rules and optional files."""
    root = Path(tmpdir)
    rules_path = root / ".flyto-rules.yaml"
    rules_path.write_text(yaml.dump(rules, default_flow_style=False))
    if files:
        for fpath, content in files.items():
            full = root / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
    return root


# ── Loading ─────────────────────────────────────────────────────────────────

class TestLoadRules:
    def test_load_from_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {"version": 1, "conventions": []})
            data = load_rules(root)
            assert data is not None
            assert data["version"] == 1

    def test_load_from_flyto_index_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            idx_dir = root / ".flyto-index"
            idx_dir.mkdir()
            (idx_dir / "rules.yaml").write_text(yaml.dump({"version": 1}))
            data = load_rules(root)
            assert data is not None

    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert load_rules(Path(tmpdir)) is None

    def test_root_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".flyto-rules.yaml").write_text(yaml.dump({"version": 1, "source": "root"}))
            idx_dir = root / ".flyto-index"
            idx_dir.mkdir()
            (idx_dir / "rules.yaml").write_text(yaml.dump({"version": 2, "source": "index"}))
            data = load_rules(root)
            assert data["source"] == "root"


# ── glob_deny ───────────────────────────────────────────────────────────────

class TestGlobDeny:
    def test_violation_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [{
                    "rule": "i18n files must be in flyto-i18n/",
                    "glob_deny": ["src/**/*.locale.json"],
                }],
            }, files={
                "src/en.locale.json": "{}",
                "flyto-i18n/en.locale.json": "{}",
            })
            checker = RulesChecker(root)
            report = checker.check()
            assert report.violation_count == 1
            assert report.violations[0].file_path == "src/en.locale.json"

    def test_no_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [{
                    "rule": "no locale outside i18n",
                    "glob_deny": ["src/**/*.locale.json"],
                }],
            }, files={
                "flyto-i18n/en.locale.json": "{}",
            })
            checker = RulesChecker(root)
            report = checker.check()
            assert report.violation_count == 0

    def test_multiple_patterns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [{
                    "rule": "no locale files outside i18n",
                    "glob_deny": [
                        "src/**/*.locale.json",
                        "lib/**/*.locale.json",
                    ],
                }],
            }, files={
                "src/en.locale.json": "{}",
                "lib/fr.locale.json": "{}",
            })
            report = RulesChecker(root).check()
            assert report.violation_count == 2


# ── grep_deny ───────────────────────────────────────────────────────────────

class TestGrepDeny:
    def test_pattern_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "style": [{
                    "rule": "Frontend does no data processing",
                    "grep_deny": [{"pattern": r"\breduce\s*\(", "glob": "*.vue"}],
                }],
            }, files={
                "App.vue": "<script>\nconst total = items.reduce((a, b) => a + b)\n</script>",
            })
            report = RulesChecker(root).check()
            assert report.violation_count == 1
            assert report.violations[0].category == "style"

    def test_glob_filter(self):
        """grep_deny only checks files matching glob."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "style": [{
                    "rule": "no reduce in Vue",
                    "grep_deny": [{"pattern": r"\breduce\s*\(", "glob": "*.vue"}],
                }],
            }, files={
                "utils.py": "result = reduce(fn, items)",
                "App.vue": "<template></template>",
            })
            report = RulesChecker(root).check()
            assert report.violation_count == 0  # .py file doesn't match *.vue

    def test_no_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "style": [{
                    "rule": "no / 100 in Vue",
                    "grep_deny": [{"pattern": r"/ 100", "glob": "*.vue"}],
                }],
            }, files={
                "App.vue": "<script>\nconst x = price\n</script>",
            })
            report = RulesChecker(root).check()
            assert report.violation_count == 0

    def test_string_shorthand(self):
        """grep_deny can be a plain string (matches all files)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [{
                    "rule": "no console.log",
                    "grep_deny": [r"console\.log"],
                }],
            }, files={
                "app.js": "console.log('debug')",
            })
            report = RulesChecker(root).check()
            assert report.violation_count == 1


# ── Conventions (text-only) ─────────────────────────────────────────────────

class TestConventions:
    def test_conventions_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "conventions": [
                    {"rule": "Commit messages in English"},
                    {"rule": "Python 3.10 compatible"},
                ],
            })
            report = RulesChecker(root).check()
            assert report.total_rules == 2
            assert report.skipped_rules == 2
            assert report.rules_checked == 0
            assert report.violation_count == 0


# ── Mixed rules ─────────────────────────────────────────────────────────────

class TestMixed:
    def test_all_categories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [{
                    "rule": "no .env files",
                    "glob_deny": ["**/.env"],
                }],
                "style": [{
                    "rule": "no inline styles",
                    "grep_deny": [{"pattern": r'style="', "glob": "*.vue"}],
                }],
                "conventions": [
                    {"rule": "Use English for code"},
                ],
            }, files={
                ".env": "SECRET=123",
                "App.vue": '<div style="color: red"></div>',
            })
            report = RulesChecker(root).check()
            assert report.total_rules == 3
            assert report.rules_checked == 2
            assert report.skipped_rules == 1
            assert report.violation_count == 2

    def test_pass_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [
                    {"rule": "rule1", "glob_deny": ["**/.env"]},
                    {"rule": "rule2", "glob_deny": ["**/secret.txt"]},
                ],
            }, files={
                ".env": "x",
            })
            report = RulesChecker(root).check()
            # 2 rules checked, 1 violated → 50% pass
            assert report.pass_rate == 0.5


# ── Rule writing ────────────────────────────────────────────────────────────

class TestAddRule:
    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = add_rule(
                root, "architecture",
                rule="no .env files committed",
                glob_deny=["**/.env"],
                source="user feedback 2026-03-13",
            )
            assert result["status"] == "added"
            assert (root / ".flyto-rules.yaml").is_file()
            data = yaml.safe_load((root / ".flyto-rules.yaml").read_text())
            assert len(data["architecture"]) == 1
            assert data["architecture"][0]["auto_created"] is True

    def test_appends_to_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [{"rule": "existing rule"}],
            })
            add_rule(root, "architecture", rule="new rule", glob_deny=["*.tmp"])
            data = yaml.safe_load((root / ".flyto-rules.yaml").read_text())
            assert len(data["architecture"]) == 2

    def test_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            add_rule(root, "style", rule="no inline styles")
            result = add_rule(root, "style", rule="no inline styles")
            assert result["status"] == "already_exists"

    def test_add_style_with_example(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = add_rule(
                root, "style",
                rule="Use UnoCSS variant groups",
                example="dark:(bg-gray-800 text-white)",
                anti_pattern="dark:bg-gray-800 dark:text-white",
                grep_deny=[{"pattern": r"dark:\w", "glob": "*.vue"}],
            )
            assert result["status"] == "added"
            data = yaml.safe_load((root / ".flyto-rules.yaml").read_text())
            assert data["style"][0]["example"] == "dark:(bg-gray-800 text-white)"

    def test_add_convention(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = add_rule(root, "conventions", rule="Commit messages in English")
            assert result["status"] == "added"


class TestRemoveRule:
    def test_removes_rule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "style": [
                    {"rule": "rule A"},
                    {"rule": "rule B"},
                ],
            })
            result = remove_rule(root, "style", "rule A")
            assert result["status"] == "removed"
            data = yaml.safe_load((root / ".flyto-rules.yaml").read_text())
            assert len(data["style"]) == 1
            assert data["style"][0]["rule"] == "rule B"

    def test_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {"version": 1, "style": []})
            result = remove_rule(root, "style", "nonexistent")
            assert result["status"] == "not_found"


# ── Convenience function ────────────────────────────────────────────────────

class TestCheckRules:
    def test_returns_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [{
                    "rule": "no tmp files",
                    "glob_deny": ["*.tmp"],
                }],
            }, files={
                "data.tmp": "x",
            })
            result = check_rules(root)
            assert result["total_rules"] == 1
            assert result["total_violations"] == 1
            assert len(result["violations"]) == 1
            assert result["violations"][0]["severity"] == "medium"

    def test_empty_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = check_rules(Path(tmpdir))
            assert result["total_rules"] == 0
            assert result["total_violations"] == 0


# ── Edge cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_invalid_regex_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "style": [{
                    "rule": "bad regex",
                    "grep_deny": [{"pattern": "[invalid(", "glob": "*"}],
                }],
            }, files={"app.py": "hello"})
            # Should not raise
            report = RulesChecker(root).check()
            assert report.violation_count == 0

    def test_binary_file_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "style": [{
                    "rule": "no eval",
                    "grep_deny": [r"eval\("],
                }],
            }, files={
                "image.png": "\x89PNG\r\n",
            })
            # Should not crash
            report = RulesChecker(root).check()

    def test_severity_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [{
                    "rule": "critical: no secrets",
                    "glob_deny": ["**/.env"],
                    "severity": "critical",
                }],
            }, files={".env": "x"})
            report = RulesChecker(root).check()
            assert report.violations[0].severity == "critical"

    def test_node_modules_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_project(tmpdir, {
                "version": 1,
                "architecture": [{
                    "rule": "no eval",
                    "grep_deny": [r"eval\("],
                }],
            }, files={
                "node_modules/lib/index.js": "eval('code')",
                "src/app.js": "const x = 1",
            })
            report = RulesChecker(root).check()
            assert report.violation_count == 0
