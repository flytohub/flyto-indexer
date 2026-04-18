"""
Project Profile — aggregates all project facts into a single structured output.

Collects data from the flyto-indexer index (if available), filesystem analysis,
dependency scanner, and git history to produce a comprehensive project profile
suitable for LLM consumption or visualization rendering.
"""

import json
import logging
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.profile")

# Directories to skip during filesystem walk
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", ".ruff_cache", "target", "out", ".next",
    ".nuxt", ".output", "coverage", ".cache", ".parcel-cache",
    "bower_components", ".eggs", "egg-info",
})

# Extension-to-language mapping
_EXT_LANG = {
    ".py": "Python", ".pyi": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".vue": "Vue",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".c": "C", ".h": "C/C++",
    ".swift": "Swift",
    ".dart": "Dart",
    ".sql": "SQL",
    ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".less": "LESS",
    ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".xml": "XML",
    ".md": "Markdown",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".lua": "Lua",
    ".r": "R",
    ".scala": "Scala",
    ".ex": "Elixir", ".exs": "Elixir",
    ".zig": "Zig",
}

# Config files to detect
_CONFIG_FILES = [
    ".env.example", ".env.sample", ".env.template",
    "docker-compose.yml", "docker-compose.yaml",
    "Makefile", "Justfile", "Taskfile.yml",
    ".editorconfig", ".prettierrc", ".prettierrc.json", ".prettierrc.yaml",
    ".eslintrc", ".eslintrc.json", ".eslintrc.js", ".eslintrc.yaml",
    "eslint.config.js", "eslint.config.mjs",
    "tsconfig.json", "jsconfig.json",
    "vite.config.ts", "vite.config.js",
    "webpack.config.js", "rollup.config.js",
    "tailwind.config.js", "tailwind.config.ts",
    "nginx.conf",
    "fly.toml", "render.yaml", "vercel.json", "netlify.toml",
    "Procfile", "app.yaml", "cloudbuild.yaml",
    ".dockerignore", ".gitignore",
    "tox.ini", "setup.cfg", "setup.py",
    "pyproject.toml", "Cargo.toml", "go.mod",
    "package.json", "composer.json", "Gemfile",
    "pom.xml", "build.gradle", "build.gradle.kts",
    "alembic.ini", "knexfile.js",
    "pytest.ini", "conftest.py",
    ".flake8", "ruff.toml", ".ruff.toml",
    "unocss.config.ts", "uno.config.ts",
]


# ---------------------------------------------------------------------------
# Filesystem analysis (no index required)
# ---------------------------------------------------------------------------

def _scan_filesystem(project_path: Path) -> dict:
    """Walk project directory to collect structure, languages, and signals."""
    file_count = 0
    folder_counts = {}  # relative dir path -> file count (top 2 levels)
    lang_counter = Counter()
    config_files_found = []
    has_docker = False
    has_ci = False
    has_tests = False
    has_docs = False
    all_files = []  # relative paths for pattern detection

    for dirpath, dirnames, filenames in os.walk(project_path):
        # Filter skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        rel_dir = os.path.relpath(dirpath, project_path)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1

        for fname in filenames:
            file_count += 1
            rel_file = os.path.join(rel_dir, fname) if rel_dir != "." else fname
            all_files.append(rel_file)

            # Language detection
            ext = os.path.splitext(fname)[1].lower()
            if ext in _EXT_LANG:
                lang_counter[_EXT_LANG[ext]] += 1

            # Folder structure (top 2 levels)
            if depth <= 2:
                if depth == 0:
                    folder_key = "."
                else:
                    parts = rel_dir.split(os.sep)
                    folder_key = os.sep.join(parts[:min(depth, 2)])
                folder_counts[folder_key] = folder_counts.get(folder_key, 0) + 1

            # Config file detection
            if fname in _CONFIG_FILES:
                config_files_found.append(rel_file)

            # Infrastructure signals
            if fname.startswith("Dockerfile"):
                has_docker = True
            if fname in ("README.md", "README.rst", "README.txt", "README"):
                has_docs = True

        # Directory-level signals
        dir_name = os.path.basename(dirpath)
        if dir_name in ("docs", "doc", "documentation"):
            has_docs = True
        if dir_name in ("tests", "test", "__tests__", "spec", "specs"):
            has_tests = True

    # CI detection
    ci_paths = [
        project_path / ".github" / "workflows",
        project_path / ".gitlab-ci.yml",
        project_path / ".circleci",
        project_path / "Jenkinsfile",
        project_path / ".travis.yml",
        project_path / "bitbucket-pipelines.yml",
    ]
    for cp in ci_paths:
        if cp.exists():
            has_ci = True
            break

    # Test detection fallback: check for test files in any directory
    if not has_tests:
        for f in all_files:
            base = os.path.basename(f).lower()
            if (base.startswith("test_") or base.endswith("_test.py")
                    or base.endswith(".test.ts") or base.endswith(".test.js")
                    or base.endswith(".spec.ts") or base.endswith(".spec.js")
                    or base.endswith("_test.go")):
                has_tests = True
                break

    # Build folder structure list sorted by file count
    folder_structure = [
        {"path": k, "files": v}
        for k, v in sorted(folder_counts.items(), key=lambda x: -x[1])
    ]

    return {
        "file_count": file_count,
        "folder_structure": folder_structure[:30],  # cap to top 30
        "languages": dict(lang_counter.most_common()),
        "has_docker": has_docker,
        "has_ci": has_ci,
        "has_tests": has_tests,
        "has_docs": has_docs,
        "config_files": sorted(config_files_found),
        "_all_files": all_files,  # internal, for pattern detection
    }


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

