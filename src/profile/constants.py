"""
Shared constants for project profile analysis.
"""

import os
import re

# Directories to skip during filesystem walk
SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", ".ruff_cache", "target", "out", ".next",
    ".nuxt", ".output", "coverage", ".cache", ".parcel-cache",
    "bower_components", ".eggs", "egg-info",
    # Go module cache and vendor
    "pkg", "testdata",
})

# Extension-to-language mapping
EXT_LANG = {
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
CONFIG_FILES = [
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

# Pattern signals for architectural pattern detection
PATTERN_SIGNALS = {
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

# Known service SDKs for detection
SERVICE_SDKS = {
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

BACKEND_EXTS = frozenset({".py", ".go", ".java", ".rb", ".php", ".rs", ".cs", ".kt", ".kts"})
FRONTEND_EXTS = frozenset({".js", ".ts", ".tsx", ".jsx", ".vue", ".mjs", ".cjs"})

HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")

ENTRY_FILE_PATTERN = re.compile(
    r"(main|index|app|server|cli|__main__|entrypoint|bootstrap)\.(py|ts|js|go|rs|java)$",
    re.IGNORECASE,
)

ENTRY_NAMES = {"main", "run", "start", "bootstrap", "cli"}

API_CATEGORY_KEYS = {
    "api_definition": "api_definitions",
    "api_call_internal": "api_calls_internal",
    "api_call_external": "api_calls_external",
}
