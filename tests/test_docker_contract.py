"""Regression checks for the reproducible scanner image dependency window."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_semgrep_mcp_override_stays_on_tested_secure_v1():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert '"semgrep==1.170.0"' in dockerfile
    assert 'pip install --upgrade "mcp==1.29.0"' in dockerfile
    assert "from mcp.server.fastmcp import FastMCP" in dockerfile
    assert 'pip install --upgrade "mcp>=1.28.1"' not in dockerfile
