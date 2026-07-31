#!/usr/bin/env python3
"""Validate and render the public per-language evidence matrix."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "benchmarks" / "language-evidence.json"
MANIFEST = ROOT / "benchmarks" / "fixture" / "corpus" / "manifest.json"
DOCUMENT = ROOT / "docs" / "LANGUAGE_EVIDENCE.md"
VALID_LEVELS = {"gated", "positive-only", "indexing-only"}


def _case_counts(manifest: dict[str, Any]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for case in manifest.get("cases", []):
        language = str(case.get("language") or "unknown")
        bucket = counts.setdefault(language, Counter())
        bucket["cases"] += 1
        bucket["positive"] += int(case.get("kind") == "positive")
        bucket["negative"] += int(case.get("kind") == "negative")
    return {language: dict(values) for language, values in counts.items()}


def validate_evidence(
    config: dict[str, Any],
    manifest: dict[str, Any],
) -> list[str]:
    """Reject missing languages and claims stronger than the committed corpus."""
    messages: list[str] = []
    languages = config.get("languages", {})
    counts = _case_counts(manifest)
    for language in sorted(counts):
        if language not in languages:
            messages.append(f"benchmark language is undocumented: {language}")
    for language, evidence in sorted(languages.items()):
        level = evidence.get("evidence_level")
        if level not in VALID_LEVELS:
            messages.append(f"{language} has invalid evidence level: {level}")
            continue
        bucket = counts.get(language, {})
        positive = int(bucket.get("positive", 0))
        negative = int(bucket.get("negative", 0))
        if level == "gated" and not (positive and negative):
            messages.append(f"{language} claims gated evidence without positive and negative cases")
        if level == "positive-only" and not (positive and not negative):
            messages.append(f"{language} positive-only claim does not match the corpus")
        if level == "indexing-only" and (positive or negative):
            messages.append(f"{language} has benchmark cases but is labeled indexing-only")
        for field in (
            "label",
            "indexing",
            "relationship_analysis",
            "security_analysis",
            "limitations",
        ):
            if not str(evidence.get(field) or "").strip():
                messages.append(f"{language} is missing {field}")
    return messages


def render_markdown(config: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Render the evidence-first matrix from the machine-readable contract."""
    counts = _case_counts(manifest)
    rows = []
    for language, evidence in config["languages"].items():
        bucket = counts.get(language, {})
        case_summary = (
            f"{int(bucket.get('cases', 0))} "
            f"({int(bucket.get('positive', 0))} positive / "
            f"{int(bucket.get('negative', 0))} negative)"
        )
        rows.append(
            "| {label} | {indexing} | {relationships} | {security} | {cases} | {level} |".format(
                label=evidence["label"],
                indexing=evidence["indexing"],
                relationships=evidence["relationship_analysis"],
                security=evidence["security_analysis"],
                cases=case_summary,
                level=evidence["evidence_level"],
            )
        )

    limitations = "\n".join(
        f"- **{evidence['label']}:** {evidence['limitations']}"
        for evidence in config["languages"].values()
    )
    return "\n".join([
        "# Language evidence",
        "",
        "Flyto2 Indexer does not claim identical precision across every supported language.",
        "This page separates built-in indexing coverage from security-analysis depth and",
        "committed benchmark evidence. The source of truth is",
        "[`benchmarks/language-evidence.json`](../benchmarks/language-evidence.json); CI",
        "rejects claims that are stronger than the checked-in corpus.",
        "",
        "## Capability and proof matrix",
        "",
        "| Language | Indexing | Relationship analysis | Security analysis | Committed cases | Evidence level |",
        "| --- | --- | --- | --- | ---: | --- |",
        *rows,
        "",
        "`gated` means the offline release corpus contains both positive and negative",
        "cases for that language. `positive-only` is narrower evidence. `indexing-only`",
        "means the parser is tested by the main test suite, but no standalone accuracy",
        "corpus is claimed.",
        "",
        "## Known limits",
        "",
        limitations,
        "",
        "The matrix describes static evidence. Runtime behavior still belongs to project-owned",
        "browser, service, integration, race, container, and deployment tests.",
        "",
        "## Verify the claims",
        "",
        "```bash",
        "python benchmarks/evaluate.py --check --json",
        "python scripts/check_language_evidence.py --check",
        "```",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the public language evidence matrix")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    messages = validate_evidence(config, manifest)
    if messages:
        for message in messages:
            print(message, file=sys.stderr)
        return 1
    rendered = render_markdown(config, manifest)
    if args.write:
        DOCUMENT.write_text(rendered, encoding="utf-8")
        print(f"Updated {DOCUMENT.relative_to(ROOT)}")
        return 0
    if not DOCUMENT.is_file() or DOCUMENT.read_text(encoding="utf-8") != rendered:
        print("Language evidence document is stale; run with --write", file=sys.stderr)
        return 1
    print("Language evidence is honest and synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