_PATTERN_SIGNALS = {
    "auth_middleware": {
        "dirs": ["auth", "middleware/auth", "middlewares/auth"],
        "files": ["auth.py", "auth.ts", "auth.js", "auth.go", "jwt.py", "jwt.ts", "jwt.go"],
        "deps": ["jsonwebtoken", "pyjwt", "jwt", "passport", "authlib", "flask-login",
                 "django-allauth", "firebase-admin", "jose"],
    },
    "websocket": {
        "dirs": ["ws", "websocket", "websockets"],
        "files": ["websocket.py", "ws.py", "websocket.ts", "ws.ts", "ws.go"],
        "deps": ["ws", "socket.io", "websockets", "channels", "gorilla/websocket"],
    },
    "queue_consumer": {
        "dirs": ["workers", "tasks", "jobs", "consumers"],
        "files": ["celery.py", "tasks.py", "worker.py", "consumer.py"],
        "deps": ["celery", "bull", "bullmq", "rabbitmq", "amqplib", "amqp",
                 "rq", "dramatiq", "huey", "nats"],
    },
    "cron_job": {
        "dirs": ["cron", "scheduler", "schedules"],
        "files": ["cron.py", "scheduler.py", "schedule.py"],
        "deps": ["apscheduler", "schedule", "cron", "node-cron", "croner"],
    },
    "orm": {
        "dirs": ["models", "entities", "schema"],
        "deps": ["sqlalchemy", "prisma", "typeorm", "sequelize", "gorm",
                 "django", "tortoise-orm", "peewee", "drizzle-orm",
                 "mongoose", "knex", "objection", "bookshelf", "mikro-orm"],
    },
    "migration": {
        "dirs": ["migrations", "alembic", "migrate", "db/migrations"],
        "deps": ["alembic", "django-migrate", "knex", "flyway", "golang-migrate"],
    },
    "i18n": {
        "dirs": ["i18n", "locales", "locale", "translations", "lang"],
        "files": ["i18n.ts", "i18n.js", "i18n.py"],
        "deps": ["i18next", "vue-i18n", "react-intl", "babel", "gettext"],
    },
    "caching": {
        "dirs": ["cache"],
        "deps": ["redis", "ioredis", "memcached", "node-cache", "cachetools",
                 "aiocache", "django-redis"],
    },
    "logging": {
        "dirs": ["logging"],
        "deps": ["winston", "pino", "bunyan", "structlog", "loguru",
                 "slog", "zerolog", "zap"],
    },
    "rate_limiting": {
        "files": ["rate_limit.py", "rate_limiter.py", "throttle.py",
                  "rate-limit.ts", "throttle.ts"],
        "deps": ["express-rate-limit", "slowapi", "django-ratelimit",
                 "throttle", "limiter"],
    },
    "graphql": {
        "dirs": ["graphql"],
        "files": ["schema.graphql", "resolvers.py", "resolvers.ts"],
        "deps": ["graphql", "apollo-server", "ariadne", "strawberry",
                 "graphene", "type-graphql", "nexus"],
    },
    "grpc": {
        "dirs": ["proto", "protos", "grpc"],
        "deps": ["grpc", "grpcio", "@grpc/grpc-js", "protobuf", "protoc"],
    },
    "testing": {
        "dirs": ["tests", "test", "__tests__", "spec"],
        "deps": ["pytest", "jest", "mocha", "vitest", "testing-library",
                 "cypress", "playwright"],
    },
    "containerization": {
        "files": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                  ".dockerignore", "Containerfile"],
        "deps": [],
    },
}


