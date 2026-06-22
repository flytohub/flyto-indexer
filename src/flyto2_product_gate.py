"""Flyto2 product-line and release-readiness gate.

This gate is intentionally manifest-driven. It checks repo classification,
product-line coverage, project-memory files, and optional health snapshots
without needing network access or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


GRADE_ORDER = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}


def _default_manifest_path() -> Path:
    package_dir = Path(__file__).resolve().parent
    candidates = [
        package_dir.parent / "config" / "flyto2" / "product-lines.json",
        package_dir / "config" / "flyto2" / "product-lines.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_MANIFEST = _default_manifest_path()


@dataclass(frozen=True)
class ProductGateOptions:
    workspace: Path
    manifest_path: Path = DEFAULT_MANIFEST
    health_report_path: Path | None = None
    skip_health: bool = False
    strict_memory: bool = True


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _discover_git_repos(workspace: Path) -> dict[str, Path]:
    if not workspace.exists():
        raise FileNotFoundError(f"workspace not found: {workspace}")
    repos: dict[str, Path] = {}
    for child in sorted(workspace.iterdir(), key=lambda p: p.name):
        if child.is_dir() and (child / ".git").exists():
            repos[child.name] = child
    return repos


def _missing_files(repo_path: Path, files: list[str]) -> list[str]:
    return [name for name in files if not (repo_path / name).exists()]


def _missing_workflow_files(repo_path: Path, files: list[str]) -> list[str]:
    return [name for name in files if not (repo_path / "workflows" / name).exists()]


def _grade_meets(actual: str | None, expected: str) -> bool:
    if not actual:
        return False
    return GRADE_ORDER.get(actual.upper(), 0) >= GRADE_ORDER.get(expected.upper(), 0)


def _health_for_repo(health: dict[str, Any], repo_name: str) -> dict[str, Any] | None:
    repos = health.get("repos", {})
    item = repos.get(repo_name)
    return item if isinstance(item, dict) else None


def _is_health_exempt(health_item: dict[str, Any] | None) -> bool:
    return bool(health_item and health_item.get("exempt") is True)


def _line_coverage(manifest: dict[str, Any]) -> dict[str, list[str]]:
    lines = {key: [] for key in manifest.get("product_lines", {})}
    for repo_name, spec in manifest.get("repos", {}).items():
        for line in spec.get("product_lines", []):
            lines.setdefault(line, []).append(repo_name)
    return {key: sorted(value) for key, value in sorted(lines.items())}


def run_product_gate(options: ProductGateOptions) -> dict[str, Any]:
    manifest = _load_json(options.manifest_path)
    health = {}
    if options.health_report_path and options.health_report_path.exists():
        health = _load_json(options.health_report_path)

    workspace = options.workspace.resolve()
    discovered = _discover_git_repos(workspace)
    manifest_repos: dict[str, Any] = manifest.get("repos", {})
    memory_files = list(manifest.get("memory_files", []))
    workflow_files = list(manifest.get("workflow_files", []))

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    repo_reports: dict[str, Any] = {}

    for repo_name in sorted(discovered):
        if repo_name not in manifest_repos:
            blockers.append({
                "repo": repo_name,
                "code": "repo_unclassified",
                "message": "Git repo is not classified in the Flyto2 product-line manifest.",
            })

    for repo_name, spec in sorted(manifest_repos.items()):
        repo_path = discovered.get(repo_name)
        status = spec.get("status", "active")
        is_expected_present = status not in {"future"}
        report: dict[str, Any] = {
            "status": status,
            "core": bool(spec.get("core")),
            "product_lines": list(spec.get("product_lines", [])),
            "core_dependency": spec.get("core_dependency", ""),
            "present": repo_path is not None,
        }

        if is_expected_present and repo_path is None:
            blockers.append({
                "repo": repo_name,
                "code": "repo_missing",
                "message": "Repo is listed in the Flyto2 manifest but is missing from the workspace.",
            })
            repo_reports[repo_name] = report
            continue

        if not report["product_lines"]:
            blockers.append({
                "repo": repo_name,
                "code": "missing_product_line",
                "message": "Repo must map to at least one Flyto2 product line.",
            })

        if repo_path and spec.get("memory_required", False):
            missing_memory = _missing_files(repo_path, memory_files)
            missing_workflows = _missing_workflow_files(repo_path, workflow_files)
            handoff_registry = repo_path / "handoffs" / "_registry.md"
            report["memory"] = {
                "missing_files": missing_memory,
                "missing_workflows": missing_workflows,
                "handoff_registry": handoff_registry.exists(),
            }
            if missing_memory or missing_workflows or not handoff_registry.exists():
                item = {
                    "repo": repo_name,
                    "code": "memory_incomplete",
                    "message": "Project memory or handoff structure is incomplete.",
                    "missing_files": missing_memory,
                    "missing_workflows": missing_workflows,
                    "handoff_registry": handoff_registry.exists(),
                }
                if options.strict_memory and status in {"active", "internal tooling", "experimental"}:
                    blockers.append(item)
                else:
                    warnings.append(item)

        if not options.skip_health:
            health_item = _health_for_repo(health, repo_name)
            report["health"] = health_item
            report["health_signal"] = {
                "role": "minimum_hygiene_signal",
                "score": health_item.get("score") if health_item else None,
                "grade": health_item.get("grade") if health_item else None,
                "reasons": health_item.get("reasons", []) if health_item else [],
            }
            target_grade = str(spec.get("health_target") or "C")
            if spec.get("core"):
                target_grade = str(manifest.get("health_targets", {}).get("core_min_grade", target_grade))
            report["health_signal"]["target_grade"] = target_grade
            if health_item is None:
                if spec.get("core"):
                    blockers.append({
                        "repo": repo_name,
                        "code": "core_health_missing",
                        "message": "Core repo needs a minimum hygiene health report entry for release gating.",
                    })
                else:
                    warnings.append({
                        "repo": repo_name,
                        "code": "health_missing",
                        "message": "No health report entry; minimum hygiene target was not evaluated.",
                    })
            elif _is_health_exempt(health_item):
                if spec.get("core"):
                    blockers.append({
                        "repo": repo_name,
                        "code": "core_health_exempt",
                        "message": "Core repos cannot be exempt from minimum hygiene release gating.",
                    })
            elif not _grade_meets(str(health_item.get("grade", "")), target_grade):
                severity = "P1" if spec.get("core") else "P2"
                target = blockers if spec.get("core") else warnings
                target.append({
                    "repo": repo_name,
                    "code": "health_below_target",
                    "severity": severity,
                    "message": f"Minimum hygiene grade {health_item.get('grade')} is below target {target_grade}.",
                    "score": health_item.get("score"),
                    "target": target_grade,
                    "reasons": health_item.get("reasons", []),
                })

        repo_reports[repo_name] = report

    line_coverage = _line_coverage(manifest)
    for line_name, repos in line_coverage.items():
        if not repos:
            blockers.append({
                "product_line": line_name,
                "code": "product_line_empty",
                "message": "Product line has no mapped repos.",
            })

    ok = not blockers
    return {
        "ok": ok,
        "product_name": manifest.get("product_name", "Flyto2"),
        "workspace": str(workspace),
        "verdict": "READY_FOR_CONTROLLED_PRODUCTION" if ok else "BLOCKED_FOR_PRODUCTION",
        "product_lines": manifest.get("product_lines", {}),
        "product_line_coverage": line_coverage,
        "repo_count": len(repo_reports),
        "repos": repo_reports,
        "blockers": blockers,
        "warnings": warnings,
    }


def format_product_gate(result: dict[str, Any]) -> str:
    lines = [
        f"# {result['product_name']} product gate",
        "",
        f"Verdict: {result['verdict']}",
        f"Workspace: {result['workspace']}",
        f"Repos checked: {result['repo_count']}",
        "",
        "## Product lines",
    ]
    for line_name, repos in result["product_line_coverage"].items():
        lines.append(f"- {line_name}: {', '.join(repos) if repos else '(none)'}")
    lines.append("")
    lines.append("## Blockers")
    if result["blockers"]:
        for item in result["blockers"]:
            scope = item.get("repo") or item.get("product_line") or "workspace"
            lines.append(f"- {scope}: {item['code']} - {item['message']}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Warnings")
    if result["warnings"]:
        for item in result["warnings"]:
            scope = item.get("repo") or item.get("product_line") or "workspace"
            lines.append(f"- {scope}: {item['code']} - {item['message']}")
    else:
        lines.append("- none")
    return "\n".join(lines)
