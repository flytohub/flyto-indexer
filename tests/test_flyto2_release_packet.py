import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.flyto2_release_packet import (
    ReleasePacketOptions,
    format_release_packet,
    run_release_packet,
)


MEMORY_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "PROJECT.md",
    "ARCHITECTURE.md",
    "STATE.md",
    "ROADMAP.md",
    "tasks.md",
    "DECISIONS.md",
    "CHANGELOG.md",
]

WORKFLOW_FILES = [
    "idea-capture.md",
    "planning.md",
    "implementation.md",
    "bugfix.md",
    "refactor.md",
    "investigation.md",
    "wrap-up.md",
]


def _repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    for filename in MEMORY_FILES:
        (repo / filename).write_text(f"# {filename}\n", encoding="utf-8")
    workflows = repo / "workflows"
    workflows.mkdir()
    for filename in WORKFLOW_FILES:
        (workflows / filename).write_text(f"# {filename}\n", encoding="utf-8")
    handoffs = repo / "handoffs"
    handoffs.mkdir()
    (handoffs / "_registry.md").write_text("# Handoffs\n", encoding="utf-8")
    return repo


def _touch(root: Path, path: str) -> None:
    file_path = root / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("ok\n", encoding="utf-8")


def _manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "product_name": "Flyto2",
                "health_targets": {"core_min_grade": "B"},
                "memory_files": MEMORY_FILES,
                "workflow_files": WORKFLOW_FILES,
                "product_lines": {
                    "cloud_apps_automation": {"label": "Cloud"},
                    "security": {"label": "Security"},
                    "data": {"label": "Data"},
                    "zero_person_agent": {"label": "Agent"},
                    "big_data_intelligence": {"label": "Intel"},
                },
                "repos": {
                    "flyto-core": {
                        "status": "active",
                        "core": True,
                        "health_target": "B",
                        "core_dependency": "root kernel",
                        "memory_required": True,
                        "product_lines": [
                            "cloud_apps_automation",
                            "security",
                            "data",
                            "zero_person_agent",
                            "big_data_intelligence",
                        ],
                    },
                    "flyto-ai": {
                        "status": "active",
                        "core": True,
                        "health_target": "B",
                        "core_dependency": "AI policy/runtime",
                        "memory_required": True,
                        "product_lines": [
                            "cloud_apps_automation",
                            "security",
                            "data",
                            "zero_person_agent",
                            "big_data_intelligence",
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _health(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "repos": {
                    "flyto-core": {"grade": "B", "score": 80},
                    "flyto-ai": {"grade": "B", "score": 82},
                }
            }
        ),
        encoding="utf-8",
    )


def _all_required_evidence(root: Path) -> None:
    for evidence_path in [
        "flyto-cloud/docs/architecture-map.md",
        "flyto-code/docs/architecture-map.md",
        "flyto-core/docs/architecture-map.md",
        "flyto-engine/docs/architecture-map.md",
        "flyto-indexer/docs/architecture-map.md",
        "flyto-ai/docs/architecture-map.md",
        "flyto-engine/api/handlers_billing.go",
        "flyto-engine/api/handlers_entitlement.go",
        "flyto-engine/api/handlers_capabilities_rbac_test.go",
        "flyto-engine/internal/billing/billing_test.go",
        "flyto-engine/api/handlers_rbac_cross_org_test.go",
        "flyto-engine/internal/store/rbac_cross_org_resolver_test.go",
        "flyto-engine/internal/store/sql_code_entitlement_guard_test.go",
        "flyto-code/src-next/configs/__tests__/navigationFeatureCheck.test.ts",
        "flyto-code/src-next/components/atoms/__tests__/GatedButton.test.tsx",
        "flyto-code/scripts/audit-data-readiness-boundaries.mjs",
        "flyto-code/scripts/audit-enterprise-airgap.mjs",
        "flyto-code/nginx.enterprise-airgap.conf",
        "flyto-code/docs/open-core/airgap-update-security.md",
        "flyto-engine/connectors/profiles/airgap.json",
        "flyto-landing-page/scripts/audit-public-geo-routes.mjs",
        "flyto-landing-page/docs/geo-log-analysis.md",
        "flyto-landing-page/public/llms.txt",
        "flyto-landing-page/public/llms-full.txt",
        "flyto-code/docs/I18N_AUDIT_SUMMARY.md",
        "flyto-code/scripts/check-i18n.py",
        "flyto-engine/scripts/check-i18n-keys.py",
        "flyto-cloud/scripts/check-i18n.py",
        "flyto-landing-page/.github/workflows/i18n-drift.yml",
        "flyto-indexer/src/verify.py",
        "flyto-code/.github/workflows/ci.yml",
        "flyto-engine/.github/workflows/ci.yml",
        "flyto-landing-page/.github/workflows/ci.yml",
        "flyto-code/reports/closed-loop-audit/ui-all-routes-dom-smoke.json",
        "flyto-core/src/recipes/flyto2-ui-smoke.yaml",
        "_audits/flyto2-ui-smoke-2026-06-18.json",
    ]:
        _touch(root, evidence_path)


def test_release_packet_passes_when_gate_and_evidence_are_complete(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-ai")
    _all_required_evidence(tmp_path)
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    _health(health)

    result = run_release_packet(
        ReleasePacketOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
        )
    )

    assert result["verdict"] == "READY_FOR_CONTROLLED_PRODUCTION"
    assert result["repo_count"] == 2
    assert result["p0_blockers"] == []
    assert result["p1_before_production"] == []
    assert "workspace_inventory" in {item["id"] for item in result["deliverables"]}


def test_release_packet_marks_missing_required_evidence_as_p1(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-ai")
    _all_required_evidence(tmp_path)
    (tmp_path / "flyto-ai" / "docs" / "architecture-map.md").unlink()
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    _health(health)

    result = run_release_packet(
        ReleasePacketOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
        )
    )

    assert result["verdict"] == "READY_FOR_CONTROLLED_BETA"
    p1 = {item["id"]: item for item in result["p1_before_production"]}
    assert "architecture_dependency_map" in p1
    assert "flyto-ai/docs/architecture-map.md" in p1["architecture_dependency_map"]["missing_evidence"]
    markdown = format_release_packet(result)
    assert "READY_FOR_CONTROLLED_BETA" in markdown
