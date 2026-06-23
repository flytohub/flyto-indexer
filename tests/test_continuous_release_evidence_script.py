import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent))

from test_flyto2_release_packet import (  # noqa: E402
    _all_fresh_evidence,
    _all_required_evidence,
    _health,
    _manifest,
    _repo,
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
            "2026-06-23T00:00:00+00:00",
        ],
        check=True,
    )

    workspace_matrix = json.loads((evidence_dir / "workspace-matrix.json").read_text(encoding="utf-8"))
    browser_smoke = json.loads((evidence_dir / "browser-smoke.json").read_text(encoding="utf-8"))
    release_packet = json.loads((evidence_dir / "release-packet.json").read_text(encoding="utf-8"))

    assert workspace_matrix["generated_at"] == "2026-06-23T00:00:00+00:00"
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
    assert release_packet["generated_at"] == "2026-06-23T00:00:00+00:00"
