"""Change-impact, taint, research-priority, and quality gate CLI handlers."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path


def _detect_changed_files(args, engine, project_path):
    """Detect changed files via git diff (if --base given) or index staleness."""
    if args.base:
        try:
            result = subprocess.run(
                ["git", "-C", str(project_path), "diff", "--name-only", f"{args.base}...HEAD"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return [f for f in result.stdout.strip().split("\n") if f]
            else:
                print(f"git diff failed: {result.stderr.strip()}", file=sys.stderr)
                sys.exit(1)
        except FileNotFoundError:
            print("git not found", file=sys.stderr)
            sys.exit(1)
        except subprocess.TimeoutExpired:
            print("git diff timed out", file=sys.stderr)
            sys.exit(1)

    # Use index staleness — re-scan and detect via incremental
    from .indexer.incremental import scan_directory_hashes

    extensions = []
    for scanner in engine.scanners:
        extensions.extend(scanner.supported_extensions)

    current_hashes = scan_directory_hashes(
        project_path, extensions,
        ignore_patterns=[
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
        ],
    )
    changes = engine.incremental.detect_changes(current_hashes)
    return changes.all_changed()

def _compute_symbol_impact(changed_files, engine):
    """For each changed file, find symbols and compute impact chains.

    Returns (symbol_details, total_affected, all_affected_files).
    """
    total_affected = 0
    all_affected_files = set()
    symbol_details = []

    for rel_path in changed_files:
        for sym_id, sym in engine.index.symbols.items():
            if sym.path == rel_path:
                chain = engine.index.get_impact_chain(sym_id, max_depth=3)
                affected_ids = []
                affected_paths = set()
                for level in chain.get("levels", []):
                    for sid in level.get("symbols", []):
                        affected_ids.append(sid)
                        if sid in engine.index.symbols:
                            affected_paths.add(engine.index.symbols[sid].path)

                if affected_ids:
                    symbol_details.append({
                        "symbol": sym_id,
                        "name": sym.name,
                        "path": sym.path,
                        "affected_count": len(affected_ids),
                        "affected_files": sorted(affected_paths),
                    })
                    total_affected += len(affected_ids)
                    all_affected_files.update(affected_paths)

    return symbol_details, total_affected, all_affected_files

def _format_check_output(output, symbol_details, args):
    """Print check results as JSON or human-readable text."""
    if hasattr(args, "as_json") and args.as_json:
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    print(f"Risk: {output['risk']}")
    print(f"Changed files: {output['changed_files']}")
    print(f"Total affected symbols: {output['total_affected']}")
    print(f"Affected files: {output['affected_files']}")
    print(f"Threshold: {output['threshold']}")
    print()

    if symbol_details:
        for detail in symbol_details[:10]:
            print(f"  {detail['name']} ({detail['path']})")
            print(f"    → {detail['affected_count']} affected symbols in {len(detail['affected_files'])} files")
        if len(symbol_details) > 10:
            print(f"  ... and {len(symbol_details) - 10} more symbols")
        print()

    if output["pass"]:
        print(f"PASS: risk {output['risk']} < threshold {output['threshold']}")
    else:
        print(f"FAIL: risk {output['risk']} >= threshold {output['threshold']}")

def cmd_call_sites(args):
    """Emit per-package call sites + local call graph as JSON.

    Output schema (consumed by flyto-engine's scanner via subprocess):
        {
          "function_calls":  {<pkg>: [<fqn>, ...], ...},
          "local_call_graph": {<user_fqn>: [<callee>, ...], ...},
          "reflection_files": [<path>, ...],
          "source": "lsp" | "regex" | "lsp+regex",
          "stats": {<lang>: {"functions": N, "edges": M}, ...}
        }

    Strategy:
      1. Always run the regex pass — fast, zero deps, works on every file.
      2. If LSP is reachable AND --no-lsp not set, run a second pass
         that walks every function symbol and asks the language server
         for `callHierarchy/outgoingCalls`. Type-aware results take
         precedence over regex matches at the same FQN.

    The merge is union, not replacement — regex catches simple chains
    LSP misses (because LSP needs a function symbol on the line) and
    LSP catches aliased imports / re-exports regex misses.
    """
    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    use_lsp = not args.no_lsp
    out: dict = {
        "function_calls": {},
        "local_call_graph": {},
        "reflection_files": [],
        "source": "regex",
        "stats": {},
    }

    # ── Regex pass — always runs ─────────────────────────────────
    try:
        from .analyzer.call_sites_regex import scan_project_call_sites
    except ImportError:
        from analyzer.call_sites_regex import scan_project_call_sites
    regex_result = scan_project_call_sites(project_path)
    out["function_calls"] = regex_result.get("function_calls", {})
    out["local_call_graph"] = regex_result.get("local_call_graph", {})
    out["reflection_files"] = regex_result.get("reflection_files", [])
    out["stats"]["regex"] = regex_result.get("stats", {})

    # ── LSP pass — opt-in upgrade ───────────────────────────────
    if use_lsp:
        try:
            try:
                from .lsp.manager import LSPManager
            except ImportError:
                from lsp.manager import LSPManager
            try:
                from .analyzer.call_sites_lsp import enrich_with_lsp
            except ImportError:
                from analyzer.call_sites_lsp import enrich_with_lsp

            mgr = LSPManager.get_instance()
            if mgr._enabled:
                lsp_result = enrich_with_lsp(project_path, out)
                if lsp_result.get("edges_added", 0) > 0:
                    out["source"] = "lsp+regex"
                    out["stats"]["lsp"] = lsp_result
        except Exception as e:
            # LSP must never block the regex result. Log to stats so
            # callers can see why the upgrade didn't happen.
            out["stats"]["lsp_error"] = str(e)

    return json.dumps(out, indent=2 if not args.path else None)

def cmd_agent_audit(args):
    """AI-agent security policy audit."""
    from .analyzer.agent_policy import AgentPolicyAnalyzer

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    findings = AgentPolicyAnalyzer(project_path).analyze()
    if getattr(args, "severity", None):
        findings = [f for f in findings if f.severity == args.severity]

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (order.get(f.severity, 9), f.category, f.file_path, f.line))

    if getattr(args, "as_json", False):
        by_cat: dict = {}
        by_band: dict = {}
        for f in findings:
            by_cat[f.category] = by_cat.get(f.category, 0) + 1
            by_band[f.band] = by_band.get(f.band, 0) + 1
        return {
            "total": len(findings),
            "by_category": by_cat,
            "by_band": by_band,  # confirm (auto) | review (→verify/LLM) | drop
            "findings": [f.to_dict() for f in findings],
        }

    print(f"AI-Agent Policy Audit: {project_path.name}")
    print(f"  Findings: {len(findings)}")
    by_band: dict = {}
    for f in findings:
        by_band[f.band] = by_band.get(f.band, 0) + 1
    print(f"  Bands: confirm={by_band.get('confirm', 0)} "
          f"review={by_band.get('review', 0)} drop={by_band.get('drop', 0)}")
    by_cat: dict = {}
    for f in findings:
        by_cat.setdefault(f.category, []).append(f)
    for cat in sorted(by_cat):
        print(f"\n[{cat}] ({len(by_cat[cat])})")
        for f in sorted(by_cat[cat], key=lambda x: -x.exploitability):
            print(f"  [{f.exploitability:3d} {f.band:7}] {f.severity:8} {f.file_path}:{f.line}  ({f.function})")
            print(f"           {f.message}")
    return None

def cmd_taint(args):
    """Analyze data flow / taint tracking."""
    from .analyzer.taint import TaintAnalyzer

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    # Try to load index for cross-function analysis
    index = {}
    index_path = project_path / ".flyto-index" / "index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    t0 = time.monotonic()
    analyzer = TaintAnalyzer(project_path, index=index)
    result = analyzer.analyze_full()
    elapsed = time.monotonic() - t0

    # Filter by severity if requested
    flows = [f for f in result.taint_flows if not f.sanitized]
    if hasattr(args, "severity") and args.severity:
        flows = [f for f in flows if f.severity == args.severity]

    if hasattr(args, "as_json") and args.as_json:
        output = result.to_dict()
        # Apply --max-results to the JSON path too. Pre-fix the flag
        # was honoured only in the human-readable branch (line ~1972);
        # JSON callers (flyto-engine scanner.go passes --max-results=500
        # and downstream caps fall back to MaxTaintFlowsKept=200) were
        # surprised by full unbounded output, especially on large repos.
        max_results = getattr(args, "max_results", 0) or 0
        if args.severity:
            target = flows
        else:
            target = [f for f in result.taint_flows if not f.sanitized]
        if max_results and max_results > 0 and len(target) > max_results:
            target = target[:max_results]
            output["truncated"] = True
            output["max_results"] = max_results
        output["taint_flows"] = [f.to_dict() for f in target]
        output["elapsed_seconds"] = round(elapsed, 2)
        return output

    # Human-readable output
    max_results = getattr(args, "max_results", 50)
    print(f"Taint Analysis: {project_path.name}")
    print(f"  Scanned in {elapsed:.1f}s")
    print(f"  Sources found: {result.total_sources}")
    print(f"  Sinks found:   {result.total_sinks}")
    print(f"  Unsanitized flows: {len(flows)}")
    print(f"  Sanitized flows:   {result.sanitized_flows}")
    print()

    if not flows:
        print("  No unsanitized data flows found.")
        return None

    # Group by severity
    severity_order = ["critical", "high", "medium", "low"]
    by_sev = {}
    for f in flows:
        by_sev.setdefault(f.severity, []).append(f)

    for sev in severity_order:
        sev_flows = by_sev.get(sev, [])
        if not sev_flows:
            continue
        label = sev.upper()
        print(f"  [{label}] {len(sev_flows)} flow(s)")
        for flow in sev_flows[:max_results]:
            src_display = flow.source_expr[:60]
            sink_display = flow.sink_expr[:60]
            print(f"    {flow.category}: {src_display}")
            print(f"      -> {sink_display}")
            print(f"      at {flow.file_path}:{flow.line}")
            if flow.path:
                print(f"      path: {' -> '.join(flow.path[:5])}")
            if flow.recommendation:
                print(f"      fix: {flow.recommendation}")
            print()

    return None

def cmd_research_priority(args):
    """Rank the code paths most worth a security researcher's next hour."""
    from .analyzer.research_priority import rank_research_priority

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    # An index is optional. Without it the test-gap and entry-point signals
    # are reported as unavailable instead of guessed.
    index = {}
    index_path = project_path / ".flyto-index" / "index.json"
    if index_path.exists():
        with contextlib.suppress(Exception):
            index = json.loads(index_path.read_text(encoding="utf-8"))

    report = rank_research_priority(
        project_path,
        index=index or None,
        project=index.get("project") if index else None,
        top_n=getattr(args, "top_n", 20),
        since_days=getattr(args, "since_days", 180),
        include_sanitized=not getattr(args, "no_sanitized", False),
        include_unproven=not getattr(args, "proven_only", False),
        sarif_path=getattr(args, "sarif_path", None),
    )

    if getattr(args, "as_json", False):
        return report.to_dict()

    coverage = report.coverage
    print(f"Security Research Priority: {project_path.name}")
    print(f"  Ranked in {report.elapsed_seconds:.1f}s")
    print(f"  Flows considered: {report.total_flows}")
    print(f"  Candidate paths:  {report.total_candidates}"
          f" (showing {len(report.candidates)})")
    print()

    if coverage.get("truncated"):
        print(f"  ! {coverage.get('truncation_note', 'scan was truncated')}")
        print()

    for rank, candidate in enumerate(report.candidates, 1):
        where = f"{candidate.file}:{candidate.line}"
        if candidate.function:
            where += f" ({candidate.function})"
        print(f"  #{rank:<3} {where:<58} {candidate.score:5.1f}")
        for reason in candidate.reasons:
            print(f"       - {reason}")
        print()

    if not report.candidates:
        print("  No reachable source-to-sink paths found.")
        print("  This is an honest empty result, not a clean bill of health —")
        print("  see the unavailable signals below for what was not measured.")
        print()

    unavailable = coverage.get("signals_unavailable") or []
    if unavailable:
        print("  Signals not measured (excluded from scoring, not scored as zero):")
        for note in unavailable:
            print(f"    - {note}")
        print()

    return None

def cmd_check(args):
    """CI-friendly impact check — exits non-zero when changes are risky."""
    from .engine import IndexEngine

    project_path = Path(args.path).resolve()
    project_name = project_path.name

    engine = IndexEngine(project_name, project_path)

    changed_files = _detect_changed_files(args, engine, project_path)
    symbol_details, total_affected, all_affected_files = _compute_symbol_impact(changed_files, engine)

    # Compute aggregate risk
    if total_affected >= 20:
        risk = "HIGH"
    elif total_affected >= 3:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    risk_levels = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    threshold_level = risk_levels[args.threshold.upper()]
    actual_level = risk_levels[risk]
    should_fail = actual_level >= threshold_level

    output = {
        "risk": risk,
        "changed_files": len(changed_files),
        "total_affected": total_affected,
        "affected_files": len(all_affected_files),
        "threshold": args.threshold.upper(),
        "pass": not should_fail,
        "symbols": symbol_details,
    }

    _format_check_output(output, symbol_details, args)

    if should_fail:
        sys.exit(1)

    return None

