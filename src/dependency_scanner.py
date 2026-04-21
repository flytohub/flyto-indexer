"""
Dependency/package version scanner — extracts external package dependencies
from manifest files across multiple ecosystems.

Supports: npm (package.json), pypi (requirements.txt, pyproject.toml, Pipfile),
Go (go.mod), Rust (Cargo.toml), Java (pom.xml, build.gradle), PHP (composer.json),
Ruby (Gemfile), Docker (Dockerfile).
"""

import json
import logging
import os
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.dependency-scanner")

# Directories to skip when walking
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", ".ruff_cache", "target", "out",
})

# Manifest filenames to look for
_MANIFEST_FILES = frozenset({
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock",
    "go.mod",
    "Cargo.toml", "Cargo.lock",
    "pom.xml", "build.gradle", "build.gradle.kts",
    "composer.json", "composer.lock",
    "Gemfile", "Gemfile.lock",
    "Dockerfile",
    "pubspec.yaml", "pubspec.lock",
    "Package.resolved",
    "packages.lock.json",
    "mix.exs", "mix.lock",
})


@dataclass
class PackageDependency:
    name: str              # package name
    version: str           # version constraint from manifest
    pinned_version: str    # exact version from lockfile, else ""
    ecosystem: str         # "npm", "pypi", "go", "cargo", "maven", "composer", "gem", "docker"
    scope: str             # "production", "dev", "peer", "optional", "build"
    source_file: str       # which manifest file it came from


@dataclass
class VersionConflict:
    name: str
    ecosystem: str
    versions: list  # list of {version, source_file}

@dataclass
class DependencyInventory:
    project_path: str
    ecosystems: list = field(default_factory=list)
    total_count: int = 0
    production_count: int = 0
    dev_count: int = 0
    indirect_count: int = 0
    dependencies: list = field(default_factory=list)
    manifest_files: list = field(default_factory=list)
    has_lockfile: bool = False
    conflicts: list = field(default_factory=list)  # VersionConflict list
    scanned_at: str = ""

    def to_dict(self) -> dict:
        return {
            "project_path": self.project_path,
            "ecosystems": self.ecosystems,
            "total_count": self.total_count,
            "production_count": self.production_count,
            "dev_count": self.dev_count,
            "indirect_count": self.indirect_count,
            "dependencies": [asdict(d) for d in self.dependencies],
            "manifest_files": self.manifest_files,
            "has_lockfile": self.has_lockfile,
            "conflicts": [asdict(c) for c in self.conflicts],
            "scanned_at": self.scanned_at,
        }


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------

def _find_manifest_files(project_path: Path) -> list[Path]:
    """Walk project directory and find all manifest files, skipping ignored dirs."""
    found = []
    for dirpath, dirnames, filenames in os.walk(project_path):
        # Filter out skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if fname in _MANIFEST_FILES:
                found.append(Path(dirpath) / fname)
            # Also match Dockerfile.* variants
            elif fname.startswith("Dockerfile"):
                found.append(Path(dirpath) / fname)
            # .NET *.csproj files (variable names)
            elif fname.endswith(".csproj"):
                found.append(Path(dirpath) / fname)
    return sorted(found)


def _rel_path(file_path: Path, project_path: Path) -> str:
    """Return relative path string."""
    try:
        return str(file_path.relative_to(project_path))
    except ValueError:
        return str(file_path)


# ---------------------------------------------------------------------------
# npm / Node.js
# ---------------------------------------------------------------------------

