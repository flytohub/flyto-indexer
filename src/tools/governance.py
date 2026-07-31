"""Lightweight, opt-in task governance for atomic changes and documentation."""

from __future__ import annotations

import fnmatch
from datetime import date
from pathlib import Path
from typing import Any

try:
    from ..finding_identity import finding_evidence, suppression_provenance
except ImportError:
    from finding_identity import finding_evidence, suppression_provenance

GOVERNANCE_VERSION = "governance.v1"
VALID_MODES = {"advisory", "guarded", "strict"}

_GUARDED_BLOCKS = {
    "dependency_cycle",
    "forbidden_layer_edge",
    "public_contract_missing_docs",
    "public_contract_missing_migration",
    "public_contract_missing_tests",
    "unrelated_change_mix",
}
_STRICT_BLOCKS = _GUARDED_BLOCKS | {
    "architecture_docs_missing",
    "behavior_docs_missing",
    "deployment_docs_missing",
    "security_docs_missing",
}

_DOC_PATHS = {
    "api_reference": ["docs/api/**", "docs/reference/**", "README.md"],
    "architecture_or_adr": ["ARCHITECTURE.md", "DECISIONS.md", "docs/adr/**"],
    "deployment_runbook": ["docs/deployment/**", "docs/runbooks/**"],
    "migration": ["migrations/**", "docs/migrations/**"],
    "readme_or_changelog": ["README.md", "CHANGELOG.md"],
    "security_or_runbook": ["SECURITY.md", "docs/security/**", "docs/runbooks/**"],
}


def _project_root(project: str | None) -> Path:
    if project:
        candidate = Path(project).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    try:
        from ..index_store import load_index
    except ImportError:
        from index_store import load_index

    index = load_index()
    root = (index.get("project_roots") or {}).get(project or "")
    if root and Path(root).is_dir():
        return Path(root).resolve()
    return Path.cwd().resolve()


def _load_project_policy(project: str | None) -> dict:
    try:
        from ..analyzer.rules import load_rules
    except ImportError:
        from analyzer.rules import load_rules

    try:
        return load_rules(_project_root(project)) or {}
    except (OSError, RuntimeError, ValueError):
        return {}


def _normalize_waivers(entries: Any, *, today: date | None = None) -> dict:
    current = today or date.today()
    valid = []
    invalid = []
    for raw in entries if isinstance(entries, list) else []:
        entry = dict(raw) if isinstance(raw, dict) else {}
        checks = entry.get("checks")
        paths = entry.get("paths")
        rationale = str(entry.get("rationale") or "").strip()
        expires = str(entry.get("expires") or "").strip()
        owner = str(entry.get("owner") or "").strip()
        reasons = []
        if not str(entry.get("id") or "").strip():
            reasons.append("missing_id")
        if not isinstance(checks, list) or not checks:
            reasons.append("missing_checks")
        if not isinstance(paths, list) or not paths:
            reasons.append("missing_paths")
        if not rationale:
            reasons.append("missing_rationale")
        if not owner:
            reasons.append("missing_owner")
        try:
            expiry = date.fromisoformat(expires)
        except ValueError:
            reasons.append("invalid_expiry")
            expiry = None
        if expiry and expiry < current:
            reasons.append("expired")
        normalized = {
            "id": str(entry.get("id") or ""),
            "checks": [str(item) for item in checks or []],
            "paths": [str(item) for item in paths or []],
            "rationale": rationale,
            "expires": expires,
            "owner": owner,
        }
        if reasons:
            invalid.append({**normalized, "reasons": reasons})
        else:
            valid.append(normalized)
    return {"valid": valid, "invalid": invalid}


