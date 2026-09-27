"""Verification command handlers for the Flyto Indexer CLI.

Argument parsing stays in :mod:`src.cli`; this module translates parsed verify
commands into verification service calls and report artifacts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .verify import (
    format_verification,
    format_workspace_verification,
    render_report,
    run_verification,
    run_workspace_verification,
)


def cmd_verify(args):
    """Run the no-external-dependency verification gate."""
    result = run_verification(
        args.path,
        full_scan=args.full_scan,
        query=args.query,
        symbol=args.symbol,
        strict=args.strict,
        baseline_path=args.baseline,
        regression_only=args.regression_only,
        policy_path=args.policy,
    )

    if getattr(args, "save_baseline", None):
        output_path = Path(args.save_baseline)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if getattr(args, "report", None):
        _write_verify_report(args.report, render_report(result, args.report_format))

    if hasattr(args, "as_json") and args.as_json:
        return result

    print(format_verification(result))
    if not result["pass"]:
        sys.exit(2)
    return None

def cmd_verify_workspace(args):
    """Run verification across a workspace."""
    result = run_workspace_verification(
        args.path,
        project_paths=args.projects,
        full_scan=args.full_scan,
        strict=args.strict,
        baseline_dir=args.baseline_dir,
        regression_only=args.regression_only,
        changed_only=args.changed_only,
        base=args.base,
        policy_path=args.policy,
    )
    if getattr(args, "report", None):
        _write_verify_report(args.report, render_report(result, args.report_format))

    if hasattr(args, "as_json") and args.as_json:
        return result

    print(format_workspace_verification(result))
    if not result["pass"]:
        sys.exit(2)
    return None

def cmd_verify_baseline(args):
    """Create, compare, or update a verification baseline."""
    project_root = Path(args.path).resolve()
    baseline_path = Path(args.baseline).resolve() if args.baseline else (
        Path(args.output_dir).resolve() / f"{project_root.name}.json"
    )
    if args.action in {"create", "update"}:
        result = run_verification(project_root, full_scan=args.full_scan)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "action": args.action, "project": project_root.name, "baseline": str(baseline_path)}

    result = run_verification(
        project_root,
        full_scan=args.full_scan,
        baseline_path=baseline_path,
        regression_only=True,
    )
    if hasattr(args, "as_json") and args.as_json:
        return result
    if not result["pass"]:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(2)
    return result

def _write_verify_report(path: str, content: str) -> None:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")

