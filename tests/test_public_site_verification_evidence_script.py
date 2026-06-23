import json
from pathlib import Path
import subprocess
import sys


def test_public_site_verification_evidence_script_writes_contract_artifacts(tmp_path):
    script = Path(__file__).parent.parent / "scripts" / "write_public_site_verification_evidence.py"
    generated_at = "2026-06-23T00:00:00+00:00"

    subprocess.run(
        [sys.executable, str(script), str(tmp_path), "--generated-at", generated_at, "--fixture-pass"],
        check=True,
    )

    data = json.loads((tmp_path / "public-site-verification.json").read_text(encoding="utf-8"))
    assert data["contract"] == "flyto2.public_site_verification.v1"
    assert data["generated_at"] == generated_at
    assert data["evidence_mode"] == "fixture_pass"
    assert data["p0_findings"] == 0
    assert data["dns_matrix"]
    assert data["tls_matrix"]
    assert data["route_matrix"]
    assert data["browser_matrix"][0]["ok"] is True
    assert data["seo_geo_matrix"]["llms_txt"] is True
    assert "P0 findings: `0`" in (tmp_path / "public-site-verification.md").read_text(
        encoding="utf-8"
    )
