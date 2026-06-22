import json
from pathlib import Path
import subprocess
import sys


def test_product_verification_evidence_script_writes_contract_artifacts(tmp_path):
    script = Path(__file__).parent.parent / "scripts" / "write_product_verification_evidence.py"
    generated_at = "2026-06-23T00:00:00+00:00"

    subprocess.run(
        [sys.executable, str(script), str(tmp_path), "--generated-at", generated_at],
        check=True,
    )

    data = json.loads((tmp_path / "product-verification.json").read_text(encoding="utf-8"))
    assert data["contract"] == "warroom.product_verification.v1"
    assert data["generated_at"] == generated_at
    assert data["evidence_mode"] == "local_dry_run"
    assert data["p0_findings"] == 0
    assert data["site_graph"]["intents"]
    assert data["site_graph"]["state_graph"]
    assert data["scores"]["reachable_coverage"] == 1.0
    assert "authenticated staging smoke" in (tmp_path / "product-verification.md").read_text(
        encoding="utf-8"
    )
