"""
Dockerfile & IaC Scanner — detect security misconfigurations in Dockerfiles
and docker-compose files.

Pure Python stdlib, no external dependencies.
"""

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("flyto-indexer.dockerfile-scanner")

# Load Docker rules from YAML (with hardcoded fallback)
try:
    from .rule_loader import get_docker_rules
except ImportError:
    try:
        from rule_loader import get_docker_rules
    except ImportError:
        get_docker_rules = None

_docker_rules = get_docker_rules() if get_docker_rules else None

# Directories to skip
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", ".ruff_cache", "target", "out", ".next",
    ".nuxt", ".output", "coverage", ".cache",
})

# Sensitive ports that should not be exposed (YAML override or hardcoded fallback)
_SENSITIVE_PORTS = frozenset(_docker_rules["sensitive_ports"]) if _docker_rules else frozenset({"22", "3306", "5432", "6379", "27017", "1433"})


def scan_dockerfiles(project_path: str | Path) -> dict:
    """
    Scan all Dockerfile* and docker-compose*.yml files for security issues.

    Args:
        project_path: Root directory to scan.

    Returns:
        Dict with total_issues, dockerfiles_scanned, and issues list.
    """
    project_path = Path(project_path).resolve()
    issues = []
    dockerfiles_scanned = 0

    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for fname in filenames:
            file_path = Path(dirpath) / fname
            try:
                rel_path = str(file_path.relative_to(project_path))
            except ValueError:
                rel_path = str(file_path)

            if fname.startswith("Dockerfile"):
                dockerfiles_scanned += 1
                issues.extend(_scan_single_dockerfile(file_path, rel_path))
            elif re.match(r"docker-compose.*\.ya?ml$", fname, re.IGNORECASE):
                dockerfiles_scanned += 1
                issues.extend(_scan_docker_compose(file_path, rel_path))

    return {
        "total_issues": len(issues),
        "dockerfiles_scanned": dockerfiles_scanned,
        "issues": issues,
    }


