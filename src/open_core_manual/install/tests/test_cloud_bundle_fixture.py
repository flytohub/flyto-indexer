from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import os
from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "install/scripts/build-cloud-bundle-fixture.py"


def _run_builder(tmp_path: Path, secret: str = "test-import-secret") -> tuple[subprocess.CompletedProcess[str], Path]:
    output = tmp_path / "bundle"
    env = {**os.environ, "FLYTO_WARROOM_BUNDLE_HMAC_SECRET": secret}
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output",
            str(output),
            "--created-at",
            "2026-07-01T00:00:00Z",
            "--json",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, output


def test_cloud_bundle_fixture_writes_signed_manifest(tmp_path: Path) -> None:
    secret = "test-import-secret"
    result, output = _run_builder(tmp_path, secret=secret)

    assert result.returncode == 0, result.stderr
    manifest_path = output / "flyto-bundle.yaml"
    assert manifest_path.exists()
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    assert manifest["kind"] == "flyto.warroom.bundle.v1"
    assert manifest["bundle_id"] == "flyto2-warroom-smoke"
    assert manifest["producer"] == "flyto-warroom-ce-fixture"
    assert manifest["secrets_policy"] == "runtime_args_only"
    assert manifest["required_runtime_args"] == ["username", "password"]
    assert sorted(manifest["assets"]) == [
        "recipes/flyto2-ui-login-smoke.yaml",
        "recipes/flyto2-ui-smoke.yaml",
        "recipes/warroom-deterministic-audit.yaml",
    ]

    for rel_path in manifest["assets"]:
        asset_path = output / rel_path
        assert asset_path.exists()
        assert manifest["hashes"][rel_path] == hashlib.sha256(asset_path.read_bytes()).hexdigest()

    payload = deepcopy(manifest)
    signature = payload.pop("signature")
    canonical = yaml.safe_dump(payload, sort_keys=True, allow_unicode=False).encode("utf-8")
    expected = "hmac-sha256:" + hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    assert signature == expected
    assert secret not in manifest_path.read_text(encoding="utf-8")

    scenario_ids = [
        scenario["scenario_id"]
        for recipe in manifest["recipes"]
        for scenario in recipe.get("scenarios", [])
    ]
    assert "authenticated-pentest" in scenario_ids
    assert "product-verification" in scenario_ids


def test_cloud_bundle_fixture_requires_signing_secret(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    env = dict(os.environ)
    env.pop("FLYTO_WARROOM_BUNDLE_HMAC_SECRET", None)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "FLYTO_WARROOM_BUNDLE_HMAC_SECRET is required" in result.stderr
    assert not output.exists()
