"""Regression checks for the reproducible scanner image dependency window."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_scanner_dependency_overrides_stay_on_tested_secure_versions():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert '"semgrep==1.170.0"' in dockerfile
    assert '"checkov==3.3.10"' in dockerfile
    assert '"aiohttp==3.14.3"' in dockerfile
    assert '"mcp==1.29.0"' in dockerfile
    assert '"msgpack==1.2.1"' in dockerfile
    assert '"setuptools==83.0.0"' in dockerfile
    assert "from mcp.server.fastmcp import FastMCP" in dockerfile
    assert 'pip install --upgrade "mcp>=1.28.1"' not in dockerfile
    assert '"checkov==3.3.8"' not in dockerfile


def test_runtime_image_applies_fixable_os_security_updates():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "apt-get upgrade -y" in dockerfile
    assert "dpkg --compare-versions" in dockerfile
    assert '"$(dpkg-query -W -f=\'${Version}\' libexpat1)"' in dockerfile
    assert 'ge "2.8.2-1~deb13u1"' in dockerfile


def test_trivy_skips_only_pip_vendor_sbom_without_weakening_gate():
    workflow = (ROOT / ".github/workflows/docker.yml").read_text(encoding="utf-8")

    assert (
        "skip-files: "
        "/usr/local/lib/python3.12/site-packages/pip/_vendor/bom.cdx.json"
    ) in workflow
    assert workflow.count("skip-files:") == 1
    assert "severity: HIGH,CRITICAL" in workflow
    assert "exit-code: '1'" in workflow
