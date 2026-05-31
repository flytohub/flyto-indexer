"""
Framework-Aware Analysis — detect project frameworks and apply framework-specific rules.

Detects frameworks from dependency manifests and file patterns across Python, JS/TS,
Go, Rust, Mobile, and Desktop ecosystems. Produces FrameworkInfo with conventions
and entry points.

Pure Python stdlib — no external dependencies.
"""

import json
import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.framework")

# Directories to skip during filesystem walk
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", ".ruff_cache", "target", "out", ".next",
    ".nuxt", ".output", "coverage", ".cache", ".parcel-cache",
    "bower_components", ".eggs", "egg-info",
})


# ---------------------------------------------------------------------------
# Framework definitions
# ---------------------------------------------------------------------------

FRAMEWORKS: dict[str, dict] = {
    # Python
    "fastapi": {
        "indicator_deps": ["fastapi"],
        "indicator_files": [],
        "type": "api",
    },
    "django": {
        "indicator_deps": ["django"],
        "indicator_files": ["manage.py"],
        "type": "api",
    },
    "flask": {
        "indicator_deps": ["flask"],
        "indicator_files": [],
        "type": "api",
    },

    # JavaScript/TypeScript
    "nextjs": {
        "indicator_deps": ["next"],
        "indicator_files": ["next.config.js", "next.config.ts", "next.config.mjs"],
        "type": "ssr",
    },
    "nuxt": {
        "indicator_deps": ["nuxt"],
        "indicator_files": ["nuxt.config.ts", "nuxt.config.js"],
        "type": "ssr",
    },
    "react": {
        "indicator_deps": ["react"],
        "indicator_files": [],
        "type": "spa",
    },
    "vue": {
        "indicator_deps": ["vue"],
        "indicator_files": [],
        "type": "spa",
    },
    "express": {
        "indicator_deps": ["express"],
        "indicator_files": [],
        "type": "api",
    },
    "nestjs": {
        "indicator_deps": ["@nestjs/core"],
        "indicator_files": [],
        "type": "api",
    },

    # Go
    "gin": {
        "indicator_deps": ["github.com/gin-gonic/gin"],
        "indicator_files": [],
        "type": "api",
    },
    "echo": {
        "indicator_deps": ["github.com/labstack/echo"],
        "indicator_files": [],
        "type": "api",
    },
    "fiber": {
        "indicator_deps": ["github.com/gofiber/fiber"],
        "indicator_files": [],
        "type": "api",
    },
    "chi": {
        "indicator_deps": ["github.com/go-chi/chi"],
        "indicator_files": [],
        "type": "api",
    },

    # Rust
    "actix": {
        "indicator_deps": ["actix-web"],
        "indicator_files": [],
        "type": "api",
    },
    "axum": {
        "indicator_deps": ["axum"],
        "indicator_files": [],
        "type": "api",
    },

    # Mobile
    "flutter": {
        "indicator_deps": [],
        "indicator_files": ["pubspec.yaml"],
        "type": "mobile",
    },
    "react_native": {
        "indicator_deps": ["react-native"],
        "indicator_files": [],
        "type": "mobile",
    },

    # Desktop
    "tauri": {
        "indicator_deps": ["tauri", "@tauri-apps/api"],
        "indicator_files": ["tauri.conf.json"],
        "type": "desktop",
    },
    "electron": {
        "indicator_deps": ["electron"],
        "indicator_files": [],
        "type": "desktop",
    },

    # UI / CSS Libraries
    "mantine": {
        "indicator_deps": ["@mantine/core"],
        "indicator_files": [],
        "type": "ui",
    },
    "tailwindcss": {
        "indicator_deps": ["tailwindcss"],
        "indicator_files": ["tailwind.config.js", "tailwind.config.ts"],
        "type": "ui",
    },

    # Data fetching / State
    "tanstack_query": {
        "indicator_deps": ["@tanstack/react-query"],
        "indicator_files": [],
        "type": "data",
    },
    "openapi_fetch": {
        "indicator_deps": ["openapi-fetch"],
        "indicator_files": [],
        "type": "data",
    },

    # Routing
    "react_router": {
        "indicator_deps": ["react-router-dom"],
        "indicator_files": [],
        "type": "routing",
    },

    # Icons
    "lucide": {
        "indicator_deps": ["lucide-react"],
        "indicator_files": [],
        "type": "ui",
    },
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FrameworkInfo:
    """Information about a detected framework."""
    name: str               # "fastapi", "nextjs", etc.
    version: str            # from deps if available
    type: str               # "api", "spa", "ssr", "mobile", "desktop"
    conventions: dict = field(default_factory=dict)
    entry_points: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "type": self.type,
            "conventions": self.conventions,
            "entry_points": self.entry_points,
        }


