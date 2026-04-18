"""
License Scanner — detect project license and dependency licenses.

Pure Python stdlib, no external dependencies. Reads LICENSE files,
manifest files, and uses the dependency scanner for package inventory.
"""

import json
import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.license-scanner")

# License detection patterns (applied to LICENSE file content)
_LICENSE_PATTERNS = [
    (re.compile(r"MIT License", re.IGNORECASE), "MIT"),
    (re.compile(r"Apache License,?\s*Version\s*2\.0", re.IGNORECASE), "Apache-2.0"),
    (re.compile(r"GNU GENERAL PUBLIC LICENSE.*Version\s*3", re.IGNORECASE | re.DOTALL), "GPL-3.0"),
    (re.compile(r"GNU GENERAL PUBLIC LICENSE.*Version\s*2", re.IGNORECASE | re.DOTALL), "GPL-2.0"),
    (re.compile(r"GNU LESSER GENERAL", re.IGNORECASE), "LGPL"),
    (re.compile(r"GNU AFFERO GENERAL", re.IGNORECASE), "AGPL-3.0"),
    (re.compile(r"BSD\s+3[- ]Clause", re.IGNORECASE), "BSD-3-Clause"),
    (re.compile(r"BSD\s+2[- ]Clause", re.IGNORECASE), "BSD-2-Clause"),
    (re.compile(r"Redistribution and use in source and binary forms.*provided that the following", re.IGNORECASE | re.DOTALL), "BSD"),
    (re.compile(r"ISC License", re.IGNORECASE), "ISC"),
    (re.compile(r"Mozilla Public License.*2\.0", re.IGNORECASE), "MPL-2.0"),
    (re.compile(r"The Unlicense", re.IGNORECASE), "Unlicense"),
    (re.compile(r"Creative Commons", re.IGNORECASE), "CC"),
    (re.compile(r"Boost Software License", re.IGNORECASE), "BSL-1.0"),
    (re.compile(r"Eclipse Public License", re.IGNORECASE), "EPL"),
    (re.compile(r"Permission is hereby granted.*without restriction", re.IGNORECASE | re.DOTALL), "MIT"),
]

# Copyleft licenses that may impose restrictions
_COPYLEFT_LICENSES = frozenset({
    "GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL",
    "GPL", "AGPL", "LGPL-2.1", "LGPL-3.0",
})


@dataclass
class LicenseScanResult:
    project_license: str       # "MIT", "Apache-2.0", "UNKNOWN"
    project_license_file: str  # "LICENSE" or ""
    dependency_licenses: dict  # {"MIT": 15, "Apache-2.0": 8, ...}
    copyleft_warning: bool     # True if any GPL/AGPL/LGPL found
    dependencies_without_license: list  # package names with unknown license


def _detect_license_from_content(content: str) -> str:
    """Detect license type from file content using keyword matching."""
    for pattern, license_name in _LICENSE_PATTERNS:
        if pattern.search(content):
            return license_name
    return "UNKNOWN"


def _find_license_file(project_path: Path) -> tuple[str, str]:
    """Find and read license file in project root. Returns (license_type, filename)."""
    candidates = [
        "LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE.rst",
        "LICENCE", "LICENCE.md", "LICENCE.txt",
        "license", "license.md", "license.txt",
    ]
    for fname in candidates:
        fpath = project_path / fname
        if fpath.is_file():
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                license_type = _detect_license_from_content(content)
                return license_type, fname
            except OSError:
                continue
    return "UNKNOWN", ""


def _read_license_from_package_json(project_path: Path) -> str:
    """Read license field from package.json."""
    fpath = project_path / "package.json"
    if not fpath.is_file():
        return ""
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        license_val = data.get("license", "")
        if isinstance(license_val, str):
            return license_val
        if isinstance(license_val, dict):
            return license_val.get("type", "")
    except (json.JSONDecodeError, OSError):
        pass
    return ""


def _read_license_from_pyproject(project_path: Path) -> str:
    """Read license from pyproject.toml [project].license."""
    fpath = project_path / "pyproject.toml"
    if not fpath.is_file():
        return ""
    try:
        data = tomllib.loads(fpath.read_text(encoding="utf-8"))
        project = data.get("project", {})
        license_val = project.get("license", {})
        if isinstance(license_val, str):
            return license_val
        if isinstance(license_val, dict):
            return license_val.get("text", "") or license_val.get("file", "")
    except Exception:
        pass
    return ""


def _read_license_from_cargo(project_path: Path) -> str:
    """Read license from Cargo.toml."""
    fpath = project_path / "Cargo.toml"
    if not fpath.is_file():
        return ""
    try:
        data = tomllib.loads(fpath.read_text(encoding="utf-8"))
        package = data.get("package", {})
        return package.get("license", "")
    except Exception:
        pass
    return ""