def _detect_patterns(all_files: list, dep_names: set,
                     index_data: dict | None = None) -> list:
    """Detect architectural patterns from file paths, dependency names, and index symbols."""
    detected = []

    # Normalize dep names for matching
    dep_names_lower = {d.lower().replace("-", "_").replace("/", "_") for d in dep_names}

    for pattern_name, signals in _PATTERN_SIGNALS.items():
        found = False

        # Check directories
        for d in signals.get("dirs", []):
            for f in all_files:
                if f"/{d}/" in f"/{f}" or f.startswith(f"{d}/") or f"\\{d}\\" in f:
                    found = True
                    break
            if found:
                break

        # Check files
        if not found:
            for target_file in signals.get("files", []):
                for f in all_files:
                    if os.path.basename(f).lower() == target_file.lower():
                        found = True
                        break
                if found:
                    break

        # Check dependencies
        if not found:
            for dep in signals.get("deps", []):
                dep_norm = dep.lower().replace("-", "_").replace("/", "_")
                if dep_norm in dep_names_lower:
                    found = True
                    break

        if found:
            detected.append(pattern_name)

    # --- Additional pattern detection ---

    # auth: also check for firebase, jwt, oauth in deps
    if "auth_middleware" not in detected:
        auth_deps = {"firebase", "firebase_admin", "jwt", "pyjwt", "jose",
                     "oauth", "oauth2", "oauthlib", "authlib", "passport",
                     "jsonwebtoken", "next_auth", "nextauth"}
        if auth_deps & dep_names_lower:
            detected.append("auth_middleware")

    # state_management: react-query, redux, vuex, pinia, zustand, etc.
    state_deps = {"react_query", "@tanstack_react_query", "tanstack_react_query",
                  "redux", "react_redux", "@reduxjs_toolkit", "reduxjs_toolkit",
                  "vuex", "pinia", "zustand", "mobx", "recoil", "jotai", "valtio"}
    if state_deps & dep_names_lower:
        detected.append("state_management")

    # routing: react-router, vue-router, gorilla/mux, etc.
    routing_deps = {"react_router", "react_router_dom", "vue_router",
                    "gorilla_mux", "@angular_router", "angular_router",
                    "next", "nuxt", "wouter", "reach_router"}
    if routing_deps & dep_names_lower:
        detected.append("routing")

    # realtime: socket.io, ws, actioncable, etc.
    if "websocket" not in detected:
        realtime_deps = {"socket.io", "socket_io", "socket.io_client", "socket_io_client",
                         "ws", "actioncable", "action_cable", "pusher", "ably",
                         "centrifugo", "phoenix"}
        if realtime_deps & dep_names_lower:
            detected.append("realtime")

    # api_gateway: if there are many API routes detected from index
    if index_data:
        api_routes = index_data.get("api_routes", [])
        if len(api_routes) >= 5:
            detected.append("api_gateway")

        # api_server: if index has api-type symbols
        sym_counts = index_data.get("symbol_counts", {})
        if sym_counts.get("api", 0) > 0:
            detected.append("api_server")

    return sorted(set(detected))


# ---------------------------------------------------------------------------
# Index-based data extraction
# ---------------------------------------------------------------------------

_BACKEND_EXTS = frozenset({".py", ".go", ".java", ".rb", ".php", ".rs", ".cs", ".kt", ".kts"})
_FRONTEND_EXTS = frozenset({".js", ".ts", ".tsx", ".jsx", ".vue", ".mjs", ".cjs"})