def load_governance_policy(
    project: str | None,
    options: dict | None = None,
) -> dict:
    """Load the project governance block with conservative defaults."""
    configured = (_load_project_policy(project).get("governance") or {})
    override = (options or {}).get("governance") or {}
    if not isinstance(configured, dict):
        configured = {}
    if not isinstance(override, dict):
        override = {}
    merged = {**configured, **override}
    mode = str(merged.get("mode") or "advisory").casefold()
    errors = []
    if mode not in VALID_MODES:
        errors.append(f"invalid_mode:{mode}")
        mode = "advisory"
    return {
        "mode": mode,
        "atomicity_enabled": (merged.get("atomicity") or {}).get("enabled", True),
        "documentation_enabled": (
            (merged.get("documentation") or {}).get("change_aware", True)
        ),
        "waivers": _normalize_waivers(merged.get("waivers")),
        "config_errors": errors,
    }


def _normalized_paths(targets: list[str], resolved: list[dict]) -> list[str]:
    paths = []
    for item in resolved:
        path = str(item.get("path") or item.get("input") or "").replace("\\", "/")
        if path and path not in paths:
            paths.append(path)
    for target in targets:
        path = str(target).replace("\\", "/")
        if path and path not in paths:
            paths.append(path)
    return paths


def _responsibility(path: str) -> str | None:
    normalized = path.casefold().lstrip("./")
    if normalized.startswith(("tests/", "test/")):
        return None
    if normalized.startswith("docs/") or normalized in {
        "readme.md", "changelog.md", "security.md", "architecture.md", "decisions.md",
    }:
        return None
    if normalized == "dockerfile" or normalized.startswith((".github/", "deploy/", "infra/")):
        return "deployment"
    if normalized.endswith(".flyto-rules.yaml") or "architecture" in normalized:
        return "architecture-policy"
    parts = normalized.split("/")
    if len(parts) >= 3 and parts[0] == "src":
        return f"src/{parts[1]}"
    if len(parts) >= 2:
        return parts[0]
    return "project-root"


def _dependency_groups(
    responsibilities: list[str],
    resolved_targets: list[dict],
) -> list[list[str]]:
    """Keep directly dependent responsibilities in the same atomic group."""
    parents = {item: item for item in responsibilities}

    def find(item: str) -> str:
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    target_paths = {
        str(item.get("path") or "").replace("\\", "/")
        for item in resolved_targets
        if item.get("path")
    }
    path_responsibilities = {
        path: _responsibility(path) for path in target_paths
    }
    if len(parents) > 1 and target_paths:
        try:
            from ..index_store import load_index
        except ImportError:
            from index_store import load_index

        index = load_index()
        symbols = index.get("symbols") or {}
        for dependency in (index.get("dependencies") or {}).values():
            source_path = (symbols.get(dependency.get("source")) or {}).get("path")
            target_path = (symbols.get(dependency.get("target")) or {}).get("path")
            if source_path not in target_paths or target_path not in target_paths:
                continue
            left = path_responsibilities.get(source_path)
            right = path_responsibilities.get(target_path)
            if left in parents and right in parents:
                union(left, right)
    groups: dict[str, list[str]] = {}
    for responsibility in responsibilities:
        groups.setdefault(find(responsibility), []).append(responsibility)
    return sorted(
        (sorted(group) for group in groups.values()),
        key=lambda group: group[0],
    )


def _change_signals(paths: list[str], description: str = "") -> list[str]:
    signals = set()
    text = f" {description.casefold()} "
    for raw_path in paths:
        path = raw_path.casefold().lstrip("./")
        parts = set(path.replace("-", "_").split("/"))
        name = Path(path).name
        if parts & {"api", "apis", "routes", "controllers", "openapi", "proto"}:
            signals.add("public_contract")
        if parts & {"schema", "schemas", "migrations"} or name in {
            "schema.py", "models.py", "openapi.json", "openapi.yaml",
        }:
            signals.add("schema")
        if (
            path.startswith(("frontend/", "web/", "ui/"))
            or path in {"src/cli.py", "src/mcp_server.py"}
        ):
            signals.add("user_behavior")
        if (
            name in {"architecture.md", "decisions.md", ".flyto-rules.yaml"}
            or parts & {"architecture", "layers"}
        ):
            signals.add("architecture")
        if parts & {
            "auth", "rbac", "security", "secrets", "taint", "permissions", "permission",
        }:
            signals.add("security")
        if (
            name == "dockerfile"
            or path.startswith((".github/", "deploy/", "infra/", "k8s/"))
        ):
            signals.add("deployment")
    keyword_signals = {
        "public_contract": (" public api ", " openapi ", " public contract "),
        "schema": (
            " database schema ",
            " persistence schema ",
            " persistent schema ",
            " schema migration ",
            " data migration ",
        ),
        "user_behavior": (" user-visible ", " user behavior ", " cli output ", " mcp output "),
        "architecture": (" architecture boundary ", " layer boundary "),
        "security": (" security posture ", " authorization ", " authentication "),
        "deployment": (" deployment ", " container image "),
    }
    for signal, needles in keyword_signals.items():
        if any(needle in text for needle in needles):
            signals.add(signal)
    return sorted(signals)


