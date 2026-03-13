"""
Smart tools — 5 consolidated entry points with association-based triggering.

Instead of 45+ tools requiring LLM to pick the right one, these 5 tools
accept intent and auto-enrich results with related information server-side.

search   → find code (BM25 + semantic, auto-attach callers & context)
impact   → what breaks (references + blast radius + cross-project)
audit    → code quality (health score, auto-expand weak dimensions)
task     → plan/gate/validate workflow
structure → project overview (APIs, dependencies, types)
"""

import logging

logger = logging.getLogger("flyto-indexer.smart")


# ---------------------------------------------------------------------------
# Lazy imports (same pattern as tool_registry.py)
# ---------------------------------------------------------------------------

def _search_mod():
    try:
        from . import search as m
    except ImportError:
        import search as m
    return m


def _refs_mod():
    try:
        from . import references as m
    except ImportError:
        import references as m
    return m


def _info_mod():
    try:
        from . import code_info as m
    except ImportError:
        import code_info as m
    return m


def _maint_mod():
    try:
        from . import maintenance as m
    except ImportError:
        import maintenance as m
    return m


def _task_mod():
    try:
        from . import task_analysis as m
    except ImportError:
        import task_analysis as m
    return m


def _validation_mod():
    try:
        from . import validation as m
    except ImportError:
        import validation as m
    return m


def _quality_mod():
    try:
        from .. import quality as m
    except ImportError:
        import quality as m
    return m


def _diff_mod():
    try:
        from .. import diff_impact as m
    except ImportError:
        import diff_impact as m
    return m


def _git_mod():
    try:
        from . import git_intel as m
    except ImportError:
        import git_intel as m
    return m


def _coverage_mod():
    try:
        from . import coverage_intel as m
    except ImportError:
        import coverage_intel as m
    return m


def _type_mod():
    try:
        from . import type_contracts as m
    except ImportError:
        import type_contracts as m
    return m


def _trace_mod():
    try:
        from . import trace as m
    except ImportError:
        import trace as m
    return m


def _change_patterns_mod():
    try:
        from . import change_patterns as m
    except ImportError:
        import change_patterns as m
    return m


def _conventions_mod():
    try:
        from . import conventions as m
    except ImportError:
        import conventions as m
    return m


def _staleness_mod():
    try:
        from . import staleness as m
    except ImportError:
        import staleness as m
    return m


def _context_budget_mod():
    try:
        from . import context_budget as m
    except ImportError:
        import context_budget as m
    return m


def _data_flow_mod():
    try:
        from . import data_flow as m
    except ImportError:
        import data_flow as m
    return m


def _enrich(label: str, func, *args, **kwargs):
    """Call an enrichment function, log and swallow errors."""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.debug("enrich[%s] failed: %s", label, e)
        return None


def _truncate_list(data: dict, key: str, max_items: int = 20):
    """Truncate a list field in-place, adding has_more flag."""
    items = data.get(key)
    if isinstance(items, list) and len(items) > max_items:
        data[key] = items[:max_items]
        data[f"{key}_total"] = len(items)
        data[f"{key}_has_more"] = True


def _truncate_structure_lists(result: dict):
    """Truncate apis and categories in structure results for LLM consumption."""
    # APIs: keep top 20 by call_count
    apis_data = result.get("apis")
    if isinstance(apis_data, dict):
        for sub_key in ("endpoints", "apis", "results"):
            items = apis_data.get(sub_key)
            if isinstance(items, list) and len(items) > 20:
                sorted_items = sorted(items, key=lambda x: x.get("call_count", 0), reverse=True)
                apis_data[sub_key] = sorted_items[:20]
                apis_data[f"{sub_key}_total"] = len(items)
                apis_data[f"{sub_key}_has_more"] = True
    elif isinstance(apis_data, list) and len(apis_data) > 20:
        sorted_items = sorted(apis_data, key=lambda x: x.get("call_count", 0) if isinstance(x, dict) else 0, reverse=True)
        result["apis"] = sorted_items[:20]
        result["apis_total"] = len(apis_data)
        result["apis_has_more"] = True

    # Categories: convert {cat: [file_list]} to {cat: count} (summary only)
    cats = result.get("categories")
    if isinstance(cats, dict):
        # Check if values are lists (full file lists) vs already summarized
        cat_data = cats.get("categories", cats)
        if isinstance(cat_data, dict):
            summarized = {}
            for cat_name, file_list in cat_data.items():
                if isinstance(file_list, list):
                    summarized[cat_name] = len(file_list)
                else:
                    summarized[cat_name] = file_list  # already a count or other value
            if cats.get("categories") is not None:
                cats["categories"] = summarized
                cats["categories_summarized"] = True
            else:
                result["categories"] = summarized
                result["categories_summarized"] = True


