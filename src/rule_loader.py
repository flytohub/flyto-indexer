"""
Rule Loader — YAML-driven configurable rules for all scanners.

Pure Python stdlib (uses built-in yaml-like parser since PyYAML is an
external dependency). Falls back to hardcoded defaults if YAML files
are missing.

Usage:
    from rule_loader import load_rules
    rules = load_rules("secrets")  # loads config/rules/secrets.yaml
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("flyto-indexer.rules")

# Cache loaded rules
_cache: dict[str, dict] = {}


def _find_rules_dir() -> Optional[Path]:
    """Find the config/rules/ directory relative to the package."""
    # Try relative to this file (inside src/)
    here = Path(__file__).parent
    candidates = [
        here.parent / "config" / "rules",       # development: src/../config/rules
        here / "config" / "rules",               # installed: src/config/rules
        Path(os.getcwd()) / "config" / "rules",  # CWD fallback
        Path(os.getcwd()) / ".flyto" / "rules",  # project-local override
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return None


def _parse_yaml_simple(text: str) -> Any:
    """Minimal YAML parser — supports the subset we use in rule files.

    Handles: mappings, sequences, strings, numbers, booleans, nulls.
    No anchors, no multi-document, no complex types.
    Pure Python stdlib, zero dependencies.
    """
    # Try JSON first (our YAML files are often JSON-compatible)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    lines = text.split("\n")
    return _parse_yaml_lines(lines, 0, 0)[0]


def _parse_yaml_lines(lines: list[str], start: int, base_indent: int) -> tuple[Any, int]:
    """Recursive YAML-like line parser."""
    result: dict = {}
    current_list: list = []
    is_list = False
    i = start

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # Calculate indent
        indent = len(line) - len(line.lstrip())
        if indent < base_indent:
            break  # dedent = end of this block

        if stripped.startswith("- "):
            is_list = True
            item_text = stripped[2:].strip()
            if ":" in item_text and not item_text.startswith('"') and not item_text.startswith("'"):
                # Inline mapping in list item: - key: value
                item_dict = {}
                # Check for multi-line mapping
                item_indent = indent + 2
                k, v = item_text.split(":", 1)
                k = k.strip().strip('"').strip("'")
                v = v.strip()
                if v:
                    item_dict[k] = _parse_scalar(v)
                # Read continuation lines at deeper indent
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    next_stripped = next_line.strip()
                    if not next_stripped or next_stripped.startswith("#"):
                        j += 1
                        continue
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if next_indent <= indent:
                        break
                    if ":" in next_stripped:
                        nk, nv = next_stripped.split(":", 1)
                        nk = nk.strip().strip('"').strip("'")
                        nv = nv.strip()
                        if nv.startswith("[") and nv.endswith("]"):
                            # Inline list
                            inner = nv[1:-1]
                            item_dict[nk] = [_parse_scalar(x.strip()) for x in inner.split(",") if x.strip()]
                        elif nv:
                            item_dict[nk] = _parse_scalar(nv)
                        else:
                            # Nested block
                            sub, j = _parse_yaml_lines(lines, j + 1, next_indent + 2)
                            item_dict[nk] = sub
                            continue
                    j += 1
                current_list.append(item_dict if item_dict else _parse_scalar(item_text))
                i = j
                continue
            else:
                current_list.append(_parse_scalar(item_text))
                i += 1
                continue

        if ":" in stripped and not stripped.startswith("-"):
            key, value = stripped.split(":", 1)
            key = key.strip().strip('"').strip("'")
            value = value.strip()

            if value == "" or value == "|" or value == ">":
                # Block value — read next lines at deeper indent
                child_indent = indent + 2
                child, i = _parse_yaml_lines(lines, i + 1, child_indent)
                result[key] = child
                continue
            elif value.startswith("[") and value.endswith("]"):
                # Inline list
                inner = value[1:-1]
                result[key] = [_parse_scalar(x.strip()) for x in inner.split(",") if x.strip()]
            else:
                result[key] = _parse_scalar(value)
        i += 1

    if is_list:
        return current_list, i
    return result, i


def _parse_scalar(s: str) -> Any:
    """Parse a YAML scalar value."""
    s = s.strip()
    if not s:
        return ""
    # Remove quotes
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    # Booleans
    if s.lower() in ("true", "yes", "on"):
        return True
    if s.lower() in ("false", "no", "off"):
        return False
    # Null
    if s.lower() in ("null", "~", "none"):
        return None
    # Numbers
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        pass
    return s


def load_rules(name: str) -> dict:
    """Load rules from config/rules/{name}.yaml.

    Returns parsed dict. Caches results. Returns empty dict if file not found.
    """
    if name in _cache:
        return _cache[name]

    rules_dir = _find_rules_dir()
    if rules_dir:
        yaml_path = rules_dir / f"{name}.yaml"
        if yaml_path.is_file():
            try:
                text = yaml_path.read_text(encoding="utf-8")
                parsed = _parse_yaml_simple(text)
                if isinstance(parsed, dict):
                    _cache[name] = parsed
                    logger.debug("Loaded rules: %s from %s", name, yaml_path)
                    return parsed
            except Exception as e:
                logger.warning("Failed to parse %s: %s", yaml_path, e)

    _cache[name] = {}
    return {}


def load_rules_with_defaults(name: str, defaults: dict) -> dict:
    """Load rules, merging with defaults for any missing keys."""
    rules = load_rules(name)
    if not rules:
        return defaults
    # Shallow merge: rules override defaults
    merged = {**defaults, **rules}
    return merged


def get_secret_patterns() -> list[tuple[str, "re.Pattern", str]]:
    """Load secret patterns from YAML, return [(id, compiled_regex, severity)]."""
    rules = load_rules("secrets")
    patterns_cfg = rules.get("patterns", [])

    if not patterns_cfg:
        return []  # caller should use hardcoded defaults

    severity_map = {}
    for sw in rules.get("severity_weights", []):
        if isinstance(sw, dict):
            severity_map.update(sw)

    result = []
    for p in patterns_cfg:
        if isinstance(p, dict):
            pid = p.get("id", "")
            pattern = p.get("pattern", "")
            severity = p.get("severity", "medium").lower()
            if pid and pattern:
                try:
                    compiled = re.compile(pattern)
                    result.append((pid, compiled, severity))
                except re.error as e:
                    logger.warning("Invalid regex for %s: %s", pid, e)

    return result


def get_complexity_thresholds() -> dict:
    """Load complexity thresholds from YAML."""
    defaults = {
        "max_lines": {"default": 80, "components": 100},
        "max_depth": 4,
        "max_params": 5,
        "max_branches": 10,
        "complex_score_threshold": 5,
    }
    rules = load_rules("complexity")
    if not rules or "thresholds" not in rules:
        return defaults
    t = rules["thresholds"]
    return {
        "max_lines": t.get("max_lines", defaults["max_lines"]),
        "max_depth": t.get("max_depth", defaults["max_depth"]),
        "max_params": t.get("max_params", defaults["max_params"]),
        "max_branches": t.get("max_branches", defaults["max_branches"]),
        "complex_score_threshold": t.get("complex_score_threshold", defaults["complex_score_threshold"]),
    }


def get_docker_rules() -> dict:
    """Load Dockerfile security rules from YAML."""
    rules = load_rules("docker")
    if not rules:
        return {
            "sensitive_ports": {"22", "3306", "5432", "6379", "27017", "1433"},
            "checks": [],
        }
    ports = set()
    for p in rules.get("sensitive_ports", []):
        if isinstance(p, dict):
            ports.add(str(p.get("port", "")))
        else:
            ports.add(str(p))
    return {
        "sensitive_ports": ports,
        "checks": rules.get("checks", []),
    }


def get_license_policies() -> dict:
    """Load license policies from YAML."""
    rules = load_rules("licenses")
    if not rules or "policies" not in rules:
        return {
            "deny": {"AGPL-3.0", "SSPL-1.0"},
            "warn": {"GPL-2.0", "GPL-3.0", "LGPL", "LGPL-2.1", "LGPL-3.0", "MPL-2.0"},
            "copyleft": {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL", "LGPL-2.1", "LGPL-3.0"},
            "allow_unlicensed": False,
        }
    p = rules["policies"]
    return {
        "deny": set(p.get("deny", [])),
        "warn": set(p.get("warn", [])),
        "copyleft": set(rules.get("copyleft", [])),
        "allow_unlicensed": p.get("allow_unlicensed", False),
    }


def get_ignore_patterns() -> dict:
    """Load ignore patterns from YAML."""
    rules = load_rules("ignore")
    if not rules:
        return {
            "directories": [
                "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
                ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
            ],
            "files": [],
            "max_file_size": 1_048_576,
        }
    return {
        "directories": rules.get("directories", []),
        "files": rules.get("files", []),
        "max_file_size": rules.get("max_file_size", 1_048_576),
    }


def get_scoring_config() -> dict:
    """Load scoring configuration from YAML."""
    rules = load_rules("scoring")
    if not rules:
        return {}
    return rules
