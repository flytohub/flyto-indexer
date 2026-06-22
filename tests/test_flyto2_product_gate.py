import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.flyto2_product_gate import ProductGateOptions, run_product_gate


def _repo(root: Path, name: str, *, memory: bool = True):
    repo = root / name
    repo.mkdir()
    (repo / ".git").mkdir()
    if memory:
        for filename in [
            "AGENTS.md",
            "CLAUDE.md",
            "PROJECT.md",
            "ARCHITECTURE.md",
            "STATE.md",
            "ROADMAP.md",
            "tasks.md",
            "DECISIONS.md",
            "CHANGELOG.md",
        ]:
            (repo / filename).write_text(f"# {filename}\n", encoding="utf-8")
        workflows = repo / "workflows"
        workflows.mkdir()
        for filename in [
            "idea-capture.md",
            "planning.md",
            "implementation.md",
            "bugfix.md",
            "refactor.md",
            "investigation.md",
            "wrap-up.md",
        ]:
            (workflows / filename).write_text(f"# {filename}\n", encoding="utf-8")
        handoffs = repo / "handoffs"
        handoffs.mkdir()
        (handoffs / "_registry.md").write_text("# Handoffs\n", encoding="utf-8")


def _manifest(path: Path):
    path.write_text(
        json.dumps(
            {
                "product_name": "Flyto2",
                "health_targets": {"core_min_grade": "B"},
                "memory_files": [
                    "AGENTS.md",
                    "CLAUDE.md",
                    "PROJECT.md",
                    "ARCHITECTURE.md",
                    "STATE.md",
                    "ROADMAP.md",
                    "tasks.md",
                    "DECISIONS.md",
                    "CHANGELOG.md",
                ],
                "workflow_files": [
                    "idea-capture.md",
                    "planning.md",
                    "implementation.md",
                    "bugfix.md",
                    "refactor.md",
                    "investigation.md",
                    "wrap-up.md",
                ],
                "product_lines": {
                    "cloud_apps_automation": {"label": "Cloud"},
                    "security": {"label": "Security"},
                },
                "repos": {
                    "flyto-core": {
                        "status": "active",
                        "core": True,
                        "health_target": "B",
                        "memory_required": True,
                        "product_lines": ["cloud_apps_automation", "security"],
                    },
                    "flyto-cloud": {
                        "status": "active",
                        "health_target": "B",
                        "memory_required": True,
                        "product_lines": ["cloud_apps_automation"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_product_gate_passes_when_manifest_memory_and_health_match(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-cloud")
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    health.write_text(
        json.dumps({"repos": {"flyto-core": {"grade": "B", "score": 80}, "flyto-cloud": {"grade": "B", "score": 82}}}),
        encoding="utf-8",
    )

    result = run_product_gate(
        ProductGateOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
        )
    )

    assert result["ok"] is True
    assert result["verdict"] == "READY_FOR_CONTROLLED_PRODUCTION"


def test_product_gate_blocks_unclassified_repo_and_core_health_regression(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-cloud")
    _repo(tmp_path, "unknown-repo")
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    health.write_text(
        json.dumps({"repos": {"flyto-core": {"grade": "C", "score": 78}, "flyto-cloud": {"grade": "B", "score": 82}}}),
        encoding="utf-8",
    )

    result = run_product_gate(
        ProductGateOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
        )
    )

    assert result["ok"] is False
    assert {item["code"] for item in result["blockers"]} >= {
        "repo_unclassified",
        "health_below_target",
    }


def test_product_gate_blocks_missing_memory_for_active_repo(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-cloud", memory=False)
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    health.write_text(
        json.dumps({"repos": {"flyto-core": {"grade": "B", "score": 80}, "flyto-cloud": {"grade": "B", "score": 82}}}),
        encoding="utf-8",
    )

    result = run_product_gate(
        ProductGateOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
        )
    )

    assert result["ok"] is False
    assert any(item["code"] == "memory_incomplete" and item["repo"] == "flyto-cloud" for item in result["blockers"])


def test_product_gate_allows_non_core_health_exemption(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-cloud")
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    health.write_text(
        json.dumps(
            {
                "repos": {
                    "flyto-core": {"grade": "B", "score": 80},
                    "flyto-cloud": {
                        "grade": "N/A",
                        "score": None,
                        "exempt": True,
                        "reasons": ["No indexed symbols; docs-only repo."],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = run_product_gate(
        ProductGateOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
        )
    )

    assert result["ok"] is True
    assert not result["warnings"]


def test_product_gate_blocks_core_health_exemption(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-cloud")
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    health.write_text(
        json.dumps(
            {
                "repos": {
                    "flyto-core": {
                        "grade": "N/A",
                        "score": None,
                        "exempt": True,
                        "reasons": ["Core repos must not be exempt."],
                    },
                    "flyto-cloud": {"grade": "B", "score": 82},
                }
            }
        ),
        encoding="utf-8",
    )

    result = run_product_gate(
        ProductGateOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
        )
    )

    assert result["ok"] is False
    assert any(item["code"] == "core_health_exempt" for item in result["blockers"])


def test_product_gate_treats_non_core_health_regression_as_warning(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-cloud")
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    health.write_text(
        json.dumps({"repos": {"flyto-core": {"grade": "B", "score": 80}, "flyto-cloud": {"grade": "C", "score": 72}}}),
        encoding="utf-8",
    )

    result = run_product_gate(
        ProductGateOptions(
            workspace=tmp_path,
            manifest_path=manifest,
            health_report_path=health,
        )
    )

    assert result["ok"] is True
    assert result["verdict"] == "READY_FOR_CONTROLLED_PRODUCTION"
    assert any(item["code"] == "health_below_target" and item["severity"] == "P2" for item in result["warnings"])
    assert result["repos"]["flyto-cloud"]["health_signal"]["role"] == "minimum_hygiene_signal"
