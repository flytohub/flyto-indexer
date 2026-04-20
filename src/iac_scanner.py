"""
IaC Scanner — security checks for Terraform, Kubernetes, Docker Compose configs.

Pure Python stdlib, no external dependencies. Rules loaded from YAML via rule_loader.

Supported frameworks:
  - Terraform (.tf) — regex-based HCL parsing
  - Kubernetes YAML — line-based state-machine parsing
  - Docker Compose YAML — line-based parsing

Usage:
    from iac_scanner import scan_iac, scan_iac_to_dict
    result = scan_iac("/path/to/project")
    data   = scan_iac_to_dict("/path/to/project")
"""

import logging
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("flyto-indexer.iac-scanner")

# ---------------------------------------------------------------------------
# Directories to skip when walking
# ---------------------------------------------------------------------------
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", ".ruff_cache", "target", "out", ".next",
    ".nuxt", ".output", "coverage", ".cache", ".terraform",
})

# K8s-related directory names (for heuristic YAML detection)
_K8S_DIR_NAMES = frozenset({
    "deploy", "k8s", "kubernetes", "manifests", "helm", "charts",
    "kustomize", "base", "overlays", "templates",
})

# K8s workload kinds that have container specs
_K8S_WORKLOAD_KINDS = frozenset({
    "Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet",
    "Job", "CronJob",
})

# Sensitive ports for Docker Compose
_SENSITIVE_PORTS = frozenset({22, 3306, 5432, 6379, 27017, 1433, 9200, 2379})

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class IaCFinding:
    """A single IaC security finding."""
    file_path: str
    resource_type: str    # "aws_s3_bucket", "Deployment", "docker-compose"
    check_id: str         # "IAC_TF_PUBLIC_S3"
    check_name: str       # Human-readable name
    severity: str         # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    line: int = 0
    guideline: str = ""   # Remediation URL or text
    framework: str = ""   # "terraform", "kubernetes", "docker_compose"


@dataclass
class IaCScanResult:
    """Aggregated IaC scan results."""
    total_files_scanned: int = 0
    total_findings: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    findings: list = field(default_factory=list)   # list[IaCFinding]
    frameworks_detected: list = field(default_factory=list)  # ["terraform", ...]


# ---------------------------------------------------------------------------
# Rule loading (with hardcoded defaults)
# ---------------------------------------------------------------------------

