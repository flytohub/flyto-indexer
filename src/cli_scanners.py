"""Dependency, documentation, architecture, and scanner CLI handlers."""

from __future__ import annotations

import fnmatch
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path


def cmd_deps(args):
    """Scan and list all external package dependencies."""
    from .dependency_scanner import scan_dependencies, format_dependency_table

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    inventory = scan_dependencies(project_path)

    if hasattr(args, "as_json") and args.as_json:
        return inventory.to_dict()

    # Human-readable table output
    print(format_dependency_table(inventory))
    return None

def cmd_profile(args):
    """Generate a comprehensive project profile."""
    from .project_profile import build_project_profile, format_profile

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    compact = hasattr(args, "compact") and args.compact
    profile = build_project_profile(project_path, compact=compact)

    if hasattr(args, "as_json") and args.as_json:
        return profile

    # Human-readable output
    print(format_profile(profile))
    return None

def cmd_export(args):
    """Export scan results as a single JSON bundle for flyto-engine upload.

    Combines profile + taint into the format expected by
    POST /api/v1/code/repos/{id}/scan-upload.
    """
    from .project_profile import build_project_profile
    from .analyzer.taint import TaintAnalyzer

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    # 1. Build full profile (same as `profile --json`)
    t0 = time.monotonic()
    profile = build_project_profile(project_path)

    # 2. Run taint analysis
    index = {}
    index_path = project_path / ".flyto-index" / "index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    analyzer = TaintAnalyzer(project_path, index=index)
    taint_result = analyzer.analyze_full()
    elapsed = time.monotonic() - t0

    # 3. Inject taint summary into the profile (matches what
    # flyto-engine scanner.ScanResult expects)
    taint_dict = taint_result.to_dict()
    unsanitized = [f for f in taint_result.taint_flows if not f.sanitized]
    file_hits = set()
    categories = set()
    for f in unsanitized:
        if hasattr(f, "source_file") and f.source_file:
            file_hits.add(f.source_file)
        if hasattr(f, "sink_file") and f.sink_file:
            file_hits.add(f.sink_file)
        if hasattr(f, "category") and f.category:
            categories.add(f.category)

    profile["taint_flow_count"] = len(unsanitized)
    profile["taint_summary"] = {
        "total_sources": taint_dict.get("total_sources", 0),
        "total_sinks": taint_dict.get("total_sinks", 0),
        "unsanitized_flows": len(unsanitized),
        "sanitized_flows": taint_dict.get("sanitized_flows", 0),
        "high_risk_count": sum(1 for f in unsanitized if getattr(f, "severity", "") == "high"),
        "file_hits": sorted(file_hits),
        "categories": sorted(categories),
    }

    # 3b. Engineering intelligence fields are already included by
    # build_project_profile() — no need to re-run analyzers here.

    # 4. Build the upload bundle
    bundle = {"profile": profile}
    if hasattr(args, "commit") and args.commit:
        bundle["commit_sha"] = args.commit
    if hasattr(args, "branch") and args.branch:
        bundle["branch"] = args.branch

    # 5. --full: include symbol graph from index.json
    if getattr(args, "full", False) and index:
        import fnmatch
        exclude_patterns = getattr(args, "exclude", []) or []

        def _should_exclude(path: str) -> bool:
            for pat in exclude_patterns:
                if fnmatch.fnmatch(path, pat):
                    return True
            return False

        # Filter symbols and dependencies by exclude patterns
        filtered_symbols = {}
        excluded_ids = set()
        for sym_id, sym in index.get("symbols", {}).items():
            sym_path = sym.get("path", "")
            if _should_exclude(sym_path):
                excluded_ids.add(sym_id)
                continue
            # Never include content (source code) in the export
            sym_copy = {k: v for k, v in sym.items() if k != "content"}
            filtered_symbols[sym_id] = sym_copy

        filtered_deps = {}
        for dep_id, dep in index.get("dependencies", {}).items():
            if dep.get("source") in excluded_ids or dep.get("target") in excluded_ids:
                continue
            filtered_deps[dep_id] = dep

        # Rebuild reverse_index from filtered deps
        filtered_reverse = {}
        for dep in filtered_deps.values():
            target = dep.get("target", "")
            source = dep.get("source", "")
            if target and source:
                filtered_reverse.setdefault(target, []).append(source)

        bundle["index"] = {
            "project": index.get("project", ""),
            "symbols": filtered_symbols,
            "dependencies": filtered_deps,
            "reverse_index": filtered_reverse,
            "entry_points": index.get("entry_points", []),
            "routes": index.get("routes", {}),
            "api_endpoints": index.get("api_endpoints", []),
        }

        sym_count = len(filtered_symbols)
        dep_count = len(filtered_deps)
        print(f"  --full: {sym_count} symbols, {dep_count} dependencies"
              + (f" ({len(excluded_ids)} excluded)" if excluded_ids else ""),
              file=sys.stderr)

    print(f"Export complete in {elapsed:.1f}s — {profile.get('file_count', 0)} files, "
          f"{len(unsanitized)} taint flows", file=sys.stderr)

    return bundle

