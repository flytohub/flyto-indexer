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
    # Go module cache and vendor
    "pkg", "testdata",
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

    # Go/Rust/Java projects: if dominant language + go.mod/Cargo.toml exists → backend
    go_dominant = languages.get("Go", 0) > max(frontend_langs, 1)
    go_mod_exists = "go.mod" in all_files
    has_go_cmd = any("cmd/" in f for f in all_files if f.endswith(".go"))
    if go_dominant and (go_mod_exists or has_go_cmd) and not has_frontend:
        has_backend = True

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
    # GitHub API
    "octokit": "GitHub API",
    "@octokit/core": "GitHub API",
    "@octokit/rest": "GitHub API",
    "@octokit/graphql": "GitHub API",
    # GitLab API
    "@gitbeaker/core": "GitLab API",
    "@gitbeaker/rest": "GitLab API",
    "@gitbeaker/node": "GitLab API",
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


_HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")

_ENTRY_FILE_PATTERN = re.compile(
    r"(main|index|app|server|cli|__main__|entrypoint|bootstrap)\.(py|ts|js|go|rs|java)$",
    re.IGNORECASE,
)

_ENTRY_NAMES = {"main", "run", "start", "bootstrap", "cli"}

_API_CATEGORY_KEYS = {
    "api_definition": "api_definitions",
    "api_call_internal": "api_calls_internal",
    "api_call_external": "api_calls_external",
}


def _empty_extract_result() -> dict:
    return {
        "api_definitions": [],
        "api_calls_internal": [],
        "api_calls_external": [],
        "api_routes": [],
        "models": [],
        "symbol_counts": {},
        "entry_points": [],
        "module_graph": [],
        "module_graph_full": [],
        "module_graph_summary": {},
        "complexity_summary": {},
    }


def _load_index_file(index_dir: Path) -> dict:
    """Load index.json (or .gz). Returns {} on missing or corrupt."""
    try:
        import gzip
        gz_path = index_dir / "index.json.gz"
        if gz_path.exists():
            with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                return json.load(f)
        json_path = index_dir / "index.json"
        if json_path.exists():
            return json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load index: %s", e)
    return {}


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
        first = summary.split(" ", 1)[0]
        if first in _HTTP_METHODS:
            method = first
    route_path = sym_or_route.get("name", "")
    for m_prefix in _HTTP_METHODS:
        if route_path.startswith(m_prefix + " "):
            route_path = route_path[len(m_prefix) + 1:]
            break
    return {
        "method": method,
        "path": route_path,
        "handler": meta.get("handler", "") if meta else "",
        "file": sym_or_route.get("path", ""),
    }


def _collect_api_from_symbols(symbols: dict, result: dict) -> None:
    for _sid, sym in symbols.items():
        if sym.get("type") != "api":
            continue
        entry = _parse_api_entry(sym)
        category = _classify_api_symbol(sym)
        result[_API_CATEGORY_KEYS[category]].append(entry)
        result["api_routes"].append(entry)


def _collect_api_from_dep_edges(index: dict, result: dict) -> None:
    raw_deps = index.get("dependencies", {})
    dep_values = raw_deps.values() if isinstance(raw_deps, dict) else raw_deps
    for dep_edge in dep_values:
        if not isinstance(dep_edge, dict):
            continue
        dep_type = dep_edge.get("type", dep_edge.get("dep_type", ""))
        if dep_type not in ("api_calls", "API_CALLS"):
            continue
        meta = dep_edge.get("metadata", {}) or {}
        url = meta.get("url", dep_edge.get("target", ""))
        method = meta.get("method", "GET")
        source = dep_edge.get("source", "")
        parts = source.split(":")
        source_file = parts[1] if len(parts) >= 2 else ""
        entry = {"method": method, "path": url, "handler": "", "file": source_file}
        if url.startswith("http://") or url.startswith("https://") or url.startswith("*/"):
            if entry not in result["api_calls_external"]:
                result["api_calls_external"].append(entry)
        elif url:
            if entry not in result["api_calls_internal"]:
                result["api_calls_internal"].append(entry)


def _collect_api_from_routes(index: dict, result: dict) -> None:
    for route in index.get("routes", []):
        if not isinstance(route, dict):
            continue
        entry = _parse_api_entry(route, is_route=True)
        if any(r["path"] == entry["path"] and r["method"] == entry["method"]
               for r in result["api_routes"]):
            continue
        result["api_definitions"].append(entry)
        result["api_routes"].append(entry)


def _is_model_symbol(sym: dict, sym_type: str, field_count: int) -> bool:
    name = sym.get("name", "")
    summary = sym.get("summary", "").lower()
    return (
        field_count > 0
        or "model" in summary or "schema" in summary or "entity" in summary
        or "dataclass" in summary or "struct" in name.lower()
        or sym_type in ("interface", "struct")
    )


def _collect_models(symbols: dict) -> list[dict]:
    models = []
    for _sid, sym in symbols.items():
        sym_type = sym.get("type", "")
        if sym_type not in ("class", "interface", "type", "struct"):
            continue
        meta = sym.get("metadata", {}) or {}
        field_count = len(meta.get("fields", []))
        if not _is_model_symbol(sym, sym_type, field_count):
            continue
        models.append({
            "name": sym.get("name", ""),
            "type": sym_type,
            "fields": field_count,
            "file": sym.get("path", ""),
            "line": sym.get("start_line", 0),
        })
    models.sort(key=lambda m: m["name"])
    return models