# ---------------------------------------------------------------------------
# Dependency extraction helpers
# ---------------------------------------------------------------------------

def _read_package_json_deps(project_path: Path) -> dict[str, str]:
    """Read dependency names + versions from package.json (root + subdirs)."""
    deps: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel = os.path.relpath(dirpath, project_path)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > 2:
            dirnames.clear()
            continue
        if "package.json" not in filenames:
            continue
        pkg_path = Path(dirpath) / "package.json"
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                for name, version in (data.get(section) or {}).items():
                    if isinstance(version, str):
                        deps[name] = version
        except (json.JSONDecodeError, OSError):
            pass
    return deps


def _read_pyproject_deps(project_path: Path) -> dict[str, str]:
    """Read dependency names + versions from pyproject.toml."""
    deps: dict[str, str] = {}
    pyproject_path = project_path / "pyproject.toml"
    if not pyproject_path.exists():
        return deps
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        # PEP 621 dependencies
        for dep_str in data.get("project", {}).get("dependencies", []):
            if isinstance(dep_str, str):
                # Parse "fastapi>=0.100" -> ("fastapi", ">=0.100")
                match = re.match(r'^([a-zA-Z0-9_\-\.]+(?:\[[^\]]+\])?)\s*(.*)', dep_str)
                if match:
                    name = match.group(1).split("[")[0].strip()
                    version = match.group(2).strip()
                    deps[name.lower()] = version
        # Optional dependencies
        for group_deps in data.get("project", {}).get("optional-dependencies", {}).values():
            for dep_str in group_deps:
                if isinstance(dep_str, str):
                    match = re.match(r'^([a-zA-Z0-9_\-\.]+(?:\[[^\]]+\])?)\s*(.*)', dep_str)
                    if match:
                        name = match.group(1).split("[")[0].strip()
                        version = match.group(2).strip()
                        deps[name.lower()] = version
        # Poetry
        for section in ("dependencies", "dev-dependencies"):
            poetry_deps = data.get("tool", {}).get("poetry", {}).get(section, {})
            for name, ver in poetry_deps.items():
                if isinstance(ver, str):
                    deps[name.lower()] = ver
                elif isinstance(ver, dict):
                    deps[name.lower()] = ver.get("version", "")
    except (ValueError, OSError):
        pass
    return deps


def _read_requirements_deps(project_path: Path) -> dict[str, str]:
    """Read dependency names + versions from requirements*.txt (root + subdirs)."""
    deps: dict[str, str] = {}
    req_names = {"requirements.txt", "requirements-dev.txt", "requirements_dev.txt"}
    # Search root + up to 2 levels deep
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel = os.path.relpath(dirpath, project_path)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > 2:
            dirnames.clear()
            continue
        for fname in filenames:
            if fname not in req_names:
                continue
            req_path = Path(dirpath) / fname
            try:
                for line in req_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    match = re.match(r'^([a-zA-Z0-9_\-\.]+(?:\[[^\]]+\])?)\s*(.*)', line)
                    if match:
                        name = match.group(1).split("[")[0].strip()
                        version = match.group(2).strip()
                        deps[name.lower()] = version
            except OSError:
                pass
    return deps


def _read_go_mod_deps(project_path: Path) -> dict[str, str]:
    """Read dependency names + versions from go.mod."""
    deps: dict[str, str] = {}
    go_mod_path = project_path / "go.mod"
    if not go_mod_path.exists():
        return deps
    try:
        content = go_mod_path.read_text(encoding="utf-8")
        in_require = False
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("require ("):
                in_require = True
                continue
            if in_require:
                if line == ")":
                    in_require = False
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    deps[parts[0]] = parts[1]
            elif line.startswith("require "):
                parts = line.split()
                if len(parts) >= 3:
                    deps[parts[1]] = parts[2]
    except OSError:
        pass
    return deps


def _read_cargo_deps(project_path: Path) -> dict[str, str]:
    """Read dependency names + versions from Cargo.toml."""
    deps: dict[str, str] = {}
    cargo_path = project_path / "Cargo.toml"
    if not cargo_path.exists():
        return deps
    try:
        data = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            for name, ver in (data.get(section) or {}).items():
                if isinstance(ver, str):
                    deps[name] = ver
                elif isinstance(ver, dict):
                    deps[name] = ver.get("version", "")
    except (ValueError, OSError):
        pass
    return deps


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def _scan_project_files(project_path: Path) -> list[str]:
    """Collect all file basenames and relative paths in the project."""
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, project_path)
        for fname in filenames:
            if rel_dir == ".":
                files.append(fname)
            else:
                files.append(os.path.join(rel_dir, fname))
    return files