def cmd_secrets(args):
    """Scan project for hardcoded secrets."""
    from .secret_scanner import scan_secrets, format_secret_scan

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    result = scan_secrets(project_path)

    if hasattr(args, "as_json") and args.as_json:
        return {
            "total_files_scanned": result.total_files_scanned,
            "total_findings": result.total_findings,
            "critical": result.critical,
            "high": result.high,
            "medium": result.medium,
            "findings": [finding.to_dict() for finding in result.findings],
        }

    print(format_secret_scan(result))
    return None

def cmd_license(args):
    """Detect project and dependency licenses."""
    from .license_scanner import scan_licenses, format_license_scan

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    result = scan_licenses(project_path)

    if hasattr(args, "as_json") and args.as_json:
        return asdict(result)

    print(format_license_scan(result))
    return None

def cmd_docs(args):
    """Analyze documentation coverage."""
    from .doc_scanner import scan_documentation, format_doc_scan

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    result = scan_documentation(project_path)

    if hasattr(args, "as_json") and args.as_json:
        return asdict(result)

    print(format_doc_scan(result))
    return None

def cmd_pr_risk(args):
    """Analyze PR/changeset risk."""
    from .pr_analyzer import analyze_pr_risk, format_pr_risk

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    result = analyze_pr_risk(
        project_path=str(project_path),
        base=args.base,
        staged=args.staged,
    )

    if hasattr(args, "as_json") and args.as_json:
        return result.to_dict()

    print(format_pr_risk(result))
    return None

def cmd_sbom(args):
    """Export SBOM in CycloneDX 1.5 format."""
    from .sbom_export import export_sbom_cyclonedx, format_sbom_json, format_sbom_summary

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    project_name = args.name or project_path.name
    sbom = export_sbom_cyclonedx(project_path, project_name)

    if hasattr(args, "summary") and args.summary:
        print(format_sbom_summary(sbom))
        return None

    output_str = format_sbom_json(sbom)

    if hasattr(args, "output") and args.output:
        output_path = Path(args.output)
        output_path.write_text(output_str + "\n", encoding="utf-8")
        print(f"SBOM written to {output_path} ({len(sbom.get('components', []))} components)")
        return None

    return sbom

def cmd_framework(args):
    """Detect project frameworks."""
    from .framework_detector import detect_frameworks, format_frameworks

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    frameworks = detect_frameworks(project_path)

    if hasattr(args, "as_json") and args.as_json:
        return [fw.to_dict() for fw in frameworks]

    print(format_frameworks(frameworks))
    return None

