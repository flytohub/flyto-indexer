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
        try:
            ref_result = refs.find_references(sid)
            if isinstance(ref_result, dict) and ref_result.get("references"):
                r["callers"] = [
                    {"caller": c.get("caller_id", ""), "path": c.get("path", ""), "line": c.get("line")}
                    for c in ref_result["references"][:5]
                ]
                r["caller_count"] = ref_result.get("references_count", len(ref_result["references"]))
        except Exception:
            pass

        # Auto-attach file siblings (other symbols in same file)
        path = r.get("path", "")
        if path:
            try:
                siblings = info.get_file_symbols(path)
                if isinstance(siblings, dict) and siblings.get("symbols"):
                    r["file_siblings"] = [
                        s.get("name", "") for s in siblings["symbols"]
                        if s.get("name") != r.get("name")
                    ][:10]
            except Exception:
                pass

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

def smart_impact(target: str = None, mode: str = None, change_type: str = "modify",
                 project: str = None) -> dict:
    """Analyze impact of a change. Auto-attaches cross-project impact and test files."""
    refs = _refs_mod()
    info = _info_mod()

    # --- Diff mode: analyze uncommitted changes ---
    if mode:
        diff = _diff_mod()
        diff_result = diff.impact_from_diff(mode=mode, project=project)

        # Auto-attach test files for affected symbols
        if isinstance(diff_result, dict):
            for change in diff_result.get("changes", [])[:10]:
                path = change.get("file", "")
                if path:
                    try:
                        test = info.find_test_file(path)
                        if isinstance(test, dict) and not test.get("error"):
                            change["test_file"] = test.get("test_file") or test.get("path")
                    except Exception:
                        pass

        return {"mode": "diff", "diff_mode": mode, "result": diff_result}

    # --- Symbol mode: analyze specific target ---
    if not target:
        return {"error": "Provide 'target' (symbol name/id) or 'mode' (unstaged/staged/committed)"}

    result = {}

    # Find references
    try:
        ref_result = refs.find_references(target)
        if isinstance(ref_result, dict):
            result["references"] = ref_result
    except Exception as e:
        result["references_error"] = str(e)

    # Impact analysis (blast radius)
    try:
        impact_result = refs.impact_analysis(target)
        if isinstance(impact_result, dict):
            result["impact"] = impact_result
    except Exception as e:
        result["impact_error"] = str(e)

    # Edit impact preview (exact lines affected)
    if change_type != "modify":
        try:
            preview = refs.edit_impact_preview(symbol_id=target, change_type=change_type)
            if isinstance(preview, dict):
                result["edit_preview"] = preview
        except Exception:
            pass

    # --- Auto: cross-project impact if multiple projects ---
    try:
        projects = info.list_projects()
        if isinstance(projects, dict) and projects.get("count", 0) > 1:
            # Extract symbol name from symbol_id
            sym_name = target.split(":")[-1] if ":" in target else target
            source_proj = target.split(":")[0] if ":" in target else None
            try:
                cross = refs.cross_project_impact(
                    symbol_name=sym_name,
                    source_project=source_proj,
                )
                if isinstance(cross, dict) and cross.get("impacts"):
                    result["cross_project"] = cross
            except Exception:
                pass
    except Exception:
        pass

    # --- Auto: find test file ---
    try:
        symbol_path = ""
        if isinstance(result.get("references"), dict):
            symbol_path = result["references"].get("target_file", "")
        if not symbol_path and isinstance(result.get("impact"), dict):
            symbol_path = result["impact"].get("target_file", "")
        if symbol_path:
            test = info.find_test_file(symbol_path)
            if isinstance(test, dict) and not test.get("error"):
                result["test_file"] = test.get("test_file") or test.get("path")
    except Exception:
        pass

    result["target"] = target
    result["change_type"] = change_type
    return result


# ---------------------------------------------------------------------------
# 3. audit — unified code quality with auto-expansion of weak dimensions
# ---------------------------------------------------------------------------