# ---------------------------------------------------------------------------
# 1. search — unified code search with auto-enrichment
# ---------------------------------------------------------------------------

def smart_search(query: str, project: str = None, include_content: bool = False) -> dict:
    """Run BM25 + semantic search, auto-attach callers and file context for top results."""
    if not query or not query.strip():
        return {"results": [], "query": query}

    search = _search_mod()
    refs = _refs_mod()
    info = _info_mod()

    # Run both search modes
    bm25_raw = search.search_by_keyword(
        query=query, max_results=10, project=project, include_content=include_content,
    )
    sem_raw = search.semantic_search(
        query=query, project=project, max_results=10, include_content=include_content,
    )

    # Merge results: deduplicate by symbol_id, keep best score
    seen = {}
    bm25_results = bm25_raw.get("results", []) if isinstance(bm25_raw, dict) else []
    sem_results = sem_raw.get("results", []) if isinstance(sem_raw, dict) else []

    for r in bm25_results:
        sid = r.get("symbol_id") or r.get("id", "")
        if sid:
            r["_source"] = "bm25"
            seen[sid] = r

    for r in sem_results:
        sid = r.get("symbol_id") or r.get("id", "")
        if sid and sid not in seen:
            r["_source"] = "semantic"
            seen[sid] = r

    merged = list(seen.values())

    # --- Association triggers for top results ---
    for r in merged[:5]:
        sid = r.get("symbol_id") or r.get("id", "")
        if not sid:
            continue

        # Auto-attach callers (top 5)
        ref_result = _enrich("callers", refs.find_references, sid)
        if isinstance(ref_result, dict) and ref_result.get("references"):
            r["callers"] = [
                {"caller": c.get("caller_id", ""), "path": c.get("path", ""), "line": c.get("line")}
                for c in ref_result["references"][:5]
            ]
            r["caller_count"] = ref_result.get("references_count", len(ref_result["references"]))

        # Auto-attach file siblings (other symbols in same file)
        path = r.get("path", "")
        if path:
            siblings = _enrich("siblings", info.get_file_symbols, path)
            if isinstance(siblings, dict) and siblings.get("symbols"):
                r["file_siblings"] = [
                    s.get("name", "") for s in siblings["symbols"]
                    if s.get("name") != r.get("name")
                ][:10]

    # Show concept expansion if available
    concept_expansion = sem_raw.get("concept_expansion", []) if isinstance(sem_raw, dict) else []

    return {
        "query": query,
        "result_count": len(merged),
        "results": merged,
        "concept_expansion": concept_expansion,
        "search_modes": ["bm25", "semantic"],
    }


# ---------------------------------------------------------------------------
# 2. impact — unified impact analysis with auto-enrichment
# ---------------------------------------------------------------------------

def _smart_impact_diff(mode: str, project: str = None) -> dict:
    """Handle diff mode: analyze uncommitted changes."""
    info = _info_mod()
    diff = _diff_mod()
    diff_result = diff.impact_from_diff(mode=mode, project=project)

    # Auto-attach test files for affected symbols
    if isinstance(diff_result, dict):
        for change in diff_result.get("changes", [])[:10]:
            path = change.get("file", "")
            if path:
                test = _enrich("diff_test_file", info.find_test_file, path)
                if isinstance(test, dict) and not test.get("error"):
                    change["test_file"] = test.get("test_file") or test.get("path")

    # Truncate changes list for LLM consumption
    if isinstance(diff_result, dict):
        _truncate_list(diff_result, "changes", max_items=15)

    return {"mode": "diff", "diff_mode": mode, "result": diff_result}