# ---------------------------------------------------------------------------
# Convention detection
# ---------------------------------------------------------------------------

def _detect_fastapi_conventions(project_path: Path, files: list[str]) -> dict:
    """Detect FastAPI-specific conventions."""
    conventions: dict = {"route_pattern": "decorator"}

    # Check for ORM
    all_deps = _read_pyproject_deps(project_path)
    all_deps.update(_read_requirements_deps(project_path))
    dep_names = {d.lower().replace("-", "_") for d in all_deps}
    if "sqlalchemy" in dep_names:
        conventions["orm"] = "sqlalchemy"
    elif "tortoise_orm" in dep_names or "tortoise" in dep_names:
        conventions["orm"] = "tortoise"
    elif "prisma" in dep_names:
        conventions["orm"] = "prisma"
    elif "django" in dep_names:
        conventions["orm"] = "django"

    # Check for auth pattern
    if "pyjwt" in dep_names or "python_jose" in dep_names or "jose" in dep_names:
        conventions["auth"] = "jwt"
    elif "firebase_admin" in dep_names:
        conventions["auth"] = "firebase"
    elif "authlib" in dep_names:
        conventions["auth"] = "oauth"

    # Check for background tasks
    if "celery" in dep_names:
        conventions["background_tasks"] = "celery"
    elif "rq" in dep_names:
        conventions["background_tasks"] = "rq"
    elif "dramatiq" in dep_names:
        conventions["background_tasks"] = "dramatiq"

    return conventions


def _detect_django_conventions(project_path: Path, files: list[str]) -> dict:
    """Detect Django-specific conventions."""
    conventions: dict = {"orm": "django"}

    # Check for DRF
    all_deps = _read_pyproject_deps(project_path)
    all_deps.update(_read_requirements_deps(project_path))
    dep_names = {d.lower().replace("-", "_") for d in all_deps}
    if "djangorestframework" in dep_names or "rest_framework" in dep_names:
        conventions["api"] = "drf"
    if "django_ninja" in dep_names:
        conventions["api"] = "ninja"
    if "graphene_django" in dep_names:
        conventions["api"] = "graphql"

    return conventions


def _detect_nextjs_conventions(project_path: Path, files: list[str]) -> dict:
    """Detect Next.js-specific conventions."""
    conventions: dict = {}

    # App router vs pages router
    has_app_dir = any(f.startswith("app/") or f.startswith("src/app/") for f in files)
    has_pages_dir = any(f.startswith("pages/") or f.startswith("src/pages/") for f in files)
    if has_app_dir:
        conventions["routing"] = "app_router"
    elif has_pages_dir:
        conventions["routing"] = "pages_router"

    # Rendering strategy
    # Check for getStaticProps / getServerSideProps patterns
    has_ssg = any(f.endswith("getStaticProps") for f in files)
    has_ssr = any(f.endswith("getServerSideProps") for f in files)
    if has_app_dir:
        conventions["rendering"] = "rsc"  # React Server Components
    elif has_ssg:
        conventions["rendering"] = "ssg"
    elif has_ssr:
        conventions["rendering"] = "ssr"

    return conventions


def _detect_nuxt_conventions(project_path: Path, files: list[str]) -> dict:
    """Detect Nuxt-specific conventions."""
    conventions: dict = {}

    # State management
    all_deps = _read_package_json_deps(project_path)
    if "pinia" in all_deps or "@pinia/nuxt" in all_deps:
        conventions["state"] = "pinia"
    elif "vuex" in all_deps:
        conventions["state"] = "vuex"

    # Composables
    has_composables = any(
        "composables/" in f for f in files
    )
    if has_composables:
        conventions["composables"] = True

    return conventions


def _detect_vue_conventions(project_path: Path, files: list[str]) -> dict:
    """Detect Vue-specific conventions."""
    conventions: dict = {}

    all_deps = _read_package_json_deps(project_path)

    # State management
    if "pinia" in all_deps:
        conventions["state"] = "pinia"
    elif "vuex" in all_deps:
        conventions["state"] = "vuex"

    # Composables
    has_composables = any("composables/" in f for f in files)
    if has_composables:
        conventions["composables"] = True

    # Router
    if "vue-router" in all_deps:
        conventions["router"] = True

    return conventions


