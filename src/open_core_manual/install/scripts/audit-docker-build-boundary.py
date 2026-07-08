#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_DOCKERFILES = {
    "engine": "flyto-engine/Dockerfile",
    "runner": "flyto-engine/runner/Dockerfile",
    "verification": "flyto-core/Dockerfile.verification",
    "brand_vision": "flyto-engine/brand-vision/Dockerfile",
    "pdf": "flyto-engine/pdf-service/Dockerfile",
    "frontend": "flyto-code/Dockerfile",
}

TEXT_EXTENSIONS = {
    "",
    ".conf",
    ".dockerignore",
    ".env",
    ".example",
    ".go",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

SKIP_PARTS = {
    ".git",
    ".github",
    ".flyto-index",
    ".pytest_cache",
    ".ruff_cache",
    "docs",
    "handoffs",
    "node_modules",
    "dist",
    "dist-next",
    "out",
    "test-results",
    "tests",
    "reports",
}

DENIED_PATTERNS = [
    re.compile(r"BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY"),
    re.compile(r"FLYTO_RUNNER_SECRET[ \t]*=[ \t]*[^\s$<]+"),
    re.compile(r"FLYTO_VERIFICATION_SECRET[ \t]*=[ \t]*[^\s$<]+"),
    re.compile(r"FLYTO_ENTERPRISE_JWT_SECRET_KEY[ \t]*=[ \t]*[^\s$<]+"),
    re.compile(r"ghcr\.io/.+-ee"),
    re.compile(r"flyto2-warroom-[a-z-]+-ee"),
]

FINAL_STAGE_RULES = {
    "engine": {
        "deny": [re.compile(r"^COPY\s+(--[^\s]+\s+)*\.\s+")],
        "allow_markers": ["COPY --from=build /out/server", "COPY --from=build /out/worker"],
    },
    "frontend": {
        "deny": [re.compile(r"^COPY\s+(--[^\s]+\s+)*\.\s+")],
        "allow_markers": ["COPY --from=builder /app/dist-next"],
    },
    "verification": {
        "deny": [],
        "allow_markers": ["COPY --from=builder /wheels", "RUN chown -R flyto:flyto /app"],
    },
    "runner": {"deny": [], "allow_markers": ["COPY main.py", "COPY handlers_health.py"]},
    "brand_vision": {"deny": [], "allow_markers": ["COPY app.py"]},
    "pdf": {"deny": [], "allow_markers": ["COPY server.py"]},
}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def final_stage(dockerfile: Path) -> str:
    body = read_text(dockerfile)
    parts = re.split(r"(?im)^FROM\s+", body)
    if len(parts) <= 1:
        return body
    return "FROM " + parts[-1]


def should_scan(path: Path) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    if path.stat().st_size > 2_000_000:
        return False
    return path.suffix in TEXT_EXTENSIONS


def scan_workspace(workspace: Path) -> list[str]:
    blockers: list[str] = []
    for rel in REQUIRED_DOCKERFILES.values():
        path = workspace / rel
        if not path.exists():
            blockers.append(f"missing Dockerfile: {rel}")
    for service, rel in REQUIRED_DOCKERFILES.items():
        path = workspace / rel
        if not path.exists():
            continue
        stage = final_stage(path)
        rules = FINAL_STAGE_RULES[service]
        for marker in rules["allow_markers"]:
            if marker not in stage:
                blockers.append(f"{rel} final stage missing expected marker: {marker}")
        for regex in rules["deny"]:
            for line in stage.splitlines():
                normalized = line.strip()
                if regex.search(normalized):
                    blockers.append(f"{rel} final stage has broad source copy: {normalized}")
    for root in ("flyto-engine", "flyto-core", "flyto-code"):
        base = workspace / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or not should_scan(path):
                continue
            body = read_text(path)
            for regex in DENIED_PATTERNS:
                if regex.search(body):
                    blockers.append(f"denied marker in {path.relative_to(workspace)}: {regex.pattern}")
    return blockers


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Flyto2 Warroom CE Docker build source boundaries")
    parser.add_argument("workspace", nargs="?", default="/Users/chester/flytohub")
    parser.add_argument("--skip-missing-workspace", action="store_true")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.exists():
        if args.skip_missing_workspace:
            print(f"skip: workspace not found: {workspace}")
            return 0
        print(f"BLOCKED: workspace not found: {workspace}", file=sys.stderr)
        return 2
    blockers = scan_workspace(workspace)
    if blockers:
        for blocker in blockers:
            print("BLOCKED: " + blocker, file=sys.stderr)
        return 2
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