def _documentation(signals: list[str], enabled: bool) -> dict:
    kinds = set()
    mapping = {
        "architecture": {"architecture_or_adr"},
        "deployment": {"deployment_runbook"},
        "public_contract": {"api_reference"},
        "schema": {"migration"},
        "security": {"security_or_runbook"},
        "user_behavior": {"readme_or_changelog"},
    }
    if enabled:
        for signal in signals:
            kinds.update(mapping.get(signal, set()))
    return {
        "required": bool(kinds),
        "signals": signals,
        "required_kinds": sorted(kinds),
        "suggested_paths": {
            kind: _DOC_PATHS[kind] for kind in sorted(kinds)
        },
    }


def evaluate_task_governance(
    *,
    description: str,
    targets: list[str],
    resolved_targets: list[dict],
    project: str | None,
    options: dict | None = None,
) -> dict:
    """Build a compact governance contract from repository evidence."""
    policy = load_governance_policy(project, options)
    paths = _normalized_paths(targets, resolved_targets)
    responsibilities = sorted({
        responsibility
        for path in paths
        if (responsibility := _responsibility(path))
    })
    dependency_groups = _dependency_groups(responsibilities, resolved_targets)
    needs_atomic_split = (
        policy["atomicity_enabled"] and len(dependency_groups) > 1
    )
    signals = _change_signals(paths, description)
    return {
        "version": GOVERNANCE_VERSION,
        "mode": policy["mode"],
        "enforcement": "recommend" if policy["mode"] == "advisory" else "gate",
        "atomicity": {
            "basis": "responsibility_dependency",
            "responsibilities": responsibilities,
            "dependency_groups": dependency_groups,
            "recommend_split": needs_atomic_split,
            "reason": (
                "Dependency-independent responsibilities should land as "
                "separate reversible changes."
                if needs_atomic_split
                else "One dependency-connected responsibility group; no forced split."
            ),
        },
        "documentation": _documentation(
            signals,
            policy["documentation_enabled"],
        ),
        "waivers": policy["waivers"],
        "config_errors": policy["config_errors"],
    }


def _explicit_findings(state: dict) -> list[dict]:
    findings = []
    for item in state.get("governance_findings") or []:
        if isinstance(item, dict) and item.get("code"):
            findings.append(dict(item))
    aliases = {
        "dependency_cycles": "dependency_cycle",
        "forbidden_layer_edges": "forbidden_layer_edge",
        "unrelated_change_groups": "unrelated_change_mix",
    }
    for key, code in aliases.items():
        values = state.get(key) or []
        if values:
            findings.append({
                "code": code,
                "severity": "high",
                "paths": state.get("changed_paths") or [],
                "evidence": values,
            })
    return findings


def _has_document_kind(paths: list[str], kind: str) -> bool:
    patterns = [pattern.casefold() for pattern in _DOC_PATHS[kind]]
    return any(
        any(
            fnmatch.fnmatchcase(path.casefold().lstrip("./"), pattern)
            for pattern in patterns
        )
        for path in paths
    )