def _detect_react_conventions(project_path: Path, files: list[str]) -> dict:
    """Detect React-specific conventions."""
    conventions: dict = {}

    all_deps = _read_package_json_deps(project_path)

    # State management
    if "@reduxjs/toolkit" in all_deps or "redux" in all_deps:
        conventions["state"] = "redux"
    elif "zustand" in all_deps:
        conventions["state"] = "zustand"
    elif "recoil" in all_deps:
        conventions["state"] = "recoil"
    elif "jotai" in all_deps:
        conventions["state"] = "jotai"
    elif "mobx" in all_deps:
        conventions["state"] = "mobx"

    # Router
    if "react-router-dom" in all_deps or "react-router" in all_deps:
        conventions["router"] = "react-router"
    elif "wouter" in all_deps:
        conventions["router"] = "wouter"

    return conventions


def _detect_express_conventions(project_path: Path, files: list[str]) -> dict:
    """Detect Express-specific conventions."""
    conventions: dict = {"route_pattern": "middleware"}

    all_deps = _read_package_json_deps(project_path)

    if "mongoose" in all_deps:
        conventions["orm"] = "mongoose"
    elif "prisma" in all_deps or "@prisma/client" in all_deps:
        conventions["orm"] = "prisma"
    elif "sequelize" in all_deps:
        conventions["orm"] = "sequelize"
    elif "typeorm" in all_deps:
        conventions["orm"] = "typeorm"
    elif "knex" in all_deps:
        conventions["orm"] = "knex"

    return conventions


_CONVENTION_DETECTORS = {
    "fastapi": _detect_fastapi_conventions,
    "django": _detect_django_conventions,
    "nextjs": _detect_nextjs_conventions,
    "nuxt": _detect_nuxt_conventions,
    "vue": _detect_vue_conventions,
    "react": _detect_react_conventions,
    "express": _detect_express_conventions,
}


# ---------------------------------------------------------------------------
# Entry point detection
# ---------------------------------------------------------------------------

_FRAMEWORK_ENTRY_PATTERNS: dict[str, list] = {
    "fastapi": [
        # Files containing app = FastAPI()
        (re.compile(r'(?:app|application)\s*=\s*FastAPI\('), "FastAPI app instance"),
    ],
    "django": [
        # settings.py, urls.py, views.py
        (re.compile(r'INSTALLED_APPS\s*='), "Django settings"),
        (re.compile(r'urlpatterns\s*='), "Django URL config"),
    ],
    "flask": [
        (re.compile(r'(?:app|application)\s*=\s*Flask\('), "Flask app instance"),
    ],
    "express": [
        (re.compile(r'(?:app|server)\s*=\s*express\(\)'), "Express app instance"),
        (re.compile(r'Router\(\)'), "Express Router"),
    ],
    "nextjs": [],  # Convention-based: app/page.tsx, pages/index.tsx
    "nuxt": [],    # Convention-based: pages/, app.vue
}

# Convention-based entry points (file patterns)
_FRAMEWORK_ENTRY_FILES: dict[str, list[str]] = {
    "nextjs": [
        "app/page.tsx", "app/page.jsx", "app/page.js",
        "app/layout.tsx", "app/layout.jsx", "app/layout.js",
        "pages/index.tsx", "pages/index.jsx", "pages/index.js",
        "src/app/page.tsx", "src/app/layout.tsx",
        "src/pages/index.tsx",
    ],
    "nuxt": [
        "app.vue", "pages/index.vue", "nuxt.config.ts", "nuxt.config.js",
    ],
    "django": [
        "manage.py",
    ],
    "flutter": [
        "lib/main.dart",
    ],
}


def _find_entry_points(
    framework_name: str,
    project_path: Path,
    files: list[str],
) -> list[str]:
    """Find framework-specific entry points."""
    entry_points: list[str] = []

    # Convention-based file patterns
    patterns = _FRAMEWORK_ENTRY_FILES.get(framework_name, [])
    for pattern in patterns:
        # Normalize separators
        normalized = pattern.replace("/", os.sep)
        for f in files:
            f_normalized = f.replace("/", os.sep)
            if f_normalized == normalized or f_normalized.endswith(os.sep + normalized):
                entry_points.append(f)

    # Regex-based detection (scan file contents)
    regex_patterns = _FRAMEWORK_ENTRY_PATTERNS.get(framework_name, [])
    if regex_patterns:
        # Only scan relevant files
        _EXT_MAP = {
            "fastapi": {".py"},
            "flask": {".py"},
            "django": {".py"},
            "express": {".js", ".ts", ".mjs", ".cjs"},
        }
        exts = _EXT_MAP.get(framework_name, {".py", ".js", ".ts"})
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in exts:
                continue
            full_path = project_path / f
            if not full_path.exists() or full_path.stat().st_size > 500_000:
                continue
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                for regex, _label in regex_patterns:
                    if regex.search(content):
                        if f not in entry_points:
                            entry_points.append(f)
                        break
            except OSError:
                continue

    return sorted(entry_points)


