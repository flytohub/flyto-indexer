import json
from pathlib import Path

from src.flyto2_memory_bootstrap import MemoryBootstrapOptions, run_memory_bootstrap


def write_manifest(path: Path) -> Path:
    manifest = {
        "product_lines": {
            "security": {"name": "Flyto2 Security"},
        },
        "repos": {
            "repo-a": {
                "status": "active",
                "memory_required": True,
                "product_lines": ["security"],
                "core_dependency": "uses core",
                "health_target": "B",
            },
            "repo-old": {
                "status": "deprecated",
                "memory_required": True,
                "product_lines": ["security"],
            },
        },
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_memory_bootstrap_dry_run_does_not_write(tmp_path):
    (tmp_path / "repo-a").mkdir()
    manifest = write_manifest(tmp_path / "manifest.json")

    result = run_memory_bootstrap(
        MemoryBootstrapOptions(workspace=tmp_path, manifest_path=manifest, apply=False)
    )

    assert result["created"]
    assert not (tmp_path / "repo-a" / "AGENTS.md").exists()
    assert result["skipped_repos"] == [{"repo": "repo-old", "reason": "deprecated"}]


def test_memory_bootstrap_apply_writes_missing_only(tmp_path):
    repo = tmp_path / "repo-a"
    repo.mkdir()
    existing = repo / "PROJECT.md"
    existing.write_text("custom project\n", encoding="utf-8")
    manifest = write_manifest(tmp_path / "manifest.json")

    result = run_memory_bootstrap(
        MemoryBootstrapOptions(workspace=tmp_path, manifest_path=manifest, apply=True)
    )

    assert (repo / "AGENTS.md").exists()
    assert (repo / "workflows" / "planning.md").exists()
    assert (repo / "handoffs" / "_registry.md").exists()
    assert existing.read_text(encoding="utf-8") == "custom project\n"
    assert str(existing) in result["skipped_existing"]
