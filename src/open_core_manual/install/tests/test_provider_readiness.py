from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "install/scripts/provider-readiness.py"


def run_readiness(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    clean_env = dict(os.environ)
    for key in list(clean_env):
        if key.startswith("FLYTO_PROVIDER_"):
            clean_env.pop(key)
    if env:
        clean_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env=clean_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_public_release_defaults_to_provider_blocked() -> None:
    result = run_readiness("--json")

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["verdict"] == "CODE_READY_PROVIDER_BLOCKED"
    blocker_ids = {item["gate_id"] for item in report["blockers"]}
    assert {"github_actions_billing", "docker_hub_publish", "domain_dns_tls"} <= blocker_ids
    assert "credentials" not in result.stdout.lower()


def test_allow_provider_blocked_keeps_local_verify_green() -> None:
    result = run_readiness("--allow-provider-blocked")

    assert result.returncode == 0
    assert "verdict: CODE_READY_PROVIDER_BLOCKED" in result.stdout
    assert "no-cost maximum commands:" in result.stdout


def test_ce_local_has_no_paid_provider_gates() -> None:
    result = run_readiness("--scope", "ce_local", "--json")

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["verdict"] == "READY_FOR_LOCAL_CE"
    assert report["blocked_count"] == 0
    assert report["gates"] == []


def test_provider_ready_env_unblocks_public_release() -> None:
    result = run_readiness(
        "--json",
        env={
            "FLYTO_PROVIDER_GITHUB_ACTIONS_READY": "1",
            "FLYTO_PROVIDER_DOCKER_HUB_READY": "true",
            "FLYTO_PROVIDER_DOMAIN_READY": "ready",
        },
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["verdict"] == "READY_FOR_RELEASE"
    assert report["blocked_count"] == 0