_SERVICE_SDKS = {
    # Firebase
    "firebase": "Firebase",
    "firebase-admin": "Firebase Admin",
    "@firebase/auth": "Firebase Auth",
    "@firebase/firestore": "Firebase Firestore",
    "@firebase/storage": "Firebase Storage",
    "firebase.google.com/go": "Firebase Admin (Go)",
    # Supabase
    "@supabase/supabase-js": "Supabase",
    "supabase": "Supabase",
    # AWS
    "boto3": "AWS SDK",
    "@aws-sdk/client-s3": "AWS S3",
    "@aws-sdk/client-dynamodb": "AWS DynamoDB",
    # GCP
    "google-cloud-storage": "Google Cloud Storage",
    "google-cloud-firestore": "Google Cloud Firestore",
    "cloud.google.com/go/storage": "Google Cloud Storage (Go)",
    # Payments
    "stripe": "Stripe",
    # Email
    "@sendgrid/mail": "SendGrid",
    "sendgrid": "SendGrid",
    # AI
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "@anthropic-ai/sdk": "Anthropic SDK",
    # Database clients
    "redis": "Redis",
    "ioredis": "Redis",
    "mongoose": "MongoDB",
    "pymongo": "MongoDB",
    "@prisma/client": "Prisma",
    "sqlalchemy": "SQLAlchemy",
    "prisma": "Prisma",
    # Messaging
    "twilio": "Twilio",
    "celery": "Celery",
    "bull": "Bull Queue",
    "bullmq": "BullMQ",
    "amqplib": "RabbitMQ",
    "pika": "RabbitMQ",
    # Auth
    "passport": "Passport.js",
    "python-jose": "JWT (python-jose)",
    "pyjwt": "JWT (PyJWT)",
    "jsonwebtoken": "JWT",
    # Monitoring
    "sentry-sdk": "Sentry",
    "@sentry/node": "Sentry",
    "newrelic": "New Relic",
    "datadog": "Datadog",
    # Search
    "elasticsearch": "Elasticsearch",
    "typesense": "Typesense",
    "qdrant-client": "Qdrant",
    # Playwright/testing
    "playwright": "Playwright",
    "@playwright/test": "Playwright",
}


def _detect_services(deps_inventory: dict) -> list[dict]:
    """Match dependency names against known SDK map to detect services."""
    services = []
    seen_names = set()
    for dep in deps_inventory.get("dependencies", []):
        if not isinstance(dep, dict):
            continue
        raw_name = dep.get("name", "")
        ecosystem = dep.get("ecosystem", "")
        # Normalize for pypi: strip extras like [standard], lowercase
        norm = re.sub(r"\[.*?\]", "", raw_name).strip().lower()

        # Try exact match first (preserving @ scoped packages)
        matched_service = _SERVICE_SDKS.get(raw_name)
        if not matched_service:
            matched_service = _SERVICE_SDKS.get(norm)
        if not matched_service:
            # Try with underscores replaced by hyphens (pypi convention)
            matched_service = _SERVICE_SDKS.get(norm.replace("_", "-"))
        if not matched_service and ecosystem == "go":
            # Go modules: match longest prefix first
            # e.g. "firebase.google.com/go/v4" -> "firebase.google.com/go" (not "firebase")
            best_key = ""
            for sdk_key, sdk_name in _SERVICE_SDKS.items():
                if norm.startswith(sdk_key) and len(sdk_key) > len(best_key):
                    best_key = sdk_key
                    matched_service = sdk_name
        if matched_service and matched_service not in seen_names:
            seen_names.add(matched_service)
            services.append({
                "name": matched_service,
                "package": raw_name,
                "ecosystem": ecosystem,
            })
    return services


def _classify_api_symbol(sym: dict) -> str:
    """Classify an API symbol into: api_definition, api_call_internal, api_call_external."""
    file_path = sym.get("path", "")
    ext = os.path.splitext(file_path)[1].lower()
    name = sym.get("name", "")
    meta = sym.get("metadata", {}) or {}

    # Check if URL contains http:// or https:// -> external
    url_text = name + " " + meta.get("path", "") + " " + meta.get("url", "")
    if "http://" in url_text or "https://" in url_text:
        return "api_call_external"

    # Backend file with method+path -> definition
    if ext in _BACKEND_EXTS:
        return "api_definition"

    # Frontend file -> internal call
    if ext in _FRONTEND_EXTS:
        return "api_call_internal"

    # Fallback: if it has handler metadata, treat as definition
    if meta.get("handler"):
        return "api_definition"

    return "api_definition"


