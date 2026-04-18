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


def _classify_project_type(
    languages: dict,
    api_definitions: list,
    components: int,
    dep_names: set,
    patterns: list,
    entry_points: list,
    all_files: list,
) -> dict:
    """Classify project as frontend, backend, fullstack, library, cli, mobile, static, unknown.

    Returns {"type": "...", "sub_type": "..."}.
    """
    dep_names_lower = {d.lower().replace("-", "_").replace("/", "_") for d in dep_names if d}

    # Backend signals
    # Server entry points: files named server/worker/app at top level (not in tests/examples/src/)
    _server_basenames = {"server.py", "server.ts", "server.js", "server.go",
                         "worker.py", "worker.ts", "worker.js", "worker.go",
                         "app.py", "app.ts", "app.js", "app.go",
                         "main.go", "main.py"}
    has_server_entry = any(
        os.path.basename(ep).lower() in _server_basenames
        and not any(ep.lower().startswith(skip) for skip in ("test", "example", "benchmark"))
        for ep in entry_points
    )
    # Also check for main_*.py entry points (flyto-cloud pattern)
    has_server_entry = has_server_entry or any(
        os.path.basename(ep).lower().startswith("main_") for ep in entry_points
    )
    # Web framework deps are a strong backend signal
    web_framework_deps = {"fastapi", "flask", "django", "express", "koa", "hono", "gin",
                          "echo", "fiber", "actix_web", "rocket", "spring_boot",
                          "uvicorn", "gunicorn", "nest", "nestjs"}
    has_web_framework = bool(web_framework_deps & dep_names_lower)

    # cmd/server/ pattern (Go convention)
    has_cmd_server = any("cmd/server" in ep.lower() or "cmd/worker" in ep.lower() for ep in entry_points)

    has_backend = (
        (len(api_definitions) > 0 and (has_server_entry or has_web_framework))
        or "api_server" in patterns
        or (has_server_entry and has_web_framework)
        or has_cmd_server  # Go-style cmd/server/ is a definitive backend signal
    )

    frontend_deps = {"react", "vue", "angular", "svelte", "next", "nuxt",
                     "react_dom", "vue_router", "svelte_kit", "solid_js",
                     "@angular_core", "angular_core"}
    has_frontend_deps = bool(frontend_deps & dep_names_lower)
    # Check if frontend deps are from root manifest or a subdirectory.
    # Subdirectory frontend (admin UI, console-ui) shouldn't classify the whole project.
    backend_langs = languages.get("Python", 0) + languages.get("Go", 0) + languages.get("Java", 0) + languages.get("Rust", 0)
    frontend_langs = languages.get("TypeScript", 0) + languages.get("JavaScript", 0) + languages.get("Vue", 0)
    frontend_is_dominant = frontend_langs > backend_langs
    # If backend languages dominate, frontend deps in a subdirectory don't count
    if has_frontend_deps and backend_langs > frontend_langs * 3:
        has_frontend_deps = False
    has_frontend = has_frontend_deps or (components > 10 and frontend_is_dominant)

    ep_names_lower = [ep.lower() for ep in entry_points]
    has_cli_entry = any("cli" in ep or "__main__" in ep for ep in ep_names_lower)

    # Check for publishable library markers
    publishable_files = {"setup.py", "pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
    has_publishable = any(f in all_files for f in publishable_files)

    # A library is a publishable package whose primary purpose is providing code to others.
    # Key signals that override "library": Docker deployment, cmd/server/ structure, web framework
    # as primary dep (not just optional/dev), or main server entry point at project root.
    has_deployment = "containerization" in patterns or any(
        f.lower().startswith("dockerfile") or f.lower() == "docker-compose.yml"
        for f in all_files
    )
    is_library = (
        not has_frontend
        and has_publishable
        and not has_cmd_server
        and not (has_deployment and has_server_entry)
    )

    # Pure CLI: has cli entry but NOT a library or backend
    has_cli = has_cli_entry and not has_frontend and not has_backend and not is_library

    is_mobile = "Dart" in languages or "Swift" in languages or "Kotlin" in languages
    is_static = not has_backend and not has_frontend and "HTML" in languages

    # Primary classification
    if has_backend and has_frontend:
        project_type = "fullstack"
    elif has_backend and not is_library:
        project_type = "backend"
    elif has_frontend:
        project_type = "frontend"
    elif is_mobile:
        project_type = "mobile"
    elif is_library:
        project_type = "library"
    elif has_cli:
        project_type = "cli"
    elif is_static:
        project_type = "static"
    else:
        project_type = "unknown"

    # Sub-classification
    sub_type = ""
    if project_type == "backend":
        if "api_server" in patterns or "api_gateway" in patterns or has_cmd_server:
            sub_type = "api_server"
        elif any("worker" in ep for ep in ep_names_lower):
            sub_type = "worker"
        else:
            sub_type = "microservice"
    elif project_type == "frontend":
        ssr_deps = {"next", "nuxt", "svelte_kit", "remix", "gatsby", "astro"}
        component_lib_signals = (
            not any(f for f in all_files if f.endswith(("index.html", "app.vue", "App.vue", "App.tsx")))
            and components > 5
        )
        if ssr_deps & dep_names_lower:
            sub_type = "ssr"
        elif component_lib_signals:
            sub_type = "component_library"
        else:
            sub_type = "spa"
    elif project_type == "library":
        # SDK: has client/interface abstractions, meant to be consumed programmatically
        has_interfaces = any("interface" in f.lower() or "client" in f.lower()
                             for f in all_files if not f.startswith(".") and not f.startswith("test"))
        has_sdk_structure = any("sdk" in f.lower() for f in all_files) or has_interfaces
        if has_sdk_structure or len(api_definitions) > 0:
            sub_type = "sdk"
        else:
            # Framework: has middleware, plugin, provider patterns
            framework_signals = {"middleware", "plugin", "hook", "provider", "adapter"}
            file_basenames = {os.path.basename(f).lower().split(".")[0] for f in all_files}
            if framework_signals & file_basenames:
                sub_type = "framework"
            else:
                sub_type = "utility"

    return {"type": project_type, "sub_type": sub_type}


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
        "module_graph": [],          # top 10 for display
        "module_graph_full": [],     # ALL connections (JSON output)
        "module_graph_summary": {},  # summary stats
        "complexity_summary": {},    # complexity stats
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

    # --- Module graph (full + summary) ---
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

    # Full graph (all connections) for JSON output
    all_connections = [
        {"source_file": pair[0], "target_file": pair[1], "import_count": count}
        for pair, count in file_connections.most_common()
    ]
    result["module_graph_full"] = all_connections
    # Top 10 for human-readable display
    result["module_graph"] = all_connections[:10]

    # Module graph summary
    if file_connections:
        # Count refs per file (both as source and target)
        file_ref_counts = Counter()
        for (src, tgt), count in file_connections.items():
            file_ref_counts[src] += count
            file_ref_counts[tgt] += count

        # Find all indexed files
        all_indexed_files = set()
        for sym in symbols.values():
            p = sym.get("path", "")
            if p:
                all_indexed_files.add(p)

        # Connected files (appear in at least one connection)
        connected_files = set()
        for src, tgt in file_connections:
            connected_files.add(src)
            connected_files.add(tgt)

        # Orphan files: indexed files that import nothing and are imported by nothing
        orphan_files = sorted(all_indexed_files - connected_files)

        most_connected = file_ref_counts.most_common(1)[0][0] if file_ref_counts else ""
        total_connections = len(file_connections)
        avg_refs = sum(file_ref_counts.values()) / max(len(file_ref_counts), 1)

        result["module_graph_summary"] = {
            "total_connections": total_connections,
            "avg_refs_per_module": round(avg_refs, 1),
            "most_connected_file": most_connected,
            "orphan_files": orphan_files,
            "orphan_count": len(orphan_files),
        }
    else:
        result["module_graph_summary"] = {
            "total_connections": 0,
            "avg_refs_per_module": 0,
            "most_connected_file": "",
            "orphan_files": [],
            "orphan_count": 0,
        }

    # --- Complexity summary ---
    result["complexity_summary"] = _compute_complexity_summary(symbols, index_dir)

    # --- Health dimensions ---
    result["health_dimensions"] = _compute_health_dimensions(
        symbols, reverse_index, index_dir, result["complexity_summary"]
    )

    return result


def _load_content_file(index_dir: Path) -> dict:
    """Load content.jsonl from an index directory."""
    content_map = {}
    content_file = index_dir / "content.jsonl"
    if content_file.exists():
        try:
            with open(content_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        content_map[record["id"]] = record["content"]
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.debug("Failed to load content from %s: %s", content_file, e)
    return content_map


def _compute_complexity_summary(symbols: dict, index_dir: Path) -> dict:
    """Compute complexity summary from indexed symbols.

    Uses the same scoring formula as quality.find_complex_functions:
    - lines > threshold: (lines - threshold) // 10
    - nesting > 3: (depth - 3) * 5
    - params > 5: (params - 5) * 2
    - branches > 10: (branches - 10)
    Complex threshold: score >= 5
    """
    try:
        try:
            from .analyzer.complexity import _line_threshold_for_file, _is_test_file
        except ImportError:
            from analyzer.complexity import _line_threshold_for_file, _is_test_file
    except ImportError:
        # Fallback if analyzer not available
        def _line_threshold_for_file(p):
            return 100 if any(p.endswith(e) for e in (".vue", ".tsx", ".jsx")) else 80
        def _is_test_file(p):
            lower = p.lower()
            return any(pat in lower for pat in ("test_", "_test.", ".test.", ".spec.", "/test/", "/tests/"))

    # Load content store for symbol bodies
    content_map = _load_content_file(index_dir) if index_dir.exists() else {}

    total_functions = 0
    complex_functions = 0
    all_scores = []
    most_complex = []

    for sym_id, sym in symbols.items():
        sym_type = sym.get("type", "")
        if sym_type not in ("function", "method"):
            continue

        path = sym.get("path", "")
        if _is_test_file(path):
            continue

        total_functions += 1

        # Get content: inline > content.jsonl
        content = ""
        if isinstance(sym.get("content"), str) and sym["content"]:
            content = sym["content"]
        else:
            content = content_map.get(sym_id, "")

        if not content:
            all_scores.append(0)
            continue

        lines_list = content.split("\n")
        line_count = len(lines_list)
        params_list = sym.get("params", [])
        param_count = len(params_list) if isinstance(params_list, list) else 0

        is_python = path.endswith(".py")
        indent_unit = 4 if is_python else 2

        max_depth = 0
        branches = 0
        base_indent = 0
        for ln in lines_list:
            stripped = ln.strip()
            if stripped:
                base_indent = len(ln) - len(ln.lstrip())
                break

        for ln in lines_list:
            stripped = ln.strip()
            if not stripped:
                continue
            indent = len(ln) - len(ln.lstrip())
            depth = max(0, (indent - base_indent) // indent_unit)
            max_depth = max(max_depth, depth)
            if is_python:
                branch_kws = ("if ", "elif ", "for ", "while ", "try:", "except ", "with ")
            else:
                branch_kws = ("if ", "if(", "else if ", "for ", "for(", "while ", "while(", "switch ", "switch(", "try ", "try{", "catch ", "catch(")
            for kw in branch_kws:
                if stripped.startswith(kw):
                    branches += 1
                    break

        score = 0
        line_threshold = _line_threshold_for_file(path)
        if line_count > line_threshold:
            score += (line_count - line_threshold) // 10
        if max_depth > 3:
            score += (max_depth - 3) * 5
        if param_count > 5:
            score += (param_count - 5) * 2
        if branches > 10:
            score += (branches - 10)

        all_scores.append(score)

        if score >= 5:
            complex_functions += 1
            most_complex.append({
                "name": sym.get("name", ""),
                "path": path,
                "score": score,
                "line": sym.get("start_line", sym.get("line", 0)),
            })

    most_complex.sort(key=lambda x: x["score"], reverse=True)
    avg_complexity = round(sum(all_scores) / max(len(all_scores), 1), 2)

    return {
        "total_functions": total_functions,
        "complex_functions": complex_functions,
        "avg_complexity": avg_complexity,
        "most_complex": most_complex[:5],
    }


def _compute_health_dimensions(
    symbols: dict,
    reverse_index: dict,
    index_dir: "Path",
    complexity_summary: dict,
) -> dict:
    """Compute health score dimensions from index data.

    Dimensions (each 0-25, total 0-100):
    - security: pattern-based security scan
    - complexity: ratio of complex functions
    - dead_code: ratio of unreferenced symbols
    - coverage: test coverage from .coverage file (optional)

    Returns dict with per-dimension scores and overall grade.
    """
    try:
        try:
            from .analyzer.complexity import _is_test_file
        except ImportError:
            from analyzer.complexity import _is_test_file
    except ImportError:
        def _is_test_file(p):
            lower = p.lower()
            return any(pat in lower for pat in ("test_", "_test.", ".test.", ".spec.", "/test/", "/tests/"))

    total_symbols = len(symbols)
    if total_symbols == 0:
        return {
            "security": {"score": 25, "max": 25, "status": "PASS", "finding_count": 0},
            "complexity": {"score": 25, "max": 25, "status": "PASS", "complex_count": 0},
            "dead_code": {"score": 25, "max": 25, "status": "PASS", "dead_count": 0},
            "coverage": {"score": 0, "max": 25, "status": "FAIL", "coverage_pct": 0},
            "overall": {"score": 75, "max": 100, "grade": "C"},
        }

    # --- Security (pattern scan from project root) ---
    security_score = 25
    finding_count = 0
    try:
        try:
            from .analyzer.security import SecurityScanner
        except ImportError:
            from analyzer.security import SecurityScanner

        # Derive project root from index_dir (parent of .flyto-index)
        project_root = index_dir.parent if index_dir.exists() else None
        if project_root and project_root.exists():
            scanner = SecurityScanner(project_root)
            report = scanner.analyze()
            finding_count = len(report.issues)
            # Penalty: -2 per critical, -1 per high, -0.5 per medium
            penalty = 0
            for issue in report.issues:
                sev = issue.severity
                if sev == "critical":
                    penalty += 2
                elif sev == "high":
                    penalty += 1
                elif sev == "medium":
                    penalty += 0.5
            security_score = max(0, 25 - int(penalty))
    except Exception:
        pass  # Security scan optional

    security_status = "PASS" if security_score >= 20 else ("WARN" if security_score >= 10 else "FAIL")

    # --- Complexity ---
    func_count = complexity_summary.get("total_functions", 0)
    complex_count = complexity_summary.get("complex_functions", 0)
    if func_count > 0:
        complexity_ratio = complex_count / func_count
        complexity_score = max(0, 25 - int(complexity_ratio * 100))
    else:
        complexity_score = 25

    complexity_status = "PASS" if complexity_score >= 20 else ("WARN" if complexity_score >= 10 else "FAIL")

    # --- Dead code ---
    # Count symbols that have no references in reverse_index and are not test files
    dead_count = 0
    non_test_symbols = {
        k: v for k, v in symbols.items()
        if not _is_test_file(v.get("path", ""))
        and v.get("type", "") in ("function", "method", "class", "component", "composable")
    }
    for sym_id, sym in non_test_symbols.items():
        ref_count = sym.get("ref_count", sym.get("reference_count", 0))
        if ref_count == 0:
            # Check reverse_index directly
            callers = reverse_index.get(sym_id, [])
            if not callers:
                # Skip private/internal symbols (starting with _)
                name = sym.get("name", "")
                if not name.startswith("_"):
                    dead_count += 1

    dead_ratio = dead_count / max(len(non_test_symbols), 1)
    dead_score = max(0, 25 - int(dead_ratio * 100))
    dead_status = "PASS" if dead_score >= 20 else ("WARN" if dead_score >= 10 else "FAIL")

    # --- Coverage ---
    coverage_pct = 0
    coverage_score = 0
    try:
        # Look for .coverage or coverage.xml in project root
        project_root = index_dir.parent if index_dir.exists() else None
        if project_root:
            coverage_file = project_root / ".coverage"
            if coverage_file.exists():
                # Try to parse coverage percentage from coverage report
                import subprocess as _sp
                try:
                    proc = _sp.run(
                        ["python", "-m", "coverage", "report", "--format=total"],
                        capture_output=True, text=True, timeout=30,
                        cwd=str(project_root),
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        try:
                            coverage_pct = int(float(proc.stdout.strip()))
                        except ValueError:
                            pass
                except (FileNotFoundError, _sp.TimeoutExpired):
                    pass
            if coverage_pct > 0:
                coverage_score = min(25, round(coverage_pct / 4))  # 100% -> 25
    except Exception:
        pass

    coverage_status = "PASS" if coverage_score >= 20 else ("WARN" if coverage_score >= 10 else "FAIL")

    # --- Overall ---
    overall_score = security_score + complexity_score + dead_score + coverage_score
    if overall_score >= 90:
        grade = "A"
    elif overall_score >= 80:
        grade = "B"
    elif overall_score >= 70:
        grade = "C"
    elif overall_score >= 60:
        grade = "D"
    else:
        grade = "F"

    return {
        "security": {"score": security_score, "max": 25, "status": security_status, "finding_count": finding_count},
        "complexity": {"score": complexity_score, "max": 25, "status": complexity_status, "complex_count": complex_count},
        "dead_code": {"score": dead_score, "max": 25, "status": dead_status, "dead_count": dead_count},
        "coverage": {"score": coverage_score, "max": 25, "status": coverage_status, "coverage_pct": coverage_pct},
        "overall": {"score": overall_score, "max": 100, "grade": grade},
    }


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

    # 5e. Taint / data flow analysis
    taint_data = {}
    try:
        try:
            from .analyzer.taint import TaintAnalyzer
        except ImportError:
            from analyzer.taint import TaintAnalyzer

        # Load raw index for dependency graph (cross-function tracking)
        raw_index = {}
        index_dir = project_path / ".flyto-index"
        if index_dir.exists():
            try:
                import gzip as _gzip
                gz_path = index_dir / "index.json.gz"
                if gz_path.exists():
                    with _gzip.open(gz_path, "rt", encoding="utf-8") as _f:
                        raw_index = json.load(_f)
                else:
                    json_path = index_dir / "index.json"
                    if json_path.exists():
                        raw_index = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        analyzer = TaintAnalyzer(project_path, index=raw_index)
        taint_result = analyzer.analyze_full()
        unsanitized = [f for f in taint_result.taint_flows if not f.sanitized]
        taint_data = {
            "total_sources": taint_result.total_sources,
            "total_sinks": taint_result.total_sinks,
            "unsanitized_flows": len(unsanitized),
            "sanitized_flows": taint_result.sanitized_flows,
            "high_risk_count": taint_result.high_risk_count,
        }
    except Exception as e:
        logger.debug("Taint analysis failed: %s", e)

    # 5f. Framework detection
    frameworks_data = []
    try:
        try:
            from .framework_detector import detect_frameworks
        except ImportError:
            from framework_detector import detect_frameworks
        frameworks = detect_frameworks(project_path)
        frameworks_data = [fw.to_dict() for fw in frameworks]
    except Exception as e:
        logger.debug("Framework detection failed: %s", e)

    # 6. Services detection
    services = _detect_services(deps)

    # 7. Project type classification
    component_count = idx["symbol_counts"].get("component", 0)
    project_type_info = _classify_project_type(
        languages=fs["languages"],
        api_definitions=idx["api_definitions"],
        components=component_count,
        dep_names=dep_names,
        patterns=patterns,
        entry_points=idx["entry_points"],
        all_files=fs["_all_files"],
    )

    # Build profile
    profile = {
        "name": project_name,
        "path": str(project_path),
        "generated_at": now,

        # Classification
        "project_type": project_type_info["type"],
        "project_sub_type": project_type_info["sub_type"],

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
        "module_graph_full": idx["module_graph_full"],
        "module_graph_summary": idx["module_graph_summary"],

        # Complexity
        "complexity_summary": idx["complexity_summary"],

        # Health
        "health_dimensions": idx.get("health_dimensions", {}),

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

        # Frameworks
        "frameworks": frameworks_data,

        # Analysis
        "secrets": secrets_data,
        "taint_flows": taint_data,
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
    # Header with project type
    project_type = profile.get("project_type", "")
    project_sub_type = profile.get("project_sub_type", "")
    type_label = project_type
    if project_sub_type:
        type_label = f"{project_type} ({project_sub_type})"
    header = f"Project Profile: {profile['name']}"
    if type_label:
        header += f" [{type_label}]"
    lines.append(header)
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
        graph_summary = profile.get("module_graph_summary", {})
        if graph_summary:
            total_conn = graph_summary.get("total_connections", 0)
            avg_refs = graph_summary.get("avg_refs_per_module", 0)
            orphan_count = graph_summary.get("orphan_count", 0)
            most_connected = graph_summary.get("most_connected_file", "")
            lines.append(f"  --- {total_conn} total connections, avg {avg_refs} refs/module")
            if most_connected:
                lines.append(f"  Most connected: {most_connected}")
            if orphan_count > 0:
                lines.append(f"  Orphan files (no imports/importers): {orphan_count}")
        lines.append("")

    # Complexity
    complexity = profile.get("complexity_summary", {})
    if complexity and complexity.get("total_functions", 0) > 0:
        total_fn = complexity["total_functions"]
        complex_fn = complexity["complex_functions"]
        avg_cx = complexity["avg_complexity"]
        lines.append(f"Complexity")
        lines.append(f"  {total_fn} functions analyzed, {complex_fn} complex (score >= 5), avg score {avg_cx}")
        most_complex = complexity.get("most_complex", [])
        if most_complex:
            lines.append(f"  Top complex functions:")
            for fn in most_complex[:5]:
                lines.append(f"    {fn['name']} (score={fn['score']}) -- {fn['path']}:{fn.get('line', 0)}")
        lines.append("")

    # Health Score
    health = profile.get("health_dimensions", {})
    if health and health.get("overall"):
        overall = health["overall"]
        lines.append(f"Health Score: {overall['grade']} ({overall['score']}/{overall['max']})")
        for dim_name in ("security", "complexity", "dead_code", "coverage"):
            dim = health.get(dim_name, {})
            if dim:
                label = dim_name.replace("_", " ").title()
                detail = ""
                if dim_name == "security" and dim.get("finding_count", 0) > 0:
                    detail = f"  ({dim['finding_count']} findings)"
                elif dim_name == "complexity" and dim.get("complex_count", 0) > 0:
                    detail = f"  ({dim['complex_count']} complex functions)"
                elif dim_name == "dead_code" and dim.get("dead_count", 0) > 0:
                    detail = f"  ({dim['dead_count']} unreferenced symbols)"
                elif dim_name == "coverage":
                    if dim.get("coverage_pct", 0) > 0:
                        detail = f"  ({dim['coverage_pct']}% covered)"
                    else:
                        detail = "  (no coverage data)"
                lines.append(f"  {label:12s} {dim['score']:2d}/{dim['max']} {dim['status']}{detail}")
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

    # Frameworks
    frameworks = profile.get("frameworks", [])
    if frameworks:
        lines.append(f"Frameworks ({len(frameworks)})")
        for fw in frameworks:
            version_str = f" v{fw['version']}" if fw.get("version") else ""
            lines.append(f"  {fw['name']}{version_str} [{fw['type']}]")
            if fw.get("conventions"):
                conv_parts = [f"{k}={v}" for k, v in fw["conventions"].items()]
                lines.append(f"    Conventions: {', '.join(conv_parts)}")
            if fw.get("entry_points"):
                ep_list = fw["entry_points"][:3]
                lines.append(f"    Entry points: {', '.join(ep_list)}")
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