def _auto_findings(paths: list[str], state: dict) -> list[dict]:
    signals = _change_signals(paths)
    has_tests = any(
        path.casefold().startswith(("tests/", "test/"))
        or Path(path).name.casefold().startswith("test_")
        for path in paths
    )
    findings = []

    def add(code: str, severity: str = "medium") -> None:
        findings.append({"code": code, "severity": severity, "paths": paths})

    if "public_contract" in signals:
        if not has_tests:
            add("public_contract_missing_tests", "high")
        if not _has_document_kind(paths, "api_reference"):
            add("public_contract_missing_docs", "high")
    if "schema" in signals and state.get("migration_required"):
        if not _has_document_kind(paths, "migration"):
            add("public_contract_missing_migration", "high")
    if (
        "architecture" in signals
        and not _has_document_kind(paths, "architecture_or_adr")
    ):
        add("architecture_docs_missing")
    if (
        "user_behavior" in signals
        and not _has_document_kind(paths, "readme_or_changelog")
    ):
        add("behavior_docs_missing")
    if (
        "security" in signals
        and not _has_document_kind(paths, "security_or_runbook")
    ):
        add("security_docs_missing")
    if (
        "deployment" in signals
        and not _has_document_kind(paths, "deployment_runbook")
    ):
        add("deployment_docs_missing")
    return findings


def _waives(finding: dict, waiver: dict) -> bool:
    if finding.get("code") not in waiver.get("checks", []):
        return False
    finding_paths = finding.get("paths") or []
    return bool(finding_paths) and all(
        any(fnmatch.fnmatchcase(path, pattern) for pattern in waiver.get("paths", []))
        for path in finding_paths
    )


def _finding_with_evidence(finding: dict, waiver: dict | None = None) -> dict:
    code = str(finding.get("code") or "governance")
    paths = finding.get("paths") or []
    primary_path = paths[0] if paths else "."
    is_waived = waiver is not None
    evidence = finding_evidence(
        f"governance/{code}",
        primary_path,
        anchor={"code": code, "paths": sorted(paths)},
        confidence="high",
        confidence_basis=["deterministic_diff_policy"],
        trace=[
            {"kind": "changed_path", "path": path}
            for path in paths
        ],
        suppression=suppression_provenance(
            suppressed=is_waived,
            mechanism="waiver" if is_waived else "none",
            rule_id=str(waiver.get("id", "")) if waiver else "",
            reason=str(waiver.get("rationale", "")) if waiver else "",
            source=".flyto-rules.yaml" if waiver else "",
            expires=str(waiver.get("expires", "")) if waiver else "",
            owner=str(waiver.get("owner", "")) if waiver else "",
        ),
        origin="governance.diff",
    )
    result = {**finding, **evidence}
    if waiver:
        result["waiver_id"] = waiver["id"]
    return result


def validate_governance_diff(
    governance: dict | None,
    *,
    changed_paths: list[str] | None,
    state: dict | None = None,
) -> dict:
    """Evaluate deterministic diff findings under the configured mode."""
    contract = governance or {}
    mode = contract.get("mode", "advisory")
    current_state = state or {}
    paths = list(dict.fromkeys(changed_paths or current_state.get("changed_paths") or []))
    findings = _explicit_findings(current_state) + _auto_findings(paths, current_state)
    valid_waivers = (contract.get("waivers") or {}).get("valid") or []
    active = []
    waived = []
    for finding in findings:
        waiver = next(
            (item for item in valid_waivers if _waives(finding, item)),
            None,
        )
        if waiver:
            waived.append(_finding_with_evidence(finding, waiver))
        else:
            active.append(_finding_with_evidence(finding))
    blocking_codes = (
        set()
        if mode == "advisory"
        else _GUARDED_BLOCKS if mode == "guarded" else _STRICT_BLOCKS
    )
    blocking = [item for item in active if item.get("code") in blocking_codes]
    return {
        "pass": not blocking,
        "decision": "pass" if not blocking else "blocked",
        "mode": mode,
        "findings": active,
        "blocking": blocking,
        "waived": waived,
        "invalid_waivers": (contract.get("waivers") or {}).get("invalid") or [],
        "required_actions": [
            f"resolve_governance:{item['code']}" for item in blocking
        ],
    }
