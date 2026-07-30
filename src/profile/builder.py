"""
Main profile builder — aggregates all data sources into one profile dict.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from .classify import classify_project_type, detect_patterns, detect_services
from .filesystem import scan_filesystem
from .health import build_health_dims, adjust_overall_health
from .index_extract import extract_from_index, compute_reachability
from .scanners import (
    git_info, scan_deps, scan_secrets, scan_code_vulnerabilities,
    scan_git_history, scan_dockerfile, scan_license, scan_documentation,
    scan_taint, scan_iac, scan_frameworks, check_license_policy,
)


_COMPACT_PROFILE_KEYS = (
    "name", "path", "generated_at", "project_type", "project_sub_type",
    "file_count", "total_file_count", "source_file_count",
    "non_production_file_count", "indexed_file_count",
    "indexed_source_file_count", "file_class_counts",
    "indexed_file_class_counts", "file_count_semantics", "analysis_scope",
    "languages",
    "api_definition_count", "model_count", "dependency_count",
    "secret_count", "taint_flow_count", "complex_functions",
    "avg_complexity", "dead_code_count", "connection_count",
    "health_score", "health_grade", "health_dimensions",
    "has_docker", "has_ci", "has_tests", "has_docs",
    "api_definitions", "api_calls_internal", "api_calls_external",
    "models", "entry_points", "module_graph", "module_graph_summary",
    "complexity_summary", "config_files", "patterns", "frameworks",
    "services", "recent_authors", "last_commit_date",
)


def _normalize_page(limit: int, cursor: int) -> tuple[int, int]:
    try:
        normalized_limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        normalized_limit = 20
    try:
        normalized_cursor = max(0, int(cursor))
    except (TypeError, ValueError):
        normalized_cursor = 0
    return normalized_limit, normalized_cursor


def _bound_lists(
    value,
    *,
    limit: int,
    cursor: int,
    path: str = "",
    pages: dict | None = None,
):
    """Copy a profile while bounding every list and recording page metadata."""
    pages = pages if pages is not None else {}
    if isinstance(value, list):
        total = len(value)
        page = value[cursor:cursor + limit]
        pages[path or "$"] = {
            "total": total,
            "returned": len(page),
            "has_more": cursor + limit < total,
            "next_cursor": cursor + limit if cursor + limit < total else None,
        }
        return [
            _bound_lists(
                item,
                limit=limit,
                cursor=0,
                path=f"{path}[]" if path else "[]",
                pages=pages,
            )
            for item in page
        ]
    if isinstance(value, dict):
        return {
            key: _bound_lists(
                item,
                limit=limit,
                cursor=cursor,
                path=f"{path}.{key}" if path else key,
                pages=pages,
            )
            for key, item in value.items()
        }
    return value


def shape_profile(
    profile: dict,
    *,
    result_mode: str = "compact",
    limit: int = 20,
    cursor: int = 0,
) -> dict:
    """Return a compact, paged, or explicitly full profile representation."""
    mode = result_mode if result_mode in {"compact", "paged", "full"} else "compact"
    if mode == "full":
        result = dict(profile)
        result["result_mode"] = "full"
        return result

    normalized_limit, normalized_cursor = _normalize_page(limit, cursor)
    source = profile
    if mode == "compact":
        source = {
            key: profile[key]
            for key in _COMPACT_PROFILE_KEYS
            if key in profile
        }

    pages: dict = {}
    result = _bound_lists(
        source,
        limit=normalized_limit,
        cursor=normalized_cursor,
        pages=pages,
    )
    result["result_mode"] = mode
    result["pagination"] = {
        "cursor": normalized_cursor,
        "limit": normalized_limit,
        "has_more": any(page["has_more"] for page in pages.values()),
        "fields": pages,
    }
    return result


def build_project_profile(
    project_path: Path,
    compact: bool = False,
    *,
    result_mode: str | None = None,
    limit: int = 20,
    cursor: int = 0,
    include_non_production: bool = False,
) -> dict:
    """
    Build a complete project profile by aggregating all available data sources.

    Args:
        project_path: Absolute path to the project root.
        compact: If True, return a summary-only profile with reduced detail.
        result_mode: ``compact``, ``paged``, or explicit unbounded ``full``.
        limit: Maximum list items per page for compact/paged output (max 50).
        cursor: Zero-based list offset for compact/paged output.
        include_non_production: Include test/fixture/example/generated index signals.

    Returns:
        A dict containing the full project profile.
    """
    project_path = project_path.resolve()
    project_name = project_path.name
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    fs = scan_filesystem(project_path)
    idx = extract_from_index(
        project_path,
        include_non_production=include_non_production,
    )
    deps = scan_deps(project_path)
    git = git_info(project_path)
    dep_names = {d.get("name", "") for d in deps.get("dependencies", []) if isinstance(d, dict)}
    patterns = detect_patterns(fs["_all_files"], dep_names, index_data=idx)

    secrets_data = scan_secrets(project_path)
    code_vulns_data = scan_code_vulnerabilities(project_path)
    git_leaked_data = scan_git_history(project_path)
    dockerfile_data = scan_dockerfile(project_path)
    license_data = scan_license(project_path)
    documentation_data = scan_documentation(project_path)
    taint_data = scan_taint(project_path)
    iac_data = scan_iac(project_path)
    license_policy_issues = check_license_policy(license_data)
    frameworks_data = scan_frameworks(project_path)

    # Engineering intelligence analyzers (v2.11+)
    # These run here so their results feed into both the health score
    # (penalties) and the export bundle (profile fields).
    _eng_intel = {}
    try:
        from ..analyzer.error_handling import analyze_error_handling
        _eng_intel["error_handling"] = analyze_error_handling(project_path).to_dict()
    except Exception:
        logger.warning("optional profile analyzer failed; continuing with partial profile", exc_info=True)
    try:
        from ..analyzer.tech_debt import analyze_tech_debt
        _eng_intel["tech_debt"] = analyze_tech_debt(project_path).to_dict()
    except Exception:
        logger.warning("optional profile analyzer failed; continuing with partial profile", exc_info=True)
    try:
        from ..analyzer.perf_patterns import analyze_perf_patterns
        _eng_intel["perf_patterns"] = analyze_perf_patterns(project_path).to_dict()
    except Exception:
        logger.warning("optional profile analyzer failed; continuing with partial profile", exc_info=True)
    try:
        from ..analyzer.import_health import analyze_import_health
        _eng_intel["import_health"] = analyze_import_health(project_path).to_dict()
    except Exception:
        logger.warning("optional profile analyzer failed; continuing with partial profile", exc_info=True)
    try:
        from ..analyzer.config_drift import analyze_config_drift
        _eng_intel["config_drift"] = analyze_config_drift(project_path).to_dict()
    except Exception:
        logger.warning("optional profile analyzer failed; continuing with partial profile", exc_info=True)
    try:
        from ..analyzer.bus_factor import analyze_bus_factor
        _eng_intel["bus_factor"] = analyze_bus_factor(project_path).to_dict()
    except Exception:
        logger.warning("optional profile analyzer failed; continuing with partial profile", exc_info=True)
    try:
        from ..analyzer.api_drift import analyze_api_drift
        api_defs = idx.get("api_definitions", [])
        api_calls = idx.get("api_calls_internal", [])
        if api_defs or api_calls:
            _eng_intel["api_drift"] = analyze_api_drift(api_defs, api_calls).to_dict()
    except Exception:
        logger.warning("optional profile analyzer failed; continuing with partial profile", exc_info=True)

    # Per-package import usage (import_counts / import_files) for flyto-engine
    # CVE reachability. Degrade to empty on failure rather than break the export.
    _import_counts: dict = {}
    _import_files: dict = {}
    try:
        from .import_usage import compute_import_usage
        _import_counts, _import_files = compute_import_usage(project_path)
    except Exception:
        logger.warning("import_usage extraction failed", exc_info=True)

    services = detect_services(deps)
    project_type_info = classify_project_type(
        languages=fs["languages"],
        api_definitions=idx["api_definitions"],
        components=idx["symbol_counts"].get("component", 0),
        dep_names=dep_names,
        patterns=patterns,
        entry_points=idx["entry_points"],
        all_files=fs["_all_files"],
    )

    health_dims = build_health_dims(idx, project_type_info["type"])
    health_dims["overall"] = adjust_overall_health(
        health_dims.get("overall", {}),
        secrets_data, taint_data, iac_data, license_policy_issues,
        documentation_data, project_type_info["type"],
        error_handling_data=_eng_intel.get("error_handling"),
        tech_debt_data=_eng_intel.get("tech_debt"),
        perf_patterns_data=_eng_intel.get("perf_patterns"),
        import_health_data=_eng_intel.get("import_health"),
    )

    profile = {
        "name": project_name,
        "path": str(project_path),
        "generated_at": now,

        # Classification
        "project_type": project_type_info["type"],
        "project_sub_type": project_type_info["sub_type"],

        # Structure
        "file_count": fs["file_count"],
        "total_file_count": fs["total_file_count"],
        "source_file_count": fs["source_file_count"],
        "non_production_file_count": fs["non_production_file_count"],
        "indexed_file_count": idx["indexed_file_count"],
        "indexed_source_file_count": idx["indexed_source_file_count"],
        "file_class_counts": fs["file_class_counts"],
        "indexed_file_class_counts": idx["indexed_file_class_counts"],
        "file_count_semantics": fs["file_count_semantics"],
        "analysis_scope": (
            "all_indexed_files" if include_non_production
            else "production_source_only"
        ),
        "languages": fs["languages"],

        # APIs (classified)
        "api_definitions": idx["api_definitions"],
        "api_calls_internal": idx["api_calls_internal"],
        "api_calls_external": idx["api_calls_external"],
        "api_routes": idx["api_routes"],  # backward compat: union of all

        # Services
        "services": services,

        # Models
        "models": idx["models"],

        # Dependencies
        "dependencies": deps,

        # Symbols
        "symbol_counts": idx["symbol_counts"],
        "entry_points": idx["entry_points"],

        # Connections
        "module_graph": idx["module_graph"],
        "module_graph_full": idx["module_graph_full"],
        "module_graph_summary": idx["module_graph_summary"],

        # Complexity
        "complexity_summary": idx["complexity_summary"],

        # Counts — for flyto-engine compat
        "api_definition_count": len(idx["api_definitions"]),
        "model_count": len(idx["models"]),
        "dependency_count": deps.get("total_count", 0),
        "secret_count": secrets_data.get("total_findings", 0),
        "taint_flow_count": taint_data.get("unsanitized_flows", 0),
        "complex_functions": idx["complexity_summary"].get("complex_functions", 0),
        "avg_complexity": idx["complexity_summary"].get("avg_complexity", 0),
        "dead_code_count": health_dims.get("dead_code", {}).get("dead_count", 0),
        "connection_count": idx["module_graph_summary"].get("total_connections", 0),

        # Health — top-level for flyto-engine compat
        "health_score": health_dims.get("overall", {}).get("score", 0),
        "health_grade": health_dims.get("overall", {}).get("grade", "?"),
        "health_dimensions": health_dims,

        # Infrastructure
        "has_docker": fs["has_docker"],
        "has_ci": fs["has_ci"],
        "has_tests": fs["has_tests"],
        "has_docs": fs["has_docs"],
        "config_files": fs["config_files"],

        # Git
        "recent_authors": git["recent_authors"],
        "last_commit_date": git["last_commit_date"],

        # Patterns
        "patterns": patterns,

        # Frameworks
        "frameworks": frameworks_data,

        # Analysis
        "secrets": secrets_data,
        "code_vulnerabilities": code_vulns_data,
        "git_leaked_secrets": git_leaked_data,
        "dockerfile_issues": dockerfile_data,
        "taint_flows": taint_data,
        "license": license_data,
        "license_policy_issues": license_policy_issues,
        "iac_findings": iac_data,
        "reachability": compute_reachability(deps, idx),
        # Top-level import_counts / import_files — flyto-engine reads these from
        # the uploaded profile (integrations/flyto-engine.md) to anchor
        # package-level CVE reachability to real source files. Previously absent,
        # so the engine silently dropped reachability on every upload.
        "import_counts": _import_counts,
        "import_files": _import_files,
        "container_findings": {
            "total_findings": dockerfile_data.get("total_issues", 0),
            "critical": sum(1 for i in dockerfile_data.get("issues", []) if i.get("severity") == "CRITICAL"),
            "high": sum(1 for i in dockerfile_data.get("issues", []) if i.get("severity") == "HIGH"),
            "medium": sum(1 for i in dockerfile_data.get("issues", []) if i.get("severity") == "MEDIUM"),
            "low": sum(1 for i in dockerfile_data.get("issues", []) if i.get("severity") == "LOW"),
            "findings": dockerfile_data.get("issues", []),
        },
        "documentation": documentation_data,
    }

    # Engineering intelligence (v2.11+) — only include non-empty results
    for key in ("config_drift", "tech_debt", "error_handling", "api_drift",
                "bus_factor", "perf_patterns", "import_health"):
        if key in _eng_intel:
            profile[key] = _eng_intel[key]

    # Pyramid composite scores (v2.12+)
    try:
        from ..analyzer.pyramid import compute_pyramids
        pyramid_report = compute_pyramids(profile)
        profile["pyramids"] = pyramid_report.to_dict()
    except Exception:
        logger.warning("optional profile analyzer failed; continuing with partial profile", exc_info=True)

    # Lens analysis — cross-signal hotspots per perspective (v2.12+)
    try:
        from ..analyzer.lens import compute_all_lenses
        profile["lenses"] = compute_all_lenses(profile)
    except Exception:
        logger.warning("optional profile analyzer failed; continuing with partial profile", exc_info=True)

    mode = result_mode or ("compact" if compact else "full")
    if mode == "full":
        profile["folder_structure"] = fs["folder_structure"]

    return shape_profile(
        profile,
        result_mode=mode,
        limit=limit,
        cursor=cursor,
    )
