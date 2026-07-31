#!/usr/bin/env python3
"""Fail when configured Ruff or mypy debt changes without review."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = ROOT / "config" / "quality-debt-baseline.json"
MYPY_CODE_RE = re.compile(r"\[([^]]+)]$")
MYPY_PLATFORM = "linux"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _tool_version(command: str) -> str:
    result = _run([command, "--version"])
    if result.returncode != 0:
        raise RuntimeError(f"{command} is unavailable: {result.stderr.strip()}")
    return result.stdout.strip()


def _configured_debt_codes() -> tuple[list[str], list[str]]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ruff_codes = sorted(config["tool"]["ruff"]["lint"].get("ignore", []))
    mypy_codes = sorted(config["tool"]["mypy"].get("disable_error_code", []))
    return ruff_codes, mypy_codes


def _ruff_counts(codes: list[str]) -> dict[str, int]:
    if not codes:
        return {}
    result = _run([
        "ruff",
        "check",
        "src",
        "--config",
        "lint.ignore=[]",
        "--select",
        ",".join(codes),
        "--output-format",
        "json",
        "--exit-zero",
    ])
    if result.returncode != 0:
        raise RuntimeError(f"Ruff debt scan failed: {result.stderr.strip()}")
    findings = json.loads(result.stdout or "[]")
    counts = Counter(str(item.get("code") or "unknown") for item in findings)
    return {code: counts.get(code, 0) for code in codes}


def _mypy_counts(codes: list[str]) -> dict[str, int]:
    if not codes:
        return {}
    command = [
        "mypy",
        "src",
        "--show-error-codes",
        "--no-error-summary",
        "--platform",
        MYPY_PLATFORM,
        "--no-site-packages",
    ]
    for code in codes:
        command.extend(["--enable-error-code", code])
    result = _run(command)
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"mypy debt scan failed: {result.stderr.strip()}")
    counts: Counter[str] = Counter()
    for line in result.stdout.splitlines():
        if " error: " not in line:
            continue
        match = MYPY_CODE_RE.search(line)
        if match:
            counts[match.group(1)] += 1
    return {code: counts.get(code, 0) for code in codes}


def collect_debt() -> dict[str, Any]:
    """Collect deterministic production-source debt for configured exemptions."""
    ruff_codes, mypy_codes = _configured_debt_codes()
    ruff_counts = _ruff_counts(ruff_codes)
    mypy_counts = _mypy_counts(mypy_codes)
    return {
        "schema_version": 1,
        "scope": "src",
        "policy": "exact baseline; every decrease requires a baseline update",
        "environment": {
            "mypy_platform": MYPY_PLATFORM,
            "mypy_site_packages": False,
        },
        "tools": {
            "ruff": _tool_version("ruff"),
            "mypy": _tool_version("mypy"),
        },
        "ruff": {
            "codes": ruff_counts,
            "total": sum(ruff_counts.values()),
        },
        "mypy": {
            "codes": mypy_counts,
            "total": sum(mypy_counts.values()),
        },
    }


def compare_debt(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Return actionable drift messages; exact equality closes baseline headroom."""
    messages: list[str] = []
    if current.get("environment") != baseline.get("environment"):
        messages.append("quality analysis environment differs from the reviewed baseline")
    if current.get("tools") != baseline.get("tools"):
        messages.append("quality tool versions differ from the reviewed baseline")
    for tool in ("ruff", "mypy"):
        current_codes = current.get(tool, {}).get("codes", {})
        baseline_codes = baseline.get(tool, {}).get("codes", {})
        for code in sorted(set(current_codes) | set(baseline_codes)):
            now = int(current_codes.get(code, 0))
            before = int(baseline_codes.get(code, 0))
            if now > before:
                messages.append(f"{tool}:{code} increased {before} -> {now}")
            elif now < before:
                messages.append(
                    f"{tool}:{code} improved {before} -> {now}; update the baseline to lock it in"
                )
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prevent ignored Ruff and mypy debt from increasing",
    )
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    current = collect_debt()
    if args.write:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Updated {args.baseline.relative_to(ROOT)}")
        return 0

    if not args.baseline.is_file():
        print(f"Missing quality debt baseline: {args.baseline}", file=sys.stderr)
        return 2
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    messages = compare_debt(current, baseline)
    if args.json:
        print(json.dumps({"pass": not messages, "drift": messages, **current}, indent=2))
    elif messages:
        print("Quality debt ratchet failed:", file=sys.stderr)
        for message in messages:
            print(f"- {message}", file=sys.stderr)
    else:
        print(
            "Quality debt ratchet passed: "
            f"Ruff={current['ruff']['total']}, mypy={current['mypy']['total']}"
        )
    return 1 if messages else 0


if __name__ == "__main__":
    raise SystemExit(main())
