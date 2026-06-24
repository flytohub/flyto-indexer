import json
from pathlib import Path
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.flyto2_release_packet import (
    ReleasePacketOptions,
    format_release_packet,
    parse_run_start,
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
        "flyto-code/scripts/audit-enterprise-airgap.mjs",
        "flyto-code/nginx.enterprise-airgap.conf",
        "flyto-code/docs/open-core/airgap-update-security.md",
        "flyto-engine/connectors/profiles/airgap.json",
        "flyto-landing-page/scripts/audit-public-geo-routes.mjs",
        "flyto-landing-page/docs/geo-log-analysis.md",
        "flyto-landing-page/public/llms.txt",
        "flyto-landing-page/public/llms-full.txt",
        "flyto-core/src/core/modules/atomic/warroom/public_site.py",
        "flyto-core/src/recipes/flyto2-public-site-verification.yaml",
        "flyto-landing-page/scripts/audit-public-site-contract.mjs",
        "flyto-landing-page/public/robots.txt",
        "flyto-code/docs/I18N_AUDIT_SUMMARY.md",
        "flyto-code/scripts/check-i18n.py",
        "flyto-engine/scripts/check-i18n-keys.py",
        "flyto-cloud/scripts/check-i18n.py",
        "flyto-landing-page/.github/workflows/i18n-drift.yml",
        "flyto-indexer/src/verify.py",
        "flyto-code/.github/workflows/ci.yml",
        "flyto-indexer/scripts/audit_github_actions_startup.py",
        "flyto-code/scripts/audit-github-actions-startup.mjs",
        "flyto-code/.github/workflows/actions-startup-probe.yml",
        "flyto-engine/.github/workflows/ci.yml",
        "flyto-core/.github/workflows/ci.yml",
        "flyto-indexer/.github/workflows/ci.yml",
        "flyto-landing-page/.github/workflows/ci.yml",
        "flyto-code/reports/closed-loop-audit/ui-all-routes-dom-smoke.json",
        "flyto-core/src/recipes/flyto2-ui-smoke.yaml",
        "_audits/flyto2-ui-smoke-2026-06-18.json",
    ]:
        _touch(root, evidence_path)