def _scan_single_dockerfile(file_path: Path, rel_path: str) -> list[dict]:
    """Scan a single Dockerfile for security issues."""
    issues = []

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return issues

    lines = content.splitlines()
    has_user_instruction = False

    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Skip comments and empty lines
        if not stripped or stripped.startswith("#"):
            continue

        upper = stripped.upper()

        # Rule 1: FROM with :latest tag
        if upper.startswith("FROM "):
            # Parse the image reference
            from_match = re.match(
                r"FROM\s+(?:--platform=\S+\s+)?(\S+?)(?:\s+[Aa][Ss]\s+\S+)?$",
                stripped, re.IGNORECASE,
            )
            if from_match:
                image = from_match.group(1)
                if "${" not in image:  # Skip ARG references
                    if image.endswith(":latest") or (":" not in image and image != "scratch"):
                        issues.append({
                            "file": rel_path,
                            "line": line_num,
                            "rule": "FROM_LATEST",
                            "severity": "MEDIUM",
                            "description": f"Using ':latest' or untagged image '{image}' — pin to a specific version for reproducibility",
                        })

        # Rule 3: EXPOSE with sensitive ports
        if upper.startswith("EXPOSE "):
            ports_str = stripped[7:].strip()
            for port_part in ports_str.split():
                # Handle port/protocol like 22/tcp
                port = port_part.split("/")[0]
                if port in _SENSITIVE_PORTS:
                    issues.append({
                        "file": rel_path,
                        "line": line_num,
                        "rule": "EXPOSE_SENSITIVE_PORT",
                        "severity": "MEDIUM",
                        "description": f"Exposing sensitive port {port} — ensure this is intentional and properly secured",
                    })

        # Rule 4: ADD instead of COPY
        if upper.startswith("ADD "):
            # ADD is fine for extracting tar archives; flag others
            args = stripped[4:].strip()
            if not args.endswith(".tar.gz") and not args.endswith(".tgz") and "http" not in args.lower():
                issues.append({
                    "file": rel_path,
                    "line": line_num,
                    "rule": "ADD_INSTEAD_OF_COPY",
                    "severity": "LOW",
                    "description": "Use COPY instead of ADD — ADD can fetch remote URLs and auto-extract archives unexpectedly",
                })

        # Rule 5: apt-get install without --no-install-recommends
        if upper.startswith("RUN "):
            run_cmd = stripped[4:]
            if "apt-get" in run_cmd and "install" in run_cmd and "-y" in run_cmd:
                if "--no-install-recommends" not in run_cmd:
                    issues.append({
                        "file": rel_path,
                        "line": line_num,
                        "rule": "APT_NO_RECOMMENDS",
                        "severity": "LOW",
                        "description": "apt-get install without --no-install-recommends increases image size with unnecessary packages",
                    })

            # Rule 7: pipe to shell
            if re.search(r"(?:curl|wget)\s+.*\|\s*(?:ba)?sh", run_cmd):
                issues.append({
                    "file": rel_path,
                    "line": line_num,
                    "rule": "PIPE_TO_SHELL",
                    "severity": "CRITICAL",
                    "description": "Piping downloaded content to shell — download, verify checksum, then execute",
                })

        # Rule 6: ENV with secret-looking values
        if upper.startswith("ENV "):
            env_content = stripped[4:].strip()
            if re.search(r"(?i)(?:PASSWORD|SECRET|PRIVATE_KEY|API_KEY|TOKEN)\s*[=\s]", env_content):
                # Skip if it's a reference like $PASSWORD or ${PASSWORD}
                if not re.search(r"\$\{?\w+\}?", env_content.split("=", 1)[-1] if "=" in env_content else ""):
                    issues.append({
                        "file": rel_path,
                        "line": line_num,
                        "rule": "ENV_SECRET",
                        "severity": "HIGH",
                        "description": "Secrets in ENV instructions are baked into the image layer — use runtime secrets or build args instead",
                    })

        # Track USER instruction
        if upper.startswith("USER "):
            has_user_instruction = True

    # Rule 2: No USER instruction — running as root
    if not has_user_instruction and lines:
        # Only flag if it's a real Dockerfile with FROM
        has_from = any(l.strip().upper().startswith("FROM ") for l in lines if l.strip() and not l.strip().startswith("#"))
        if has_from:
            issues.append({
                "file": rel_path,
                "line": 1,
                "rule": "NO_USER",
                "severity": "HIGH",
                "description": "No USER instruction — container runs as root. Add 'USER nonroot' or 'USER 1000' for least privilege",
            })

    return issues


def _scan_docker_compose(file_path: Path, rel_path: str) -> list[dict]:
    """Scan a docker-compose file for security issues."""
    issues = []

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return issues

    lines = content.splitlines()

    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Rule 8: privileged: true
        if re.match(r"privileged:\s*true", stripped, re.IGNORECASE):
            issues.append({
                "file": rel_path,
                "line": line_num,
                "rule": "PRIVILEGED",
                "severity": "CRITICAL",
                "description": "privileged: true gives the container full host access — avoid unless absolutely necessary",
            })

        # Rule 9: Ports mapping sensitive ports to host
        # Match patterns like "22:22", "0.0.0.0:3306:3306", "5432:5432"
        port_match = re.match(r"""['"]*(?:\d+\.\d+\.\d+\.\d+:)?(\d+):(\d+)['"]*""", stripped)
        if port_match:
            host_port = port_match.group(1)
            container_port = port_match.group(2)
            if host_port in _SENSITIVE_PORTS or container_port in _SENSITIVE_PORTS:
                issues.append({
                    "file": rel_path,
                    "line": line_num,
                    "rule": "COMPOSE_SENSITIVE_PORT",
                    "severity": "MEDIUM",
                    "description": f"Mapping sensitive port {host_port}:{container_port} to host — ensure this is intentional",
                })

    return issues