def _extract_from_index(project_path: Path) -> dict:
    """Extract data from the flyto-indexer index if available."""
    result = {
        "api_definitions": [],
        "api_calls_internal": [],
        "api_calls_external": [],
        "api_routes": [],  # kept for backward compat (union of all)
        "models": [],
        "symbol_counts": {},
        "entry_points": [],
        "module_graph": [],
    }

    index_dir = project_path / ".flyto-index"
    if not index_dir.exists():
        return result

    # Load index.json
    index = {}
    try:
        import gzip
        gz_path = index_dir / "index.json.gz"
        if gz_path.exists():
            with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                index = json.load(f)
        else:
            json_path = index_dir / "index.json"
            if json_path.exists():
                index = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load index: %s", e)
        return result

    if not index:
        return result

    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})
    reverse_index = index.get("reverse_index", {})

    # --- Symbol counts ---
    type_counter = Counter()
    for sym in symbols.values():
        sym_type = sym.get("type", "unknown")
        type_counter[sym_type] += 1
    result["symbol_counts"] = dict(type_counter.most_common())

    # --- API routes (classified) ---
    def _parse_api_entry(sym_or_route: dict, *, is_route: bool = False) -> dict:
        """Build a normalized API entry dict from a symbol or route record."""
        if is_route:
            return {
                "method": sym_or_route.get("method", "GET"),
                "path": sym_or_route.get("path", sym_or_route.get("url", "")),
                "handler": sym_or_route.get("handler", ""),
                "file": sym_or_route.get("file", sym_or_route.get("defined_in", "")),
            }
        meta = sym_or_route.get("metadata", {}) or {}
        method = meta.get("method", "GET") if meta else "GET"
        if not meta:
            summary = sym_or_route.get("summary", "")
            parts = summary.split(" ", 1)
            if parts[0] in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                method = parts[0]
        route_path = sym_or_route.get("name", "")
        for m_prefix in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            if route_path.startswith(m_prefix + " "):
                route_path = route_path[len(m_prefix) + 1:]
                break
        return {
            "method": method,
            "path": route_path,
            "handler": meta.get("handler", "") if meta else "",
            "file": sym_or_route.get("path", ""),
        }

    _category_keys = {
        "api_definition": "api_definitions",
        "api_call_internal": "api_calls_internal",
        "api_call_external": "api_calls_external",
    }

    for sid, sym in symbols.items():
        if sym.get("type") == "api":
            entry = _parse_api_entry(sym)
            category = _classify_api_symbol(sym)
            result[_category_keys[category]].append(entry)
            result["api_routes"].append(entry)

    # Also check routes/api_endpoints from index
    for route in index.get("routes", []):
        if isinstance(route, dict):
            entry = _parse_api_entry(route, is_route=True)
            # Deduplicate
            if not any(r["path"] == entry["path"] and r["method"] == entry["method"]
                       for r in result["api_routes"]):
                # Routes from index-level are always backend definitions
                result["api_definitions"].append(entry)
                result["api_routes"].append(entry)

    # Sort all lists
    for key in ("api_definitions", "api_calls_internal", "api_calls_external", "api_routes"):
        result[key].sort(key=lambda r: (r["method"], r["path"]))

    # --- Models (classes with fields) ---
    for sid, sym in symbols.items():
        sym_type = sym.get("type", "")
        if sym_type not in ("class", "interface", "type", "struct"):
            continue
        name = sym.get("name", "")
        path = sym.get("path", "")
        line = sym.get("start_line", 0)

        # Count fields from metadata or children
        meta = sym.get("metadata", {}) or {}
        field_count = len(meta.get("fields", []))

        # Try to detect model classes: Pydantic, dataclass, struct, interface with fields
        summary = sym.get("summary", "").lower()
        is_model = (
            field_count > 0
            or "model" in summary or "schema" in summary or "entity" in summary
            or "dataclass" in summary or "struct" in name.lower()
            or sym_type in ("interface", "struct")
        )
        if is_model:
            result["models"].append({
                "name": name,
                "type": sym_type,
                "fields": field_count,
                "file": path,
                "line": line,
            })

    # Sort models by name
    result["models"].sort(key=lambda m: m["name"])

    # --- Entry points ---
    _ENTRY_PATTERNS = re.compile(
        r"(main|index|app|server|cli|__main__|entrypoint|bootstrap)\.(py|ts|js|go|rs|java)$",
        re.IGNORECASE,
    )
    entry_files = set()
    for sym in symbols.values():
        path = sym.get("path", "")
        if path and _ENTRY_PATTERNS.search(path):
            entry_files.add(path)
        # Also detect main functions
        name = sym.get("name", "").lower()
        if name in ("main", "run", "start", "bootstrap", "cli"):
            entry_files.add(path)
    result["entry_points"] = sorted(entry_files)

    # --- Module graph (top 50 strongest connections) ---
    # Build file-to-file import counts from dependencies
    file_connections = Counter()
    for dep_key, dep_info in dependencies.items():
        if not isinstance(dep_info, dict):
            continue
        source_file = dep_info.get("source_path", "")
        target = dep_info.get("target", "")
        if source_file and target:
            # Try to resolve target to a file
            target_file = ""
            for sid, sym in symbols.items():
                if target in sid and sym.get("path"):
                    target_file = sym["path"]
                    break
            if target_file and source_file != target_file:
                pair = (source_file, target_file)
                file_connections[pair] += 1

    # Also build from reverse_index
    for sym_id, callers in reverse_index.items():
        if ":" not in sym_id:
            continue
        parts = sym_id.split(":")
        target_file = parts[1] if len(parts) >= 2 else ""
        if not target_file:
            continue
        for caller_id in callers:
            if ":" not in caller_id:
                continue
            caller_parts = caller_id.split(":")
            source_file = caller_parts[1] if len(caller_parts) >= 2 else ""
            if source_file and source_file != target_file:
                pair = (source_file, target_file)
                file_connections[pair] += 1

    result["module_graph"] = [
        {"source_file": pair[0], "target_file": pair[1], "import_count": count}
        for pair, count in file_connections.most_common(50)
    ]

    return result


