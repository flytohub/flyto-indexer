"""
Rebuild semgrep_baseline.csv from the Semgrep OSS registry.

Usage:
    python3 build_semgrep_baseline.py [--registry /path/to/semgrep-rules] \
        [--out semgrep_baseline.csv]

If --registry is omitted, the script shallow-clones
https://github.com/semgrep/semgrep-rules into a temp directory.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile

try:
    import yaml  # PyYAML
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")


LANG_DIRS = ["python", "go", "javascript", "typescript", "terraform"]

SEV_MAP = {"INFO": "low", "WARNING": "medium", "ERROR": "high"}

LANG_ORDER = {"python": 0, "javascript": 1, "typescript": 2, "go": 3, "terraform": 4}


def normalize_lang(rel_dir: str) -> str:
    first = rel_dir.split(os.sep, 1)[0]
    return {
        "javascript": "javascript",
        "typescript": "typescript",
        "python":     "python",
        "go":         "go",
        "terraform":  "terraform",
    }.get(first, first)


def clone_registry(dest: str) -> str:
    subprocess.check_call([
        "git", "clone", "--depth", "1",
        "https://github.com/semgrep/semgrep-rules.git", dest,
    ])
    return dest


def extract(registry_root: str) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for lang in LANG_DIRS:
        lang_root = os.path.join(registry_root, lang)
        if not os.path.isdir(lang_root):
            continue
        for dirpath, _, files in os.walk(lang_root):
            for fname in files:
                if not fname.endswith(".yaml"):
                    continue
                if fname.endswith((".test.yaml", ".fixed.yaml")):
                    continue
                path = os.path.join(dirpath, fname)
                try:
                    with open(path) as fh:
                        doc = yaml.safe_load(fh)
                except Exception:
                    continue
                if not isinstance(doc, dict):
                    continue
                rel_dir = os.path.relpath(dirpath, registry_root)
                rel_path = os.path.relpath(path, registry_root)
                for rule in doc.get("rules") or []:
                    rid = rule.get("id")
                    if not rid:
                        continue
                    sev = (rule.get("severity") or "WARNING").upper()
                    meta = rule.get("metadata") or {}
                    if meta.get("category") != "security":
                        continue
                    subcat = meta.get("subcategory") or []
                    if isinstance(subcat, str):
                        subcat = [subcat]
                    # Drop low-confidence audit-only rules — they are useful
                    # as context signals but too noisy for an FP-parity gate.
                    if any(s == "audit" for s in subcat):
                        if (meta.get("confidence") or "").upper() != "HIGH":
                            continue
                    full_id = rel_dir.replace(os.sep, ".") + "." + rid
                    source = f"semgrep/semgrep-rules:{rel_path}"
                    rows.append((full_id, SEV_MAP.get(sev, "medium"), source, normalize_lang(rel_dir)))

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, str, str]] = []
    for row in rows:
        key = (row[0], row[3])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    unique.sort(key=lambda r: (LANG_ORDER.get(r[3], 99), r[0]))
    return unique


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=None,
                    help="Path to a checkout of semgrep/semgrep-rules. "
                         "If omitted, a shallow clone is made in a temp dir.")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "semgrep_baseline.csv"))
    args = ap.parse_args()

    tmp = None
    if args.registry:
        registry_root = args.registry
    else:
        tmp = tempfile.mkdtemp(prefix="semgrep-rules-")
        registry_root = clone_registry(tmp)

    try:
        rows = extract(registry_root)
        with open(args.out, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["rule_id", "expected_severity", "source_repo"])
            for rid, sev, src, _lang in rows:
                writer.writerow([rid, sev, src])
        by_lang: dict[str, int] = {}
        for r in rows:
            by_lang[r[3]] = by_lang.get(r[3], 0) + 1
        print(f"wrote {len(rows)} rules to {args.out}")
        print(f"by language: {by_lang}")
    finally:
        if tmp and os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