def _collect_entry_points(symbols: dict) -> list[str]:
    entry_files = set()
    for sym in symbols.values():
        path = sym.get("path", "")
        if path and _ENTRY_FILE_PATTERN.search(path):
            entry_files.add(path)
        if sym.get("name", "").lower() in _ENTRY_NAMES:
            if path:
                entry_files.add(path)
    return sorted(entry_files)


def _file_pair_from_dep(dep_info: dict, symbols: dict) -> Optional[tuple]:
    source_file = dep_info.get("source_path", "")
    target = dep_info.get("target", "")
    if not (source_file and target):
        return None
    target_file = ""
    for sid, sym in symbols.items():
        if target in sid and sym.get("path"):
            target_file = sym["path"]
            break
    if not target_file or source_file == target_file:
        return None
    return (source_file, target_file)


def _build_file_connections(symbols: dict, dependencies: dict, reverse_index: dict) -> Counter:
    connections: Counter = Counter()

    for _key, dep_info in dependencies.items():
        if not isinstance(dep_info, dict):
            continue
        pair = _file_pair_from_dep(dep_info, symbols)
        if pair is not None:
            connections[pair] += 1

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
                connections[(source_file, target_file)] += 1
    return connections


def _empty_graph_summary() -> dict:
    return {
        "total_connections": 0,
        "avg_refs_per_module": 0,
        "most_connected_file": "",
        "orphan_files": [],
        "orphan_count": 0,
    }


def _compute_graph_summary(symbols: dict, file_connections: Counter) -> dict:
    if not file_connections:
        return _empty_graph_summary()

    file_ref_counts: Counter = Counter()
    for (src, tgt), count in file_connections.items():
        file_ref_counts[src] += count
        file_ref_counts[tgt] += count

    all_indexed_files = {sym.get("path", "") for sym in symbols.values() if sym.get("path")}
    connected_files = set()
    for src, tgt in file_connections:
        connected_files.add(src)
        connected_files.add(tgt)
    orphan_files = sorted(all_indexed_files - connected_files)

    most_connected = file_ref_counts.most_common(1)[0][0] if file_ref_counts else ""
    avg_refs = sum(file_ref_counts.values()) / max(len(file_ref_counts), 1)

    return {
        "total_connections": len(file_connections),
        "avg_refs_per_module": round(avg_refs, 1),
        "most_connected_file": most_connected,
        "orphan_files": orphan_files,
        "orphan_count": len(orphan_files),
    }


def _extract_from_index(project_path: Path) -> dict:
    """Extract data from the flyto-indexer index if available."""
    result = _empty_extract_result()

    index_dir = project_path / ".flyto-index"
    if not index_dir.exists():
        return result

    index = _load_index_file(index_dir)
    if not index:
        return result

    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})
    reverse_index = index.get("reverse_index", {})

    result["symbol_counts"] = dict(Counter(
        sym.get("type", "unknown") for sym in symbols.values()
    ).most_common())

    _collect_api_from_symbols(symbols, result)
    _collect_api_from_dep_edges(index, result)
    _collect_api_from_routes(index, result)
    for key in ("api_definitions", "api_calls_internal", "api_calls_external", "api_routes"):
        result[key].sort(key=lambda r: (r["method"], r["path"]))

    result["models"] = _collect_models(symbols)
    result["entry_points"] = _collect_entry_points(symbols)

    file_connections = _build_file_connections(symbols, dependencies, reverse_index)
    all_connections = [
        {"source_file": pair[0], "target_file": pair[1], "import_count": count}
        for pair, count in file_connections.most_common()
    ]
    result["module_graph_full"] = all_connections
    result["module_graph"] = all_connections[:10]
    result["module_graph_summary"] = _compute_graph_summary(symbols, file_connections)

    result["complexity_summary"] = _compute_complexity_summary(symbols, index_dir)
    # Health dimensions computed later in build_project_profile (needs project_type)
    result["_health_inputs"] = {
        "symbols": symbols,
        "reverse_index": reverse_index,
        "index_dir": index_dir,
        "complexity_summary": result["complexity_summary"],
    }
    result["_raw_dependencies"] = index.get("dependencies", [])
    result["_raw_symbols"] = symbols

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
        "most_complex": most_complex[:50],
    }