# ---------------------------------------------------------------------------
# Git info
# ---------------------------------------------------------------------------

def _git_info(project_path: Path) -> dict:
    """Extract git metadata."""
    result = {"recent_authors": [], "last_commit_date": ""}

    try:
        # Recent authors
        proc = subprocess.run(
            ["git", "-C", str(project_path), "log", "--format=%aN", "-50"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            authors = sorted(set(proc.stdout.strip().split("\n")))
            result["recent_authors"] = authors

        # Last commit date
        proc = subprocess.run(
            ["git", "-C", str(project_path), "log", "-1", "--format=%aI"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            result["last_commit_date"] = proc.stdout.strip()

    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("Git info unavailable: %s", e)

    return result


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _scan_deps(project_path: Path) -> dict:
    """Scan dependencies using the dependency scanner."""
    try:
        try:
            from .dependency_scanner import scan_dependencies
        except ImportError:
            from dependency_scanner import scan_dependencies

        inventory = scan_dependencies(project_path)
        return inventory.to_dict()
    except Exception as e:
        logger.debug("Dependency scan failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Main profile builder
# ---------------------------------------------------------------------------

def build_project_profile(project_path: Path, compact: bool = False) -> dict:
    """
    Build a complete project profile by aggregating all available data sources.

    Args:
        project_path: Absolute path to the project root.
        compact: If True, return a summary-only profile with reduced detail.

    Returns:
        A dict containing the full project profile.
    """
    project_path = project_path.resolve()
    project_name = project_path.name
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Filesystem analysis (always available)
    fs = _scan_filesystem(project_path)

    # 2. Index-based data (may be empty)
    idx = _extract_from_index(project_path)

    # 3. Dependencies
    deps = _scan_deps(project_path)

    # 4. Git info
    git = _git_info(project_path)

    # 5. Pattern detection
    dep_names = set()
    for d in deps.get("dependencies", []):
        if isinstance(d, dict):
            dep_names.add(d.get("name", ""))
    patterns = _detect_patterns(fs["_all_files"], dep_names, index_data=idx)

    # 5b. Secret scan
    secrets_data = {}
    try:
        try:
            from .secret_scanner import scan_secrets
        except ImportError:
            from secret_scanner import scan_secrets
        secret_result = scan_secrets(project_path)
        secrets_data = {
            "total_files_scanned": secret_result.total_files_scanned,
            "total_findings": secret_result.total_findings,
            "critical": secret_result.critical,
            "high": secret_result.high,
            "medium": secret_result.medium,
        }
    except Exception as e:
        logger.debug("Secret scan failed: %s", e)

    # 5c. License scan
    license_data = {}
    try:
        try:
            from .license_scanner import scan_licenses
        except ImportError:
            from license_scanner import scan_licenses
        license_result = scan_licenses(project_path)
        license_data = {
            "project_license": license_result.project_license,
            "project_license_file": license_result.project_license_file,
            "dependency_licenses": license_result.dependency_licenses,
            "copyleft_warning": license_result.copyleft_warning,
            "dependencies_without_license_count": len(license_result.dependencies_without_license),
        }
    except Exception as e:
        logger.debug("License scan failed: %s", e)

    # 5d. Documentation coverage
    documentation_data = {}
    try:
        try:
            from .doc_scanner import scan_documentation
        except ImportError:
            from doc_scanner import scan_documentation
        doc_result = scan_documentation(project_path)
        documentation_data = {
            "overall_score": doc_result.overall_score,
            "readme_score": doc_result.readme_score,
            "readme_sections": doc_result.readme_sections,
            "api_doc_coverage": doc_result.api_doc_coverage,
            "module_doc_coverage": doc_result.module_doc_coverage,
            "inline_doc_coverage": doc_result.inline_doc_coverage,
            "has_env_example": doc_result.has_env_example,
            "has_changelog": doc_result.has_changelog,
            "has_contributing": doc_result.has_contributing,
            "suggestions": doc_result.suggestions,
        }
    except Exception as e:
        logger.debug("Documentation scan failed: %s", e)

    # 6. Services detection
    services = _detect_services(deps)

    # Build profile
    profile = {
        "name": project_name,
        "path": str(project_path),
        "generated_at": now,

        # Structure
        "file_count": fs["file_count"],
        "languages": fs["languages"],

        # APIs (classified)
        "api_definitions": idx["api_definitions"],
        "api_calls_internal": idx["api_calls_internal"],
        "api_calls_external": idx["api_calls_external"],
        "api_routes": idx["api_routes"],  # backward compat: union of all

        # Services
        "services": services,

        # Models
        "models": idx["models"],

        # Dependencies
        "dependencies": deps,

        # Symbols
        "symbol_counts": idx["symbol_counts"],
        "entry_points": idx["entry_points"],

        # Connections
        "module_graph": idx["module_graph"],

        # Infrastructure
        "has_docker": fs["has_docker"],
        "has_ci": fs["has_ci"],
        "has_tests": fs["has_tests"],
        "has_docs": fs["has_docs"],
        "config_files": fs["config_files"],

        # Git
        "recent_authors": git["recent_authors"],
        "last_commit_date": git["last_commit_date"],

        # Patterns
        "patterns": patterns,

        # Analysis
        "secrets": secrets_data,
        "license": license_data,
        "documentation": documentation_data,
    }

    if not compact:
        profile["folder_structure"] = fs["folder_structure"]

    return profile


# ---------------------------------------------------------------------------
# Human-readable formatter
# ---------------------------------------------------------------------------

def format_profile(profile: dict) -> str:
    """Format a project profile as human-readable text."""
    lines = []
    lines.append(f"Project Profile: {profile['name']}")
    lines.append(f"Generated: {profile['generated_at']}")
    lines.append("")

    # Structure
    langs = profile.get("languages", {})
    lang_str = ", ".join(f"{k} ({v})" for k, v in
                         sorted(langs.items(), key=lambda x: -x[1])[:8])
    lines.append("Structure")
    struct_parts = [f"Files: {profile['file_count']}"]
    folder_structure = profile.get("folder_structure")
    if folder_structure:
        struct_parts.append(f"Folders: {len(folder_structure)}")
    struct_parts.append(f"Languages: {lang_str}")
    lines.append(f"  {' | '.join(struct_parts)}")
    lines.append("")

    # Services & APIs
    api_defs = profile.get("api_definitions", [])
    api_internal = profile.get("api_calls_internal", [])
    api_external = profile.get("api_calls_external", [])
    services = profile.get("services", [])
    has_api_section = api_defs or api_internal or api_external or services
    if has_api_section:
        lines.append("Services & APIs")
        if api_defs:
            lines.append(f"  Backend routes: {len(api_defs)} defined")
            for route in api_defs[:15]:
                method = route.get("method", "GET")
                path = route.get("path", "")
                lines.append(f"    {method:6s} {path}")
            if len(api_defs) > 15:
                lines.append(f"    ... and {len(api_defs) - 15} more")
            lines.append("")
        if services:
            svc_names = ", ".join(s["name"] for s in services)
            lines.append(f"  Services: {svc_names}")
            lines.append("")
        if api_internal:
            lines.append(f"  Frontend API calls: {len(api_internal)} internal")
        if api_external:
            lines.append(f"  External API calls: {len(api_external)}")
        if api_internal or api_external:
            lines.append("")

    # Models
    models = profile.get("models", [])
    if models:
        lines.append(f"Models ({len(models)})")
        for m in models[:15]:
            field_str = f"{m['fields']} fields" if m.get("fields") else "no fields extracted"
            lines.append(f"  {m['name']} ({field_str}) -- {m['file']}:{m['line']}")
        if len(models) > 15:
            lines.append(f"  ... and {len(models) - 15} more")
        lines.append("")

    # Symbols
    sym_counts = profile.get("symbol_counts", {})
    if sym_counts:
        lines.append("Symbols")
        def _plural(word, count):
            if count == 1:
                return word
            if word.endswith("s"):
                return word + "es"
            return word + "s"
        sym_parts = [f"{v} {_plural(k, v)}" for k, v in
                     sorted(sym_counts.items(), key=lambda x: -x[1])]
        lines.append(f"  {', '.join(sym_parts)}")
        lines.append("")

    # Dependencies
    deps = profile.get("dependencies", {})
    if deps and deps.get("total_count", 0) > 0:
        eco_str = ", ".join(deps.get("ecosystems", []))
        lines.append("Dependencies")
        lines.append(
            f"  {deps['total_count']} packages "
            f"({deps.get('production_count', 0)} production, "
            f"{deps.get('dev_count', 0)} dev"
            + (f", {deps.get('indirect_count', 0)} indirect" if deps.get("indirect_count") else "")
            + f") across {len(deps.get('ecosystems', []))} ecosystem{'s' if len(deps.get('ecosystems', [])) != 1 else ''} [{eco_str}]"
        )
        lines.append("")

    # Connections
    module_graph = profile.get("module_graph", [])
    if module_graph:
        lines.append(f"Connections (top {min(10, len(module_graph))} module pairs)")
        for edge in module_graph[:10]:
            lines.append(
                f"  {edge['source_file']} -> {edge['target_file']} ({edge['import_count']} refs)"
            )
        lines.append("")

    # Entry points
    entry_points = profile.get("entry_points", [])
    if entry_points:
        lines.append(f"Entry Points ({len(entry_points)})")
        for ep in entry_points[:10]:
            lines.append(f"  {ep}")
        if len(entry_points) > 10:
            lines.append(f"  ... and {len(entry_points) - 10} more")
        lines.append("")

    # Patterns
    patterns = profile.get("patterns", [])
    if patterns:
        lines.append("Patterns Detected")
        lines.append(f"  {', '.join(patterns)}")
        lines.append("")

    # Infrastructure
    infra_parts = []
    for key, label in [("has_docker", "Docker"), ("has_ci", "CI"),
                       ("has_tests", "Tests"), ("has_docs", "Docs")]:
        infra_parts.append(f"{label}: {'yes' if profile.get(key) else 'no'}")
    lines.append("Infrastructure")
    lines.append(f"  {' | '.join(infra_parts)}")

    config_files = profile.get("config_files", [])
    if config_files:
        lines.append(f"  Config: {', '.join(config_files[:10])}")
        if len(config_files) > 10:
            lines.append(f"    ... and {len(config_files) - 10} more")
    lines.append("")

    # Git
    authors = profile.get("recent_authors", [])
    last_commit = profile.get("last_commit_date", "")
    if authors or last_commit:
        lines.append("Git")
        if authors:
            lines.append(f"  Authors: {', '.join(authors)}")
        if last_commit:
            # Truncate to date only
            date_only = last_commit[:10] if len(last_commit) >= 10 else last_commit
            lines.append(f"  Last commit: {date_only}")

    return "\n".join(lines)
