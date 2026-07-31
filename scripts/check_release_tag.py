#!/usr/bin/env python3
"""Verify that a release tag matches package metadata and the changelog."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def package_version(pyproject: Path = ROOT / "pyproject.toml") -> str:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def release_errors(
    tag: str,
    *,
    pyproject: Path = ROOT / "pyproject.toml",
    changelog: Path = ROOT / "CHANGELOG.md",
) -> list[str]:
    version = package_version(pyproject)
    expected_tag = f"v{version}"
    errors: list[str] = []
    if tag != expected_tag:
        errors.append(f"tag {tag!r} does not match package version {version!r}; expected {expected_tag!r}")

    heading = f"## [{version}] -"
    changelog_text = changelog.read_text(encoding="utf-8")
    if heading not in changelog_text:
        errors.append(f"CHANGELOG.md has no dated release heading starting with {heading!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="release tag, for example v2.18.0")
    args = parser.parse_args()

    errors = release_errors(args.tag)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"release tag PASS: {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
