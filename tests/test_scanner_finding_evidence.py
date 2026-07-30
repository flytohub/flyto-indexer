"""Common finding-evidence contract across the lightweight scanners."""

from src.iac_scanner import IaCFinding
from src.secret_scanner import (
    SecretFinding,
    VulnerabilityFinding,
    scan_code_vulnerabilities,
)


def _assert_envelope(finding: dict, expected_origin: str) -> None:
    assert finding["schema"] == "finding-evidence.v1"
    assert finding["finding_id"].startswith("flyto-")
    assert len(finding["fingerprint"]) == 64
    assert finding["origin"] == expected_origin
    assert finding["confidence"]["level"] in {"medium", "high"}
    assert finding["trace"]
    assert finding["suppression"] == {
        "status": "active",
        "mechanism": "none",
    }


def test_secret_finding_has_privacy_preserving_evidence():
    finding = SecretFinding(
        file="src/settings.py",
        line=9,
        pattern="github_token",
        severity="high",
        masked_value="ghp_***",
    ).to_dict()

    _assert_envelope(finding, "secret.regex")
    assert finding["confidence"]["level"] == "high"
    assert "raw-secret" not in str(finding)


def test_sast_scanner_emits_common_evidence(tmp_path):
    source = tmp_path / "app.py"
    source.write_text(
        "def run(command):\n    os.system(command)\n",
        encoding="utf-8",
    )

    result = scan_code_vulnerabilities(tmp_path)

    assert result["total_findings"] == 1
    finding = result["findings"][0]
    _assert_envelope(finding, "sast.regex")
    assert finding["rule_id"] == "CMDI-PY"
    assert finding["finding_rule_id"] == "sast/CMDI-PY"
    assert finding["cwe"] == "CWE-78"


def test_iac_finding_emits_common_evidence():
    finding = IaCFinding(
        file_path="infra/main.tf",
        resource_type="aws_s3_bucket",
        check_id="IAC_TF_PUBLIC_S3",
        check_name="Public S3 bucket",
        severity="HIGH",
        line=12,
        framework="terraform",
    ).to_dict()

    _assert_envelope(finding, "iac.terraform")
    assert finding["rule_id"] == "iac/IAC_TF_PUBLIC_S3"
    assert finding["check_id"] == "IAC_TF_PUBLIC_S3"
    assert finding["trace"][1]["resource_type"] == "aws_s3_bucket"
