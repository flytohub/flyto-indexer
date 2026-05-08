"""
Config Drift detector — find mismatches between .env definitions and code references.

Checks:
  1. Vars in .env.example but never referenced in code → possibly dead config
  2. Vars referenced in code but missing from .env.example → deploy risk
  3. Vars in docker-compose.yml environment but missing from .env.example
  4. Duplicate var definitions across .env files

Pure Python stdlib, no external dependencies.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", "target", "out", ".next", ".nuxt",
})

# Patterns to extract env var references from code
_ENV_PATTERNS = {
    "python": re.compile(
        r"""(?:os\.environ\.get\(\s*["'](\w+)["']|"""
        r"""os\.environ\[["'](\w+)["']\]|"""
        r"""os\.getenv\(\s*["'](\w+)["'])""",
    ),
    "javascript": re.compile(
        r"""process\.env\.(\w+)|"""
        r"""process\.env\[["'](\w+)["']\]""",
    ),
    "go": re.compile(
        r"""os\.Getenv\(\s*"(\w+)"\)""",
    ),
    "rust": re.compile(
        r"""env::var\(\s*"(\w+)"\)""",
    ),
    "docker_compose": re.compile(
        r"""^\s*-?\s*(\w+)=|"""
        r"""^\s*(\w+):\s*\$\{?(\w+)""",
        re.MULTILINE,
    ),
}

_CODE_EXTENSIONS = {
    ".py": "python", ".ts": "javascript", ".tsx": "javascript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".go": "go", ".rs": "rust",
}


@dataclass
class ConfigVar:
    """A single config variable reference."""
    name: str
    source: str          # "env_example", "env", "code", "docker_compose"
    file: str = ""
    line: int = 0


@dataclass
class ConfigDriftIssue:
    """A drift issue between config and code."""
    var_name: str
    category: str        # "missing_in_env", "unused_in_code", "duplicate", "missing_in_compose"
    severity: str        # "high", "medium", "low"
    description: str
    files: list[str] = field(default_factory=list)


@dataclass
class ConfigDriftReport:
    """Config drift analysis result."""
    env_vars_defined: int = 0
    env_vars_referenced: int = 0
    issues: list[ConfigDriftIssue] = field(default_factory=list)
    env_files_found: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "env_vars_defined": self.env_vars_defined,
            "env_vars_referenced": self.env_vars_referenced,
            "issue_count": len(self.issues),
            "issues": [
                {"var": i.var_name, "category": i.category,
                 "severity": i.severity, "description": i.description}
                for i in self.issues
            ],
        }


def _parse_env_file(path: Path) -> dict[str, int]:
    """Parse .env file, return {VAR_NAME: line_number}."""
    result = {}
    try:
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^(\w+)\s*=", line)
            if match:
                result[match.group(1)] = i
    except OSError:
        pass
    return result


def _scan_code_env_refs(project_root: Path) -> dict[str, list[str]]:
    """Scan code files for env var references. Return {VAR: [file1, file2]}."""
    refs: dict[str, list[str]] = {}

    for fpath in sorted(project_root.rglob("*")):
        if not fpath.is_file():
            continue
        if any(skip in fpath.parts for skip in _SKIP_DIRS):
            continue

        lang = _CODE_EXTENSIONS.get(fpath.suffix)
        if not lang:
            continue

        pattern = _ENV_PATTERNS.get(lang)
        if not pattern:
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel = str(fpath.relative_to(project_root))
        for match in pattern.finditer(content):
            var_name = next((g for g in match.groups() if g), None)
            if var_name:
                refs.setdefault(var_name, []).append(rel)

    return refs


def _scan_compose_env(project_root: Path) -> dict[str, str]:
    """Scan docker-compose files for environment variable references."""
    compose_vars: dict[str, str] = {}

    for name in ("docker-compose.yml", "docker-compose.yaml",
                 "docker-compose.dev.yml", "docker-compose.prod.yml",
                 "compose.yml", "compose.yaml"):
        fpath = project_root / name
        if not fpath.is_file():
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # Look for environment sections
        in_env = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "environment:" or stripped.startswith("environment:"):
                in_env = True
                continue
            if in_env:
                if stripped.startswith("- "):
                    match = re.match(r"-\s*(\w+)=", stripped)
                    if match:
                        compose_vars[match.group(1)] = name
                elif re.match(r"\w+:", stripped) and not stripped.startswith("#"):
                    match = re.match(r"(\w+):", stripped)
                    if match and match.group(1).isupper():
                        compose_vars[match.group(1)] = name
                elif not stripped.startswith("#") and not stripped.startswith("-"):
                    in_env = False

    return compose_vars


def analyze_config_drift(project_root: str | Path) -> ConfigDriftReport:
    """Analyze config drift between .env files and code references."""
    project_root = Path(project_root)
    report = ConfigDriftReport()

    # 1. Parse all .env files
    env_defined: dict[str, list[str]] = {}  # VAR -> [file1, file2]
    for env_name in (".env.example", ".env.sample", ".env.template", ".env"):
        env_path = project_root / env_name
        if env_path.is_file():
            report.env_files_found.append(env_name)
            for var_name in _parse_env_file(env_path):
                env_defined.setdefault(var_name, []).append(env_name)

    report.env_vars_defined = len(env_defined)

    # 2. Scan code for env var references
    code_refs = _scan_code_env_refs(project_root)
    report.env_vars_referenced = len(code_refs)

    # 3. Scan docker-compose
    compose_vars = _scan_compose_env(project_root)

    # 4. Find issues

    # Vars in code but NOT in any .env file → deploy risk
    for var, files in code_refs.items():
        if var not in env_defined:
            # Skip common framework vars that don't need .env
            if var in ("NODE_ENV", "PATH", "HOME", "USER", "PWD", "SHELL",
                       "LANG", "TERM", "PYTHONPATH", "GOPATH", "PYTHONDONTWRITEBYTECODE",
                       "PYTHONUNBUFFERED", "CI", "DEBUG"):
                continue
            report.issues.append(ConfigDriftIssue(
                var_name=var,
                category="missing_in_env",
                severity="high",
                description=f"Referenced in code but not defined in any .env file",
                files=files[:5],
            ))

    # Vars in .env but NOT in code → possibly dead config
    for var, env_files in env_defined.items():
        if var not in code_refs and var not in compose_vars:
            report.issues.append(ConfigDriftIssue(
                var_name=var,
                category="unused_in_code",
                severity="low",
                description=f"Defined in {', '.join(env_files)} but never referenced in code",
                files=env_files,
            ))

    # Vars in docker-compose but NOT in .env → compose drift
    for var, compose_file in compose_vars.items():
        if var not in env_defined:
            if var in ("NODE_ENV", "PATH", "HOME", "CI", "DEBUG"):
                continue
            report.issues.append(ConfigDriftIssue(
                var_name=var,
                category="missing_in_compose",
                severity="medium",
                description=f"Used in {compose_file} but not in .env.example",
                files=[compose_file],
            ))

    # Duplicate definitions
    for var, env_files in env_defined.items():
        if len(env_files) > 1 and ".env" in env_files and ".env.example" in env_files:
            pass  # Normal — .env mirrors .env.example
        elif len(set(env_files)) > 1:
            report.issues.append(ConfigDriftIssue(
                var_name=var,
                category="duplicate",
                severity="low",
                description=f"Defined in multiple files: {', '.join(env_files)}",
                files=env_files,
            ))

    # Sort: high first
    severity_order = {"high": 0, "medium": 1, "low": 2}
    report.issues.sort(key=lambda i: severity_order.get(i.severity, 3))

    return report