def _all_fresh_evidence(root: Path, *, generated_at: str = "2026-06-22T01:00:00+00:00") -> Path:
    evidence_dir = root / "reports" / "fresh"
    for evidence_path in [
        "workspace-matrix.json",
        "workspace-matrix.md",
        "architecture-map.md",
        "billing-entitlement.md",
        "rbac-tenant-isolation.md",
        "state-machine.md",
        "enterprise-airgap.md",
        "geo-ai-crawler.md",
        "i18n.md",
        "security-performance.md",
        "product-verification.json",
        "product-verification.md",
        "public-site-verification.json",
        "public-site-verification.md",
        "browser-smoke.json",
        "browser-smoke.md",
        "github-actions-startup.json",
        "release-packet.json",
        "release-packet.md",
    ]:
        file_path = evidence_dir / evidence_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if evidence_path == "product-verification.json":
            file_path.write_text(
                json.dumps(
                    {
                        "contract": "warroom.product_verification.v1",
                        "generated_at": generated_at,
                        "p0_findings": 0,
                        "site_graph": {
                            "intents": [{"id": "create_project", "label": "Create Project"}],
                            "state_graph": {"states": ["loading", "resolved_data"]},
                        },
                        "scores": {
                            "observed_coverage": 0.91,
                            "reachable_coverage": 0.82,
                            "api_ui_consistency": 1.0,
                            "business_logic_confidence": 0.94,
                        },
                    }
                ),
                encoding="utf-8",
            )
        elif evidence_path == "public-site-verification.json":
            file_path.write_text(
                json.dumps(
                    {
                        "contract": "flyto2.public_site_verification.v1",
                        "generated_at": generated_at,
                        "p0_findings": 0,
                        "p1_findings": 0,
                        "dns_matrix": [{"host": "flyto2.com", "family": "ipv4", "ok": True}],
                        "tls_matrix": [{"host": "flyto2.com", "ok": True}],
                        "route_matrix": [
                            {"path": "/", "status": 200, "final_status": 200, "ok": True},
                            {"path": "/robots.txt", "status": 200, "final_status": 200, "ok": True},
                            {"path": "/sitemap.xml", "status": 200, "final_status": 200, "ok": True},
                            {"path": "/llms.txt", "status": 200, "final_status": 200, "ok": True},
                            {"path": "/llms-full.txt", "status": 200, "final_status": 200, "ok": True},
                        ],
                        "browser_matrix": [{"path": "/", "status": "ok", "ok": True}],
                        "seo_geo_matrix": {
                            "title": True,
                            "meta_description": True,
                            "canonical": True,
                            "open_graph": True,
                            "structured_data": True,
                            "llms_txt": True,
                            "sitemap": True,
                            "robots": True,
                            "server_rendered_content": True,
                        },
                        "scores": {
                            "public_route_readiness": 1.0,
                            "seo_geo_readiness": 1.0,
                            "browser_render_readiness": 1.0,
                        },
                    }
                ),
                encoding="utf-8",
            )
        elif evidence_path == "github-actions-startup.json":
            file_path.write_text(
                json.dumps(
                    {
                        "schema": "flyto.workspace-github-actions-startup-audit.v1",
                        "generated_at": generated_at,
                        "ok": True,
                        "repositories": [
                            {
                                "repo": "flytohub/flyto-code",
                                "head": "0000000000000000000000000000000000000000",
                                "requiredWorkflows": ["CI"],
                                "ok": True,
                                "workflows": [
                                    {
                                        "workflow": "CI",
                                        "id": 123,
                                        "status": "completed",
                                        "conclusion": "success",
                                        "jobs": [
                                            {
                                                "name": "ci",
                                                "status": "completed",
                                                "conclusion": "success",
                                            }
                                        ],
                                        "ok": True,
                                    }
                                ],
                            }
                        ],
                        "summary": {
                            "repo_count": 1,
                            "workflow_count": 1,
                            "failure_count": 0,
                            "failures": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
        elif file_path.suffix == ".json":
            file_path.write_text(json.dumps({"generated_at": generated_at}), encoding="utf-8")
        else:
            file_path.write_text("fresh evidence\n", encoding="utf-8")
    return evidence_dir


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
    assert result["health_signal"]["label"] == "minimum hygiene signal"
    assert result["score_limitations"]
    assert result["confidence_basis"]
    assert result["not_proven"] == []


def test_release_packet_requires_fresh_evidence_when_requested(tmp_path):
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
            fresh_evidence_dir=tmp_path / "reports" / "missing",
            require_fresh=True,
        )
    )

    assert result["verdict"] == "BLOCKED_FOR_PRODUCTION"
    by_id = {item["id"]: item for item in result["deliverables"]}
    assert by_id["release_readiness_verdict"]["status"] == "pass"
    assert by_id["workspace_inventory"]["status"] == "needs_fresh_evidence"
    assert result["p1_before_production"]
    assert result["not_proven"]


def test_release_packet_accepts_fresh_evidence_after_run_start(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-ai")
    _all_required_evidence(tmp_path)
    evidence_dir = _all_fresh_evidence(tmp_path)
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    _health(health)

    result = run_release_packet(
        ReleasePacketOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
            fresh_evidence_dir=evidence_dir,
            require_fresh=True,
            run_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
        )
    )

    assert result["verdict"] == "READY_FOR_CONTROLLED_PRODUCTION"
    assert result["p1_before_production"] == []
    by_id = {item["id"]: item for item in result["deliverables"]}
    product_fresh = {
        item["path"]: item for item in by_id["deterministic_product_verification"]["fresh_evidence"]
    }
    assert product_fresh["product-verification.json"]["contract_valid"] is True


def test_release_packet_rejects_invalid_product_verification_contract(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-ai")
    _all_required_evidence(tmp_path)
    evidence_dir = _all_fresh_evidence(tmp_path)
    (evidence_dir / "product-verification.json").write_text(
        json.dumps(
            {
                "contract": "warroom.product_verification.v1",
                "generated_at": "2026-06-22T01:00:00+00:00",
                "p0_findings": 1,
                "site_graph": {"intents": [], "state_graph": {}},
                "scores": {"observed_coverage": 1.0},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    _health(health)

    result = run_release_packet(
        ReleasePacketOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
            fresh_evidence_dir=evidence_dir,
            require_fresh=True,
            run_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
        )
    )

    assert result["verdict"] == "BLOCKED_FOR_PRODUCTION"
    by_id = {item["id"]: item for item in result["deliverables"]}
    product_verification = by_id["deterministic_product_verification"]
    assert product_verification["status"] == "needs_fresh_evidence"
    product_fresh = {item["path"]: item for item in product_verification["fresh_evidence"]}
    assert product_fresh["product-verification.json"]["reason"] == "invalid_contract"
    assert product_fresh["product-verification.json"]["contract_valid"] is False
    assert "p0_findings must be integer 0" in product_fresh["product-verification.json"]["contract_errors"]


def test_release_packet_rejects_invalid_public_site_verification_contract(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-ai")
    _all_required_evidence(tmp_path)
    evidence_dir = _all_fresh_evidence(tmp_path)
    (evidence_dir / "public-site-verification.json").write_text(
        json.dumps(
            {
                "contract": "flyto2.public_site_verification.v1",
                "generated_at": "2026-06-22T01:00:00+00:00",
                "p0_findings": 1,
                "dns_matrix": [],
                "tls_matrix": [],
                "route_matrix": [],
                "browser_matrix": [],
                "seo_geo_matrix": {},
                "scores": {"public_route_readiness": 0.0},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    _health(health)

    result = run_release_packet(
        ReleasePacketOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
            fresh_evidence_dir=evidence_dir,
            require_fresh=True,
            run_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
        )
    )

    assert result["verdict"] == "BLOCKED_FOR_PRODUCTION"
    by_id = {item["id"]: item for item in result["deliverables"]}
    public_site = by_id["public_site_verification"]
    assert public_site["status"] == "needs_fresh_evidence"
    public_fresh = {item["path"]: item for item in public_site["fresh_evidence"]}
    assert public_fresh["public-site-verification.json"]["reason"] == "invalid_contract"
    assert public_fresh["public-site-verification.json"]["contract_valid"] is False
    assert "p0_findings must be integer 0" in public_fresh["public-site-verification.json"]["contract_errors"]
    assert "dns_matrix must be a non-empty list" in public_fresh["public-site-verification.json"]["contract_errors"]


def test_release_packet_blocks_public_site_p1_findings(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-ai")
    _all_required_evidence(tmp_path)
    evidence_dir = _all_fresh_evidence(tmp_path)
    public_site = evidence_dir / "public-site-verification.json"
    data = json.loads(public_site.read_text(encoding="utf-8"))
    data["p1_findings"] = 1
    data["findings"] = [
        {
            "severity": "P1",
            "code": "ai_crawler_blocked",
            "message": "AI/search crawler route unavailable: Claude-User /",
        }
    ]
    public_site.write_text(json.dumps(data), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    _health(health)

    result = run_release_packet(
        ReleasePacketOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
            fresh_evidence_dir=evidence_dir,
            require_fresh=True,
            run_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
        )
    )

    assert result["verdict"] == "BLOCKED_FOR_PRODUCTION"
    by_id = {item["id"]: item for item in result["deliverables"]}
    public_deliverable = by_id["public_site_verification"]
    assert public_deliverable["status"] == "needs_fresh_evidence"
    public_fresh = {item["path"]: item for item in public_deliverable["fresh_evidence"]}
    assert public_fresh["public-site-verification.json"]["reason"] == "blocking_findings"
    assert public_fresh["public-site-verification.json"]["contract_valid"] is True
    assert public_fresh["public-site-verification.json"]["contract_finding_counts"]["P1"] == 1
    assert public_fresh["public-site-verification.json"]["contract_blocking_findings"] == [
        {"severity": "P1", "count": 1}
    ]
    p1 = {item["id"]: item for item in result["p1_before_production"]}
    assert "public_site_verification" in p1


def test_release_packet_blocks_github_actions_startup_failure(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-ai")
    _all_required_evidence(tmp_path)
    evidence_dir = _all_fresh_evidence(tmp_path)
    (evidence_dir / "github-actions-startup.json").write_text(
        json.dumps(
            {
                "schema": "flyto.workspace-github-actions-startup-audit.v1",
                "generated_at": "2026-06-22T01:00:00+00:00",
                "ok": False,
                "repositories": [
                    {
                        "repo": "flytohub/flyto-code",
                        "head": "0000000000000000000000000000000000000000",
                        "requiredWorkflows": ["CI"],
                        "ok": False,
                        "workflows": [
                            {
                                "workflow": "CI",
                                "id": 28072960830,
                                "status": "completed",
                                "conclusion": "startup_failure",
                                "jobs": [],
                                "ok": False,
                                "reason": "no_jobs_created",
                            }
                        ],
                    }
                ],
                "summary": {
                    "repo_count": 1,
                    "workflow_count": 1,
                    "failure_count": 1,
                    "failures": ["flytohub/flyto-code/CI: no_jobs_created"],
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    _health(health)

    result = run_release_packet(
        ReleasePacketOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
            fresh_evidence_dir=evidence_dir,
            require_fresh=True,
            run_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
        )
    )

    assert result["verdict"] == "BLOCKED_FOR_PRODUCTION"
    by_id = {item["id"]: item for item in result["deliverables"]}
    github_actions = by_id["github_actions_startup"]
    assert github_actions["status"] == "needs_fresh_evidence"
    fresh = {item["path"]: item for item in github_actions["fresh_evidence"]}
    startup = fresh["github-actions-startup.json"]
    assert startup["reason"] == "invalid_contract"
    assert startup["contract_valid"] is False
    assert "ok must be true" in startup["contract_errors"]
    assert "repositories[0].ok must be true" in startup["contract_errors"]
    assert "repositories[0].workflows[0].jobs must be a non-empty list" in startup["contract_errors"]
    assert "repositories[0].workflows[0].conclusion must be success" in startup["contract_errors"]
    p0 = {item["id"]: item for item in result["p0_blockers"]}
    assert "github_actions_startup" in p0
    not_proven = {item["id"]: item for item in result["not_proven"]}
    assert "release_operations" in not_proven


def test_parse_run_start_rejects_invalid_timestamp():
    with pytest.raises(ValueError):
        parse_run_start("not-a-timestamp")


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

    assert result["verdict"] == "BLOCKED_FOR_PRODUCTION"
    p1 = {item["id"]: item for item in result["p1_before_production"]}
    assert "architecture_dependency_map" in p1
    assert "flyto-ai/docs/architecture-map.md" in p1["architecture_dependency_map"]["missing_evidence"]
    assert any(item["id"] == "cloud_apps_automation" for item in result["not_proven"])
    markdown = format_release_packet(result)
    assert "BLOCKED_FOR_PRODUCTION" in markdown
    assert "## Score Limitations" in markdown
    assert "## Not proven" in markdown


def test_release_packet_blocks_high_score_workspace_without_product_evidence(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-ai")
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    health.write_text(
        json.dumps(
            {
                "repos": {
                    "flyto-core": {"grade": "A", "score": 98},
                    "flyto-ai": {"grade": "A", "score": 97},
                }
            }
        ),
        encoding="utf-8",
    )

    result = run_release_packet(
        ReleasePacketOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
        )
    )

    assert result["verdict"] == "BLOCKED_FOR_PRODUCTION"
    assert result["health_signal"]["repos"]["flyto-core"]["grade"] == "A"
    assert result["p1_before_production"]
    assert result["not_proven"]