def smart_audit(project: str = None, focus: str = None) -> dict:
    """Code health audit. Auto-expands weak dimensions with detailed findings."""
    quality = _quality_mod()
    git = _git_mod()

    result = {}

    # Always start with health score
    try:
        health = quality.code_health_score(project=project)
        if isinstance(health, dict):
            result["health"] = health
    except Exception as e:
        result["health_error"] = str(e)

    score_data = result.get("health", {})
    dimensions = score_data.get("dimensions", {})

    # Determine which dimensions to expand
    should_expand = set()
    if focus:
        should_expand.add(focus)
    else:
        # Auto-expand dimensions scoring below 80
        for dim_name, dim_data in dimensions.items():
            if isinstance(dim_data, dict) and dim_data.get("score", 100) < 80:
                should_expand.add(dim_name)

    # --- Security ---
    if "security" in should_expand or focus == "security":
        try:
            result["security_findings"] = quality.security_scan(
                project=project, max_results=10,
            )
        except Exception:
            pass

    # --- Complexity ---
    if "complexity" in should_expand or focus == "complexity":
        try:
            result["complex_functions"] = quality.find_complex_functions(
                project=project, max_results=10,
            )
        except Exception:
            pass
        try:
            result["duplicates"] = quality.find_duplicates(
                project=project, max_results=5,
            )
        except Exception:
            pass

    # --- Dead code ---
    if "dead_code" in should_expand or focus == "dead_code":
        try:
            maint = _maint_mod()
            result["dead_code"] = maint.find_dead_code(
                project=project, min_lines=5,
            )
        except Exception:
            pass

    # --- Coverage ---
    if "coverage" in should_expand or focus == "coverage":
        try:
            cov = _coverage_mod()
            result["coverage_gaps"] = cov.coverage_gaps(
                project=project, max_results=10,
            )
        except Exception:
            pass

    # --- Always include git hotspots (high-churn + complex = highest risk) ---
    try:
        result["git_hotspots"] = git.git_hotspots(
            project=project, max_results=5,
        )
    except Exception:
        pass

    # Suggest refactoring if overall score < 80
    overall = score_data.get("score", 100)
    if overall < 80 or focus == "all":
        try:
            result["refactoring_suggestions"] = quality.suggest_refactoring(
                project=project, max_results=10,
            )
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# 4. task — plan / gate / validate workflow
# ---------------------------------------------------------------------------

def smart_task(action: str, description: str = "", targets: list = None,
               intent: str = "refactor", task_contract: dict = None,
               next_phase: str = None, current_state: dict = None,
               project: str = None, run_tests: bool = True,
               test_path: str = None) -> dict:
    """Unified task workflow: plan, gate check, or validate."""
    if action == "plan":
        task = _task_mod()
        return task.analyze_task(
            description=description,
            targets=targets or [],
            intent=intent,
            project=project,
        )

    elif action == "gate":
        task = _task_mod()
        return task.task_gate_check(
            task_contract=task_contract or {},
            next_phase=next_phase,
            current_state=current_state or {},
        )

    elif action == "validate":
        val = _validation_mod()
        result = val.validate_changes(
            project=project,
            run_tests=run_tests,
            test_path=test_path,
        )

        # Auto-attach: if tests fail, show untested changes
        if isinstance(result, dict) and not result.get("tests_passed", True):
            try:
                cov = _coverage_mod()
                result["untested_changes"] = cov.untested_changes(
                    project=project, mode="unstaged",
                )
            except Exception:
                pass

        return result

    else:
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
        try:
            result["apis"] = info.list_apis()
        except Exception:
            pass
        try:
            result["categories"] = info.list_categories()
        except Exception:
            pass

        # Auto: check for contract drift across projects
        try:
            tc = _type_mod()
            drift = tc.contract_drift(project=project)
            if isinstance(drift, dict) and drift.get("drifts"):
                result["contract_drift"] = drift
        except Exception:
            pass
        return result

    # --- Dependencies focus ---
    if focus == "dependencies":
        try:
            result["graph"] = refs.dependency_graph(
                file_path=path, symbol_id=symbol_id,
                project=project, direction="both", max_depth=2,
            )
        except Exception as e:
            result["graph_error"] = str(e)
        return result

    # --- Types focus ---
    if focus == "types":
        if symbol_id:
            try:
                tc = _type_mod()
                result["schema"] = tc.extract_type_schema(symbol_id=symbol_id)
            except Exception:
                pass
        try:
            tc = _type_mod()
            result["contract_drift"] = tc.contract_drift(project=project)
        except Exception:
            pass
        return result

    # --- Default: project overview ---
    try:
        result["projects"] = info.list_projects()
    except Exception as e:
        result["projects_error"] = str(e)

    # If specific project, add more detail
    if project:
        try:
            result["apis"] = info.list_apis()
        except Exception:
            pass
        try:
            result["categories"] = info.list_categories()
        except Exception:
            pass

        # Auto: check index freshness
        try:
            maint = _maint_mod()
            result["index_status"] = maint.check_index_status()
        except Exception:
            pass

    return result
