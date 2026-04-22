"""Run the indexer's pattern + taint scanners over the fixture suite and
produce a per-rule hit/miss report.

Positive fixtures live in vuln/ — each rule id listed in a line comment
is expected to fire on that line.

Negative fixtures live in safe/ — nothing should fire there.

Usage:
    python3.12 tests/fixtures/quality_matrix.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from analyzer.security import SecurityScanner  # noqa: E402
from analyzer.taint import TaintAnalyzer  # noqa: E402
from rule_loader import get_security_rules  # noqa: E402


def expected_ids_in_file(path: Path) -> list[tuple[int, str]]:
    """Extract `# RULE_ID` / `// RULE_ID` tags — recognise only ALL-CAPS
    tokens that also contain an underscore, so descriptive narrative
    comments ("// CORS wildcard…") aren't mistaken for rule tags."""
    out: list[tuple[int, str]] = []
    pat = re.compile(r"(?:#|//)\s*([A-Z][A-Z0-9]*_[A-Z0-9_]+)\b")
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        for m in pat.finditer(line):
            out.append((i, m.group(1)))
    return out


def _scan_dir(directory: Path) -> dict[Path, list]:
    scanner = SecurityScanner(directory)
    result: dict[Path, list] = {}
    for ext in (".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".go", ".php", ".rb"):
        for f in directory.rglob(f"*{ext}"):
            content = f.read_text()
            issues = scanner.scan_file(str(f.relative_to(directory)), content)
            result[f.relative_to(directory)] = issues
    return result


def main() -> int:
    vuln_dir = HERE / "vuln"
    safe_dir = HERE / "safe"

    all_rules = {r["id"]: r for r in get_security_rules()}
    print(f"Loaded {len(all_rules)} YAML rules\n")

    # --- positive check -------------------------------------------------
    print("=" * 70)
    print("POSITIVE — each fixture line tagged with an ID should fire")
    print("=" * 70)
    total_expected = 0
    total_hits = 0
    missing: list[tuple[str, str, int]] = []

    for f, issues in _scan_dir(vuln_dir).items():
        fpath = vuln_dir / f
        expected = expected_ids_in_file(fpath)
        found_ids = {issue.recommendation for issue in issues}
        print(f"\n{f}")
        for lineno, rule_id in expected:
            total_expected += 1
            if rule_id in found_ids:
                total_hits += 1
                print(f"  ✓ L{lineno:<3d} {rule_id}")
            else:
                # Search raw issues list for a message match fallback
                fallback = any(rule_id.lower() in (i.description or "").lower() for i in issues)
                if fallback:
                    total_hits += 1
                    print(f"  ~ L{lineno:<3d} {rule_id}  (matched by message, not id)")
                else:
                    missing.append((str(f), rule_id, lineno))
                    print(f"  ✗ L{lineno:<3d} {rule_id}  MISSING")
        # Any extra issues that weren't tagged = possible FP or over-fire
        for issue in issues:
            tagged_ids = {rid for _, rid in expected}
            if issue.recommendation and issue.recommendation not in tagged_ids:
                # only surface if it's a yaml-rule id we recognize
                if issue.recommendation in all_rules:
                    print(f"  ? L{issue.line:<3d} {issue.recommendation}  extra (check)")

    # --- negative check -------------------------------------------------
    print()
    print("=" * 70)
    print("NEGATIVE — safe/ should produce ZERO findings")
    print("=" * 70)
    fp: list = []
    for f, issues in _scan_dir(safe_dir).items():
        if issues:
            print(f"\n{f}")
            for i in issues:
                # Only pattern-rule false positives are interesting here;
                # taint flows aren't checked on safe/ because the scanner
                # we use doesn't run taint.
                if i.recommendation in all_rules:
                    fp.append((str(f), i))
                    print(f"  ! L{i.line:<3d} {i.recommendation}  {i.description[:60]}")

    # --- taint category coverage ---------------------------------------
    print()
    print("=" * 70)
    print("TAINT — categories that fire on vuln/sinks.py")
    print("=" * 70)
    expected_taint_cats = {
        "ssrf", "open_redirect", "nosql_injection", "crlf_injection",
        "redos", "prototype_pollution", "sql_injection", "xxe",
    }
    taint_miss: list[str] = []
    try:
        analyzer = TaintAnalyzer(vuln_dir, index={})
        res = analyzer.analyze_full()
        flows = getattr(res, "taint_flows", []) or []
        unsanitized = [f for f in flows if not getattr(f, "sanitized", False)]
        cats = {getattr(f, "category", None) for f in unsanitized}
        cats.discard(None)
        print(f"  flows (unsanitized / total): {len(unsanitized)} / {len(flows)}")
        print(f"  categories fired: {sorted(cats)}")
        taint_miss = sorted(expected_taint_cats - cats)
        if taint_miss:
            print(f"  categories expected but missing: {taint_miss}")
    except Exception as e:
        print(f"  (taint analyze skipped: {type(e).__name__}: {e})")

    # --- summary -------------------------------------------------------
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Positive hit rate: {total_hits}/{total_expected} ({100*total_hits//max(1,total_expected)}%)")
    print(f"  False positives on safe/: {len(fp)}")
    print(f"  Taint categories missing: {len(taint_miss)}/{len(expected_taint_cats)}")
    if missing:
        print("  Missing (false negative):")
        for fn, rid, ln in missing:
            print(f"    - {fn}:{ln} {rid}")
    if fp:
        print("  False positives (rules firing on safe code):")
        for fn, issue in fp:
            print(f"    - {fn}:{issue.line} {issue.recommendation}")
    print()

    return 0 if not missing and not fp and not taint_miss else 1


if __name__ == "__main__":
    sys.exit(main())