def _compute_reachability(deps: dict, idx: dict) -> dict:
    """Compute basic reachability: which dependencies are actually imported.

    Uses the dependency graph's import edges to check if a package name
    appears in any import statement across the codebase. Deps that are
    never imported are considered "unreachable" — their CVEs can be
    deprioritized.

    This is a conservative analysis: if ANY file imports the package,
    the entire package is considered reachable.
    """
    dep_list = deps.get("dependencies", [])
    if isinstance(dep_list, dict):
        dep_list = dep_list.get("dependencies", [])
    if not dep_list:
        return {"total_deps": 0, "reachable": 0, "unreachable": 0, "unreachable_pct": 0, "details": []}

    # Collect all import targets from dependency graph edges
    all_imports = set()
    raw_deps = idx.get("_raw_dependencies", [])
    if isinstance(raw_deps, dict):
        raw_deps = raw_deps.get("dependencies", [])
    for dep_edge in raw_deps:
        if isinstance(dep_edge, dict):
            dep_type = dep_edge.get("dep_type", dep_edge.get("type", ""))
            if dep_type == "imports":
                target = dep_edge.get("target_id", dep_edge.get("target", ""))
                if target:
                    all_imports.add(target.lower())

    # Also scan module_graph for actual file-level imports
    for conn in idx.get("module_graph_full", idx.get("module_graph", [])):
        if isinstance(conn, dict):
            target = conn.get("target_file", "")
            if target:
                all_imports.add(target.lower())

    # For Go projects: scan .go files for import statements directly
    # This catches `import "github.com/jackc/pgx/v5"` etc.
    health_inputs = idx.get("_health_inputs", {})
    index_dir = health_inputs.get("index_dir")
    if index_dir:
        project_root = index_dir.parent if hasattr(index_dir, 'parent') else None
        if project_root and project_root.exists():
            import_re = re.compile(r'"([^"]+)"')
            for go_file in project_root.rglob("*.go"):
                # Skip vendor/test dirs
                rel = str(go_file.relative_to(project_root))
                if any(skip in rel for skip in ("vendor/", "testdata/", "pkg/mod/")):
                    continue
                try:
                    src = go_file.read_text(encoding="utf-8", errors="replace")
                    # Find import blocks
                    in_import = False
                    for line in src.splitlines()[:200]:  # Only check top of file
                        stripped = line.strip()
                        if stripped.startswith("import ("):
                            in_import = True
                            continue
                        if in_import and stripped == ")":
                            in_import = False
                            continue
                        if in_import or stripped.startswith("import "):
                            for m in import_re.finditer(stripped):
                                all_imports.add(m.group(1).lower())
                except Exception:
                    pass

    # Build a set of package root names from imports
    import_packages = set()
    for imp in all_imports:
        imp_norm = imp.replace("\\", "/")
        parts = imp_norm.split("/")
        if parts:
            # npm scoped: @scope/package
            if parts[0].startswith("@") and len(parts) > 1:
                import_packages.add(f"{parts[0]}/{parts[1]}")
            else:
                import_packages.add(parts[0])
            import_packages.add(imp_norm)

    # Check each dependency
    total = 0
    reachable = 0
    unreachable = 0
    details = []
    for dep in dep_list:
        if isinstance(dep, dict):
            name = dep.get("name", "")
        else:
            name = str(dep)
        if not name:
            continue
        total += 1
        name_lower = name.lower()
        # Check if any import path contains this package name
        is_reachable = any(name_lower in imp for imp in import_packages) or any(name_lower in imp for imp in all_imports)
        if is_reachable:
            reachable += 1
        else:
            unreachable += 1
            details.append({"package": name, "reachable": False})

    unreachable_pct = round(unreachable / max(total, 1) * 100) if total > 0 else 0

    return {
        "total_deps": total,
        "reachable": reachable,
        "unreachable": unreachable,
        "unreachable_pct": unreachable_pct,
        "unreachable_packages": [d["package"] for d in details],
    }


def _is_test_file_fallback(path: str) -> bool:
    try:
        try:
            from .analyzer.complexity import _is_test_file
        except ImportError:
            from analyzer.complexity import _is_test_file
        return _is_test_file(path)
    except ImportError:
        lower = path.lower()
        return any(pat in lower for pat in ("test_", "_test.", ".test.", ".spec.", "/test/", "/tests/"))


def _project_root_from_index_dir(index_dir: "Path") -> Optional["Path"]:
    if index_dir.exists():
        return index_dir.parent
    return None


def _status_from_score(score: int) -> str:
    if score >= 20:
        return "PASS"
    if score >= 10:
        return "WARN"
    return "FAIL"


def _security_dim(index_dir: "Path") -> tuple[int, int]:
    """Return (security_score, finding_count)."""
    try:
        try:
            from .analyzer.security import SecurityScanner
        except ImportError:
            from analyzer.security import SecurityScanner

        project_root = _project_root_from_index_dir(index_dir)
        if not (project_root and project_root.exists()):
            return 25, 0

        scanner = SecurityScanner(project_root)
        report = scanner.analyze()
        finding_count = len(report.issues)
        penalty = 0.0
        sev_weights = {"critical": 3, "high": 1.5, "medium": 0.5}
        for issue in report.issues:
            penalty += sev_weights.get(issue.severity, 0)
        # Logistic curve: 1 finding → 24, 5 → 22, 20 → 15, 50 → 12, 200 → 5
        scaled = int(25 * penalty / (penalty + 50)) if penalty > 0 else 0
        return max(0, 25 - scaled), finding_count
    except Exception:
        return 25, 0


def _complexity_dim(complexity_summary: dict) -> tuple[int, int]:
    """Return (complexity_score, complex_count)."""
    func_count = complexity_summary.get("total_functions", 0)
    complex_count = complexity_summary.get("complex_functions", 0)
    if func_count <= 0:
        return 25, complex_count
    pct = complex_count / func_count
    score = max(0, int(25 * (1 - min(pct * 2, 1))))
    return score, complex_count


def _is_dead_symbol(sym: dict, sym_id: str, reverse_index: dict) -> bool:
    if sym.get("ref_count", sym.get("reference_count", 0)) != 0:
        return False
    if reverse_index.get(sym_id, []):
        return False
    name = sym.get("name", "")
    path = sym.get("path", "")
    if name.startswith("_"):
        return False
    # Go exported names are public API — regex scanner can't see cross-package refs
    if path.endswith(".go") and name and name[0].isupper():
        return False
    return True