def _parse_package_json(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse package.json for dependencies."""
    deps = []
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return deps

    source = _rel_path(file_path, project_path)
    scope_map = {
        "dependencies": "production",
        "devDependencies": "dev",
        "peerDependencies": "peer",
        "optionalDependencies": "optional",
    }
    for section, scope in scope_map.items():
        for name, version in (data.get(section) or {}).items():
            if isinstance(version, str):
                deps.append(PackageDependency(
                    name=name, version=version, pinned_version="",
                    ecosystem="npm", scope=scope, source_file=source,
                ))
    return deps


def _parse_package_lock_json(file_path: Path) -> dict[str, str]:
    """Parse package-lock.json to extract pinned versions. Returns {name: version}."""
    pinned = {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return pinned

    # v2/v3 format: packages["node_modules/<name>"].version
    packages = data.get("packages", {})
    for key, info in packages.items():
        if key.startswith("node_modules/") and isinstance(info, dict):
            pkg_name = key.split("node_modules/")[-1]
            ver = info.get("version", "")
            if ver:
                pinned[pkg_name] = ver

    # v1 fallback: dependencies.<name>.version
    if not pinned:
        for name, info in (data.get("dependencies") or {}).items():
            if isinstance(info, dict) and info.get("version"):
                pinned[name] = info["version"]

    return pinned


def _parse_yarn_lock(file_path: Path) -> dict[str, str]:
    """Parse yarn.lock (v1 text format) to extract pinned versions."""
    pinned = {}
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return pinned

    current_names = []
    for line in content.splitlines():
        # Package header lines like: "react@^18.2.0":  or  react@^18.2.0, react@^18.0.0:
        if not line.startswith(" ") and not line.startswith("#") and line.strip().endswith(":"):
            header = line.rstrip(":").strip().strip('"')
            current_names = []
            for part in header.split(","):
                part = part.strip().strip('"')
                at_idx = part.rfind("@")
                if at_idx > 0:
                    current_names.append(part[:at_idx])
        elif line.strip().startswith("version "):
            ver = line.strip().split('"')[1] if '"' in line else line.strip().split()[-1]
            for name in current_names:
                pinned[name] = ver
            current_names = []

    return pinned


def _parse_pnpm_lock(file_path: Path) -> dict[str, str]:
    """Parse pnpm-lock.yaml to extract pinned versions (simple regex approach)."""
    pinned = {}
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return pinned

    # Match patterns like: /package-name@1.2.3: or /@scope/name@1.2.3:
    # Also v9 format: package-name@1.2.3: at top level
    for m in re.finditer(r"['/]?(@?[^@\s']+)@(\d+\.\d+[^:\s']*)", content):
        name, ver = m.group(1), m.group(2)
        if name.startswith("/"):
            name = name[1:]
        pinned[name] = ver

    return pinned


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

_PY_REQ_RE = re.compile(
    r"^\s*([A-Za-z0-9][\w.\-]*(?:\[[^\]]+\])?)\s*"
    r"(~=|==|!=|>=|<=|>|<|===)?\s*"
    r"([^\s;#,]*)",
)


# Known dev/test package names (normalized lowercase with _ instead of -)
_KNOWN_DEV_PACKAGES = frozenset({
    # Testing
    "pytest", "pytest_asyncio", "pytest_cov", "pytest_mock", "pytest_xdist",
    "pytest_httpx", "pytest_django", "pytest_flask", "pytest_benchmark",
    "unittest2", "nose", "nose2", "tox", "nox",
    "factory_boy", "faker", "hypothesis", "responses", "vcrpy", "moto",
    "freezegun", "time_machine", "trustme", "respx", "aioresponses",
    # Linting & formatting
    "mypy", "ruff", "flake8", "black", "isort", "pylint", "pyright",
    "autopep8", "yapf", "pyflakes", "pycodestyle", "pydocstyle",
    "flake8_bugbear", "flake8_comprehensions",
    # Type stubs
    "types_requests", "types_pyyaml", "types_setuptools", "types_redis",
    "types_python_dateutil", "types_pillow", "types_toml", "types_ujson",
    # Coverage
    "coverage", "codecov", "coveralls",
    # Security scanning
    "bandit", "safety", "pip_audit",
    # Docs
    "sphinx", "mkdocs", "mkdocs_material", "pdoc", "pydoc_markdown",
    "sphinx_rtd_theme", "sphinxcontrib_apidoc",
    # Debug
    "ipython", "ipdb", "debugpy", "pudb", "pdb_plus_plus",
    # Build
    "setuptools", "wheel", "build", "twine", "hatch", "hatchling",
    "flit", "flit_core", "poetry", "poetry_core", "maturin",
    "pre_commit",
})

# Path patterns that indicate dev/test requirements
_DEV_PATH_PATTERNS = ("test", "dev", "lint", "ci", "doc")


def _infer_pypi_scope(name: str, source_file: str) -> str:
    """Infer scope for a pypi package based on name and source file path."""
    # Strip extras: "uvicorn[standard]" → "uvicorn"
    base_name = name.split("[")[0].lower().replace("-", "_")
    if base_name in _KNOWN_DEV_PACKAGES or base_name.startswith("types_"):
        return "dev"
    # Check source file path
    source_lower = source_file.lower()
    for pattern in _DEV_PATH_PATTERNS:
        if pattern in source_lower:
            return "dev"
    return "production"


def _parse_requirements_txt(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse requirements.txt."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return deps

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _PY_REQ_RE.match(line)
        if m:
            name = m.group(1)
            op = m.group(2) or ""
            ver = m.group(3) or ""
            version_str = f"{op}{ver}" if op else ver
            scope = _infer_pypi_scope(name, source)
            deps.append(PackageDependency(
                name=name, version=version_str, pinned_version="",
                ecosystem="pypi", scope=scope, source_file=source,
            ))
    return deps


def _parse_pyproject_toml(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse pyproject.toml for [project.dependencies] and [project.optional-dependencies]."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        data = tomllib.loads(file_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return deps

    project_section = data.get("project", {})

    # [project.dependencies]
    for req_str in (project_section.get("dependencies") or []):
        m = _PY_REQ_RE.match(req_str)
        if m:
            name = m.group(1)
            op = m.group(2) or ""
            ver = m.group(3) or ""
            deps.append(PackageDependency(
                name=name, version=f"{op}{ver}" if op else ver,
                pinned_version="", ecosystem="pypi",
                scope="production", source_file=source,
            ))

    # [project.optional-dependencies]
    for group_name, group_deps in (project_section.get("optional-dependencies") or {}).items():
        scope = "dev" if group_name in ("dev", "test", "testing", "lint", "docs") else "optional"
        for req_str in (group_deps or []):
            m = _PY_REQ_RE.match(req_str)
            if m:
                name = m.group(1)
                op = m.group(2) or ""
                ver = m.group(3) or ""
                deps.append(PackageDependency(
                    name=name, version=f"{op}{ver}" if op else ver,
                    pinned_version="", ecosystem="pypi",
                    scope=scope, source_file=source,
                ))

    # Poetry: [tool.poetry.dependencies] / [tool.poetry.group.*.dependencies]
    poetry = data.get("tool", {}).get("poetry", {})
    for name, spec in (poetry.get("dependencies") or {}).items():
        if name.lower() == "python":
            continue
        ver = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
        deps.append(PackageDependency(
            name=name, version=ver, pinned_version="",
            ecosystem="pypi", scope="production", source_file=source,
        ))
    for group_name, group_data in (poetry.get("group") or {}).items():
        scope = "dev" if group_name in ("dev", "test", "lint", "docs") else "optional"
        for name, spec in (group_data.get("dependencies") or {}).items():
            ver = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
            deps.append(PackageDependency(
                name=name, version=ver, pinned_version="",
                ecosystem="pypi", scope=scope, source_file=source,
            ))

    return deps


def _parse_pipfile(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse Pipfile for [packages] and [dev-packages]."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        data = tomllib.loads(file_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return deps

    for section, scope in [("packages", "production"), ("dev-packages", "dev")]:
        for name, spec in (data.get(section) or {}).items():
            ver = spec if isinstance(spec, str) else (spec.get("version", "*") if isinstance(spec, dict) else "*")
            deps.append(PackageDependency(
                name=name, version=ver, pinned_version="",
                ecosystem="pypi", scope=scope, source_file=source,
            ))
    return deps


def _parse_poetry_lock(file_path: Path) -> dict[str, str]:
    """Parse poetry.lock to get pinned versions. Returns {name: version}."""
    pinned = {}
    try:
        data = tomllib.loads(file_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return pinned

    for pkg in (data.get("package") or []):
        name = pkg.get("name", "")
        ver = pkg.get("version", "")
        if name and ver:
            pinned[name.lower()] = ver
    return pinned


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

_GO_REQUIRE_RE = re.compile(r"^\s*(\S+)\s+(v\S+)")


def _parse_go_mod(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse go.mod require block."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return deps

    in_require = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if stripped.startswith("require ") and "(" not in stripped:
            # Single-line require
            m = _GO_REQUIRE_RE.match(stripped[len("require "):].strip())
            if m:
                deps.append(PackageDependency(
                    name=m.group(1), version=m.group(2), pinned_version=m.group(2),
                    ecosystem="go", scope="production", source_file=source,
                ))
            continue
        if in_require:
            if stripped == ")":
                in_require = False
                continue
            m = _GO_REQUIRE_RE.match(stripped)
            if m:
                scope = "production"
                if "// indirect" in line:
                    scope = "indirect"
                deps.append(PackageDependency(
                    name=m.group(1), version=m.group(2), pinned_version=m.group(2),
                    ecosystem="go", scope=scope, source_file=source,
                ))
    return deps


# ---------------------------------------------------------------------------
# Rust (Cargo.toml)
# ---------------------------------------------------------------------------

def _parse_cargo_toml(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse Cargo.toml for [dependencies], [dev-dependencies], [build-dependencies]."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        data = tomllib.loads(file_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return deps

    section_map = {
        "dependencies": "production",
        "dev-dependencies": "dev",
        "build-dependencies": "build",
    }
    for section, scope in section_map.items():
        for name, spec in (data.get(section) or {}).items():
            if isinstance(spec, str):
                ver = spec
            elif isinstance(spec, dict):
                ver = spec.get("version", "")
            else:
                ver = ""
            deps.append(PackageDependency(
                name=name, version=ver, pinned_version="",
                ecosystem="cargo", scope=scope, source_file=source,
            ))

    # Also check [target.*.dependencies]
    for _target_name, target_data in (data.get("target") or {}).items():
        if isinstance(target_data, dict):
            for section, scope in section_map.items():
                for name, spec in (target_data.get(section) or {}).items():
                    if isinstance(spec, str):
                        ver = spec
                    elif isinstance(spec, dict):
                        ver = spec.get("version", "")
                    else:
                        ver = ""
                    deps.append(PackageDependency(
                        name=name, version=ver, pinned_version="",
                        ecosystem="cargo", scope=scope, source_file=source,
                    ))
    return deps


# ---------------------------------------------------------------------------
# Java (pom.xml, build.gradle)
# ---------------------------------------------------------------------------

def _parse_pom_xml(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse pom.xml <dependency> elements."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        tree = ET.parse(file_path)
    except (ET.ParseError, OSError) as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return deps

    root = tree.getroot()
    # Handle Maven namespace
    ns_match = re.match(r"\{(.+)\}", root.tag)
    ns = ns_match.group(1) if ns_match else ""
    prefix = f"{{{ns}}}" if ns else ""

    for dep_elem in root.iter(f"{prefix}dependency"):
        group_id = (dep_elem.findtext(f"{prefix}groupId") or "").strip()
        artifact_id = (dep_elem.findtext(f"{prefix}artifactId") or "").strip()
        version = (dep_elem.findtext(f"{prefix}version") or "").strip()
        scope_elem = dep_elem.findtext(f"{prefix}scope")
        optional = dep_elem.findtext(f"{prefix}optional")

        if not artifact_id:
            continue

        name = f"{group_id}:{artifact_id}" if group_id else artifact_id
        scope = "production"
        if scope_elem:
            scope_val = scope_elem.strip().lower()
            if scope_val == "test":
                scope = "dev"
            elif scope_val == "provided":
                scope = "optional"
            elif scope_val == "runtime":
                scope = "production"
            elif scope_val == "compile":
                scope = "production"
        if optional and optional.strip().lower() == "true":
            scope = "optional"

        deps.append(PackageDependency(
            name=name, version=version, pinned_version="",
            ecosystem="maven", scope=scope, source_file=source,
        ))
    return deps


_GRADLE_DEP_RE = re.compile(
    r"""(?:implementation|api|compileOnly|runtimeOnly|testImplementation|"""
    r"""testCompileOnly|testRuntimeOnly|kapt|ksp|annotationProcessor|"""
    r"""androidTestImplementation|debugImplementation)\s*"""
    r"""[\(]?\s*['"]([^'"]+)['"]\s*[\)]?""",
)

_GRADLE_SCOPE_MAP = {
    "implementation": "production",
    "api": "production",
    "compileOnly": "optional",
    "runtimeOnly": "production",
    "testImplementation": "dev",
    "testCompileOnly": "dev",
    "testRuntimeOnly": "dev",
    "kapt": "build",
    "ksp": "build",
    "annotationProcessor": "build",
    "androidTestImplementation": "dev",
    "debugImplementation": "dev",
}


def _parse_build_gradle(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse build.gradle / build.gradle.kts for dependency declarations."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return deps

    for m in re.finditer(
        r"(implementation|api|compileOnly|runtimeOnly|testImplementation|"
        r"testCompileOnly|testRuntimeOnly|kapt|ksp|annotationProcessor|"
        r"androidTestImplementation|debugImplementation)\s*"
        r"""[\(]?\s*['"]([^'"]+)['"]\s*[\)]?""",
        content,
    ):
        config = m.group(1)
        coord = m.group(2)
        parts = coord.split(":")
        if len(parts) >= 2:
            name = f"{parts[0]}:{parts[1]}"
            ver = parts[2] if len(parts) >= 3 else ""
        else:
            name = coord
            ver = ""
        scope = _GRADLE_SCOPE_MAP.get(config, "production")
        deps.append(PackageDependency(
            name=name, version=ver, pinned_version="",
            ecosystem="maven", scope=scope, source_file=source,
        ))
    return deps


# ---------------------------------------------------------------------------
# PHP (composer.json)
# ---------------------------------------------------------------------------

def _parse_composer_json(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse composer.json for require and require-dev."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return deps

    for section, scope in [("require", "production"), ("require-dev", "dev")]:
        for name, ver in (data.get(section) or {}).items():
            # Skip PHP platform requirements (php, php-64bit, ext-*, etc.)
            if name in ("php", "php-64bit", "php-ipv6", "hhvm", "composer-plugin-api") or name.startswith("ext-"):
                continue
            deps.append(PackageDependency(
                name=name, version=ver if isinstance(ver, str) else "",
                pinned_version="", ecosystem="composer",
                scope=scope, source_file=source,
            ))
    return deps


# ---------------------------------------------------------------------------
# Ruby (Gemfile, Gemfile.lock)
# ---------------------------------------------------------------------------

_GEMFILE_RE = re.compile(
    r"""^\s*gem\s+['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]*)['"]\s*)?""",
)


def _parse_gemfile(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse Gemfile for gem declarations."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return deps

    in_dev_group = False
    for line in content.splitlines():
        stripped = line.strip()
        if re.match(r"group\s+:(?:development|test)", stripped):
            in_dev_group = True
            continue
        if stripped == "end":
            in_dev_group = False
            continue

        m = _GEMFILE_RE.match(stripped)
        if m:
            name = m.group(1)
            ver = m.group(2) or ""
            scope = "dev" if in_dev_group else "production"
            deps.append(PackageDependency(
                name=name, version=ver, pinned_version="",
                ecosystem="gem", scope=scope, source_file=source,
            ))
    return deps


def _parse_gemfile_lock(file_path: Path) -> dict[str, str]:
    """Parse Gemfile.lock to extract pinned versions. Returns {name: version}."""
    pinned = {}
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return pinned

    in_specs = False
    for line in content.splitlines():
        if line.strip() == "specs:":
            in_specs = True
            continue
        if in_specs:
            # Gem entries are indented with 4 spaces: "    name (version)"
            m = re.match(r"^    (\S+)\s+\(([^)]+)\)", line)
            if m:
                pinned[m.group(1)] = m.group(2)
            elif not line.startswith("  "):
                in_specs = False

    return pinned


# ---------------------------------------------------------------------------
# Docker (Dockerfile)
# ---------------------------------------------------------------------------

_DOCKERFILE_FROM_RE = re.compile(
    r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+?)(?:\s+[Aa][Ss]\s+\S+)?\s*$",
    re.IGNORECASE,
)


def _parse_dockerfile(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse Dockerfile for FROM base images."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return deps

    for line in content.splitlines():
        m = _DOCKERFILE_FROM_RE.match(line)
        if m:
            image = m.group(1)
            # Skip ARG references like ${BASE_IMAGE}
            if "${" in image:
                continue
            if ":" in image:
                name, tag = image.rsplit(":", 1)
            else:
                name, tag = image, "latest"
            # Skip scratch
            if name == "scratch":
                continue
            deps.append(PackageDependency(
                name=name, version=tag, pinned_version=tag,
                ecosystem="docker", scope="production", source_file=source,
            ))
    return deps


# ---------------------------------------------------------------------------
# Rust lockfile (Cargo.lock)
# ---------------------------------------------------------------------------

def _parse_cargo_lock(file_path: Path) -> dict[str, str]:
    """Parse Cargo.lock for pinned versions. Returns {name: version}.

    Only reads name/version pairs that live inside a ``[[package]]`` block;
    ``[metadata]`` and ``[patch.*]`` tables can contain ``name =`` keys too
    and were bleeding into the pinned map in earlier revisions.
    """
    pinned: dict[str, str] = {}
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return pinned

    in_package = False
    current_name = ""
    for raw in text.splitlines():
        line = raw.strip()
        # Section headers close the previous block.
        if line.startswith("[["):
            in_package = line == "[[package]]"
            current_name = ""
            continue
        if line.startswith("[") and not line.startswith("[["):
            in_package = False
            current_name = ""
            continue
        if not in_package:
            continue
        if line.startswith("name = "):
            current_name = line.split('"')[1] if '"' in line else ""
        elif line.startswith("version = ") and current_name:
            ver = line.split('"')[1] if '"' in line else ""
            if ver:
                pinned[current_name] = ver
            current_name = ""
    return pinned


# ---------------------------------------------------------------------------
# PHP lockfile (composer.lock)
# ---------------------------------------------------------------------------

def _parse_composer_lock(file_path: Path) -> dict[str, str]:
    """Parse composer.lock for pinned versions. Returns {name: version}."""
    pinned: dict[str, str] = {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return pinned
    for pkg in data.get("packages", []) + data.get("packages-dev", []):
        name = pkg.get("name", "")
        ver = pkg.get("version", "").lstrip("v")
        if name and ver:
            pinned[name] = ver
    return pinned


# ---------------------------------------------------------------------------
# Dart / Flutter (pubspec.yaml + pubspec.lock)
# ---------------------------------------------------------------------------

_YAML_KV_RE = re.compile(r"^\s{2,4}(\S+):\s*(.*)$")

def _parse_pubspec_yaml(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse pubspec.yaml for dependencies and dev_dependencies."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return deps

    current_section = ""
    for line in text.splitlines():
        stripped = line.rstrip()
        # Top-level section detection
        if not stripped.startswith(" ") and stripped.endswith(":"):
            current_section = stripped.rstrip(":")
            continue

        if current_section in ("dependencies", "dev_dependencies"):
            m = _YAML_KV_RE.match(stripped)
            if m:
                name = m.group(1)
                ver_raw = m.group(2).strip()
                # Skip SDK, path, git deps
                if name in ("flutter", "flutter_test", "flutter_localizations"):
                    continue
                ver = ver_raw.strip("'^~>=< ") if ver_raw and not ver_raw.startswith("{") else ""
                scope = "dev" if current_section == "dev_dependencies" else "production"
                deps.append(PackageDependency(
                    name=name, version=ver, pinned_version="",
                    ecosystem="pub", scope=scope, source_file=source,
                ))
    return deps


def _parse_pubspec_lock(file_path: Path) -> dict[str, str]:
    """Parse pubspec.lock for pinned versions. Returns {name: version}."""
    pinned: dict[str, str] = {}
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return pinned
    current_name = ""
    for line in text.splitlines():
        stripped = line.rstrip()
        # Package names are at 2-space indent, not 4
        if len(stripped) > 0 and not stripped.startswith("  ") and stripped.endswith(":") and stripped != "packages:":
            continue
        if stripped.startswith("  ") and not stripped.startswith("    ") and stripped.endswith(":"):
            current_name = stripped.strip().rstrip(":")
        elif '    version: ' in stripped and current_name:
            ver = stripped.split("version:")[1].strip().strip('"')
            if ver:
                pinned[current_name] = ver
    return pinned


# ---------------------------------------------------------------------------
# Swift (Package.resolved v2)
# ---------------------------------------------------------------------------

def _parse_package_resolved(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse Swift Package.resolved (v1 and v2) for dependencies."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deps

    # v2 format
    pins = data.get("pins", [])
    # v1 format
    if not pins:
        obj = data.get("object", {})
        pins = obj.get("pins", [])

    for pin in pins:
        # v2: identity, location, state.version
        name = pin.get("identity", pin.get("package", ""))
        state = pin.get("state", {})
        ver = state.get("version", state.get("revision", ""))
        if not name:
            continue
        deps.append(PackageDependency(
            name=name, version=ver, pinned_version=ver,
            ecosystem="swift", scope="production", source_file=source,
        ))
    return deps


# ---------------------------------------------------------------------------
# .NET (*.csproj + packages.lock.json)
# ---------------------------------------------------------------------------

def _parse_csproj(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse *.csproj for PackageReference elements."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return deps

    # Use regex instead of xml.etree to handle malformed XML and namespaces
    pkg_ref_re = re.compile(
        r'<PackageReference\s+Include="([^"]+)"(?:\s+Version="([^"]*)")?',
        re.IGNORECASE,
    )
    for m in pkg_ref_re.finditer(text):
        name = m.group(1)
        ver = m.group(2) or ""
        deps.append(PackageDependency(
            name=name, version=ver, pinned_version="",
            ecosystem="nuget", scope="production", source_file=source,
        ))
    return deps


def _parse_packages_lock_json(file_path: Path) -> dict[str, str]:
    """Parse .NET packages.lock.json for pinned versions."""
    pinned: dict[str, str] = {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return pinned
    for _framework, pkgs in (data.get("dependencies") or {}).items():
        if isinstance(pkgs, dict):
            for name, info in pkgs.items():
                if isinstance(info, dict):
                    ver = info.get("resolved", "")
                    if ver:
                        pinned[name] = ver
    return pinned


# ---------------------------------------------------------------------------
# Elixir (mix.exs + mix.lock)
# ---------------------------------------------------------------------------

_MIX_DEP_RE = re.compile(
    r"""\{:(\w+),\s*"([~><=!]*\s*[\d.]+)"(?:,\s*only:\s*:(\w+))?""",
)

def _parse_mix_exs(file_path: Path, project_path: Path) -> list[PackageDependency]:
    """Parse mix.exs for {:name, "version"} dependency tuples."""
    deps = []
    source = _rel_path(file_path, project_path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return deps

    for m in _MIX_DEP_RE.finditer(text):
        name = m.group(1)
        ver = m.group(2).strip()
        only = m.group(3) or ""
        scope = "dev" if only in ("dev", "test") else "production"
        deps.append(PackageDependency(
            name=name, version=ver, pinned_version="",
            ecosystem="hex", scope=scope, source_file=source,
        ))
    return deps


def _parse_mix_lock(file_path: Path) -> dict[str, str]:
    """Parse mix.lock for pinned versions. Returns {name: version}."""
    pinned: dict[str, str] = {}
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return pinned
    # Format: "name": {:hex, :name, "version", ...}
    lock_re = re.compile(r'"(\w+)":\s*\{:hex,\s*:\w+,\s*"([^"]+)"')
    for m in lock_re.finditer(text):
        pinned[m.group(1)] = m.group(2)
    return pinned


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

def scan_dependencies(project_path: str | Path) -> DependencyInventory:
    """
    Scan a project directory for all manifest files and extract dependencies.

    Args:
        project_path: Root directory to scan.

    Returns:
        DependencyInventory with all discovered dependencies.
    """
    project_path = Path(project_path).resolve()
    manifest_files = _find_manifest_files(project_path)

    all_deps: list[PackageDependency] = []
    found_manifests: list[str] = []
    has_lockfile = False

    # Collect lockfile pinned versions (keyed by directory + ecosystem)
    # Structure: {dir_path: {"npm": {name: ver}, "pypi": {name: ver}, ...}}
    pinned_versions: dict[str, dict[str, dict[str, str]]] = {}

    for fpath in manifest_files:
        rel = _rel_path(fpath, project_path)
        fname = fpath.name
        parent_dir = str(fpath.parent)

        # First pass: parse lockfiles to collect pinned versions
        if fname == "package-lock.json":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["npm"] = _parse_package_lock_json(fpath)
        elif fname == "yarn.lock":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["npm"] = _parse_yarn_lock(fpath)
        elif fname == "pnpm-lock.yaml":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["npm"] = _parse_pnpm_lock(fpath)
        elif fname == "poetry.lock":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["pypi"] = _parse_poetry_lock(fpath)
        elif fname == "Gemfile.lock":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["gem"] = _parse_gemfile_lock(fpath)
        elif fname == "Cargo.lock":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["cargo"] = _parse_cargo_lock(fpath)
        elif fname == "composer.lock":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["composer"] = _parse_composer_lock(fpath)
        elif fname == "pubspec.lock":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["pub"] = _parse_pubspec_lock(fpath)
        elif fname == "packages.lock.json":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["nuget"] = _parse_packages_lock_json(fpath)
        elif fname == "mix.lock":
            has_lockfile = True
            found_manifests.append(rel)
            pinned_versions.setdefault(parent_dir, {})["hex"] = _parse_mix_lock(fpath)

    # Second pass: parse manifest files
    for fpath in manifest_files:
        rel = _rel_path(fpath, project_path)
        fname = fpath.name

        # Skip lockfiles (already processed)
        if fname in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
                      "Gemfile.lock", "Cargo.lock", "composer.lock", "pubspec.lock",
                      "packages.lock.json", "mix.lock"):
            continue

        parsed = []
        if fname == "package.json":
            parsed = _parse_package_json(fpath, project_path)
        elif fname == "requirements.txt":
            parsed = _parse_requirements_txt(fpath, project_path)
        elif fname == "pyproject.toml":
            parsed = _parse_pyproject_toml(fpath, project_path)
        elif fname == "Pipfile":
            parsed = _parse_pipfile(fpath, project_path)
        elif fname == "go.mod":
            parsed = _parse_go_mod(fpath, project_path)
        elif fname == "Cargo.toml":
            parsed = _parse_cargo_toml(fpath, project_path)
        elif fname == "pom.xml":
            parsed = _parse_pom_xml(fpath, project_path)
        elif fname in ("build.gradle", "build.gradle.kts"):
            parsed = _parse_build_gradle(fpath, project_path)
        elif fname == "composer.json":
            parsed = _parse_composer_json(fpath, project_path)
        elif fname == "Gemfile":
            parsed = _parse_gemfile(fpath, project_path)
        elif fname.startswith("Dockerfile"):
            parsed = _parse_dockerfile(fpath, project_path)
        elif fname == "pubspec.yaml":
            parsed = _parse_pubspec_yaml(fpath, project_path)
        elif fname == "Package.resolved":
            parsed = _parse_package_resolved(fpath, project_path)
        elif fname.endswith(".csproj"):
            parsed = _parse_csproj(fpath, project_path)
        elif fname == "mix.exs":
            parsed = _parse_mix_exs(fpath, project_path)
        else:
            continue

        if parsed:
            if rel not in found_manifests:
                found_manifests.append(rel)
            all_deps.extend(parsed)

    # Apply pinned versions from lockfiles
    for dep in all_deps:
        if dep.pinned_version:
            continue
        # Find matching lockfile data in the same directory as the source file
        source_dir = str((project_path / dep.source_file).parent)
        eco_pins = pinned_versions.get(source_dir, {}).get(dep.ecosystem, {})
        if eco_pins:
            # Try exact name match (case-insensitive for pypi)
            lookup_name = dep.name.lower() if dep.ecosystem == "pypi" else dep.name
            pinned_ver = eco_pins.get(lookup_name, "")
            if not pinned_ver and dep.ecosystem == "pypi":
                # pypi packages may use hyphens or underscores interchangeably
                normalized = dep.name.lower().replace("-", "_")
                for k, v in eco_pins.items():
                    if k.replace("-", "_") == normalized:
                        pinned_ver = v
                        break
            dep.pinned_version = pinned_ver

    # Sort: ecosystem, scope, name
    scope_order = {"production": 0, "dev": 1, "peer": 2, "indirect": 3, "optional": 4, "build": 5}
    all_deps.sort(key=lambda d: (d.ecosystem, scope_order.get(d.scope, 99), d.name.lower()))

    # Detect version conflicts: same package name + ecosystem, different versions
    # Skip path/file/git dependencies — they're not real version conflicts
    _PATH_PREFIXES = ("file:", "@", "git+", "git://", "https://", "http://", "./", "../", "/")

    conflicts: list[VersionConflict] = []
    seen: dict[tuple[str, str], list[dict]] = {}
    for dep in all_deps:
        # Skip non-versioned deps (path refs, git refs)
        if not dep.version or any(dep.version.startswith(p) for p in _PATH_PREFIXES):
            continue
        # Strip extras for comparison: "uvicorn[standard]" → "uvicorn"
        raw_name = dep.name.split("[")[0]
        norm_name = raw_name.lower().replace("-", "_") if dep.ecosystem == "pypi" else raw_name
        key = (dep.ecosystem, norm_name)
        entry = {"version": dep.version, "source_file": dep.source_file}
        if key not in seen:
            seen[key] = [entry]
        else:
            existing_versions = {e["version"] for e in seen[key]}
            if dep.version not in existing_versions:
                seen[key].append(entry)
            elif dep.source_file not in {e["source_file"] for e in seen[key]}:
                seen[key].append(entry)

    for (eco, name), entries in seen.items():
        unique_versions = {e["version"] for e in entries}
        if len(unique_versions) > 1:
            conflicts.append(VersionConflict(name=name, ecosystem=eco, versions=entries))

    # Build inventory
    ecosystems = sorted(set(d.ecosystem for d in all_deps))
    prod_count = sum(1 for d in all_deps if d.scope == "production")
    dev_count = sum(1 for d in all_deps if d.scope in ("dev", "build"))
    indirect_count = sum(1 for d in all_deps if d.scope == "indirect")

    return DependencyInventory(
        project_path=str(project_path),
        ecosystems=ecosystems,
        total_count=len(all_deps),
        production_count=prod_count,
        dev_count=dev_count,
        indirect_count=indirect_count,
        dependencies=all_deps,
        manifest_files=sorted(found_manifests),
        has_lockfile=has_lockfile,
        conflicts=conflicts,
        scanned_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def format_dependency_table(inventory: DependencyInventory) -> str:
    """Format the dependency inventory as a human-readable table."""
    if not inventory.dependencies:
        return "No dependencies found."

    # Column widths
    eco_w = max(len("Ecosystem"), max(len(d.ecosystem) for d in inventory.dependencies))
    scope_w = max(len("Scope"), max(len(d.scope) for d in inventory.dependencies))
    name_w = max(len("Package"), min(50, max(len(d.name) for d in inventory.dependencies)))
    ver_w = max(len("Version"), min(20, max(len(d.version) for d in inventory.dependencies)))
    pin_w = max(len("Pinned"), min(20, max((len(d.pinned_version) for d in inventory.dependencies), default=0)))
    src_w = max(len("Source"), max(len(d.source_file) for d in inventory.dependencies))

    header = (
        f"{'Ecosystem':<{eco_w}}  {'Scope':<{scope_w}}  {'Package':<{name_w}}  "
        f"{'Version':<{ver_w}}  {'Pinned':<{pin_w}}  {'Source':<{src_w}}"
    )
    separator = "-" * len(header)

    lines = [header, separator]
    for dep in inventory.dependencies:
        name_display = dep.name[:name_w] if len(dep.name) > name_w else dep.name
        ver_display = dep.version[:ver_w] if len(dep.version) > ver_w else dep.version
        pin_display = dep.pinned_version[:pin_w] if len(dep.pinned_version) > pin_w else dep.pinned_version
        lines.append(
            f"{dep.ecosystem:<{eco_w}}  {dep.scope:<{scope_w}}  {name_display:<{name_w}}  "
            f"{ver_display:<{ver_w}}  {pin_display:<{pin_w}}  {dep.source_file:<{src_w}}"
        )

    # Summary
    summary_parts = [f"{inventory.production_count} production", f"{inventory.dev_count} dev"]
    if inventory.indirect_count > 0:
        summary_parts.append(f"{inventory.indirect_count} indirect")
    other_count = inventory.total_count - inventory.production_count - inventory.dev_count - inventory.indirect_count
    if other_count > 0:
        summary_parts.append(f"{other_count} other")
    eco_str = ", ".join(inventory.ecosystems)
    lines.append("")
    lines.append(
        f"Summary: {inventory.total_count} packages "
        f"({', '.join(summary_parts)}) "
        f"across {len(inventory.ecosystems)} ecosystem{'s' if len(inventory.ecosystems) != 1 else ''} [{eco_str}]"
    )

    # Version conflicts
    if inventory.conflicts:
        lines.append("")
        lines.append(f"Version conflicts ({len(inventory.conflicts)}):")
        for conflict in inventory.conflicts:
            versions_str = ", ".join(
                f"{v['version']} ({v['source_file']})" for v in conflict.versions
            )
            lines.append(f"  {conflict.ecosystem}/{conflict.name}: {versions_str}")

    return "\n".join(lines)
