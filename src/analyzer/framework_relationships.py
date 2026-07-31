"""On-demand framework relationship hints for dynamic application wiring."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


RELATIONSHIP_SCHEMA = "framework-relationships.v1"
_SUPPORTED_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".py"}
_MAX_BYTES = 2_000_000


def _line(content: str, position: int) -> int:
    return content.count("\n", 0, position) + 1


def _relationship(
    *,
    path: str,
    kind: str,
    target: str,
    line: int,
    confidence: str,
    basis: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    material = f"{path}\0{kind}\0{target}\0{basis}".encode("utf-8")
    return {
        "relationship_id": "relation-" + hashlib.sha256(material).hexdigest()[:24],
        "kind": kind,
        "source": path,
        "target": target,
        "line": line,
        "confidence": {"level": confidence, "basis": [basis]},
        "metadata": metadata or {},
    }


def _typescript_relationships(content: str, path: str) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for match in re.finditer(
        r"(?:React\s*\.\s*)?lazy\s*\(\s*\(\s*\)\s*=>\s*import\s*\(\s*['\"]([^'\"]+)['\"]",
        content,
    ):
        relationships.append(_relationship(
            path=path,
            kind="react_lazy_import",
            target=match.group(1),
            line=_line(content, match.start()),
            confidence="high",
            basis="literal_lazy_import",
        ))
    for match in re.finditer(
        r"import\s*\.\s*meta\s*\.\s*glob(?:Eager)?\s*\(\s*['\"]([^'\"]+)['\"]",
        content,
    ):
        relationships.append(_relationship(
            path=path,
            kind="dynamic_import_glob",
            target=match.group(1),
            line=_line(content, match.start()),
            confidence="medium",
            basis="literal_import_meta_glob",
            metadata={"resolution": "pattern_requires_expansion"},
        ))
    for match in re.finditer(
        r"\b(?:app|router|server)\s*\.\s*use\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([A-Za-z_$][\w$]*)",
        content,
    ):
        relationships.append(_relationship(
            path=path,
            kind="route_mount",
            target=match.group(2),
            line=_line(content, match.start()),
            confidence="high",
            basis="literal_router_mount",
            metadata={"path_prefix": match.group(1)},
        ))
    route_pattern = re.compile(
        r"\b(?:app|router|server)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*"
        r"['\"]([^'\"]+)['\"]\s*,\s*([^\n;]+)",
        re.IGNORECASE,
    )
    for match in route_pattern.finditer(content):
        arguments = match.group(3)
        guards = re.findall(
            r"\b(?:requirePermission|requireRole|authorize|authGuard|permissionGuard)\s*"
            r"\(\s*['\"]?([\w:.*-]+)?",
            arguments,
            re.IGNORECASE,
        )
        if not guards:
            continue
        relationships.append(_relationship(
            path=path,
            kind="route_authorization",
            target=f"{match.group(1).upper()} {match.group(2)}",
            line=_line(content, match.start()),
            confidence="medium",
            basis="recognized_route_guard",
            metadata={"guards": [guard for guard in guards if guard][:8]},
        ))
    orm_patterns = (
        re.compile(r"\bwhere\s*:\s*\{[^}]*\b(tenantId|tenant_id|organizationId|workspaceId)\b", re.DOTALL),
        re.compile(r"\.\s*where\s*\([^)]*\b(tenantId|tenant_id|organizationId|workspaceId)\b", re.DOTALL),
    )
    for pattern in orm_patterns:
        for match in pattern.finditer(content):
            relationships.append(_relationship(
                path=path,
                kind="orm_tenant_scope",
                target=match.group(1),
                line=_line(content, match.start()),
                confidence="medium",
                basis="recognized_orm_scope_key",
            ))
    return relationships


def _python_relationships(content: str, path: str) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for match in re.finditer(
        r"\.(?:filter|filter_by|where)\s*\([^)]*\b"
        r"(tenant_id|organization_id|workspace_id)\b",
        content,
        re.DOTALL,
    ):
        relationships.append(_relationship(
            path=path,
            kind="orm_tenant_scope",
            target=match.group(1),
            line=_line(content, match.start()),
            confidence="medium",
            basis="recognized_orm_scope_key",
        ))
    for match in re.finditer(
        r"@(?:permission_required|roles_required|requires_permission)\s*\(\s*['\"]([^'\"]+)['\"]",
        content,
    ):
        relationships.append(_relationship(
            path=path,
            kind="route_authorization",
            target=match.group(1),
            line=_line(content, match.start()),
            confidence="high",
            basis="literal_authorization_decorator",
        ))
    return relationships


def _project_file(path: str, project: str | None) -> tuple[Path | None, str]:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate, candidate.name
    try:
        try:
            from ..index_store import load_index
        except ImportError:
            from index_store import load_index
        index = load_index()
        roots = index.get("project_roots") or {}
        root = roots.get(project or "")
        if root:
            return Path(root) / candidate, candidate.as_posix()
    except (OSError, ValueError):
        pass
    return Path.cwd() / candidate, candidate.as_posix()


def analyze_framework_relationships(path: str, project: str | None = None) -> dict[str, Any]:
    """Inspect one requested file only; this never runs on the default scan path."""
    source_path, display_path = _project_file(path, project)
    if not source_path or source_path.suffix.casefold() not in _SUPPORTED_SUFFIXES:
        return {
            "schema": RELATIONSHIP_SCHEMA,
            "status": "not_applicable",
            "relationships": [],
            "performance": "on_demand_only",
        }
    try:
        if not source_path.is_file() or source_path.stat().st_size > _MAX_BYTES:
            return {
                "schema": RELATIONSHIP_SCHEMA,
                "status": "skipped",
                "reason": "missing_or_too_large",
                "relationships": [],
                "performance": "on_demand_only",
            }
        content = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "schema": RELATIONSHIP_SCHEMA,
            "status": "skipped",
            "reason": type(exc).__name__,
            "relationships": [],
            "performance": "on_demand_only",
        }
    if source_path.suffix.casefold() == ".py":
        relationships = _python_relationships(content, display_path)
    else:
        relationships = _typescript_relationships(content, display_path)
    deduplicated = {
        item["relationship_id"]: item
        for item in relationships
    }
    return {
        "schema": RELATIONSHIP_SCHEMA,
        "status": "analyzed",
        "path": display_path,
        "relationship_count": len(deduplicated),
        "relationships": list(deduplicated.values()),
        "limits": [
            "heuristic_edges_do_not_prove_runtime_authorization",
            "dynamic_globs_require_file_expansion_or_runtime_evidence",
        ],
        "performance": "on_demand_only",
    }