def _dead_code_dim(symbols: dict, reverse_index: dict) -> tuple[int, int, list]:
    """Return (dead_score, dead_count, dead_symbols_list)."""
    non_test_symbols = {
        k: v for k, v in symbols.items()
        if not _is_test_file_fallback(v.get("path", ""))
        and v.get("type", "") in ("function", "method", "class", "component", "composable")
    }
    dead_list = []
    for sym_id, sym in non_test_symbols.items():
        if _is_dead_symbol(sym, sym_id, reverse_index):
            dead_list.append({
                "name": sym.get("name", ""),
                "path": sym.get("path", ""),
                "line": sym.get("line", 0),
                "type": sym.get("type", ""),
            })
    dead_count = len(dead_list)
    dead_pct = dead_count / max(len(non_test_symbols), 1)
    score = max(0, int(25 * (1 - min(dead_pct * 2, 1))))
    return score, dead_count, dead_list


def _coverage_dim(index_dir: "Path") -> tuple[int, int]:
    """Return (coverage_score, coverage_pct)."""
    try:
        project_root = _project_root_from_index_dir(index_dir)
        if not project_root:
            return 0, 0
        if not (project_root / ".coverage").exists():
            return 0, 0
        try:
            proc = subprocess.run(
                ["python", "-m", "coverage", "report", "--format=total"],
                capture_output=True, text=True, timeout=30,
                cwd=str(project_root),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 0, 0
        if proc.returncode != 0 or not proc.stdout.strip():
            return 0, 0
        try:
            pct = int(float(proc.stdout.strip()))
        except ValueError:
            return 0, 0
        return min(25, round(pct / 4)), pct
    except Exception:
        return 0, 0


def _doc_score(index_dir: "Path") -> int:
    try:
        try:
            from .doc_scanner import scan_documentation
        except ImportError:
            from doc_scanner import scan_documentation
        project_root = _project_root_from_index_dir(index_dir)
        if not (project_root and project_root.exists()):
            return 0
        return scan_documentation(str(project_root)).overall_score
    except Exception:
        return 0


def _doc_penalty_for_score(doc_score: int) -> int:
    if doc_score < 30:
        return -10
    if doc_score < 50:
        return -5
    if doc_score >= 70:
        return 5
    return 0


def _select_active_dims(
    project_type: str,
    security_score: int,
    complexity_score: int,
    dead_score: int,
    doc_score_val: int,
) -> dict:
    """Pick which dimensions count for the overall score for this project type."""
    if project_type in ("backend", "fullstack"):
        return {"security": security_score, "complexity": complexity_score, "dead_code": dead_score}
    if project_type == "frontend":
        return {
            "complexity": complexity_score,
            "dead_code": dead_score,
            "security": min(25, security_score + 10),
        }
    if project_type == "library":
        return {"dead_code": dead_score, "complexity": complexity_score}
    if project_type == "mobile":
        return {"complexity": complexity_score, "dead_code": dead_score}
    if project_type in ("static", "unknown", ""):
        return {"documentation": min(25, round(doc_score_val / 4))}
    return {"security": security_score, "complexity": complexity_score, "dead_code": dead_score}


def _grade_for_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _empty_health_dimensions() -> dict:
    return {
        "security": {"score": 25, "max": 25, "status": "PASS", "finding_count": 0},
        "complexity": {"score": 25, "max": 25, "status": "PASS", "complex_count": 0},
        "dead_code": {"score": 25, "max": 25, "status": "PASS", "dead_count": 0},
        "overall": {"score": 100, "max": 100, "grade": "A"},
    }


def _compute_health_dimensions(
    symbols: dict,
    reverse_index: dict,
    index_dir: "Path",
    complexity_summary: dict,
    project_type: str = "",
) -> dict:
    """Compute health score dimensions based on project type.

    Dimensions vary by type:
    - backend / fullstack: security, complexity, dead_code (+ docs penalty)
    - frontend: complexity, dead_code, lighter security
    - library / mobile: dead_code, complexity
    - static / unknown: docs only

    Returns dict with per-dimension scores and overall grade.
    """
    if not symbols:
        return _empty_health_dimensions()

    security_score, finding_count = _security_dim(index_dir)
    complexity_score, complex_count = _complexity_dim(complexity_summary)
    dead_score, dead_count, dead_list = _dead_code_dim(symbols, reverse_index)
    coverage_score, coverage_pct = _coverage_dim(index_dir)
    doc_score_val = _doc_score(index_dir)
    doc_penalty = _doc_penalty_for_score(doc_score_val)

    has_coverage = coverage_pct > 0 or coverage_score > 0
    active_dims = _select_active_dims(
        project_type, security_score, complexity_score, dead_score, doc_score_val,
    )
    if has_coverage:
        active_dims["coverage"] = coverage_score

    max_possible = len(active_dims) * 25
    raw_score = sum(active_dims.values())
    overall_score = round(raw_score / max_possible * 100) if max_possible > 0 else 50
    if project_type not in ("static", "unknown", ""):
        overall_score += doc_penalty
    overall_score = max(0, min(100, overall_score))

    result: dict = {}
    if "security" in active_dims:
        result["security"] = {
            "score": security_score, "max": 25,
            "status": _status_from_score(security_score),
            "finding_count": finding_count,
        }
    if "complexity" in active_dims:
        result["complexity"] = {
            "score": complexity_score, "max": 25,
            "status": _status_from_score(complexity_score),
            "complex_count": complex_count,
        }
    if "dead_code" in active_dims:
        result["dead_code"] = {
            "score": dead_score, "max": 25,
            "status": _status_from_score(dead_score),
            "dead_count": dead_count,
            "dead_symbols": dead_list[:50],
        }
    if "documentation" in active_dims:
        doc_score_dim = active_dims["documentation"]
        result["documentation"] = {
            "score": doc_score_dim, "max": 25,
            "status": _status_from_score(doc_score_dim),
        }
    if has_coverage and "coverage" in active_dims:
        coverage_status = _status_from_score(coverage_score) if has_coverage else "N/A"
        result["coverage"] = {
            "score": coverage_score, "max": 25,
            "status": coverage_status,
            "coverage_pct": coverage_pct,
        }

    result["overall"] = {"score": int(overall_score), "max": 100, "grade": _grade_for_score(overall_score)}
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
# Scanner helpers (each returns a dict, swallowing failures to keep profile robust)
# ---------------------------------------------------------------------------

def _scan_secrets(project_path: Path) -> dict:
    try:
        try:
            from .secret_scanner import scan_secrets
        except ImportError:
            from secret_scanner import scan_secrets
        r = scan_secrets(project_path)
        return {
            "total_files_scanned": r.total_files_scanned,
            "total_findings": r.total_findings,
            "critical": r.critical, "high": r.high, "medium": r.medium,
        }
    except Exception as e:
        logger.debug("Secret scan failed: %s", e)
        return {}


def _scan_code_vulnerabilities(project_path: Path) -> dict:
    try:
        try:
            from .secret_scanner import scan_code_vulnerabilities
        except ImportError:
            from secret_scanner import scan_code_vulnerabilities
        return scan_code_vulnerabilities(project_path)
    except Exception as e:
        logger.debug("Code vulnerability scan failed: %s", e)
        return {}


def _scan_git_history(project_path: Path) -> dict:
    try:
        try:
            from .git_secret_scanner import scan_git_history
        except ImportError:
            from git_secret_scanner import scan_git_history
        return scan_git_history(project_path)
    except Exception as e:
        logger.debug("Git history secret scan failed: %s", e)
        return {}


def _scan_dockerfile(project_path: Path) -> dict:
    try:
        try:
            from .dockerfile_scanner import scan_dockerfiles
        except ImportError:
            from dockerfile_scanner import scan_dockerfiles
        return scan_dockerfiles(project_path)
    except Exception as e:
        logger.debug("Dockerfile scan failed: %s", e)
        return {}


def _scan_license(project_path: Path) -> dict:
    try:
        try:
            from .license_scanner import scan_licenses
        except ImportError:
            from license_scanner import scan_licenses
        r = scan_licenses(project_path)
        return {
            "project_license": r.project_license,
            "project_license_file": r.project_license_file,
            "dependency_licenses": r.dependency_licenses,
            "copyleft_warning": r.copyleft_warning,
            "dependencies_without_license_count": len(r.dependencies_without_license),
        }
    except Exception as e:
        logger.debug("License scan failed: %s", e)
        return {}


def _scan_documentation(project_path: Path) -> dict:
    try:
        try:
            from .doc_scanner import scan_documentation
        except ImportError:
            from doc_scanner import scan_documentation
        r = scan_documentation(project_path)
        return {
            "overall_score": r.overall_score,
            "readme_score": r.readme_score,
            "readme_sections": r.readme_sections,
            "api_doc_coverage": r.api_doc_coverage,
            "module_doc_coverage": r.module_doc_coverage,
            "inline_doc_coverage": r.inline_doc_coverage,
            "has_env_example": r.has_env_example,
            "has_changelog": r.has_changelog,
            "has_contributing": r.has_contributing,
            "suggestions": r.suggestions,
        }
    except Exception as e:
        logger.debug("Documentation scan failed: %s", e)
        return {}


def _scan_taint(project_path: Path) -> dict:
    try:
        try:
            from .analyzer.taint import TaintAnalyzer
        except ImportError:
            from analyzer.taint import TaintAnalyzer

        index_dir = project_path / ".flyto-index"
        raw_index = _load_index_file(index_dir) if index_dir.exists() else {}

        analyzer = TaintAnalyzer(project_path, index=raw_index)
        r = analyzer.analyze_full()
        unsanitized = [f for f in r.taint_flows if not f.sanitized]
        return {
            "total_sources": r.total_sources,
            "total_sinks": r.total_sinks,
            "unsanitized_flows": len(unsanitized),
            "sanitized_flows": r.sanitized_flows,
            "high_risk_count": r.high_risk_count,
        }
    except Exception as e:
        logger.debug("Taint analysis failed: %s", e)
        return {}


def _scan_iac(project_path: Path) -> dict:
    default = {
        "total_findings": 0, "critical": 0, "high": 0, "medium": 0, "low": 0,
        "findings": [], "frameworks_detected": [],
    }
    try:
        try:
            from .iac_scanner import scan_iac_to_dict
        except ImportError:
            from iac_scanner import scan_iac_to_dict
        return scan_iac_to_dict(project_path)
    except Exception as e:
        logger.debug("IaC scan failed: %s", e)
        return default


def _scan_frameworks(project_path: Path) -> list:
    try:
        try:
            from .framework_detector import detect_frameworks
        except ImportError:
            from framework_detector import detect_frameworks
        return [fw.to_dict() for fw in detect_frameworks(project_path)]
    except Exception as e:
        logger.debug("Framework detection failed: %s", e)
        return []


def _check_license_policy(license_data: dict) -> list[dict]:
    issues: list[dict] = []
    try:
        try:
            from .rule_loader import get_license_policies
        except ImportError:
            from rule_loader import get_license_policies
        policies = get_license_policies()
        dep_licenses = license_data.get("dependency_licenses", {})
        for lic_id, count in dep_licenses.items():
            if lic_id in policies.get("deny", set()):
                issues.append({
                    "license": lic_id, "risk_level": "critical",
                    "reason": f"License {lic_id} is in deny list", "count": count,
                })
            elif lic_id in policies.get("warn", set()):
                issues.append({
                    "license": lic_id, "risk_level": "high",
                    "reason": f"Copyleft license {lic_id} may force open-source derivatives",
                    "count": count,
                })
        if not policies.get("allow_unlicensed", False):
            unlicensed_count = license_data.get("dependencies_without_license_count", 0)
            if unlicensed_count > 0:
                issues.append({
                    "license": "UNLICENSED", "risk_level": "medium",
                    "reason": f"{unlicensed_count} dependencies have no detectable license",
                    "count": unlicensed_count,
                })
    except Exception as e:
        logger.debug("License policy check failed: %s", e)
    return issues


def _build_health_dims(idx: dict, project_type: str) -> dict:
    health_inputs = idx.get("_health_inputs")
    if not health_inputs:
        return {"overall": {"score": 0, "max": 100, "grade": "?"}}
    return _compute_health_dimensions(
        health_inputs["symbols"],
        health_inputs["reverse_index"],
        health_inputs["index_dir"],
        health_inputs["complexity_summary"],
        project_type,
    )


def _adjust_overall_health(
    overall: dict,
    secrets_data: dict, taint_data: dict, iac_data: dict,
    license_policy_issues: list, documentation_data: dict,
    project_type: str,
) -> dict:
    """Apply secret/taint/IaC/license/doc penalties on top of dimension-derived score."""
    score = overall.get("score", 0)

    # Secrets penalty: critical=-5, high=-3, medium=-1; logistic cap 20
    if isinstance(secrets_data, dict):
        raw = (secrets_data.get("critical", 0) * 5 +
               secrets_data.get("high", 0) * 3 +
               secrets_data.get("medium", 0))
        if raw > 0:
            score -= int(20 * raw / (raw + 30))

    # Taint penalty: -3 per unsanitized high-risk, cap 15
    if isinstance(taint_data, dict):
        high = taint_data.get("high_risk_count", 0)
        if high > 0:
            score -= min(high * 3, 15)

    # IaC penalty: critical=-5, high=-3; logistic cap 15
    if isinstance(iac_data, dict):
        raw = iac_data.get("critical", 0) * 5 + iac_data.get("high", 0) * 3
        if raw > 0:
            score -= int(15 * raw / (raw + 30))

    # License policy penalty
    for issue in license_policy_issues:
        risk = issue.get("risk_level")
        if risk == "critical":
            score -= 5
        elif risk == "high":
            score -= 2

    # Extra docs penalty for very poor docs (only on code projects)
    if project_type not in ("static", "unknown", "") and isinstance(documentation_data, dict):
        if documentation_data.get("overall_score", 0) < 30:
            score -= 5

    score = max(0, min(100, score))
    return {"score": score, "max": 100, "grade": _grade_for_score(score)}


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

    fs = _scan_filesystem(project_path)
    idx = _extract_from_index(project_path)
    deps = _scan_deps(project_path)
    git = _git_info(project_path)
    dep_names = {d.get("name", "") for d in deps.get("dependencies", []) if isinstance(d, dict)}
    patterns = _detect_patterns(fs["_all_files"], dep_names, index_data=idx)

    secrets_data = _scan_secrets(project_path)
    code_vulns_data = _scan_code_vulnerabilities(project_path)
    git_leaked_data = _scan_git_history(project_path)
    dockerfile_data = _scan_dockerfile(project_path)
    license_data = _scan_license(project_path)
    documentation_data = _scan_documentation(project_path)
    taint_data = _scan_taint(project_path)
    iac_data = _scan_iac(project_path)
    license_policy_issues = _check_license_policy(license_data)
    frameworks_data = _scan_frameworks(project_path)

    services = _detect_services(deps)
    project_type_info = _classify_project_type(
        languages=fs["languages"],
        api_definitions=idx["api_definitions"],
        components=idx["symbol_counts"].get("component", 0),
        dep_names=dep_names,
        patterns=patterns,
        entry_points=idx["entry_points"],
        all_files=fs["_all_files"],
    )

    health_dims = _build_health_dims(idx, project_type_info["type"])
    health_dims["overall"] = _adjust_overall_health(
        health_dims.get("overall", {}),
        secrets_data, taint_data, iac_data, license_policy_issues,
        documentation_data, project_type_info["type"],
    )

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

        # Counts — for flyto-engine compat
        "api_definition_count": len(idx["api_definitions"]),
        "model_count": len(idx["models"]),
        "dependency_count": deps.get("total_count", 0),
        "secret_count": secrets_data.get("total_findings", 0),
        "taint_flow_count": taint_data.get("unsanitized_flows", 0),
        "complex_functions": idx["complexity_summary"].get("complex_functions", 0),
        "avg_complexity": idx["complexity_summary"].get("avg_complexity", 0),
        "dead_code_count": health_dims.get("dead_code", {}).get("dead_count", 0),
        "connection_count": idx["module_graph_summary"].get("total_connections", 0),

        # Health — top-level for flyto-engine compat
        "health_score": health_dims.get("overall", {}).get("score", 0),
        "health_grade": health_dims.get("overall", {}).get("grade", "?"),
        "health_dimensions": health_dims,

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
        "code_vulnerabilities": code_vulns_data,
        "git_leaked_secrets": git_leaked_data,
        "dockerfile_issues": dockerfile_data,
        "taint_flows": taint_data,
        "license": license_data,
        "license_policy_issues": license_policy_issues,
        "iac_findings": iac_data,
        # Container findings: derive from dockerfile_scanner in a standard format
        # Reachability: check which dependencies are actually imported
        "reachability": _compute_reachability(deps, idx),
        "container_findings": {
            "total_findings": dockerfile_data.get("total_issues", 0),
            "critical": sum(1 for i in dockerfile_data.get("issues", []) if i.get("severity") == "CRITICAL"),
            "high": sum(1 for i in dockerfile_data.get("issues", []) if i.get("severity") == "HIGH"),
            "medium": sum(1 for i in dockerfile_data.get("issues", []) if i.get("severity") == "MEDIUM"),
            "low": sum(1 for i in dockerfile_data.get("issues", []) if i.get("severity") == "LOW"),
            "findings": dockerfile_data.get("issues", []),
        },
        "documentation": documentation_data,
    }

    if not compact:
        profile["folder_structure"] = fs["folder_structure"]

    return profile