def _read_license_from_composer(project_path: Path) -> str:
    """Read license from composer.json."""
    fpath = project_path / "composer.json"
    if not fpath.is_file():
        return ""
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        license_val = data.get("license", "")
        if isinstance(license_val, str):
            return license_val
        if isinstance(license_val, list) and license_val:
            return license_val[0]
    except (json.JSONDecodeError, OSError):
        pass
    return ""


def _collect_dependency_licenses(project_path: Path) -> tuple[dict, list]:
    """
    Collect license information from dependency manifests.
    Returns (license_counts, packages_without_license).
    """
    license_counts: dict[str, int] = {}
    no_license: list[str] = []

    # Try to get dependency inventory
    try:
        try:
            from .dependency_scanner import scan_dependencies
        except ImportError:
            from dependency_scanner import scan_dependencies

        inventory = scan_dependencies(project_path)
    except Exception as e:
        logger.debug("Dependency scan failed: %s", e)
        return license_counts, no_license

    # For npm deps, we can check package.json license field in node_modules
    # For other ecosystems, license info is typically not in manifests
    # We'll check what we can from manifest files

    # Check package.json for npm license field (the project's own)
    pkg_json = project_path / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            # Check individual dependencies in node_modules if available
            node_modules = project_path / "node_modules"
            if node_modules.is_dir():
                for dep in inventory.dependencies:
                    if dep.ecosystem != "npm" or dep.scope in ("indirect",):
                        continue
                    dep_pkg = node_modules / dep.name / "package.json"
                    if dep_pkg.is_file():
                        try:
                            dep_data = json.loads(dep_pkg.read_text(encoding="utf-8"))
                            dep_license = dep_data.get("license", "")
                            if isinstance(dep_license, dict):
                                dep_license = dep_license.get("type", "")
                            if dep_license:
                                license_counts[dep_license] = license_counts.get(dep_license, 0) + 1
                            else:
                                no_license.append(dep.name)
                        except (json.JSONDecodeError, OSError):
                            no_license.append(dep.name)
                    else:
                        no_license.append(dep.name)
            else:
                # No node_modules, can't check individual dep licenses
                for dep in inventory.dependencies:
                    if dep.ecosystem == "npm":
                        no_license.append(dep.name)
        except (json.JSONDecodeError, OSError):
            pass

    # For pypi, check installed packages metadata (site-packages)
    # This is a best-effort approach
    for dep in inventory.dependencies:
        if dep.ecosystem != "npm":
            # For non-npm, we don't have easy access to license info
            # Mark them as unknown unless already processed
            if dep.name not in no_license:
                no_license.append(dep.name)

    return license_counts, no_license


def scan_licenses(project_path: str | Path) -> LicenseScanResult:
    """
    Scan a project for license information.

    Args:
        project_path: Root directory to scan.

    Returns:
        LicenseScanResult with project and dependency license info.
    """
    project_path = Path(project_path).resolve()

    # 1. Detect project license from LICENSE file
    file_license, license_file = _find_license_file(project_path)

    # 2. Check manifest files for license field
    manifest_license = (
        _read_license_from_package_json(project_path)
        or _read_license_from_pyproject(project_path)
        or _read_license_from_cargo(project_path)
        or _read_license_from_composer(project_path)
    )

    # Use file license if available, otherwise manifest license
    project_license = file_license if file_license != "UNKNOWN" else (manifest_license or "UNKNOWN")

    # 3. Collect dependency licenses
    dep_licenses, no_license = _collect_dependency_licenses(project_path)

    # 4. Check for copyleft
    all_licenses = set(dep_licenses.keys())
    if project_license != "UNKNOWN":
        all_licenses.add(project_license)
    copyleft_warning = bool(all_licenses & _COPYLEFT_LICENSES)

    return LicenseScanResult(
        project_license=project_license,
        project_license_file=license_file,
        dependency_licenses=dep_licenses,
        copyleft_warning=copyleft_warning,
        dependencies_without_license=no_license,
    )


def format_license_scan(result: LicenseScanResult) -> str:
    """Format license scan results as human-readable text."""
    lines = []
    lines.append("License Scan Report")
    lines.append(f"  Project license: {result.project_license}")
    if result.project_license_file:
        lines.append(f"  License file: {result.project_license_file}")
    else:
        lines.append("  License file: (none found)")
    lines.append("")

    if result.dependency_licenses:
        lines.append("  Dependency licenses:")
        for lic, count in sorted(result.dependency_licenses.items(), key=lambda x: -x[1]):
            lines.append(f"    {lic}: {count}")
        lines.append("")

    if result.copyleft_warning:
        lines.append("  [WARNING] Copyleft license detected (GPL/AGPL/LGPL).")
        lines.append("  Review compatibility with your project license.")
        lines.append("")

    if result.dependencies_without_license:
        count = len(result.dependencies_without_license)
        lines.append(f"  Dependencies without license info: {count}")
        for name in result.dependencies_without_license[:20]:
            lines.append(f"    - {name}")
        if count > 20:
            lines.append(f"    ... and {count - 20} more")

    return "\n".join(lines)