def _impact_core_analysis(target: str, change_type: str) -> dict:
    """Run core reference and impact analysis for a target symbol."""
    refs = _refs_mod()
    result = {}

    try:
        ref_result = refs.find_references(target)
        if isinstance(ref_result, dict):
            result["references"] = ref_result
    except Exception as e:
        logger.debug("find_references failed for %s: %s", target, e)
        result["references_error"] = str(e)

    try:
        impact_result = refs.impact_analysis(target)
        if isinstance(impact_result, dict):
            result["impact"] = impact_result
    except Exception as e:
        logger.debug("impact_analysis failed for %s: %s", target, e)
        result["impact_error"] = str(e)

    if change_type != "modify":
        preview = _enrich("edit_preview", refs.edit_impact_preview, symbol_id=target, change_type=change_type)
        if isinstance(preview, dict):
            result["edit_preview"] = preview

    return result


def _impact_auto_enrich(result: dict, target: str):
    """Auto-attach cross-project impact, test file, call paths, and context budget."""
    refs = _refs_mod()
    info = _info_mod()

    # Cross-project impact
    projects = _enrich("list_projects", info.list_projects)
    if isinstance(projects, dict) and projects.get("count", 0) > 1:
        sym_name = target.split(":")[-1] if ":" in target else target
        source_proj = target.split(":")[0] if ":" in target else None
        cross = _enrich("cross_project", refs.cross_project_impact,
                        symbol_name=sym_name, source_project=source_proj)
        if isinstance(cross, dict) and cross.get("impacts"):
            result["cross_project"] = cross

    # Test file
    symbol_path = ""
    if isinstance(result.get("references"), dict):
        symbol_path = result["references"].get("target_file", "")
    if not symbol_path and isinstance(result.get("impact"), dict):
        symbol_path = result["impact"].get("target_file", "")
    if symbol_path:
        test = _enrich("test_file", info.find_test_file, symbol_path)
        if isinstance(test, dict) and not test.get("error"):
            result["test_file"] = test.get("test_file") or test.get("path")

    # Call path tracing
    trace = _enrich("trace_paths", _trace_mod().trace_paths, target, direction="up", max_depth=6, max_paths=5)
    if isinstance(trace, dict) and trace.get("paths"):
        result["call_paths"] = trace

    # Context budget scoring
    if isinstance(result.get("references"), dict):
        refs_list = result["references"].get("references", [])
        if refs_list:
            result["references"]["references"] = _context_budget_mod().score_references(refs_list, target)


def _truncate_impact_results(result: dict):
    """Cap impact result lists for LLM consumption."""
    if isinstance(result.get("references"), dict):
        _truncate_list(result["references"], "references", max_items=20)
    if isinstance(result.get("impact"), dict):
        _truncate_list(result["impact"], "affected_files", max_items=20)
        _truncate_list(result["impact"], "affected_symbols", max_items=20)
    if isinstance(result.get("cross_project"), dict):
        _truncate_list(result["cross_project"], "impacts", max_items=10)


def _smart_impact_symbol(target: str, change_type: str = "modify") -> dict:
    """Handle symbol mode: analyze specific target."""
    result = _impact_core_analysis(target, change_type)
    _impact_auto_enrich(result, target)
    _truncate_impact_results(result)

    result["target"] = target
    result["change_type"] = change_type
    return result


def smart_impact(target: str = None, mode: str = None, change_type: str = "modify",
                 project: str = None) -> dict:
    """Analyze impact of a change. Auto-attaches cross-project impact and test files."""
    # --- Diff mode: analyze uncommitted changes ---
    if mode:
        return _smart_impact_diff(mode, project)

    # --- Symbol mode: analyze specific target ---
    if not target:
        return {"error": "Provide 'target' (symbol name/id) or 'mode' (unstaged/staged/committed)"}

    return _smart_impact_symbol(target, change_type)


# ---------------------------------------------------------------------------
# 3. audit — unified code quality with auto-expansion of weak dimensions
# ---------------------------------------------------------------------------

def _audit_reindex(project):
    """Force incremental reindex before audit to ensure fresh data."""
    try:
        maint = _maint_mod()
        reindex_result = maint.check_and_reindex(dry_run=False, project=project, auto_reindex=True)
        if reindex_result.get("total_changes", 0) > 0:
            logger.info("Pre-audit reindex: %d changes applied", reindex_result["total_changes"])
    except Exception as e:
        logger.debug("Pre-audit reindex skipped: %s", e)


