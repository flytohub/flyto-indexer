#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "install/fixtures/cloud-bundle"
MANIFEST_TEMPLATE = "bundle-template.yaml"
OUTPUT_MANIFEST = "flyto-bundle.yaml"
SECRET_ENV = "FLYTO_WARROOM_BUNDLE_HMAC_SECRET"
KIND = "flyto.warroom.bundle.v1"
BUNDLE_ID = "flyto2-warroom-smoke"
FORBIDDEN_FIELD_FRAGMENTS = {
    "authorization",
    "cookie",
    "firebase_session",
    "password",
    "pat",
    "token",
}


class BundleBuildError(ValueError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a signed Flyto Cloud Warroom bundle fixture")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="fixture source directory")
    parser.add_argument("--output", required=True, help="output bundle directory")
    parser.add_argument("--producer", default="flyto-warroom-ce-fixture")
    parser.add_argument("--created-at", default=None, help="UTC ISO timestamp; defaults to now")
    parser.add_argument("--force", action="store_true", help="replace an existing output directory")
    parser.add_argument("--json", action="store_true", help="print machine-readable summary")
    args = parser.parse_args()

    try:
        source = Path(args.source).expanduser().resolve()
        output = Path(args.output).expanduser().resolve()
        manifest = build_bundle(
            source=source,
            output=output,
            producer=args.producer,
            created_at=args.created_at,
            force=args.force,
        )
    except BundleBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary = {
        "ok": True,
        "bundle_id": manifest["bundle_id"],
        "kind": manifest["kind"],
        "manifest": str(output / OUTPUT_MANIFEST),
        "asset_count": len(manifest["assets"]),
        "scenario_ids": _scenario_ids(manifest),
    }
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"ok: wrote {summary['manifest']} ({summary['asset_count']} assets)")
    return 0


def build_bundle(
    *,
    source: Path,
    output: Path,
    producer: str,
    created_at: str | None,
    force: bool = False,
) -> dict[str, Any]:
    secret = os.getenv(SECRET_ENV, "")
    if not secret:
        raise BundleBuildError(f"{SECRET_ENV} is required")
    if not source.exists() or not source.is_dir():
        raise BundleBuildError(f"source directory not found: {source}")
    if output.exists():
        if not force:
            raise BundleBuildError(f"output already exists: {output}; pass --force to replace it")
        if output == source or source in output.parents:
            raise BundleBuildError("refusing to remove a source or nested source directory")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=False)

    template_path = source / MANIFEST_TEMPLATE
    if template_path.is_symlink() or not template_path.is_file():
        raise BundleBuildError(f"manifest template not found: {template_path}")
    manifest = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise BundleBuildError("manifest template must be a mapping")

    assets = _collect_assets(source)
    hashes: dict[str, str] = {}
    for rel in assets:
        src = _resolve_asset(source, rel)
        dest = output / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        hashes[rel] = hashlib.sha256(dest.read_bytes()).hexdigest()

    manifest.update(
        {
            "kind": KIND,
            "producer": producer,
            "bundle_id": BUNDLE_ID,
            "created_at": created_at or _utc_now(),
            "assets": assets,
            "hashes": hashes,
            "secrets_policy": "runtime_args_only",
        }
    )
    _validate_manifest(manifest)
    _assert_no_forbidden_secret_values(manifest)
    manifest["signature"] = _sign_manifest(manifest, secret)

    manifest_path = output / OUTPUT_MANIFEST
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    return manifest


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _collect_assets(source: Path) -> list[str]:
    recipes = source / "recipes"
    if not recipes.exists() or not recipes.is_dir():
        raise BundleBuildError("source must contain a recipes directory")
    assets = []
    for path in sorted(recipes.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source).as_posix()
        _resolve_asset(source, rel)
        assets.append(rel)
    if not assets:
        raise BundleBuildError("source recipes directory contains no assets")
    return assets


def _resolve_asset(root: Path, rel: str) -> Path:
    raw = Path(rel)
    if raw.is_absolute() or ".." in raw.parts:
        raise BundleBuildError(f"unsafe asset path: {rel}")
    resolved = (root / raw).resolve()
    if root not in resolved.parents and resolved != root:
        raise BundleBuildError(f"asset escapes source root: {rel}")
    if resolved.is_symlink():
        raise BundleBuildError(f"asset must not be a symlink: {rel}")
    if not resolved.is_file():
        raise BundleBuildError(f"asset is missing: {rel}")
    return resolved


def _validate_manifest(manifest: dict[str, Any]) -> None:
    required = {
        "kind",
        "producer",
        "bundle_id",
        "created_at",
        "assets",
        "hashes",
        "required_runtime_args",
        "secrets_policy",
        "cloud_target",
        "recipes",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise BundleBuildError(f"manifest missing required fields: {', '.join(missing)}")
    if manifest["kind"] != KIND:
        raise BundleBuildError(f"manifest kind must be {KIND}")
    if manifest["bundle_id"] != BUNDLE_ID:
        raise BundleBuildError(f"manifest bundle_id must be {BUNDLE_ID}")
    if manifest["secrets_policy"] != "runtime_args_only":
        raise BundleBuildError("manifest secrets_policy must be runtime_args_only")
    if not isinstance(manifest["assets"], list) or not all(isinstance(item, str) for item in manifest["assets"]):
        raise BundleBuildError("manifest assets must be a string list")
    if not isinstance(manifest["hashes"], dict):
        raise BundleBuildError("manifest hashes must be a mapping")
    if set(manifest["assets"]) != set(manifest["hashes"]):
        raise BundleBuildError("manifest hashes must cover exactly the asset list")
    if not isinstance(manifest["recipes"], list) or not manifest["recipes"]:
        raise BundleBuildError("manifest recipes must be a non-empty list")


def _assert_no_forbidden_secret_values(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            next_path = (*path, key_text)
            if _is_forbidden_key(key_text) and nested not in (None, "", [], {}):
                raise BundleBuildError(f"manifest stores forbidden field at {'.'.join(next_path)}")
            _assert_no_forbidden_secret_values(nested, next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_forbidden_secret_values(item, (*path, str(index)))


def _is_forbidden_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    for fragment in FORBIDDEN_FIELD_FRAGMENTS:
        if fragment == "pat":
            if normalized in {"pat", "personal_access_token"}:
                return True
            continue
        if fragment in normalized:
            return True
    return False


def _sign_manifest(manifest: dict[str, Any], secret: str) -> str:
    payload = _canonical_payload(manifest)
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def _canonical_payload(manifest: dict[str, Any]) -> bytes:
    payload = deepcopy(manifest)
    payload.pop("signature", None)
    payload.pop("_source_path", None)
    return yaml.safe_dump(payload, sort_keys=True, allow_unicode=False).encode("utf-8")


def _scenario_ids(manifest: dict[str, Any]) -> list[str]:
    ids = []
    for recipe in manifest.get("recipes", []):
        if not isinstance(recipe, dict):
            continue
        for scenario in recipe.get("scenarios", []):
            if isinstance(scenario, dict) and scenario.get("scenario_id"):
                ids.append(str(scenario["scenario_id"]))
    return ids


if __name__ == "__main__":
    raise SystemExit(main())