# ---------------------------------------------------------------------------
# Human-readable formatter
# ---------------------------------------------------------------------------

def _format_header(profile: dict) -> list[str]:
    project_type = profile.get("project_type", "")
    project_sub_type = profile.get("project_sub_type", "")
    type_label = project_type
    if project_sub_type:
        type_label = f"{project_type} ({project_sub_type})"
    header = f"Project Profile: {profile['name']}"
    if type_label:
        header += f" [{type_label}]"
    return [header, f"Generated: {profile['generated_at']}", ""]


def _format_structure(profile: dict) -> list[str]:
    langs = profile.get("languages", {})
    lang_str = ", ".join(f"{k} ({v})" for k, v in
                         sorted(langs.items(), key=lambda x: -x[1])[:8])
    parts = [f"Files: {profile['file_count']}"]
    folder_structure = profile.get("folder_structure")
    if folder_structure:
        parts.append(f"Folders: {len(folder_structure)}")
    parts.append(f"Languages: {lang_str}")
    return ["Structure", f"  {' | '.join(parts)}", ""]


def _format_apis(profile: dict) -> list[str]:
    api_defs = profile.get("api_definitions", [])
    api_internal = profile.get("api_calls_internal", [])
    api_external = profile.get("api_calls_external", [])
    services = profile.get("services", [])
    if not (api_defs or api_internal or api_external or services):
        return []

    out = ["Services & APIs"]
    if api_defs:
        out.append(f"  Backend routes: {len(api_defs)} defined")
        for route in api_defs[:15]:
            method = route.get("method", "GET")
            path = route.get("path", "")
            out.append(f"    {method:6s} {path}")
        if len(api_defs) > 15:
            out.append(f"    ... and {len(api_defs) - 15} more")
        out.append("")
    if services:
        svc_names = ", ".join(s["name"] for s in services)
        out.append(f"  Services: {svc_names}")
        out.append("")
    if api_internal:
        out.append(f"  Frontend API calls: {len(api_internal)} internal")
    if api_external:
        out.append(f"  External API calls: {len(api_external)}")
    if api_internal or api_external:
        out.append("")
    return out