def cmd_layers(args):
    """Check architecture layer rules (import graph)."""
    from .analyzer.layers import check_layers_dict

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    result = check_layers_dict(project_path)

    if hasattr(args, "as_json") and args.as_json:
        if args.fail_on_violation and result["total_violations"] > 0:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            sys.exit(2)
        return result

    if not result["layers"]:
        print("No layers declared in .flyto-rules.yaml")
        print("Run: flyto-index add-layer --name <name> --paths '<glob>'")
        return None

    print(f"Layers: {len(result['layers'])}")
    for layer in result["layers"]:
        constraints = []
        if layer["can_import"]:
            constraints.append(f"can_import={layer['can_import']}")
        if layer["cannot_import"]:
            constraints.append(f"cannot_import={layer['cannot_import']}")
        suffix = f" [{', '.join(constraints)}]" if constraints else ""
        print(f"  - {layer['name']}: {layer['paths']}{suffix}")

    print()
    print(f"Files checked: {result['files_checked']}")
    print(f"Edges checked: {result['edges_checked']} (skipped: {result['edges_skipped']})")
    print(f"Violations: {result['total_violations']}")

    for v in result["violations"][:20]:
        print()
        print(f"  {v['severity'].upper()}  {v['from_layer']} → {v['to_layer']}  ({v['kind']})")
        print(f"    {v['from_file']}:{v['line']}")
        print(f"    imports {v['to_file']}")
        if v["reason"]:
            print(f"    reason: {v['reason']}")

    if args.fail_on_violation and result["total_violations"] > 0:
        sys.exit(2)
    return None

def cmd_add_layer(args):
    """Write a layer definition into .flyto-rules.yaml."""
    from .analyzer.layers import add_layer

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    can_import = [p.strip() for p in args.can_import.split(",") if p.strip()] if args.can_import else None
    cannot_import = [p.strip() for p in args.cannot_import.split(",") if p.strip()] if args.cannot_import else None

    return add_layer(
        project_path,
        name=args.name,
        paths=paths,
        can_import=can_import,
        cannot_import=cannot_import,
        reason=args.reason or None,
    )

def cmd_add_taint_source(args):
    from .analyzer.taint_dsl import add_taint_source

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    return add_taint_source(
        project_path,
        pattern=args.pattern,
        language=args.language,
        taint_type=args.taint_type or None,
    )

def cmd_add_taint_sink(args):
    from .analyzer.taint_dsl import add_taint_sink

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    requires = None
    if getattr(args, "requires", ""):
        import json

        try:
            requires = json.loads(args.requires)
        except json.JSONDecodeError as error:
            print(f"--requires is not valid JSON: {error}", file=sys.stderr)
            sys.exit(1)
        if isinstance(requires, dict):
            requires = [requires]
        if not isinstance(requires, list):
            print("--requires must be a JSON object or list of objects", file=sys.stderr)
            sys.exit(1)

    return add_taint_sink(
        project_path,
        pattern=args.pattern,
        vuln_type=args.vuln_type,
        severity=args.severity,
        recommendation=args.recommendation,
        requires=requires,
    )

def _agent_guard_project(args):
    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)
    return project_path

def cmd_add_agent_guard(args):
    from .analyzer.agent_guards_dsl import add_agent_guard

    return add_agent_guard(_agent_guard_project(args), args.domain, args.name)

def cmd_remove_agent_guard(args):
    from .analyzer.agent_guards_dsl import remove_agent_guard

    return remove_agent_guard(_agent_guard_project(args), args.domain, args.name)

def cmd_list_agent_guards(args):
    from .analyzer.agent_guards_dsl import list_agent_guards

    return list_agent_guards(_agent_guard_project(args))

def cmd_add_taint_sanitizer(args):
    from .analyzer.taint_dsl import add_taint_sanitizer

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    cleanses = None
    if args.cleanses and args.cleanses != "*":
        cleanses = [p.strip() for p in args.cleanses.split(",") if p.strip()]

    return add_taint_sanitizer(
        project_path,
        pattern=args.pattern,
        cleanses=cleanses,
    )

def cmd_remove_taint_rule(args):
    from .analyzer.taint_dsl import remove_taint_rule

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    return remove_taint_rule(project_path, kind=args.kind, pattern=args.pattern)

def cmd_list_taint_rules(args):
    from .analyzer.taint_dsl import list_taint_rules

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    return list_taint_rules(project_path)

