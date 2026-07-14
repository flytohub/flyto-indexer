import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.flyto2_release_packet import _deliverable_specs  # noqa: E402


GENERATED_AT = "2026-06-23T00:00:00+00:00"


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit_repo(repo: Path) -> None:
    _run("git", "-C", str(repo), "add", ".", cwd=repo)
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not status:
        return
    _run(
        "git",
        "-C",
        str(repo),
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "test fixtures",
        cwd=repo,
    )


def _repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _run("git", "-C", str(repo), "init", cwd=repo)
    _commit_repo(repo)
    return repo


def _write(path: Path, content: str = "evidence\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _all_required_evidence(root: Path) -> None:
    for spec in _deliverable_specs():
        for relative in spec.get("required", []):
            _write(root / relative)
    for repo in root.iterdir():
        if repo.is_dir() and (repo / ".git").exists():
            _commit_repo(repo)


def _fresh_json(relative: str) -> dict:
    if relative == "product-verification.json":
        return {
            "contract": "warroom.product_verification.v1",
            "generated_at": GENERATED_AT,
            "site_graph": {"intents": ["smoke"], "state_graph": {"start": ["done"]}},
            "scores": {
                "observed_coverage": 1,
                "reachable_coverage": 1,
                "api_ui_consistency": 1,
                "business_logic_confidence": 1,
            },
            "p0_findings": 0,
        }
    if relative == "public-site-verification.json":
        return {
            "contract": "flyto2.public_site_verification.v1",
            "generated_at": GENERATED_AT,
            "p0_findings": 0,
            "dns_matrix": [{"ok": True}],
            "tls_matrix": [{"ok": True}],
            "route_matrix": [{"ok": True}],
            "browser_matrix": [{"ok": True}],
            "seo_geo_matrix": {"home": {"ok": True}},
            "scores": {
                "public_route_readiness": 1,
                "seo_geo_readiness": 1,
                "browser_render_readiness": 1,
            },
        }
    if relative == "github-actions-startup.json":
        return {
            "schema": "flyto.workspace-github-actions-startup-audit.v1",
            "ok": True,
            "generated_at": GENERATED_AT,
            "repositories": [
                {
                    "repo": "flyto-core",
                    "head": "abc123",
                    "ok": True,
                    "workflows": [
                        {
                            "ok": True,
                            "status": "completed",
                            "conclusion": "success",
                            "jobs": [{"status": "completed", "conclusion": "success"}],
                        }
                    ],
                }
            ],
        }
    return {"generated_at": GENERATED_AT}


def _all_fresh_evidence(root: Path) -> Path:
    evidence_dir = root / "fresh-evidence"
    for spec in _deliverable_specs():
        for relative in spec.get("fresh", []):
            path = evidence_dir / relative
            if path.suffix == ".json":
                _write(path, json.dumps(_fresh_json(relative), indent=2) + "\n")
            else:
                _write(path, "# Fresh evidence\n")
    return evidence_dir


def _manifest(path: Path) -> None:
    _write(
        path,
        json.dumps(
            {
                "product_name": "Flyto2",
                "product_lines": {
                    "core": {"label": "Core"},
                    "ai": {"label": "AI"},
                },
                "memory_files": [],
                "workflow_files": [],
                "health_targets": {"core_min_grade": "C"},
                "repos": {
                    "flyto-core": {
                        "status": "active",
                        "core": True,
                        "product_lines": ["core"],
                        "core_dependency": "core runtime",
                        "memory_required": False,
                    },
                    "flyto-ai": {
                        "status": "active",
                        "core": False,
                        "product_lines": ["ai"],
                        "core_dependency": "optional ai",
                        "memory_required": False,
                    },
                },
            },
            indent=2,
        )
        + "\n",
    )


def _health(path: Path) -> None:
    _write(
        path,
        json.dumps(
            {
                "repos": {
                    "flyto-core": {"score": 95, "grade": "A", "reasons": []},
                    "flyto-ai": {"score": 90, "grade": "A", "reasons": []},
                }
            },
            indent=2,
        )
        + "\n",
    )


def test_continuous_release_evidence_script_writes_digest_artifacts(tmp_path):
    _repo(tmp_path, "flyto-core")
    _repo(tmp_path, "flyto-ai")
    _all_required_evidence(tmp_path)
    evidence_dir = _all_fresh_evidence(tmp_path)
    manifest = tmp_path / "manifest.json"
    health = tmp_path / "health.json"
    _manifest(manifest)
    _health(health)

    script = Path(__file__).parent.parent / "scripts" / "write_continuous_release_evidence.py"
    subprocess.run(
        [
            sys.executable,
            str(script),
            str(tmp_path),
            str(evidence_dir),
            "--manifest",
            str(manifest),
            "--health-report",
            str(health),
            "--run-start",
            "2026-06-22T00:00:00+00:00",
            "--generated-at",
            GENERATED_AT,
        ],
        check=True,
    )

    workspace_matrix = json.loads((evidence_dir / "workspace-matrix.json").read_text(encoding="utf-8"))
    browser_smoke = json.loads((evidence_dir / "browser-smoke.json").read_text(encoding="utf-8"))
    release_packet = json.loads((evidence_dir / "release-packet.json").read_text(encoding="utf-8"))

    assert workspace_matrix["generated_at"] == GENERATED_AT
    assert workspace_matrix["repo_count"] == 2
    assert "flyto-core" in workspace_matrix["repos"]
    assert (evidence_dir / "architecture-map.md").exists()
    assert (evidence_dir / "billing-entitlement.md").exists()
    assert (evidence_dir / "rbac-tenant-isolation.md").exists()
    assert (evidence_dir / "state-machine.md").exists()
    assert (evidence_dir / "enterprise-airgap.md").exists()
    assert (evidence_dir / "geo-ai-crawler.md").exists()
    assert (evidence_dir / "i18n.md").exists()
    assert (evidence_dir / "security-performance.md").exists()
    assert browser_smoke["deliverable"] == "e2e_browser_smoke_matrix"
    assert "authenticated browser smoke" in browser_smoke["residual"]
    assert release_packet["generated_at"] == GENERATED_AT