def _format_models(profile: dict) -> list[str]:
    models = profile.get("models", [])
    if not models:
        return []
    out = [f"Models ({len(models)})"]
    for m in models[:15]:
        field_str = f"{m['fields']} fields" if m.get("fields") else "no fields extracted"
        out.append(f"  {m['name']} ({field_str}) -- {m['file']}:{m['line']}")
    if len(models) > 15:
        out.append(f"  ... and {len(models) - 15} more")
    out.append("")
    return out


def _format_symbols(profile: dict) -> list[str]:
    sym_counts = profile.get("symbol_counts", {})
    if not sym_counts:
        return []

    def _plural(word: str, count: int) -> str:
        if count == 1:
            return word
        if word.endswith("s"):
            return word + "es"
        return word + "s"

    parts = [f"{v} {_plural(k, v)}" for k, v in
             sorted(sym_counts.items(), key=lambda x: -x[1])]
    return ["Symbols", f"  {', '.join(parts)}", ""]


def _format_dependencies(profile: dict) -> list[str]:
    deps = profile.get("dependencies", {})
    if not (deps and deps.get("total_count", 0) > 0):
        return []
    eco = deps.get("ecosystems", [])
    indirect = (f", {deps.get('indirect_count', 0)} indirect"
                if deps.get("indirect_count") else "")
    plural = "s" if len(eco) != 1 else ""
    return [
        "Dependencies",
        (f"  {deps['total_count']} packages "
         f"({deps.get('production_count', 0)} production, "
         f"{deps.get('dev_count', 0)} dev{indirect}) "
         f"across {len(eco)} ecosystem{plural} [{', '.join(eco)}]"),
        "",
    ]


