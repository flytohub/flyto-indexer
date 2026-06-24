"""Flyto2 workspace release packet generator.

The packet is deliberately evidence-driven: it records what can be proven from
local repositories and marks missing proof as residual evidence instead of
turning audit intent into a false release claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any

from .flyto2_product_gate import (
    DEFAULT_MANIFEST,
    ProductGateOptions,
    _discover_git_repos,
    _load_json,
    run_product_gate,
)


def _default_evidence_gates_path() -> Path:
    package_dir = Path(__file__).resolve().parent
    candidates = [
        package_dir.parent / "config" / "flyto2" / "evidence-gates.json",
        package_dir / "config" / "flyto2" / "evidence-gates.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_EVIDENCE_GATES = _default_evidence_gates_path()


@dataclass(frozen=True)
class ReleasePacketOptions:
    workspace: Path
    manifest_path: Path = DEFAULT_MANIFEST
    evidence_gate_path: Path = DEFAULT_EVIDENCE_GATES
    health_report_path: Path | None = None
    skip_health: bool = False
    strict_memory: bool = True
    fresh_evidence_dir: Path | None = None
    require_fresh: bool = False
    run_start: datetime | None = None


def _run_git(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _read_package_json(repo: Path) -> dict[str, Any]:
    path = repo / "package.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _detect_package_manager(repo: Path) -> str:
    if (repo / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo / "yarn.lock").exists():
        return "yarn"
    if (repo / "package-lock.json").exists():
        return "npm"
    if (repo / "uv.lock").exists():
        return "uv"
    if (repo / "poetry.lock").exists():
        return "poetry"
    if (repo / "go.mod").exists():
        return "go"
    if (repo / "Cargo.toml").exists():
        return "cargo"
    if (repo / "pubspec.yaml").exists():
        return "flutter/dart"
    if (repo / "pyproject.toml").exists():
        return "python"
    return "unknown"


def _detect_languages(repo: Path) -> list[str]:
    signals = {
        "typescript": ["tsconfig.json", "package.json"],
        "go": ["go.mod"],
        "python": ["pyproject.toml", "requirements.txt"],
        "rust": ["Cargo.toml"],
        "flutter": ["pubspec.yaml"],
    }
    found = [name for name, files in signals.items() if any((repo / file).exists() for file in files)]
    return found or ["unknown"]


def _detect_frameworks(repo: Path, package_json: dict[str, Any]) -> list[str]:
    deps: dict[str, Any] = {}
    for field in ("dependencies", "devDependencies"):
        value = package_json.get(field)
        if isinstance(value, dict):
            deps.update(value)
    frameworks: list[str] = []
    if "next" in deps:
        frameworks.append("Next.js")
    if "vite" in deps or (repo / "vite.config.ts").exists() or (repo / "vite.config.js").exists():
        frameworks.append("Vite")
    if "react" in deps:
        frameworks.append("React")
    if "vue" in deps:
        frameworks.append("Vue")
    if "express" in deps:
        frameworks.append("Express")
    if (repo / "go.mod").exists():
        frameworks.append("Go service/library")
    if (repo / "pyproject.toml").exists():
        frameworks.append("Python package")
    if (repo / "pubspec.yaml").exists():
        frameworks.append("Flutter")
    return sorted(set(frameworks))


def _script_name(package_json: dict[str, Any], candidates: tuple[str, ...]) -> str:
    scripts = package_json.get("scripts")
    if not isinstance(scripts, dict):
        return ""
    for name in candidates:
        if name in scripts:
            return name
    return ""


def _deploy_targets(repo: Path) -> list[str]:
    targets: list[str] = []
    workflows = repo / ".github" / "workflows"
    if workflows.exists() and any(workflows.glob("*.yml")):
        targets.append("github-actions")
    if (repo / "Dockerfile").exists() or any(repo.glob("Dockerfile.*")):
        targets.append("docker")
    if (repo / "docker-compose.yml").exists() or (repo / "compose.yml").exists():
        targets.append("compose")
    if (repo / "charts").exists() or (repo / "helm").exists():
        targets.append("helm")
    if (repo / "wrangler.toml").exists() or (repo / "wrangler.jsonc").exists():
        targets.append("cloudflare")
    if (repo / "firebase.json").exists():
        targets.append("firebase")
    return targets


def _repo_inventory(repo_name: str, repo_path: Path, gate_repo: dict[str, Any]) -> dict[str, Any]:
    package_json = _read_package_json(repo_path)
    status_short = _run_git(repo_path, "status", "--short")
    origin_main = _run_git(repo_path, "rev-parse", "origin/main")
    head = _run_git(repo_path, "rev-parse", "HEAD")
    return {
        "path": str(repo_path),
        "branch": _run_git(repo_path, "branch", "--show-current"),
        "head": head,
        "origin_main": origin_main,
        "origin_main_aligned": bool(head and origin_main and head == origin_main),
        "dirty_files": [line for line in status_short.splitlines() if line],
        "languages": _detect_languages(repo_path),
        "frameworks": _detect_frameworks(repo_path, package_json),
        "package_manager": _detect_package_manager(repo_path),
        "lint_script": _script_name(package_json, ("lint", "check", "typecheck")),
        "test_script": _script_name(package_json, ("test", "test:unit", "unit")),
        "build_script": _script_name(package_json, ("build", "build:prod", "compile")),
        "deploy_targets": _deploy_targets(repo_path),
        "role": gate_repo.get("core_dependency", ""),
        "status": gate_repo.get("status", ""),
        "core": bool(gate_repo.get("core")),
        "product_lines": list(gate_repo.get("product_lines", [])),
        "health": gate_repo.get("health"),
        "health_signal": gate_repo.get("health_signal"),
        "memory": gate_repo.get("memory"),
    }


def _path_exists(workspace: Path, path: str) -> bool:
    return (workspace / path).exists()


def _evidence(paths: list[str], workspace: Path) -> list[dict[str, Any]]:
    return [{"path": path, "exists": _path_exists(workspace, path)} for path in paths]


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_run_start(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        raise ValueError(f"invalid ISO timestamp for run start: {value}")
    return parsed


def _file_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _metadata_timestamp(path: Path) -> datetime | None:
    if path.suffix.lower() != ".json":
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in (
        "run_started_at",
        "generated_at",
        "generatedAt",
        "created_at",
        "createdAt",
        "completed_at",
        "completedAt",
    ):
        value = data.get(key)
        if isinstance(value, str):
            parsed = _parse_iso_datetime(value)
            if parsed:
                return parsed
    return None


def _validate_product_verification_contract(path: Path) -> tuple[bool, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return False, ["root must be an object"]

    errors: list[str] = []
    if data.get("contract") != "warroom.product_verification.v1":
        errors.append("contract must be warroom.product_verification.v1")

    site_graph = data.get("site_graph")
    if not isinstance(site_graph, dict):
        errors.append("site_graph must be an object")
        site_graph = {}
    intents = site_graph.get("intents")
    if not isinstance(intents, (list, dict)) or len(intents) == 0:
        errors.append("site_graph.intents must be non-empty")
    state_graph = site_graph.get("state_graph")
    if not isinstance(state_graph, dict) or len(state_graph) == 0:
        errors.append("site_graph.state_graph must be non-empty")

    scores = data.get("scores")
    if not isinstance(scores, dict):
        errors.append("scores must be an object")
        scores = {}
    for key in (
        "observed_coverage",
        "reachable_coverage",
        "api_ui_consistency",
        "business_logic_confidence",
    ):
        value = scores.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"scores.{key} must be numeric")

    p0_findings = data.get("p0_findings")
    if isinstance(p0_findings, bool) or not isinstance(p0_findings, int) or p0_findings != 0:
        errors.append("p0_findings must be integer 0")

    return not errors, errors


def _validate_public_site_verification_contract(path: Path) -> tuple[bool, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return False, ["root must be an object"]

    errors: list[str] = []
    if data.get("contract") != "flyto2.public_site_verification.v1":
        errors.append("contract must be flyto2.public_site_verification.v1")

    p0_findings = data.get("p0_findings")
    if isinstance(p0_findings, bool) or not isinstance(p0_findings, int) or p0_findings != 0:
        errors.append("p0_findings must be integer 0")

    for key in ("dns_matrix", "tls_matrix", "route_matrix", "browser_matrix"):
        value = data.get(key)
        if not isinstance(value, list) or len(value) == 0:
            errors.append(f"{key} must be a non-empty list")

    seo_geo_matrix = data.get("seo_geo_matrix")
    if not isinstance(seo_geo_matrix, dict) or len(seo_geo_matrix) == 0:
        errors.append("seo_geo_matrix must be a non-empty object")

    scores = data.get("scores")
    if not isinstance(scores, dict):
        errors.append("scores must be an object")
        scores = {}
    for key in ("public_route_readiness", "seo_geo_readiness", "browser_render_readiness"):
        value = scores.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"scores.{key} must be numeric")

    return not errors, errors


def _validate_github_actions_workflows(workflows: Any, prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(workflows, list) or len(workflows) == 0:
        return [f"{prefix} must be a non-empty list"]

    for index, workflow in enumerate(workflows):
        if not isinstance(workflow, dict):
            errors.append(f"{prefix}[{index}] must be an object")
            continue
        if workflow.get("ok") is not True:
            errors.append(f"{prefix}[{index}].ok must be true")
        if workflow.get("status") != "completed":
            errors.append(f"{prefix}[{index}].status must be completed")
        if workflow.get("conclusion") != "success":
            errors.append(f"{prefix}[{index}].conclusion must be success")
        jobs = workflow.get("jobs")
        if not isinstance(jobs, list) or len(jobs) == 0:
            errors.append(f"{prefix}[{index}].jobs must be a non-empty list")
            continue
        successful_jobs = 0
        for job_index, job in enumerate(jobs):
            if not isinstance(job, dict):
                errors.append(f"{prefix}[{index}].jobs[{job_index}] must be an object")
                continue
            status = job.get("status")
            conclusion = job.get("conclusion")
            if status != "completed":
                errors.append(f"{prefix}[{index}].jobs[{job_index}].status must be completed")
            if conclusion == "success":
                successful_jobs += 1
            elif conclusion in {"action_required", "cancelled", "failure", "startup_failure", "timed_out"}:
                errors.append(f"{prefix}[{index}].jobs[{job_index}].conclusion must not be {conclusion}")
        if successful_jobs == 0:
            errors.append(f"{prefix}[{index}].jobs must include at least one successful job")
    return errors


def _validate_github_actions_startup_contract(path: Path) -> tuple[bool, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return False, ["root must be an object"]

    errors: list[str] = []
    schema = data.get("schema")
    if schema not in {
        "flyto-code.github-actions-startup-audit.v1",
        "flyto.workspace-github-actions-startup-audit.v1",
    }:
        errors.append(
            "schema must be flyto-code.github-actions-startup-audit.v1 "
            "or flyto.workspace-github-actions-startup-audit.v1"
        )
    if data.get("ok") is not True:
        errors.append("ok must be true")

    if schema == "flyto.workspace-github-actions-startup-audit.v1":
        repositories = data.get("repositories")
        if not isinstance(repositories, list) or len(repositories) == 0:
            errors.append("repositories must be a non-empty list")
            repositories = []
        for repo_index, repository in enumerate(repositories):
            if not isinstance(repository, dict):
                errors.append(f"repositories[{repo_index}] must be an object")
                continue
            if not isinstance(repository.get("repo"), str) or not repository.get("repo"):
                errors.append(f"repositories[{repo_index}].repo must be a non-empty string")
            if not isinstance(repository.get("head"), str) or not repository.get("head"):
                errors.append(f"repositories[{repo_index}].head must be a non-empty string")
            if repository.get("ok") is not True:
                errors.append(f"repositories[{repo_index}].ok must be true")
            errors.extend(
                _validate_github_actions_workflows(
                    repository.get("workflows"),
                    f"repositories[{repo_index}].workflows",
                )
            )
    else:
        errors.extend(_validate_github_actions_workflows(data.get("workflows"), "workflows"))

    return not errors, errors


def _fresh_finding_counts(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    counts: dict[str, int] = {}
    for severity in ("p0", "p1", "p2"):
        key = f"{severity}_findings"
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            counts[severity.upper()] = value

    findings = data.get("findings")
    if isinstance(findings, list):
        inferred: dict[str, int] = {}
        for item in findings:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity") or "").upper()
            if severity in {"P0", "P1", "P2"}:
                inferred[severity] = inferred.get(severity, 0) + 1
        for severity, count in inferred.items():
            counts.setdefault(severity, count)
    return counts


def _fresh_contract_status(relative: str, path: Path, contract: str | None) -> dict[str, Any]:
    if not contract:
        return {}
    finding_counts = _fresh_finding_counts(path)
    blocking_findings = [
        {"severity": severity, "count": count}
        for severity, count in sorted(finding_counts.items())
        if severity in {"P0", "P1"} and count > 0
    ]
    if contract == "warroom.product_verification.v1":
        valid, errors = _validate_product_verification_contract(path)
        return {
            "contract": contract,
            "contract_valid": valid,
            "contract_errors": errors,
            "contract_finding_counts": finding_counts,
            "contract_blocking_findings": blocking_findings,
        }
    if contract == "flyto2.public_site_verification.v1":
        valid, errors = _validate_public_site_verification_contract(path)
        return {
            "contract": contract,
            "contract_valid": valid,
            "contract_errors": errors,
            "contract_finding_counts": finding_counts,
            "contract_blocking_findings": blocking_findings,
        }
    if contract == "flyto.github_actions_startup.v1":
        valid, errors = _validate_github_actions_startup_contract(path)
        return {
            "contract": contract,
            "contract_valid": valid,
            "contract_errors": errors,
            "contract_finding_counts": finding_counts,
            "contract_blocking_findings": blocking_findings,
        }
    return {
        "contract": contract,
        "contract_valid": False,
        "contract_errors": [f"unknown fresh evidence contract for {relative}: {contract}"],
        "contract_finding_counts": finding_counts,
        "contract_blocking_findings": blocking_findings,
    }


def _fresh_evidence(
    paths: list[str],
    evidence_dir: Path | None,
    run_start: datetime | None,
    contracts: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for relative in paths:
        contract = (contracts or {}).get(relative)
        if evidence_dir is None:
            results.append({
                "path": relative,
                "exists": False,
                "fresh": False,
                "reason": "fresh evidence directory was not provided",
                **({"contract": contract, "contract_valid": False} if contract else {}),
            })
            continue
        path = evidence_dir / relative
        exists = path.exists()
        timestamp = _metadata_timestamp(path) if exists else None
        timestamp_source = "metadata"
        if timestamp is None and exists:
            timestamp = _file_mtime(path)
            timestamp_source = "mtime"
        contract_status = _fresh_contract_status(relative, path, contract) if exists else {}
        contract_valid = contract_status.get("contract_valid", True)
        contract_blocking_findings = contract_status.get("contract_blocking_findings") or []
        fresh = bool(
            exists
            and contract_valid
            and not contract_blocking_findings
            and (run_start is None or (timestamp is not None and timestamp >= run_start))
        )
        reason = ""
        if not exists:
            reason = "missing"
        elif not contract_valid:
            reason = "invalid_contract"
        elif contract_blocking_findings:
            reason = "blocking_findings"
        elif run_start is not None and not fresh:
            reason = "stale"
        results.append({
            "path": relative,
            "exists": exists,
            "fresh": fresh,
            "timestamp": timestamp.isoformat() if timestamp else "",
            "timestamp_source": timestamp_source if timestamp else "",
            "reason": reason,
            **contract_status,
        })
    return results


def _deliverable_specs() -> list[dict[str, Any]]:
    return [
        {
            "id": "workspace_inventory",
            "title": "25-project workspace inventory",
            "severity": "P1",
            "required": [],
            "packet_generated": True,
            "fresh": ["workspace-matrix.json", "workspace-matrix.md"],
        },
        {
            "id": "architecture_dependency_map",
            "title": "Architecture / dependency map",
            "severity": "P1",
            "required": [
                "flyto-cloud/docs/architecture-map.md",
                "flyto-code/docs/architecture-map.md",
                "flyto-core/docs/architecture-map.md",
                "flyto-engine/docs/architecture-map.md",
                "flyto-indexer/docs/architecture-map.md",
                "flyto-ai/docs/architecture-map.md",
            ],
            "fresh": ["architecture-map.md"],
        },
        {
            "id": "billing_entitlement_audit",
            "title": "SaaS billing + entitlement audit",
            "severity": "P1",
            "required": [
                "flyto-engine/api/handlers_billing.go",
                "flyto-engine/api/handlers_entitlement.go",
                "flyto-engine/api/handlers_capabilities_rbac_test.go",
                "flyto-engine/internal/billing/billing_test.go",
            ],
            "fresh": ["billing-entitlement.md"],
        },
        {
            "id": "rbac_tenant_isolation_audit",
            "title": "RBAC / tenant isolation audit",
            "severity": "P1",
            "required": [
                "flyto-engine/api/handlers_rbac_cross_org_test.go",
                "flyto-engine/internal/store/rbac_cross_org_resolver_test.go",
                "flyto-engine/internal/store/sql_code_entitlement_guard_test.go",
            ],
            "fresh": ["rbac-tenant-isolation.md"],
        },
        {
            "id": "product_state_machine_audit",
            "title": "Product state machine audit",
            "severity": "P1",
            "required": [
                "flyto-code/src-next/configs/__tests__/navigationFeatureCheck.test.ts",
                "flyto-code/src-next/components/atoms/__tests__/GatedButton.test.tsx",
                "flyto-code/scripts/audit-data-readiness-boundaries.mjs",
            ],
            "fresh": ["state-machine.md"],
        },
        {
            "id": "deterministic_product_verification",
            "title": "Deterministic Product Verification gate",
            "severity": "P1",
            "required": [
                "flyto-core/src/recipes/warroom-deterministic-audit.yaml",
                "flyto-core/tests/modules/test_warroom_modules.py",
                "flyto-engine/api/handlers_warroom_verification.go",
                "flyto-engine/api/handlers_workflow_test.go",
                "flyto-engine/internal/permission/capabilities_commercial_test.go",
                "flyto-code/src-next/components/compounds/product-verification/ProductVerificationView.tsx",
                "flyto-code/src-next/lib/engine/code/warroomVerification.ts",
                "flyto-cloud/src/ui/web/backend/data/recipe_bundles/flyto2-warroom-smoke.yaml",
                "flyto-cloud/src/ui/web/backend/tests/unit/test_recipe_bundles.py",
                "flyto-cloud/docs/warroom-recipe-bundle-closure.md",
            ],
            "fresh": ["product-verification.json", "product-verification.md"],
            "fresh_contracts": {
                "product-verification.json": "warroom.product_verification.v1",
            },
        },
        {
            "id": "enterprise_airgap_open_core_audit",
            "title": "Enterprise / airgap / open-core audit",
            "severity": "P1",
            "required": [
                "flyto-code/scripts/audit-enterprise-airgap.mjs",
                "flyto-code/nginx.enterprise-airgap.conf",
                "flyto-code/docs/open-core/airgap-update-security.md",
                "flyto-engine/connectors/profiles/airgap.json",
            ],
            "fresh": ["enterprise-airgap.md"],
        },
        {
            "id": "geo_aeo_seo_ai_crawler_audit",
            "title": "GEO / AEO / SEO / AI crawler audit",
            "severity": "P1",
            "required": [
                "flyto-landing-page/scripts/audit-public-geo-routes.mjs",
                "flyto-landing-page/docs/geo-log-analysis.md",
                "flyto-landing-page/public/llms.txt",
                "flyto-landing-page/public/llms-full.txt",
            ],
            "fresh": ["geo-ai-crawler.md"],
        },
        {
            "id": "public_site_verification",
            "title": "Public site DNS / route / browser verification",
            "severity": "P1",
            "required": [
                "flyto-core/src/core/modules/atomic/warroom/public_site.py",
                "flyto-core/src/recipes/flyto2-public-site-verification.yaml",
                "flyto-core/tests/modules/test_warroom_modules.py",
                "flyto-landing-page/scripts/audit-public-site-contract.mjs",
                "flyto-landing-page/scripts/audit-public-geo-routes.mjs",
                "flyto-landing-page/public/robots.txt",
                "flyto-landing-page/public/llms.txt",
                "flyto-landing-page/public/llms-full.txt",
            ],
            "fresh": ["public-site-verification.json", "public-site-verification.md"],
            "fresh_contracts": {
                "public-site-verification.json": "flyto2.public_site_verification.v1",
            },
        },
        {
            "id": "i18n_multilingual_audit",
            "title": "i18n / multilingual audit",
            "severity": "P1",
            "required": [
                "flyto-code/docs/I18N_AUDIT_SUMMARY.md",
                "flyto-code/scripts/check-i18n.py",
                "flyto-engine/scripts/check-i18n-keys.py",
                "flyto-cloud/scripts/check-i18n.py",
                "flyto-landing-page/.github/workflows/i18n-drift.yml",
            ],
            "fresh": ["i18n.md"],
        },
        {
            "id": "security_performance_cicd_audit",
            "title": "Security / performance / CI/CD audit",
            "severity": "P1",
            "required": [
                "flyto-indexer/src/verify.py",
                "flyto-code/.github/workflows/ci.yml",
                "flyto-engine/.github/workflows/ci.yml",
                "flyto-landing-page/.github/workflows/ci.yml",
            ],
            "fresh": ["security-performance.md"],
        },
        {
            "id": "github_actions_startup",
            "title": "GitHub Actions startup / green CI",
            "severity": "P0",
            "required": [
                "flyto-indexer/scripts/audit_github_actions_startup.py",
                "flyto-code/scripts/audit-github-actions-startup.mjs",
                "flyto-code/.github/workflows/ci.yml",
                "flyto-code/.github/workflows/actions-startup-probe.yml",
                "flyto-engine/.github/workflows/ci.yml",
                "flyto-core/.github/workflows/ci.yml",
                "flyto-indexer/.github/workflows/ci.yml",
            ],
            "fresh": ["github-actions-startup.json"],
            "fresh_contracts": {
                "github-actions-startup.json": "flyto.github_actions_startup.v1",
            },
        },
        {
            "id": "e2e_browser_smoke_matrix",
            "title": "E2E browser smoke matrix",
            "severity": "P1",
            "required": [
                "flyto-code/reports/closed-loop-audit/ui-all-routes-dom-smoke.json",
                "flyto-core/src/recipes/flyto2-ui-smoke.yaml",
                "_audits/flyto2-ui-smoke-2026-06-18.json",
            ],
            "fresh": ["browser-smoke.json", "browser-smoke.md"],
        },
        {
            "id": "release_readiness_verdict",
            "title": "Release readiness verdict",
            "severity": "P0",
            "required": [],
            "product_gate_required": True,
            "fresh": [],
        },
    ]


def _audit_deliverables(
    workspace: Path,
    product_gate: dict[str, Any],
    options: ReleasePacketOptions,
) -> list[dict[str, Any]]:
    deliverables: list[dict[str, Any]] = []
    for spec in _deliverable_specs():
        required = list(spec.get("required", []))
        evidence = _evidence(required, workspace)
        missing = [item["path"] for item in evidence if not item["exists"]]
        if spec.get("packet_generated"):
            status = "pass"
            missing = []
        elif spec.get("product_gate_required"):
            status = "pass" if product_gate.get("ok") else "blocked"
            missing = []
        else:
            status = "pass" if not missing else "needs_evidence"
        fresh = _fresh_evidence(
            list(spec.get("fresh", [])),
            options.fresh_evidence_dir.resolve() if options.fresh_evidence_dir else None,
            options.run_start,
            dict(spec.get("fresh_contracts", {})),
        )
        stale_or_missing_fresh = [item["path"] for item in fresh if not item["fresh"]]
        if status == "pass" and options.require_fresh and stale_or_missing_fresh:
            status = "needs_fresh_evidence"
        deliverables.append({
            "id": spec["id"],
            "title": spec["title"],
            "severity": spec["severity"],
            "status": status,
            "evidence": evidence,
            "missing_evidence": missing,
            "fresh_evidence": fresh,
            "missing_fresh_evidence": stale_or_missing_fresh,
        })
    return deliverables


def _load_evidence_gate_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "health_signal_policy": _default_health_signal_policy(),
            "gates": [],
        }
    data = _load_json(path)
    if not isinstance(data.get("gates", []), list):
        raise ValueError(f"invalid evidence gate config: {path}")
    if "health_signal_policy" not in data:
        data["health_signal_policy"] = _default_health_signal_policy()
    return data


def _default_health_signal_policy() -> dict[str, Any]:
    return {
        "label": "minimum hygiene signal",
        "summary": "Health grades are triage signals for code hygiene. They do not prove product readiness.",
        "limitations": [
            "Health scores do not prove real user workflow quality.",
            "Health scores do not prove enterprise deployment readiness.",
            "Health scores do not prove security control effectiveness beyond the scanned checks.",
            "Health scores do not prove market positioning, AI citation, or customer trust.",
        ],
    }


def _health_signal_summary(inventory: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    repos: dict[str, Any] = {}
    for name, repo in sorted(inventory.items()):
        signal = repo.get("health_signal") or {}
        health = repo.get("health") or {}
        repos[name] = {
            "score": signal.get("score", health.get("score")),
            "grade": signal.get("grade", health.get("grade", "N/A")),
            "target_grade": signal.get("target_grade"),
            "role": signal.get("role", "minimum_hygiene_signal"),
            "core": repo.get("core", False),
            "status": repo.get("status", ""),
        }
    return {
        "label": policy.get("label", "minimum hygiene signal"),
        "summary": policy.get("summary", ""),
        "limitations": list(policy.get("limitations", [])),
        "repos": repos,
    }


def _evaluate_evidence_gates(
    config: dict[str, Any],
    deliverables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in deliverables}
    gates: list[dict[str, Any]] = []
    for spec in config.get("gates", []):
        deliverable_ids = list(spec.get("deliverables", []))
        missing_deliverables: list[str] = []
        missing_evidence: list[str] = []
        missing_fresh: list[str] = []
        for deliverable_id in deliverable_ids:
            deliverable = by_id.get(deliverable_id)
            if not deliverable:
                missing_deliverables.append(deliverable_id)
                continue
            if deliverable.get("status") != "pass":
                missing_deliverables.append(deliverable_id)
                missing_evidence.extend(deliverable.get("missing_evidence", []))
                missing_fresh.extend(deliverable.get("missing_fresh_evidence", []))

        gates.append({
            "id": spec["id"],
            "title": spec.get("title", spec["id"]),
            "category": spec.get("category", "release"),
            "severity": spec.get("severity", "P1"),
            "product_lines": list(spec.get("product_lines", [])),
            "deliverables": deliverable_ids,
            "status": "pass" if not missing_deliverables and not missing_evidence and not missing_fresh else "not_proven",
            "confidence": spec.get("confidence", ""),
            "missing_deliverables": missing_deliverables,
            "missing_evidence": sorted(set(missing_evidence)),
            "missing_fresh_evidence": sorted(set(missing_fresh)),
        })
    return gates


def _confidence_basis(evidence_gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": gate["id"],
            "title": gate["title"],
            "category": gate["category"],
            "severity": gate["severity"],
            "product_lines": gate["product_lines"],
            "deliverables": gate["deliverables"],
            "basis": gate["confidence"],
        }
        for gate in evidence_gates
        if gate["status"] == "pass"
    ]


def _not_proven(evidence_gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": gate["id"],
            "title": gate["title"],
            "category": gate["category"],
            "severity": gate["severity"],
            "product_lines": gate["product_lines"],
            "missing_deliverables": gate["missing_deliverables"],
            "missing_evidence": gate["missing_evidence"],
            "missing_fresh_evidence": gate["missing_fresh_evidence"],
        }
        for gate in evidence_gates
        if gate["status"] != "pass"
    ]


def _residuals(
    deliverables: list[dict[str, Any]],
    evidence_gates: list[dict[str, Any]],
    product_gate: dict[str, Any],
    inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    residuals: list[dict[str, Any]] = []
    for item in deliverables:
        if item["status"] != "pass":
            residuals.append({
                "id": item["id"],
                "severity": item["severity"],
                "status": item["status"],
                "message": f"{item['title']} lacks required evidence.",
                "missing_evidence": item["missing_evidence"],
                "missing_fresh_evidence": item.get("missing_fresh_evidence", []),
            })
    for gate in evidence_gates:
        if gate["status"] != "pass":
            residuals.append({
                "id": f"evidence_gate:{gate['id']}",
                "severity": gate["severity"],
                "status": gate["status"],
                "message": f"{gate['title']} is not proven by required evidence.",
                "missing_deliverables": gate["missing_deliverables"],
                "missing_evidence": gate["missing_evidence"],
                "missing_fresh_evidence": gate["missing_fresh_evidence"],
            })
    for blocker in product_gate.get("blockers", []):
        residuals.append({
            "id": blocker.get("code", "product_gate_blocker"),
            "severity": blocker.get("severity", "P0"),
            "status": "blocked",
            "message": blocker.get("message", "Product gate blocker"),
            "scope": blocker.get("repo") or blocker.get("product_line") or "workspace",
        })
    dirty = [name for name, repo in inventory.items() if repo["dirty_files"]]
    if dirty:
        residuals.append({
            "id": "dirty_repos",
            "severity": "P0",
            "status": "blocked",
            "message": "One or more repos have uncommitted changes.",
            "repos": dirty,
        })
    unaligned = [name for name, repo in inventory.items() if repo["origin_main"] and not repo["origin_main_aligned"]]
    if unaligned:
        residuals.append({
            "id": "remote_alignment",
            "severity": "P0",
            "status": "blocked",
            "message": "One or more repos are not aligned with origin/main.",
            "repos": unaligned,
        })
    return residuals


def run_release_packet(options: ReleasePacketOptions) -> dict[str, Any]:
    workspace = options.workspace.resolve()
    manifest = _load_json(options.manifest_path)
    evidence_config = _load_evidence_gate_config(options.evidence_gate_path)
    product_gate = run_product_gate(
        ProductGateOptions(
            workspace=workspace,
            manifest_path=options.manifest_path,
            health_report_path=options.health_report_path,
            skip_health=options.skip_health,
            strict_memory=options.strict_memory,
        )
    )
    discovered = _discover_git_repos(workspace)
    inventory: dict[str, Any] = {}
    for repo_name, gate_repo in sorted(product_gate.get("repos", {}).items()):
        repo_path = discovered.get(repo_name)
        if repo_path is None:
            continue
        inventory[repo_name] = _repo_inventory(repo_name, repo_path, gate_repo)

    deliverables = _audit_deliverables(workspace, product_gate, options)
    evidence_gates = _evaluate_evidence_gates(evidence_config, deliverables)
    residuals = _residuals(deliverables, evidence_gates, product_gate, inventory)
    p0 = [item for item in residuals if item.get("severity") == "P0"]
    p1 = [item for item in residuals if item.get("severity") == "P1"]
    p2 = [item for item in residuals if item.get("severity") == "P2"]
    if p0 or p1 or product_gate.get("blockers"):
        verdict = "BLOCKED_FOR_PRODUCTION"
    elif p2:
        verdict = "READY_FOR_CONTROLLED_BETA"
    else:
        verdict = "READY_FOR_CONTROLLED_PRODUCTION"
    health_policy = evidence_config.get("health_signal_policy", _default_health_signal_policy())

    return {
        "product_name": manifest.get("product_name", "Flyto2"),
        "workspace": str(workspace),
        "repo_count": len(inventory),
        "manifest_repo_count": len(manifest.get("repos", {})),
        "evidence_gate_config": str(options.evidence_gate_path.resolve()) if options.evidence_gate_path else "",
        "fresh_evidence_dir": str(options.fresh_evidence_dir.resolve()) if options.fresh_evidence_dir else "",
        "require_fresh": options.require_fresh,
        "run_start": options.run_start.isoformat() if options.run_start else "",
        "product_gate_verdict": product_gate.get("verdict"),
        "verdict": verdict,
        "health_signal": _health_signal_summary(inventory, health_policy),
        "score_limitations": list(health_policy.get("limitations", [])),
        "product_lines": manifest.get("product_lines", {}),
        "product_line_coverage": product_gate.get("product_line_coverage", {}),
        "inventory": inventory,
        "deliverables": deliverables,
        "evidence_gates": evidence_gates,
        "confidence_basis": _confidence_basis(evidence_gates),
        "not_proven": _not_proven(evidence_gates),
        "residuals": residuals,
        "p0_blockers": p0,
        "p1_before_production": p1,
        "post_launch": [item for item in residuals if item.get("severity") not in {"P0", "P1"}],
    }


def format_release_packet(result: dict[str, Any]) -> str:
    lines = [
        f"# {result['product_name']} release packet",
        "",
        f"Verdict: {result['verdict']}",
        f"Product gate verdict: {result['product_gate_verdict']}",
        f"Workspace: {result['workspace']}",
        f"Repos discovered: {result['repo_count']} / manifest {result['manifest_repo_count']}",
        "",
        "## Product lines",
    ]
    for line_name, repos in result["product_line_coverage"].items():
        label = result["product_lines"].get(line_name, {}).get("label", line_name)
        lines.append(f"- {label}: {', '.join(repos) if repos else '(none)'}")

    lines.extend(["", "## Health Signal"])
    health_signal = result.get("health_signal", {})
    lines.append(f"- Role: {health_signal.get('label', 'minimum hygiene signal')}")
    summary = health_signal.get("summary")
    if summary:
        lines.append(f"- Meaning: {summary}")

    lines.extend(["", "## Score Limitations"])
    for limitation in result.get("score_limitations", []):
        lines.append(f"- {limitation}")

    lines.extend(["", "## Workspace inventory"])
    for name, repo in result["inventory"].items():
        dirty = len(repo["dirty_files"])
        signal = repo.get("health_signal") or repo.get("health") or {}
        grade = signal.get("grade", "N/A")
        lines.append(
            f"- {name}: {repo['status']}, branch={repo['branch'] or 'unknown'}, "
            f"health_signal={grade}, dirty={dirty}, role={repo['role']}"
        )

    lines.extend(["", "## Deliverables"])
    for item in result["deliverables"]:
        lines.append(f"- {item['id']}: {item['status']} ({item['severity']})")
        if item["missing_evidence"]:
            lines.append(f"  missing: {', '.join(item['missing_evidence'])}")
        if item.get("missing_fresh_evidence"):
            lines.append(f"  missing fresh: {', '.join(item['missing_fresh_evidence'])}")

    lines.extend(["", "## Evidence gates"])
    for gate in result.get("evidence_gates", []):
        lines.append(f"- {gate['id']}: {gate['status']} ({gate['severity']})")
        if gate.get("missing_deliverables"):
            lines.append(f"  missing deliverables: {', '.join(gate['missing_deliverables'])}")

    lines.extend(["", "## Confidence basis"])
    if result.get("confidence_basis"):
        for item in result["confidence_basis"]:
            lines.append(f"- {item['id']}: {item['basis']}")
    else:
        lines.append("- none")

    lines.extend(["", "## Not proven"])
    if result.get("not_proven"):
        for item in result["not_proven"]:
            missing = item.get("missing_deliverables") or []
            suffix = f" Missing deliverables: {', '.join(missing)}" if missing else ""
            lines.append(f"- {item['id']}: {item['title']}.{suffix}")
    else:
        lines.append("- none")

    lines.extend(["", "## P0 blockers"])
    if result["p0_blockers"]:
        for item in result["p0_blockers"]:
            lines.append(f"- {item['id']}: {item['message']}")
    else:
        lines.append("- none")

    lines.extend(["", "## P1 before production"])
    if result["p1_before_production"]:
        for item in result["p1_before_production"]:
            missing = item.get("missing_evidence") or []
            suffix = f" Missing: {', '.join(missing)}" if missing else ""
            lines.append(f"- {item['id']}: {item['message']}{suffix}")
    else:
        lines.append("- none")
    return "\n".join(lines)