# ---------------------------------------------------------------------------
# Main detection logic
# ---------------------------------------------------------------------------

def detect_frameworks(project_path: Path) -> list[FrameworkInfo]:
    """
    Detect all frameworks used in a project.

    Args:
        project_path: Absolute path to the project root.

    Returns:
        List of FrameworkInfo objects for each detected framework,
        sorted by type priority (api first, then ssr, spa, mobile, desktop).
    """
    project_path = project_path.resolve()

    # Collect all dependencies
    all_deps: dict[str, str] = {}
    all_deps.update(_read_package_json_deps(project_path))
    all_deps.update(_read_pyproject_deps(project_path))
    all_deps.update(_read_requirements_deps(project_path))
    all_deps.update(_read_go_mod_deps(project_path))
    all_deps.update(_read_cargo_deps(project_path))

    # Normalize dep names for matching
    dep_names_raw = set(all_deps.keys())

    # Collect all files
    files = _scan_project_files(project_path)
    file_basenames = {os.path.basename(f) for f in files}

    detected: list[FrameworkInfo] = []

    for fw_name, fw_def in FRAMEWORKS.items():
        found = False

        # Check dependency indicators
        for indicator_dep in fw_def["indicator_deps"]:
            if indicator_dep in dep_names_raw:
                found = True
                break
            # Also check lowercase normalized
            indicator_lower = indicator_dep.lower().replace("-", "_")
            for raw_dep in dep_names_raw:
                if raw_dep.lower().replace("-", "_") == indicator_lower:
                    found = True
                    break
            if found:
                break

        # Check file indicators
        if not found:
            for indicator_file in fw_def["indicator_files"]:
                if indicator_file in file_basenames:
                    found = True
                    break
                # Also check in files list for path-based indicators
                for f in files:
                    if f == indicator_file or f.endswith(os.sep + indicator_file):
                        found = True
                        break
                if found:
                    break

        if not found:
            continue

        # Get version from deps
        version = ""
        for indicator_dep in fw_def["indicator_deps"]:
            if indicator_dep in all_deps:
                version = all_deps[indicator_dep]
                break
            for raw_dep in dep_names_raw:
                if raw_dep.lower().replace("-", "_") == indicator_dep.lower().replace("-", "_"):
                    version = all_deps[raw_dep]
                    break
            if version:
                break

        # Detect conventions
        detector = _CONVENTION_DETECTORS.get(fw_name)
        conventions = detector(project_path, files) if detector else {}

        # Find entry points
        entry_points = _find_entry_points(fw_name, project_path, files)

        info = FrameworkInfo(
            name=fw_name,
            version=version,
            type=fw_def["type"],
            conventions=conventions,
            entry_points=entry_points,
        )
        detected.append(info)

    # De-duplicate: if both "react" and "nextjs" detected, nextjs is more specific
    _OVERRIDES = {
        "nextjs": {"react"},      # nextjs implies react
        "nuxt": {"vue"},          # nuxt implies vue
        "react_native": {"react"},  # react-native implies react
    }
    names_detected = {fw.name for fw in detected}
    for specific, generals in _OVERRIDES.items():
        if specific in names_detected:
            detected = [fw for fw in detected if fw.name not in generals]

    # Sort by type priority
    _TYPE_ORDER = {"api": 0, "ssr": 1, "spa": 2, "mobile": 3, "desktop": 4, "data": 5, "routing": 6, "ui": 7}
    detected.sort(key=lambda fw: (_TYPE_ORDER.get(fw.type, 9), fw.name))

    return detected


def format_frameworks(frameworks: list[FrameworkInfo]) -> str:
    """Format framework detection results as human-readable text."""
    if not frameworks:
        return "No frameworks detected."

    lines = [f"Detected {len(frameworks)} framework(s):", ""]
    for fw in frameworks:
        version_str = f" v{fw.version}" if fw.version else ""
        lines.append(f"  {fw.name}{version_str} [{fw.type}]")
        if fw.conventions:
            conv_parts = [f"{k}={v}" for k, v in fw.conventions.items()]
            lines.append(f"    Conventions: {', '.join(conv_parts)}")
        if fw.entry_points:
            lines.append(f"    Entry points: {', '.join(fw.entry_points[:5])}")
            if len(fw.entry_points) > 5:
                lines.append(f"      ... and {len(fw.entry_points) - 5} more")
    return "\n".join(lines)
