#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

REQUIRED_MARKERS = {
    "README.md": [
        "BYO offensive validation platform",
        "Bring your own tools",
        "turns their findings into verified attack paths, pentest evidence, and red-team scenarios",
        "self-hosted open-core security warroom",
        "scanner-only dashboard",
        "Findings -> Attack Paths -> Offensive Validation -> Evidence -> Remediation",
        "Existing security tools are inputs",
        "CE is useful without Flyto Cloud",
        "Enterprise Cloud Bridge",
    ],
    "docs/docker-hub-overview.md": [
        "BYO offensive validation platform",
        "Bring your own tools",
        "turns their findings into verified attack paths, pentest evidence, and red-team scenarios",
        "self-hosted open-core security warroom",
        "Not a scanner-only image",
        "Findings -> Attack Paths -> Offensive Validation -> Evidence -> Remediation",
        "Enterprise Path",
    ],
    "docs/feature-matrix.md": [
        "BYO offensive validation platform",
        "turns existing tool findings into verified attack paths, pentest evidence, and red-team scenarios",
        "self-hosted open-core security warroom",
        "not a scanner-only dashboard",
        "Findings -> Attack Paths -> Offensive Validation -> Evidence -> Remediation",
        "Premium actions must fail closed",
        "Contribution Boundary",
    ],
    "docs/public-roadmap.md": [
        "Findings -> Attack Paths -> Offensive Validation -> Evidence -> Remediation",
        "bring your own tools",
        "turn their findings into attack paths, replayable evidence, and remediation records",
        "Shipped In CE",
        "Enterprise Cloud Bridge",
        "Non-Claims",
    ],
    "docs/autofix-whitepaper.md": [
        "BYO offensive validation platform",
        "turns existing tool findings into verified attack paths, pentest evidence, and red-team scenarios",
        "evidence-backed remediation",
        "detect, triage, remediate, verify, audit, and rerun",
        "AI may propose, but it cannot be the final authorization gate",
        "False positives are not noise to hide",
    ],
    "docs/benchmark-evidence.md": [
        "BYO offensive validation loop",
        "Findings -> Attack Paths -> Offensive Validation -> Evidence -> Remediation",
        "originating tool, import contract, asset, and normalized evidence",
        "live results from seeded/demo data",
        "fabricated percentage",
    ],
    "docs/README.md": [
        "BYO offensive validation platform",
        "bring your own tools",
        "verified attack paths, pentest evidence, and red-team scenarios",
        "self-hosted open-core security warroom",
        "not a scanner-only dashboard",
    ],
}

DANGEROUS_CLAIMS = [
    (re.compile(r"\bfully replaces?\s+Aikido\b", re.IGNORECASE), "Do not claim full replacement of Aikido."),
    (re.compile(r"\bcomplete replacement\s+(for|of)\s+Aikido\b", re.IGNORECASE), "Do not claim complete replacement of Aikido."),
    (re.compile(r"\bguaranteed coverage\b", re.IGNORECASE), "Do not claim guaranteed coverage."),
    (re.compile(r"\b100%\s+AutoFix\s+success\b", re.IGNORECASE), "Do not claim 100% AutoFix success."),
    (re.compile(r"\bno false positives\b", re.IGNORECASE), "Do not claim zero false positives."),
    (re.compile(r"\bbenchmark leadership\b", re.IGNORECASE), "Do not claim benchmark leadership without evidence."),
    (re.compile(r"\breplaces?\s+your\s+existing\s+security\s+tools\b", re.IGNORECASE), "Do not claim replacement of the customer's existing stack."),
    (re.compile(r"\bdrop\s+your\s+(scanners|security tools)\b", re.IGNORECASE), "Do not tell users to drop existing tools."),
]

SAFE_NEGATION_HINTS = (
    "does not claim",
    "do not claim",
    "must not be claimed",
    "what must not be claimed",
    "non-claims",
    "unsupported accuracy claims",
)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def normalize(text: str) -> str:
    return " ".join(text.split())


def claim_is_negated(lines: list[str], index: int) -> bool:
    window = " ".join(lines[max(0, index - 4) : index + 1]).lower()
    return any(hint in window for hint in SAFE_NEGATION_HINTS)


def main() -> int:
    blockers: list[str] = []

    for rel, markers in REQUIRED_MARKERS.items():
        path = ROOT / rel
        if not path.exists():
            blockers.append(f"missing positioning file: {rel}")
            continue
        body = read(path)
        normalized_body = normalize(body)
        for marker in markers:
            if normalize(marker) not in normalized_body:
                blockers.append(f"{rel} missing positioning marker: {marker}")

        lines = body.splitlines()
        for index, line in enumerate(lines):
            for pattern, reason in DANGEROUS_CLAIMS:
                if pattern.search(line) and not claim_is_negated(lines, index):
                    blockers.append(f"{rel}:{index + 1}: {reason}")

    if blockers:
        for blocker in blockers:
            print("BLOCKED: " + blocker, file=sys.stderr)
        return 2
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
