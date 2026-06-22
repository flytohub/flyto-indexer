"""Flyto2 workspace release packet generator.

The packet is deliberately evidence-driven: it records what can be proven from
local repositories and marks missing proof as residual evidence instead of
turning audit intent into a false release claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from .flyto2_product_gate import (
    DEFAULT_MANIFEST,
    ProductGateOptions,
    _discover_git_repos,
    _load_json,
    run_product_gate,
)


@dataclass(frozen=True)
class ReleasePacketOptions:
    workspace: Path
    manifest_path: Path = DEFAULT_MANIFEST
    health_report_path: Path | None = None
    skip_health: bool = False
    strict_memory: bool = True


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
        "memory": gate_repo.get("memory"),
    }


def _path_exists(workspace: Path, path: str) -> bool:
    return (workspace / path).exists()


def _evidence(paths: list[str], workspace: Path) -> list[dict[str, Any]]:
    return [{"path": path, "exists": _path_exists(workspace, path)} for path in paths]


def _deliverable_specs() -> list[dict[str, Any]]:
    return [
        {
            "id": "workspace_inventory",
            "title": "25-project workspace inventory",
            "severity": "P1",
            "required": [],
            "packet_generated": True,
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
        },
        {
            "id": "release_readiness_verdict",
            "title": "Release readiness verdict",
            "severity": "P0",
            "required": [],
            "product_gate_required": True,
        },
    ]


def _audit_deliverables(workspace: Path, product_gate: dict[str, Any]) -> list[dict[str, Any]]:
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
        deliverables.append({
            "id": spec["id"],
            "title": spec["title"],
            "severity": spec["severity"],
            "status": status,
            "evidence": evidence,
            "missing_evidence": missing,
        })
    return deliverables


def _residuals(deliverables: list[dict[str, Any]], product_gate: dict[str, Any], inventory: dict[str, Any]) -> list[dict[str, Any]]:
    residuals: list[dict[str, Any]] = []
    for item in deliverables:
        if item["status"] != "pass":
            residuals.append({
                "id": item["id"],
                "severity": item["severity"],
                "status": item["status"],
                "message": f"{item['title']} lacks required evidence.",
                "missing_evidence": item["missing_evidence"],
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

    deliverables = _audit_deliverables(workspace, product_gate)
    residuals = _residuals(deliverables, product_gate, inventory)
    p0 = [item for item in residuals if item.get("severity") == "P0"]
    p1 = [item for item in residuals if item.get("severity") == "P1"]
    if p0 or product_gate.get("blockers"):
        verdict = "BLOCKED_FOR_PRODUCTION"
    elif p1:
        verdict = "READY_FOR_CONTROLLED_BETA"
    else:
        verdict = "READY_FOR_CONTROLLED_PRODUCTION"

    return {
        "product_name": manifest.get("product_name", "Flyto2"),
        "workspace": str(workspace),
        "repo_count": len(inventory),
        "manifest_repo_count": len(manifest.get("repos", {})),
        "product_gate_verdict": product_gate.get("verdict"),
        "verdict": verdict,
        "product_lines": manifest.get("product_lines", {}),
        "product_line_coverage": product_gate.get("product_line_coverage", {}),
        "inventory": inventory,
        "deliverables": deliverables,
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

    lines.extend(["", "## Workspace inventory"])
    for name, repo in result["inventory"].items():
        dirty = len(repo["dirty_files"])
        health = repo.get("health") or {}
        grade = health.get("grade", "N/A")
        lines.append(
            f"- {name}: {repo['status']}, branch={repo['branch'] or 'unknown'}, "
            f"grade={grade}, dirty={dirty}, role={repo['role']}"
        )

    lines.extend(["", "## Deliverables"])
    for item in result["deliverables"]:
        lines.append(f"- {item['id']}: {item['status']} ({item['severity']})")
        if item["missing_evidence"]:
            lines.append(f"  missing: {', '.join(item['missing_evidence'])}")

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