def _audit_health_score(project):
    """Compute health score, returns (result_dict, score_data, breakdown)."""
    result = {}
    try:
        health = _quality_mod().code_health_score(project=project)
        if isinstance(health, dict):
            result["health"] = health
    except Exception as e:
        logger.debug("code_health_score failed: %s", e)
        result["health_error"] = str(e)

    score_data = result.get("health", {})
    breakdown = score_data.get("breakdown", {})
    return result, score_data, breakdown


def _determine_dimensions_to_expand(focus, breakdown):
    """Determine which audit dimensions need expansion."""
    if focus:
        return {focus}
    # Auto-expand dimensions scoring below 80% (20 out of 25)
    return {
        dim_name for dim_name, dim_data in breakdown.items()
        if isinstance(dim_data, dict) and dim_data.get("score", 25) < 20
    }


def _expand_audit_dimensions(result, should_expand, focus, project):
    """Expand weak dimensions with detailed findings."""
    quality = _quality_mod()

    # --- Security (local pattern scan) ---
    if "security" in should_expand or focus == "security" or focus == "all":
        r = _enrich("security_scan", quality.security_scan, project=project, max_results=10)
        if r is not None:
            result["security_findings"] = r

    # --- Project rules (.flyto-rules.yaml) ---
    if focus in (None, "all", "rules"):
        r = _enrich("rules_check", quality.rules_check, project=project)
        if isinstance(r, dict) and r.get("total_rules", 0) > 0:
            result["rules_compliance"] = r

    # --- Complexity ---
    if "complexity" in should_expand or focus == "complexity":
        r = _enrich("complex_functions", quality.find_complex_functions, project=project, max_results=10)
        if r is not None:
            result["complex_functions"] = r
        r = _enrich("duplicates", quality.find_duplicates, project=project, max_results=5)
        if r is not None:
            result["duplicates"] = r

    # --- Dead code ---
    if "dead_code" in should_expand or focus == "dead_code":
        r = _enrich("dead_code", _maint_mod().find_dead_code, project=project, min_lines=5)
        if r is not None:
            result["dead_code"] = r

    # --- Coverage ---
    if "coverage" in should_expand or focus == "coverage":
        r = _enrich("coverage_gaps", _coverage_mod().coverage_gaps, project=project, max_results=10)
        if r is not None:
            result["coverage_gaps"] = r


def _audit_supplementary(result, score_data, project):
    """Add git hotspots, stale symbols, and refactoring suggestions."""
    r = _enrich("git_hotspots", _git_mod().git_hotspots, project=project, max_results=5)
    if r is not None:
        result["git_hotspots"] = r

    stale = _enrich("stale_symbols", _staleness_mod().find_stale_symbols,
                     project=project, stale_days=180, min_refs=3, max_results=10)
    if isinstance(stale, dict) and stale.get("stale_symbols"):
        result["stale_symbols"] = stale

    overall = score_data.get("score", 100)
    if overall < 80:
        r = _enrich("suggest_refactoring", _quality_mod().suggest_refactoring, project=project, max_results=10)
        if r is not None:
            result["refactoring_suggestions"] = r


def _truncate_audit_results(result):
    """Cap all list fields for LLM consumption."""
    for key in ("security_findings", "complex_functions", "dead_code",
                "coverage_gaps", "refactoring_suggestions", "rules_compliance"):
        val = result.get(key)
        if isinstance(val, dict):
            for sub_key in list(val.keys()):
                _truncate_list(val, sub_key, max_items=10)
        elif isinstance(val, list):
            _truncate_list(result, key, max_items=10)


def smart_audit(project: str = None, focus: str = None) -> dict:
    """Code health audit. Auto-expands weak dimensions with detailed findings."""
    _audit_reindex(project)

    result, score_data, breakdown = _audit_health_score(project)
    should_expand = _determine_dimensions_to_expand(focus, breakdown)

    _expand_audit_dimensions(result, should_expand, focus, project)
    _audit_supplementary(result, score_data, project)
    _truncate_audit_results(result)

    return result


# ---------------------------------------------------------------------------
# 4. task — plan / gate / validate workflow
# ---------------------------------------------------------------------------

