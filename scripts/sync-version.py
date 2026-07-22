#!/usr/bin/env python3
"""Synchronize static package manifests with the pyproject version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = (ROOT / "server.json", ROOT / ".mcp" / "server.json")


def package_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def synchronized_manifest(path: Path, version: str) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    for package in data.get("packages", []):
        if package.get("identifier") == "flyto-indexer":
            package["version"] = version
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail instead of updating drift")
    args = parser.parse_args()
    version = package_version()
    stale: list[str] = []

    for path in MANIFESTS:
        expected = synchronized_manifest(path, version)
        if path.read_text(encoding="utf-8") == expected:
            continue
        if args.check:
            stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.write_text(expected, encoding="utf-8")

    if stale:
        print("version metadata is stale: " + ", ".join(stale), file=sys.stderr)
        return 1
    print(f"version metadata PASS: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