def _load_iac_rules() -> dict:
    """Try to load IaC rules from YAML via rule_loader, fall back to empty."""
    try:
        from rule_loader import load_rules_with_defaults
        return load_rules_with_defaults("iac", {})
    except Exception:
        pass
    try:
        from src.rule_loader import load_rules_with_defaults
        return load_rules_with_defaults("iac", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Terraform checks
# ---------------------------------------------------------------------------

# Regex to find resource blocks: resource "type" "name" {
_TF_RESOURCE_RE = re.compile(
    r'^resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.MULTILINE
)

# Patterns for specific checks inside resource blocks
_TF_PUBLIC_ACL_RE = re.compile(r'acl\s*=\s*"public-read(?:-write)?"')
_TF_OPEN_CIDR_RE = re.compile(r'cidr_blocks\s*=\s*\[\s*"0\.0\.0\.0/0"\s*\]')
_TF_PUBLICLY_ACCESSIBLE_RE = re.compile(r'publicly_accessible\s*=\s*true')
_TF_SSE_RE = re.compile(r'server_side_encryption_configuration\b|sse_algorithm\b')
_TF_HARDCODED_SECRET_RE = re.compile(
    r'(?:default\s*=\s*"[^"]+").*', re.IGNORECASE
)
_TF_VARIABLE_DEFAULT_RE = re.compile(
    r'default\s*=\s*"([^"]+)"'
)
_TF_SENSITIVE_VAR_NAMES = re.compile(
    r'variable\s+"[^"]*(?:password|secret|token|api_key|access_key|private_key)[^"]*"',
    re.IGNORECASE,
)

# Standard web ports that are commonly open to 0.0.0.0/0
_WEB_PORTS = {80, 443, 8080, 8443}


def _extract_tf_block(content: str, start_pos: int) -> tuple[str, int]:
    """Extract a brace-delimited block starting from the opening '{'.

    Returns (block_text, end_pos). Uses brace counting, not full HCL parsing.
    """
    brace_pos = content.find("{", start_pos)
    if brace_pos == -1:
        return "", start_pos

    depth = 0
    i = brace_pos
    while i < len(content):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[brace_pos:i + 1], i + 1
        elif ch == "#":
            # Skip single-line comment
            nl = content.find("\n", i)
            i = nl if nl != -1 else len(content)
            continue
        elif ch == '"':
            # Skip string literal
            i += 1
            while i < len(content) and content[i] != '"':
                if content[i] == "\\":
                    i += 1
                i += 1
        i += 1
    # Unmatched brace — return what we have
    return content[brace_pos:], len(content)


def _line_number_at(content: str, pos: int) -> int:
    """Return 1-based line number for a character position."""
    return content[:pos].count("\n") + 1


def _check_terraform(file_path: str, content: str) -> list[IaCFinding]:
    """Run Terraform security checks on file content."""
    findings: list[IaCFinding] = []
    rel_path = file_path

    # --- Resource block checks ---
    for match in _TF_RESOURCE_RE.finditer(content):
        res_type = match.group(1)
        res_name = match.group(2)
        block_start = match.start()
        block_text, _ = _extract_tf_block(content, match.end() - 1)
        block_line = _line_number_at(content, block_start)

        # 1. IAC_TF_PUBLIC_S3
        if res_type == "aws_s3_bucket":
            m = _TF_PUBLIC_ACL_RE.search(block_text)
            if m:
                findings.append(IaCFinding(
                    file_path=rel_path,
                    resource_type=res_type,
                    check_id="IAC_TF_PUBLIC_S3",
                    check_name="S3 bucket with public ACL",
                    severity="CRITICAL",
                    line=block_line + block_text[:m.start()].count("\n"),
                    guideline="Remove public-read ACL or use S3 Block Public Access.",
                    framework="terraform",
                ))

        # 3. IAC_TF_NO_ENCRYPT (S3 without server-side encryption)
        if res_type == "aws_s3_bucket":
            if not _TF_SSE_RE.search(block_text):
                findings.append(IaCFinding(
                    file_path=rel_path,
                    resource_type=res_type,
                    check_id="IAC_TF_NO_ENCRYPT",
                    check_name="S3 bucket without server-side encryption",
                    severity="MEDIUM",
                    line=block_line,
                    guideline="Add server_side_encryption_configuration with AES256 or aws:kms.",
                    framework="terraform",
                ))

        # 2. IAC_TF_OPEN_SG (security group open to 0.0.0.0/0 on non-web ports)
        if res_type == "aws_security_group":
            if _TF_OPEN_CIDR_RE.search(block_text):
                # Try to find the port in the ingress block
                port_matches = re.findall(
                    r'(?:from_port|to_port)\s*=\s*(\d+)', block_text
                )
                ports = {int(p) for p in port_matches}
                non_web = ports - _WEB_PORTS
                if non_web or not ports:
                    m = _TF_OPEN_CIDR_RE.search(block_text)
                    line_offset = block_text[:m.start()].count("\n") if m else 0
                    findings.append(IaCFinding(
                        file_path=rel_path,
                        resource_type=res_type,
                        check_id="IAC_TF_OPEN_SG",
                        check_name="Security group open to 0.0.0.0/0 on non-web port",
                        severity="HIGH",
                        line=block_line + line_offset,
                        guideline="Restrict cidr_blocks to specific IP ranges for non-HTTP(S) ports.",
                        framework="terraform",
                    ))

        # 5. IAC_TF_PUBLIC_RDS
        if res_type == "aws_db_instance":
            m = _TF_PUBLICLY_ACCESSIBLE_RE.search(block_text)
            if m:
                findings.append(IaCFinding(
                    file_path=rel_path,
                    resource_type=res_type,
                    check_id="IAC_TF_PUBLIC_RDS",
                    check_name="RDS instance publicly accessible",
                    severity="CRITICAL",
                    line=block_line + block_text[:m.start()].count("\n"),
                    guideline="Set publicly_accessible = false and use VPC/private subnets.",
                    framework="terraform",
                ))

    # --- Variable block checks (hardcoded secrets) ---
    # 4. IAC_TF_HARDCODED_SECRET
    for var_match in _TF_SENSITIVE_VAR_NAMES.finditer(content):
        var_block_text, _ = _extract_tf_block(content, var_match.end())
        default_match = _TF_VARIABLE_DEFAULT_RE.search(var_block_text)
        if default_match:
            default_val = default_match.group(1)
            # Skip empty defaults and obvious placeholders
            if default_val and default_val not in ("", "CHANGE_ME", "TODO", "null"):
                findings.append(IaCFinding(
                    file_path=rel_path,
                    resource_type="variable",
                    check_id="IAC_TF_HARDCODED_SECRET",
                    check_name="Hardcoded secret in variable default",
                    severity="HIGH",
                    line=_line_number_at(content, var_match.start()),
                    guideline="Remove default value; pass secrets via environment or vault.",
                    framework="terraform",
                ))

    return findings


# ---------------------------------------------------------------------------
# Kubernetes YAML checks
# ---------------------------------------------------------------------------

def _parse_yaml_simple_lines(content: str) -> list[dict]:
    """Minimal line-based YAML 'parser' for Kubernetes manifests.

    Returns a list of (key_path, value, line_number) tuples accumulated
    during a line-by-line scan. Not a real parser — just enough to detect
    security-relevant patterns in typical K8s YAML.
    """
    entries: list[dict] = []
    indent_stack: list[tuple[int, str]] = []  # (indent, key)

    for line_num, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # Pop indent stack to current level
        while indent_stack and indent_stack[-1][0] >= indent:
            indent_stack.pop()

        # Handle list items
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()

        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            path_parts = [s[1] for s in indent_stack] + [key]
            path = ".".join(path_parts)

            entries.append({
                "path": path,
                "key": key,
                "value": value,
                "line": line_num,
                "indent": indent,
            })

            if not value:
                indent_stack.append((indent, key))

    return entries


def _check_kubernetes(file_path: str, content: str) -> list[IaCFinding]:
    """Run Kubernetes security checks on file content."""
    findings: list[IaCFinding] = []
    rel_path = file_path

    # Split multi-document YAML
    documents = re.split(r'^---\s*$', content, flags=re.MULTILINE)

    for doc in documents:
        if not doc.strip():
            continue

        entries = _parse_yaml_simple_lines(doc)
        if not entries:
            continue

        # Find kind
        kind = ""
        kind_line = 0
        for e in entries:
            if e["key"] == "kind" and e["value"] in _K8S_WORKLOAD_KINDS:
                kind = e["value"]
                kind_line = e["line"]
                break

        if not kind:
            continue

        # Build quick lookup sets
        has_run_as_non_root = False
        has_resource_limits = False
        has_privileged = False
        has_host_network = False
        has_host_path = False
        images: list[tuple[str, int]] = []

        for e in entries:
            path_lower = e["path"].lower()
            val = e["value"]
            key = e["key"]

            # IAC_K8S_RUN_ROOT
            if key == "runAsNonRoot" and val.lower() == "true":
                has_run_as_non_root = True

            # IAC_K8S_PRIVILEGED
            if key == "privileged" and val.lower() == "true":
                has_privileged = True
                findings.append(IaCFinding(
                    file_path=rel_path,
                    resource_type=kind,
                    check_id="IAC_K8S_PRIVILEGED",
                    check_name="Container running in privileged mode",
                    severity="CRITICAL",
                    line=e["line"],
                    guideline="Remove privileged: true; use specific capabilities instead.",
                    framework="kubernetes",
                ))

            # IAC_K8S_NO_LIMITS — check for limits key
            if "limits" in path_lower and key in ("cpu", "memory"):
                has_resource_limits = True

            # IAC_K8S_HOST_NETWORK
            if key == "hostNetwork" and val.lower() == "true":
                has_host_network = True
                findings.append(IaCFinding(
                    file_path=rel_path,
                    resource_type=kind,
                    check_id="IAC_K8S_HOST_NETWORK",
                    check_name="Pod using host network",
                    severity="HIGH",
                    line=e["line"],
                    guideline="Remove hostNetwork: true; use NetworkPolicies instead.",
                    framework="kubernetes",
                ))

            # IAC_K8S_HOST_PATH
            if key == "hostPath":
                has_host_path = True
                findings.append(IaCFinding(
                    file_path=rel_path,
                    resource_type=kind,
                    check_id="IAC_K8S_HOST_PATH",
                    check_name="Volume using hostPath mount",
                    severity="MEDIUM",
                    line=e["line"],
                    guideline="Use PersistentVolumeClaims instead of hostPath.",
                    framework="kubernetes",
                ))

            # Collect images for tag check
            if key == "image" and val:
                images.append((val, e["line"]))

        # IAC_K8S_RUN_ROOT (absence check)
        if not has_run_as_non_root:
            findings.append(IaCFinding(
                file_path=rel_path,
                resource_type=kind,
                check_id="IAC_K8S_RUN_ROOT",
                check_name="Container may run as root (runAsNonRoot not set)",
                severity="HIGH",
                line=kind_line,
                guideline="Set securityContext.runAsNonRoot: true.",
                framework="kubernetes",
            ))

        # IAC_K8S_NO_LIMITS (absence check)
        if not has_resource_limits:
            findings.append(IaCFinding(
                file_path=rel_path,
                resource_type=kind,
                check_id="IAC_K8S_NO_LIMITS",
                check_name="Container without resource limits",
                severity="MEDIUM",
                line=kind_line,
                guideline="Set resources.limits.cpu and resources.limits.memory.",
                framework="kubernetes",
            ))

        # IAC_K8S_LATEST_TAG
        for image_ref, img_line in images:
            # Remove quotes
            image_ref = image_ref.strip("'\"")
            if image_ref.endswith(":latest") or ":" not in image_ref.split("/")[-1]:
                findings.append(IaCFinding(
                    file_path=rel_path,
                    resource_type=kind,
                    check_id="IAC_K8S_LATEST_TAG",
                    check_name="Image using :latest or no tag",
                    severity="MEDIUM",
                    line=img_line,
                    guideline="Pin image to a specific version tag or digest.",
                    framework="kubernetes",
                ))

    return findings


# ---------------------------------------------------------------------------
# Docker Compose checks
# ---------------------------------------------------------------------------

def _check_docker_compose(file_path: str, content: str) -> list[IaCFinding]:
    """Run Docker Compose security checks on file content."""
    findings: list[IaCFinding] = []
    rel_path = file_path

    entries = _parse_yaml_simple_lines(content)
    if not entries:
        return findings

    # Identify service blocks: under "services:" key
    # We look for the pattern: services.<name>.<property>
    service_names: set[str] = set()
    service_lines: dict[str, int] = {}
    service_has_limits: dict[str, bool] = {}

    in_services = False
    services_indent = -1

    for e in entries:
        path = e["path"]
        key = e["key"]
        val = e["value"]
        line = e["line"]

        # Detect top-level "services" key
        if key == "services" and e["indent"] == 0:
            in_services = True
            services_indent = e["indent"]
            continue

        if not in_services:
            continue

        path_parts = path.split(".")

        # Detect service names (direct children of services)
        if len(path_parts) >= 2 and path_parts[0] == "services":
            svc = path_parts[1]
            if svc not in service_names:
                service_names.add(svc)
                service_lines[svc] = line
                service_has_limits[svc] = False

        # IAC_DC_PRIVILEGED
        if key == "privileged" and val.lower() == "true":
            findings.append(IaCFinding(
                file_path=rel_path,
                resource_type="docker-compose",
                check_id="IAC_DC_PRIVILEGED",
                check_name="Service running in privileged mode",
                severity="CRITICAL",
                line=line,
                guideline="Remove privileged: true; use cap_add for specific capabilities.",
                framework="docker_compose",
            ))

        # Track resource limits
        if key in ("mem_limit", "memory") and "limits" in path.lower():
            for svc in service_names:
                if svc in path:
                    service_has_limits[svc] = True
        if key == "mem_limit":
            for svc in service_names:
                if svc in path:
                    service_has_limits[svc] = True

    # --- Port-based checks (need to parse port mappings) ---
    # Look for lines like: - "3306:3306" or - 5432:5432
    port_re = re.compile(r'^\s*-\s*["\']?(\d+)(?::(\d+))?["\']?\s*$')
    in_ports = False
    current_svc_for_ports = ""

    for line_num, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()

        # Detect "ports:" section
        if stripped == "ports:":
            in_ports = True
            # Find which service this belongs to by looking at indent context
            indent = len(raw_line) - len(raw_line.lstrip())
            # Walk backwards to find service name
            for e in reversed(entries):
                if e["line"] < line_num and e["indent"] < indent:
                    path_parts = e["path"].split(".")
                    if len(path_parts) >= 2 and path_parts[0] == "services":
                        current_svc_for_ports = path_parts[1]
                    break
            continue

        if in_ports:
            if stripped.startswith("- "):
                m = port_re.match(raw_line)
                if m:
                    host_port = int(m.group(1))
                    if host_port in _SENSITIVE_PORTS:
                        findings.append(IaCFinding(
                            file_path=rel_path,
                            resource_type="docker-compose",
                            check_id="IAC_DC_SENSITIVE_PORT",
                            check_name=f"Sensitive port {host_port} exposed",
                            severity="MEDIUM",
                            line=line_num,
                            guideline="Avoid exposing sensitive service ports directly; use internal networks.",
                            framework="docker_compose",
                        ))
            elif stripped and not stripped.startswith("#"):
                in_ports = False

    # IAC_DC_NO_LIMITS (absence check)
    # Also scan raw content for mem_limit at service level
    for line_num, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("mem_limit"):
            for svc in service_names:
                service_has_limits[svc] = True

    # Check for deploy.resources.limits pattern
    deploy_limits_re = re.compile(r'(?:mem_limit|memory\s*:)', re.IGNORECASE)
    if deploy_limits_re.search(content):
        # At least some limit exists; mark all services that have it
        # (simplified: we already tracked above)
        pass

    for svc, has_limits in service_has_limits.items():
        if not has_limits:
            findings.append(IaCFinding(
                file_path=rel_path,
                resource_type="docker-compose",
                check_id="IAC_DC_NO_LIMITS",
                check_name=f"Service '{svc}' without memory limits",
                severity="LOW",
                line=service_lines.get(svc, 0),
                guideline="Add mem_limit or deploy.resources.limits.memory.",
                framework="docker_compose",
            ))

    return findings


# ---------------------------------------------------------------------------
# File detection and routing
# ---------------------------------------------------------------------------

def _is_k8s_yaml(file_path: Path, content: str) -> bool:
    """Heuristic: is this YAML file likely a Kubernetes manifest?"""
    # Check directory names
    for part in file_path.parts:
        if part.lower() in _K8S_DIR_NAMES:
            return True

    # Check content for K8s markers
    if re.search(r'^apiVersion\s*:', content, re.MULTILINE):
        return True
    if re.search(r'^kind\s*:\s*(?:' + "|".join(_K8S_WORKLOAD_KINDS) + r')', content, re.MULTILINE):
        return True

    return False


def _is_docker_compose(file_name: str) -> bool:
    """Check if filename matches docker-compose pattern."""
    return bool(re.match(r'^docker-compose[.\-\w]*\.ya?ml$', file_name, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Main scan function
# ---------------------------------------------------------------------------

def scan_iac(project_path: str) -> IaCScanResult:
    """Scan project for IaC security issues.

    Walks the directory tree (skipping common non-project dirs), detects
    Terraform, Kubernetes, and Docker Compose files, and runs the
    appropriate security checks on each.

    Args:
        project_path: Root directory to scan.

    Returns:
        IaCScanResult with all findings aggregated.
    """
    root = Path(project_path).resolve()
    result = IaCScanResult()
    frameworks: set[str] = set()

    # Try loading custom rules (currently used for future extensibility)
    _load_iac_rules()

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped directories
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for fname in filenames:
            file_path = Path(dirpath) / fname
            try:
                rel_path = str(file_path.relative_to(root))
            except ValueError:
                rel_path = str(file_path)

            # Normalize path separators
            rel_path = rel_path.replace("\\", "/")

            try:
                # --- Terraform ---
                if fname.endswith(".tf"):
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    result.total_files_scanned += 1
                    tf_findings = _check_terraform(rel_path, content)
                    if tf_findings:
                        frameworks.add("terraform")
                        result.findings.extend(tf_findings)
                    continue

                # --- Docker Compose (check before generic YAML) ---
                if _is_docker_compose(fname):
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    result.total_files_scanned += 1
                    dc_findings = _check_docker_compose(rel_path, content)
                    if dc_findings:
                        frameworks.add("docker_compose")
                        result.findings.extend(dc_findings)
                    continue

                # --- Kubernetes YAML ---
                if fname.endswith((".yaml", ".yml")):
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    if _is_k8s_yaml(file_path, content):
                        result.total_files_scanned += 1
                        k8s_findings = _check_kubernetes(rel_path, content)
                        if k8s_findings:
                            frameworks.add("kubernetes")
                            result.findings.extend(k8s_findings)
                    continue

            except OSError as e:
                logger.debug("Could not read %s: %s", file_path, e)
                continue

    # Tally severity counts
    for f in result.findings:
        sev = f.severity.upper()
        if sev == "CRITICAL":
            result.critical += 1
        elif sev == "HIGH":
            result.high += 1
        elif sev == "MEDIUM":
            result.medium += 1
        elif sev == "LOW":
            result.low += 1

    result.total_findings = len(result.findings)
    result.frameworks_detected = sorted(frameworks)

    logger.info(
        "IaC scan complete: %d files, %d findings (%d critical, %d high) across %s",
        result.total_files_scanned,
        result.total_findings,
        result.critical,
        result.high,
        result.frameworks_detected,
    )

    return result


def scan_iac_to_dict(project_path: str) -> dict:
    """Return IaC scan results as a dict suitable for JSON serialization.

    Converts the dataclass result into a plain dict matching the
    structure that flyto-engine expects.
    """
    result = scan_iac(project_path)
    return {
        "total_files_scanned": result.total_files_scanned,
        "total_findings": result.total_findings,
        "severity_counts": {
            "critical": result.critical,
            "high": result.high,
            "medium": result.medium,
            "low": result.low,
        },
        "frameworks_detected": result.frameworks_detected,
        "findings": [
            {
                "file_path": f.file_path,
                "resource_type": f.resource_type,
                "check_id": f.check_id,
                "check_name": f.check_name,
                "severity": f.severity,
                "line": f.line,
                "guideline": f.guideline,
                "framework": f.framework,
            }
            for f in result.findings
        ],
    }