def _format_connections(profile: dict) -> list[str]:
    module_graph = profile.get("module_graph", [])
    if not module_graph:
        return []
    out = [f"Connections (top {min(10, len(module_graph))} module pairs)"]
    for edge in module_graph[:10]:
        out.append(
            f"  {edge['source_file']} -> {edge['target_file']} ({edge['import_count']} refs)"
        )
    summary = profile.get("module_graph_summary", {})
    if summary:
        total_conn = summary.get("total_connections", 0)
        avg_refs = summary.get("avg_refs_per_module", 0)
        orphan_count = summary.get("orphan_count", 0)
        most_connected = summary.get("most_connected_file", "")
        out.append(f"  --- {total_conn} total connections, avg {avg_refs} refs/module")
        if most_connected:
            out.append(f"  Most connected: {most_connected}")
        if orphan_count > 0:
            out.append(f"  Orphan files (no imports/importers): {orphan_count}")
    out.append("")
    return out


def _format_complexity(profile: dict) -> list[str]:
    complexity = profile.get("complexity_summary", {})
    if not (complexity and complexity.get("total_functions", 0) > 0):
        return []
    out = [
        "Complexity",
        (f"  {complexity['total_functions']} functions analyzed, "
         f"{complexity['complex_functions']} complex (score >= 5), "
         f"avg score {complexity['avg_complexity']}"),
    ]
    most_complex = complexity.get("most_complex", [])
    if most_complex:
        out.append("  Top complex functions:")
        for fn in most_complex[:5]:
            out.append(f"    {fn['name']} (score={fn['score']}) -- {fn['path']}:{fn.get('line', 0)}")
    out.append("")
    return out


