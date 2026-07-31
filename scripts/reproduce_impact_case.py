#!/usr/bin/env python3
"""Reproduce the pinned public impact-analysis case study."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "https://github.com/fastapi/full-stack-fastapi-template.git"
REPOSITORY_PAGE = "https://github.com/fastapi/full-stack-fastapi-template"
TAG = "0.10.0"
COMMIT = "d40de23896d27d15c17a7bf9649123fd167a0aa8"
TARGET = "render_email_template"
DEPTH = 2
SNAPSHOT = ROOT / "docs" / "evidence" / "fastapi-full-stack-0.10.0.json"
REQUIRED_TRANSITIVE_FUNCTIONS = {
    "backend/app/api/routes/login.py::recover_password",
    "backend/app/api/routes/login.py::recover_password_html_content",
    "backend/app/api/routes/users.py::create_user",
    "backend/app/api/routes/utils.py::test_email",
}


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd or ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        rendered = " ".join(command)
        raise RuntimeError(
            f"Command failed ({result.returncode}): {rendered}\n{result.stderr.strip()}"
        )
    return result


def _checkout_project(destination: Path) -> Path:
    project = destination / "full-stack-fastapi-template"
    _run([
        "git",
        "clone",
        "--quiet",
        "--depth",
        "1",
        "--branch",
        TAG,
        REPOSITORY,
        str(project),
    ])
    return project


def _verify_commit(project: Path) -> None:
    actual = _run(["git", "-C", str(project), "rev-parse", "HEAD"]).stdout.strip()
    if actual != COMMIT:
        raise RuntimeError(f"Pinned source changed: expected {COMMIT}, got {actual}")


def _text_search(project: Path) -> list[dict[str, Any]]:
    result = _run([
        "git",
        "-C",
        str(project),
        "grep",
        "-n",
        "-w",
        TARGET,
        "--",
        "backend",
        "frontend",
    ])
    matches = []
    for line in result.stdout.splitlines():
        path, line_number, text = line.split(":", 2)
        matches.append({
            "path": path,
            "line": int(line_number),
            "text": text.strip(),
        })
    return matches


def _indexer_result(project: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT) if not existing else f"{ROOT}{os.pathsep}{existing}"
    scan = _run(
        [sys.executable, "-m", "src.cli", "scan", str(project), "--full"],
        env=env,
    )
    impact = _run(
        [
            sys.executable,
            "-m",
            "src.cli",
            "impact",
            TARGET,
            "--path",
            str(project),
            "--depth",
            str(DEPTH),
        ],
        env=env,
    )
    return json.loads(scan.stdout), json.loads(impact.stdout)


def build_evidence(
    text_matches: list[dict[str, Any]],
    scan: dict[str, Any],
    impact: dict[str, Any],
) -> dict[str, Any]:
    """Build a path-stable public receipt from raw command results."""
    raw_scan_errors = scan.get("errors", 0)
    scan_error_count = (
        len(raw_scan_errors) if isinstance(raw_scan_errors, list) else int(raw_scan_errors or 0)
    )
    affected_functions: set[str] = set()
    affected_files: set[str] = set()
    for level in impact.get("impact_chain", []):
        if int(level.get("depth", 0)) > DEPTH:
            continue
        for item in level.get("affected", []):
            path = str(item.get("path") or "")
            if path:
                affected_files.add(path)
            if item.get("type") == "function":
                affected_functions.add(f"{path}::{item.get('name')}")

    text_files = sorted({str(match["path"]) for match in text_matches})
    missing_from_text = sorted(
        function
        for function in affected_functions
        if function.split("::", 1)[0] not in text_files
    )
    required_found = sorted(REQUIRED_TRANSITIVE_FUNCTIONS & affected_functions)
    required_missing = sorted(REQUIRED_TRANSITIVE_FUNCTIONS - affected_functions)
    stable = {
        "schema_version": 1,
        "case": {
            "repository": REPOSITORY_PAGE,
            "tag": TAG,
            "commit": COMMIT,
            "license": "MIT",
            "target": TARGET,
            "depth": DEPTH,
        },
        "text_search": {
            "command": f"git grep -n -w {TARGET} -- backend frontend",
            "matching_lines": len(text_matches),
            "matching_files": text_files,
        },
        "indexer": {
            "files_scanned": int(scan.get("files_scanned", 0)),
            "symbols_found": int(scan.get("symbols_found", 0)),
            "scan_errors": scan_error_count,
            "direct_references": int(impact.get("total_direct_references", 0)),
            "affected_functions_through_depth_2": sorted(affected_functions),
            "affected_files_through_depth_2": sorted(affected_files),
        },
        "proof": {
            "required_transitive_functions": sorted(REQUIRED_TRANSITIVE_FUNCTIONS),
            "required_found": required_found,
            "required_missing": required_missing,
            "functions_in_files_missed_by_text_search": missing_from_text,
            "pass": (
                not required_missing
                and bool(missing_from_text)
                and scan_error_count == 0
            ),
        },
        "limits": [
            "This proves static transitive impact discovery, not runtime correctness.",
            "The source commit and expected functions are pinned; no customer code is used.",
        ],
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    return {**stable, "evidence_fingerprint": hashlib.sha256(encoded).hexdigest()}


def compare_snapshot(current: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Require the checked-in public receipt to match the reproducible result."""
    messages = []
    if not current.get("proof", {}).get("pass"):
        messages.append("public proof assertions failed")
    if current != expected:
        messages.append("public proof snapshot is stale")
    return messages


def _evaluate(project: Path) -> dict[str, Any]:
    _verify_commit(project)
    text_matches = _text_search(project)
    scan, impact = _indexer_result(project)
    return build_evidence(text_matches, scan, impact)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce the pinned FastAPI full-stack impact case",
    )
    parser.add_argument("--project", type=Path, help="Use an existing pinned checkout")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-snapshot", action="store_true")
    args = parser.parse_args()

    if args.project:
        evidence = _evaluate(args.project.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="flyto-public-proof-") as tmpdir:
            evidence = _evaluate(_checkout_project(Path(tmpdir)))

    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")

    if not evidence.get("proof", {}).get("pass"):
        return 1
    if args.check_snapshot:
        if not SNAPSHOT.is_file():
            print(f"Missing snapshot: {SNAPSHOT}", file=sys.stderr)
            return 2
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        messages = compare_snapshot(evidence, expected)
        for message in messages:
            print(message, file=sys.stderr)
        return 1 if messages else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