def _task_plan(description, targets, intent, project):
    """Handle task action='plan': analyze task and attach co-change suggestions."""
    task = _task_mod()
    result = task.analyze_task(
        description=description,
        targets=targets or [],
        intent=intent,
        project=project,
    )
    if isinstance(result, dict) and targets:
        cochanges = _enrich("suggest_cochanges",
                            _change_patterns_mod().suggest_cochanges,
                            target_files=targets, project=project)
        if isinstance(cochanges, dict) and cochanges.get("suggestions"):
            result["cochange_suggestions"] = cochanges
    return result


def _task_validate(project, run_tests, test_path):
    """Handle task action='validate': run validation and attach untested changes on failure."""
    val = _validation_mod()
    result = val.validate_changes(
        project=project,
        run_tests=run_tests,
        test_path=test_path,
    )
    if isinstance(result, dict) and not result.get("tests_passed", True):
        r = _enrich("untested_changes", _coverage_mod().untested_changes, project=project, mode="unstaged")
        if r is not None:
            result["untested_changes"] = r
    return result


def smart_task(action: str, description: str = "", targets: list = None,
               intent: str = "refactor", task_contract: dict = None,
               next_phase: str = None, current_state: dict = None,
               project: str = None, run_tests: bool = True,
               test_path: str = None) -> dict:
    """Unified task workflow: plan, gate check, or validate."""
    if action == "plan":
        return _task_plan(description, targets, intent, project)

    if action == "gate":
        return _task_mod().task_gate_check(
            task_contract=task_contract or {},
            next_phase=next_phase,
            current_state=current_state or {},
        )

    if action == "validate":
        return _task_validate(project, run_tests, test_path)

    return {"error": f"Unknown action: {action}. Use 'plan', 'gate', or 'validate'."}


# ---------------------------------------------------------------------------
# 5. structure — project overview with auto-enrichment
# ---------------------------------------------------------------------------

def smart_structure(project: str = None, focus: str = None,
                    symbol_id: str = None, path: str = None) -> dict:
    """Project structure overview. Auto-enriches based on focus."""
    info = _info_mod()
    refs = _refs_mod()

    result = {}

    # --- APIs focus ---
    if focus == "apis":
        r = _enrich("list_apis", info.list_apis)
        if r is not None:
            result["apis"] = r
        r = _enrich("list_categories", info.list_categories)
        if r is not None:
            result["categories"] = r

        # Auto: check for contract drift across projects
        tc = _type_mod()
        drift = _enrich("contract_drift", tc.contract_drift, project=project)
        if isinstance(drift, dict) and drift.get("drifts"):
            result["contract_drift"] = drift

        _truncate_structure_lists(result)
        return result

    # --- Dependencies focus ---
    if focus == "dependencies":
        try:
            result["graph"] = refs.dependency_graph(
                file_path=path, symbol_id=symbol_id,
                project=project, direction="both", max_depth=2,
            )
        except Exception as e:
            logger.debug("dependency_graph failed: %s", e)
            result["graph_error"] = str(e)
        return result

    # --- Types focus ---
    if focus == "types":
        tc = _type_mod()
        if symbol_id:
            r = _enrich("extract_type_schema", tc.extract_type_schema, symbol_id=symbol_id)
            if r is not None:
                result["schema"] = r
        r = _enrich("contract_drift", tc.contract_drift, project=project)
        if r is not None:
            result["contract_drift"] = r
        return result

    # --- Conventions focus ---
    if focus == "conventions":
        r = _enrich("conventions", _conventions_mod().extract_conventions, project=project)
        if r is not None:
            result["conventions"] = r
        return result

    # --- Change patterns focus ---
    if focus == "change_patterns":
        r = _enrich("change_clusters", _change_patterns_mod().discover_change_clusters, project=project)
        if r is not None:
            result["change_clusters"] = r
        return result

    # --- Default: project overview ---
    try:
        result["projects"] = info.list_projects()
    except Exception as e:
        logger.debug("list_projects failed: %s", e)
        result["projects_error"] = str(e)

    # If specific project, add more detail
    if project:
        r = _enrich("list_apis", info.list_apis)
        if r is not None:
            result["apis"] = r
        r = _enrich("list_categories", info.list_categories)
        if r is not None:
            result["categories"] = r

        # Auto: check index freshness
        maint = _maint_mod()
        r = _enrich("check_index_status", maint.check_index_status)
        if r is not None:
            result["index_status"] = r

    _truncate_structure_lists(result)
    return result