def _format_health_dim_detail(dim_name: str, dim: dict) -> str:
    if dim_name == "security" and dim.get("finding_count", 0) > 0:
        return f"  ({dim['finding_count']} findings)"
    if dim_name == "complexity" and dim.get("complex_count", 0) > 0:
        return f"  ({dim['complex_count']} complex functions)"
    if dim_name == "dead_code" and dim.get("dead_count", 0) > 0:
        return f"  ({dim['dead_count']} unreferenced symbols)"
    if dim_name == "coverage":
        if dim.get("coverage_pct", 0) > 0:
            return f"  ({dim['coverage_pct']}% covered)"
        return "  (no coverage data)"
    return ""


def _format_health(profile: dict) -> list[str]:
    health = profile.get("health_dimensions", {})
    if not (health and health.get("overall")):
        return []
    overall = health["overall"]
    out = [f"Health Score: {overall['grade']} ({overall['score']}/{overall['max']})"]
    for dim_name in ("security", "complexity", "dead_code", "coverage"):
        dim = health.get(dim_name, {})
        if not dim:
            continue
        label = dim_name.replace("_", " ").title()
        detail = _format_health_dim_detail(dim_name, dim)
        out.append(f"  {label:12s} {dim['score']:2d}/{dim['max']} {dim['status']}{detail}")
    out.append("")
    return out


def _format_entry_points(profile: dict) -> list[str]:
    entry_points = profile.get("entry_points", [])
    if not entry_points:
        return []
    out = [f"Entry Points ({len(entry_points)})"]
    for ep in entry_points[:10]:
        out.append(f"  {ep}")
    if len(entry_points) > 10:
        out.append(f"  ... and {len(entry_points) - 10} more")
    out.append("")
    return out


def _format_frameworks(profile: dict) -> list[str]:
    frameworks = profile.get("frameworks", [])
    if not frameworks:
        return []
    out = [f"Frameworks ({len(frameworks)})"]
    for fw in frameworks:
        version_str = f" v{fw['version']}" if fw.get("version") else ""
        out.append(f"  {fw['name']}{version_str} [{fw['type']}]")
        if fw.get("conventions"):
            conv_parts = [f"{k}={v}" for k, v in fw["conventions"].items()]
            out.append(f"    Conventions: {', '.join(conv_parts)}")
        if fw.get("entry_points"):
            ep_list = fw["entry_points"][:3]
            out.append(f"    Entry points: {', '.join(ep_list)}")
    out.append("")
    return out


def _format_patterns(profile: dict) -> list[str]:
    patterns = profile.get("patterns", [])
    if not patterns:
        return []
    return ["Patterns Detected", f"  {', '.join(patterns)}", ""]


def _format_infrastructure(profile: dict) -> list[str]:
    parts = [
        f"{label}: {'yes' if profile.get(key) else 'no'}"
        for key, label in [("has_docker", "Docker"), ("has_ci", "CI"),
                           ("has_tests", "Tests"), ("has_docs", "Docs")]
    ]
    out = ["Infrastructure", f"  {' | '.join(parts)}"]
    config_files = profile.get("config_files", [])
    if config_files:
        out.append(f"  Config: {', '.join(config_files[:10])}")
        if len(config_files) > 10:
            out.append(f"    ... and {len(config_files) - 10} more")
    out.append("")
    return out


def _format_git(profile: dict) -> list[str]:
    authors = profile.get("recent_authors", [])
    last_commit = profile.get("last_commit_date", "")
    if not (authors or last_commit):
        return []
    out = ["Git"]
    if authors:
        out.append(f"  Authors: {', '.join(authors)}")
    if last_commit:
        date_only = last_commit[:10] if len(last_commit) >= 10 else last_commit
        out.append(f"  Last commit: {date_only}")
    return out


def format_profile(profile: dict) -> str:
    """Format a project profile as human-readable text."""
    sections = [
        _format_header(profile),
        _format_structure(profile),
        _format_apis(profile),
        _format_models(profile),
        _format_symbols(profile),
        _format_dependencies(profile),
        _format_connections(profile),
        _format_complexity(profile),
        _format_health(profile),
        _format_entry_points(profile),
        _format_frameworks(profile),
        _format_patterns(profile),
        _format_infrastructure(profile),
        _format_git(profile),
    ]
    lines = [line for section in sections for line in section]
    return "\n".join(lines)
