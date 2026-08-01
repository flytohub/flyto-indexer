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
import os

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


def _grill_mod():
    try:
        from . import grill as m
    except ImportError:
        import grill as m
    return m


def _validation_mod():
    try:
        from . import validation as m
    except ImportError:
        import validation as m
    return m


def _conformance_mod():
    from . import grill_conformance as m
    return m


def _outcomes_mod():
    from . import grill_outcomes as m
    return m


def _feedback_mod():
    from . import development_feedback as m
    return m


def _proof_receipts_mod():
    from . import proof_receipts as m
    return m


def _task_runs_mod():
    try:
        from .. import task_runs as m
    except ImportError:
        from src import task_runs as m
    return m


def _framework_relationships_mod():
    try:
        from ..analyzer import framework_relationships as m
    except ImportError:
        from analyzer import framework_relationships as m
    return m


def _task_context_mod():
    from . import task_context as m
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


def _evidence_mod():
    from . import evidence_portfolio as m
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


def _add_explicit_project_counts(projects_result: dict) -> dict:
    """Label legacy overview counts without changing their existing values."""
    if not isinstance(projects_result, dict):
        return projects_result
    total_indexed_files = 0
    for item in projects_result.get("projects", []):
        if not isinstance(item, dict):
            continue
        indexed_count = item.get("files", 0)
        item["indexed_file_count"] = indexed_count
        item["file_count_semantics"] = {
            "files": "unique file paths represented in the code index",
            "indexed_file_count": "unique file paths represented in the code index",
        }
        if isinstance(indexed_count, int):
            total_indexed_files += indexed_count
    projects_result["total_indexed_files"] = total_indexed_files
    return projects_result


def _bounded_profile_projects() -> dict:
    """List indexed project names from small headers, without loading graphs."""
    try:
        from ..index_store import _active_index_dirs, _peek_index_project
    except ImportError:
        from index_store import _active_index_dirs, _peek_index_project

    projects = set()
    for index_dir in _active_index_dirs():
        project = _peek_index_project(index_dir) or index_dir.parent.name
        if project:
            projects.add(project)
    return {
        "total_projects": len(projects),
        "projects": [
            {"project": project, "name": project}
            for project in sorted(projects)
        ],
        "counts_included": False,
    }


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
    if focus == "all":
        return set(breakdown) | {"security", "coverage", "rules"}
    if focus:
        return {focus}
    # Auto-expand dimensions scoring below 80% (20 out of 25)
    return {
        dim_name for dim_name, dim_data in breakdown.items()
        if isinstance(dim_data, dict) and dim_data.get("score", 25) < 20
    }


def _canonical_complexity_detail(score_data, max_results=10):
    """Project the canonical health complexity evidence without rescanning."""
    dimension = score_data.get("breakdown", {}).get("complexity", {})
    metrics = dimension.get("metrics", {})
    return {
        "total_analyzed": metrics.get("total_functions", 0),
        "complex_count": metrics.get("complex_functions", 0),
        "complexity_burden": metrics.get("complexity_burden", 0),
        "max_complexity_score": metrics.get("max_complexity_score", 0),
        "avg_complexity": metrics.get("avg_complexity", 0.0),
        "functions": list(dimension.get("hotspots", []))[:max_results],
        "snapshot": score_data.get("snapshot"),
        "evidence_source": "canonical_health_snapshot",
    }


def _canonical_dead_code_detail(score_data, max_results=10):
    """Project canonical dead-code evidence without a second analysis pass."""
    dimension = score_data.get("breakdown", {}).get("dead_code", {})
    metrics = dimension.get("metrics", {})
    symbols = list(dimension.get("symbols", []))
    return {
        "total": metrics.get("dead_count", 0),
        "total_dead": metrics.get("dead_count", 0),
        "total_dead_lines": metrics.get("dead_lines", 0),
        "dead_symbols": symbols[:max_results],
        "top_20": symbols[:max_results],
        "snapshot": score_data.get("snapshot"),
        "evidence_source": "canonical_health_snapshot",
    }


def _audit_evidence_integrity(result):
    """Fail closed when expanded evidence diverges from the canonical snapshot."""
    health = result.get("health", {})
    snapshot = health.get("snapshot")
    failures = []
    checks = 0

    complexity = result.get("complex_functions")
    if isinstance(complexity, dict):
        checks += 1
        metrics = health.get("breakdown", {}).get("complexity", {}).get("metrics", {})
        expected = (
            metrics.get("total_functions"),
            metrics.get("complex_functions"),
            metrics.get("complexity_burden"),
            metrics.get("max_complexity_score"),
        )
        actual = (
            complexity.get("total_analyzed"),
            complexity.get("complex_count"),
            complexity.get("complexity_burden"),
            complexity.get("max_complexity_score"),
        )
        if actual != expected or complexity.get("snapshot") != snapshot:
            failures.append({
                "dimension": "complexity",
                "expected": expected,
                "actual": actual,
            })

    dead_code = result.get("dead_code")
    if isinstance(dead_code, dict):
        checks += 1
        expected = health.get("breakdown", {}).get("dead_code", {}).get(
            "metrics", {}
        ).get("dead_count")
        actual = dead_code.get("total_dead", dead_code.get("total"))
        if actual != expected or dead_code.get("snapshot") != snapshot:
            failures.append({
                "dimension": "dead_code",
                "expected": expected,
                "actual": actual,
            })

    return {
        "schema": "audit-evidence-integrity.v1",
        "pass": not failures,
        "status": "verified" if not failures else "blocked",
        "snapshot": snapshot,
        "checks": checks,
        "failures": failures,
        "reason_codes": ["EVIDENCE_SNAPSHOT_DIVERGED"] if failures else [],
    }


def _expand_audit_dimensions(result, score_data, should_expand, focus, project):
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
    if "complexity" in should_expand or focus in ("complexity", "all"):
        result["complex_functions"] = _canonical_complexity_detail(score_data)
        r = _enrich("duplicates", quality.find_duplicates, project=project, max_results=5)
        if r is not None:
            result["duplicates"] = r

    # --- Dead code ---
    if "dead_code" in should_expand or focus in ("dead_code", "all"):
        result["dead_code"] = _canonical_dead_code_detail(score_data)

    # --- Coverage ---
    if "coverage" in should_expand or focus in ("coverage", "all"):
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

    _expand_audit_dimensions(result, score_data, should_expand, focus, project)
    _audit_supplementary(result, score_data, project)
    _truncate_audit_results(result)
    result["evidence_integrity"] = _audit_evidence_integrity(result)
    if not result["evidence_integrity"]["pass"]:
        result["blocked"] = True
        result["reason_codes"] = result["evidence_integrity"]["reason_codes"]
        return result

    evidence = _evidence_mod()
    portfolio = _enrich(
        "evidence_portfolio",
        evidence.build_evidence_portfolio,
        project=project,
    )
    if isinstance(portfolio, dict):
        result["evidence_portfolio"] = portfolio
        verdict = _enrich(
            "audit_verdict",
            evidence.build_audit_verdict,
            result,
            portfolio,
        )
        if isinstance(verdict, dict):
            result["verdict"] = verdict

    return result


# ---------------------------------------------------------------------------
# 4. task — plan / gate / validate workflow
# ---------------------------------------------------------------------------

def _task_plan(description, targets, intent, project, grill_session_id=None):
    """Build one risk, instruction, and intent contract."""
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
    if isinstance(result, dict) and grill_session_id:
        try:
            decision_contract = _grill_mod().export_decision_contract(grill_session_id)
        except ValueError as exc:
            return {
                "pass": False,
                "decision": "blocked",
                "error": str(exc),
                "reason_codes": ["DECISION_CONTRACT_NOT_READY"],
                "required_actions": ["complete_and_freeze_grill_session"],
            }
        result["decision_contract"] = decision_contract
        result.setdefault("task_profile", {})["decision_session_id"] = grill_session_id
    return _task_context_mod().attach_task_context(
        result,
        project=project,
        description=description,
        targets=targets or [],
    )


def _task_validate(
    project,
    run_tests,
    test_path,
    task_contract=None,
    proof_receipts=None,
    required_proof_kinds=None,
):
    """Run code checks and every contract-backed closure gate."""
    val = _validation_mod()
    contract = task_contract if isinstance(task_contract, dict) else {}
    ledger = contract.get("intent_ledger")
    lint_paths = None
    if isinstance(ledger, dict):
        lint_paths = sorted({
            path
            for path in ledger.get("allowed_paths") or []
            if isinstance(path, str) and path.endswith(".py")
        })
    result = val.validate_changes(
        project=project,
        run_tests=run_tests,
        test_path=test_path,
        lint_paths=lint_paths,
    )
    contract_project = (
        contract.get("task_profile", {}).get("project") or project
    )
    validation_passed = result.get("overall", "pass") == "pass"
    closed_loop_passed = validation_passed
    reason_codes = []
    required_actions = []
    if not validation_passed:
        reason_codes.append("CODE_VALIDATION_FAILED")
        required_actions.append("fix_lint_or_tests")

    ledger_gate = _task_context_mod().validate_intent_ledger(
        contract,
        project=contract_project,
        validation=result,
    )
    if ledger_gate.get("status") != "not_required":
        result["intent_ledger_validation"] = ledger_gate
        if not ledger_gate.get("pass"):
            closed_loop_passed = False
            reason_codes.append("INTENT_LEDGER_NONCONFORMANT")
            required_actions.extend(ledger_gate.get("required_actions", []))

    changed_paths = (
        ledger_gate.get("change_set", {}).get("changed_paths", [])
        if isinstance(ledger_gate, dict)
        else []
    )
    instruction_gate = _task_context_mod().validate_instruction_context(
        contract,
        project=contract_project,
        changed_paths=changed_paths,
    )
    if instruction_gate.get("status") != "not_required":
        result["instruction_context_validation"] = instruction_gate
        if not instruction_gate.get("pass"):
            closed_loop_passed = False
            reason_codes.append("INSTRUCTION_CONTEXT_NONCONFORMANT")
            required_actions.extend(instruction_gate.get("required_actions", []))

    declared_receipts = list(proof_receipts or contract.get("proof_receipts") or [])
    declared_required_kinds = sorted(set(
        list(required_proof_kinds or [])
        + list(contract.get("required_proof_kinds") or [])
    ))
    if declared_receipts or declared_required_kinds:
        external_proof = _proof_receipts_mod().validate_proof_receipts(
            declared_receipts,
            required_kinds=declared_required_kinds,
            project=contract_project,
        )
        result["external_proof_validation"] = external_proof
        if not external_proof.get("pass"):
            closed_loop_passed = False
            reason_codes.extend(external_proof.get("reason_codes", []))
            required_actions.extend(external_proof.get("required_actions", []))

    decision_contract = (
        contract.get("decision_contract")
    )
    if decision_contract:
        contract_project = decision_contract.get("project") or project
        contract_gate = _grill_mod().validate_decision_contract(
            contract, project=contract_project
        )
        result["decision_contract_validation"] = contract_gate
        if contract_gate.get("pass"):
            conformance = _conformance_mod().validate_decision_conformance(
                contract,
                project=contract_project,
                validation=result,
            )
        else:
            conformance = {
                "pass": False,
                "status": "blocked_by_decision_contract",
                "violations": [],
                "required_actions": contract_gate.get("required_actions", []),
            }
        result["decision_conformance"] = conformance
        if not contract_gate.get("pass"):
            closed_loop_passed = False
            reason_codes.extend(contract_gate.get("reason_codes", []))
            required_actions.extend(contract_gate.get("required_actions", []))
        if contract_gate.get("pass") and not conformance.get("pass"):
            closed_loop_passed = False
            reason_codes.append("DECISION_DIFF_NONCONFORMANT")
            required_actions.extend(conformance.get("required_actions", []))
        result["artifacts"] = contract_gate.get(
            "artifacts", decision_contract.get("artifacts", {})
        )
        if contract_gate.get("pass"):
            result["outcome_learning"] = _outcomes_mod().record_outcome(
                contract,
                success=closed_loop_passed,
                validation=result,
                conformance=conformance,
            )
    has_contract_gates = bool(declared_receipts or declared_required_kinds) or any(
        key in contract
        for key in ("instruction_context", "intent_ledger", "decision_contract")
    )
    if has_contract_gates:
        result["overall"] = "pass" if closed_loop_passed else "fail"
        result["pass"] = closed_loop_passed
        result["decision"] = "pass" if closed_loop_passed else "blocked"
        result["reason_codes"] = list(dict.fromkeys(reason_codes))
        result["required_actions"] = list(dict.fromkeys(required_actions))
    if isinstance(result, dict) and result.get("overall") == "fail":
        r = _enrich("untested_changes", _coverage_mod().untested_changes, project=project, mode="unstaged")
        if r is not None:
            result["untested_changes"] = r
        feedback = _enrich(
            "validation_feedback",
            _feedback_mod().record_validation_feedback,
            result,
            project=contract_project,
            task_id=str(contract.get("task_profile", {}).get("task_id") or ""),
        )
        if feedback is not None:
            result["feedback_learning"] = feedback
    return result


def _task_grill(
    *,
    grill_action,
    description,
    project,
    decisions,
    mode,
    locale,
    max_questions,
    grill_session_id,
    decision_id,
    answer,
    selected_option,
    accept_recommendation,
    request_id,
) -> dict:
    """Execute the persistent decision-tree branch."""
    return _grill_mod().run_grill(
        operation=grill_action,
        description=description,
        project=project,
        decisions=decisions,
        mode=mode,
        locale=locale,
        max_questions=max_questions,
        session_id=grill_session_id,
        decision_id=decision_id,
        answer=answer,
        selected_option=selected_option,
        accept_recommendation=accept_recommendation,
        request_id=request_id,
        fact_resolver=lambda query, scoped_project: _search_mod().search_by_keyword(
            query=query,
            max_results=5,
            project=scoped_project,
            include_content=False,
        ),
    )


def _task_gate(task_contract, next_phase, current_state, project) -> dict:
    """Validate context and decision contracts before the phase gate."""
    contract = task_contract or {}
    contract_project = contract.get("task_profile", {}).get("project") or project
    instruction_gate = _task_context_mod().validate_instruction_context(
        contract,
        project=contract_project,
    )
    ledger_gate = _task_context_mod().validate_intent_ledger(
        contract,
        project=contract_project,
        check_diff=False,
    )
    context_failures = [
        gate
        for gate in (instruction_gate, ledger_gate)
        if not gate.get("pass", False)
    ]
    if context_failures:
        return {
            "pass": False,
            "decision": "blocked",
            "phase": next_phase,
            "reason_codes": [
                (
                    "INSTRUCTION_CONTEXT_NONCONFORMANT"
                    if gate is instruction_gate
                    else "INTENT_LEDGER_NONCONFORMANT"
                )
                for gate in context_failures
            ],
            "required_actions": [
                action
                for gate in context_failures
                for action in gate.get("required_actions", [])
            ],
            "instruction_context_validation": instruction_gate,
            "intent_ledger_validation": ledger_gate,
        }
    decision_gate = _grill_mod().validate_decision_contract(contract, project=project)
    if not decision_gate.get("pass"):
        return decision_gate
    derived_state = dict(current_state or {})
    if contract.get("decision_contract"):
        derived_state["decision_completeness_done"] = True
    return _task_mod().task_gate_check(
        task_contract=contract,
        next_phase=next_phase,
        current_state=derived_state,
    )


def _task_feedback(
    *,
    feedback_action,
    project,
    feedback_category,
    feedback_summary,
    feedback_severity,
    feedback_tool,
    finding_id,
    rule_id,
    framework,
    duration_ms,
    expected,
    actual,
    feedback_id,
    resolution,
    resolved_by,
    since_days,
    limit,
    request_id,
) -> dict:
    """Record, aggregate, or resolve local AI-development feedback."""
    feedback = _feedback_mod()
    try:
        if feedback_action == "record":
            return feedback.record_feedback(
                project=project,
                category=feedback_category,
                summary=feedback_summary,
                severity=feedback_severity,
                tool_name=feedback_tool,
                finding_id=finding_id,
                rule_id=rule_id,
                framework=framework,
                duration_ms=duration_ms,
                expected=expected,
                actual=actual,
                request_id=request_id,
            )
        if feedback_action == "summary":
            return feedback.summarize_feedback(
                project=project,
                since_days=since_days,
                limit=limit,
            )
        if feedback_action == "resolve":
            return feedback.resolve_feedback(
                feedback_id,
                resolution=resolution,
                resolved_by=resolved_by,
                request_id=request_id,
            )
    except ValueError as exc:
        return {"error": str(exc)}
    return {
        "error": (
            f"Unknown feedback action: {feedback_action}. "
            "Use 'record', 'summary', or 'resolve'."
        )
    }


def smart_task(action: str, description: str = "", targets: list = None,
               intent: str = "refactor", task_contract: dict = None,
               next_phase: str = None, current_state: dict = None,
               project: str = None, run_tests: bool = True,
               test_path: str = None, grill_action: str = "start",
               grill_session_id: str = None, decisions: list = None,
               decision_id: str = None, answer: str = None,
               selected_option: str = None, accept_recommendation: bool = False,
               mode: str = "interactive", locale: str = "und",
               max_questions: int = 8, request_id: str = None,
               proof_receipts: list = None, required_proof_kinds: list = None,
               feedback_action: str = "record", feedback_category: str = "other",
               feedback_summary: str = "", feedback_severity: str = "medium",
               feedback_tool: str = "", finding_id: str = "", rule_id: str = "",
               framework: str = "", duration_ms: float = None,
               expected: str = "", actual: str = "", feedback_id: str = "",
               resolution: str = "", resolved_by: str = "",
               since_days: int = 90, limit: int = 10) -> dict:
    """Route one task action to its atomic workflow branch."""
    if action == "grill":
        return _task_grill(
            grill_action=grill_action,
            description=description,
            project=project,
            decisions=decisions,
            mode=mode,
            locale=locale,
            max_questions=max_questions,
            grill_session_id=grill_session_id,
            decision_id=decision_id,
            answer=answer,
            selected_option=selected_option,
            accept_recommendation=accept_recommendation,
            request_id=request_id,
        )
    if action == "plan":
        result = _task_plan(description, targets, intent, project, grill_session_id)
        return _observe_task_continuity(
            action,
            result,
            project=project,
            description=description,
        )
    if action == "gate":
        result = _task_gate(task_contract, next_phase, current_state, project)
        return _observe_task_continuity(
            action,
            result,
            project=project,
            task_contract=task_contract,
            current_state=current_state,
        )
    if action == "validate":
        result = _task_validate(
            project,
            run_tests,
            test_path,
            task_contract,
            proof_receipts,
            required_proof_kinds,
        )
        return _observe_task_continuity(
            action,
            result,
            project=project,
            task_contract=task_contract,
        )
    if action == "feedback":
        return _task_feedback(
            feedback_action=feedback_action,
            project=project,
            feedback_category=feedback_category,
            feedback_summary=feedback_summary,
            feedback_severity=feedback_severity,
            feedback_tool=feedback_tool,
            finding_id=finding_id,
            rule_id=rule_id,
            framework=framework,
            duration_ms=duration_ms,
            expected=expected,
            actual=actual,
            feedback_id=feedback_id,
            resolution=resolution,
            resolved_by=resolved_by,
            since_days=since_days,
            limit=limit,
            request_id=request_id,
        )
    return {
        "error": (
            f"Unknown action: {action}. Use 'grill', 'plan', 'gate', 'validate', "
            "or 'feedback'."
        )
    }


def _observe_task_continuity(
    action: str,
    result: dict,
    *,
    project: str | None,
    description: str = "",
    task_contract: dict | None = None,
    current_state: dict | None = None,
) -> dict:
    """Keep continuity instrumentation best-effort and project scoped."""
    try:
        project_root = _structure_scan_path(_info_mod(), project)
        return _task_runs_mod().observe_task_action(
            action,
            result,
            project=project,
            project_root=project_root,
            description=description,
            task_contract=task_contract,
            current_state=current_state,
        )
    except Exception as exc:
        logger.debug("task continuity update failed: %s", exc)
        if isinstance(result, dict):
            result.setdefault(
                "continuity",
                {
                    "status": "unavailable",
                    "handoff_required": False,
                    "reason": type(exc).__name__,
                },
            )
        return result


# ---------------------------------------------------------------------------
# 5. structure — project overview with auto-enrichment
# ---------------------------------------------------------------------------

def _structure_scan_path(info, project: str | None) -> str:
    """Resolve a bounded project root for package/profile scans."""
    if project:
        try:
            for item in info.list_projects().get("projects") or []:
                if item.get("name") == project:
                    return item.get("root") or os.getcwd()
        except Exception as exc:
            logger.debug("project root lookup failed: %s", exc)
    try:
        index = info._load_index()
        if hasattr(index, "root_dir"):
            return str(index.root_dir)
    except Exception as exc:
        logger.debug("index root lookup failed: %s", exc)
    return os.getcwd()


def _structure_apis(info, project) -> dict:
    result = {}
    for key, fn in (
        ("apis", info.list_apis),
        ("categories", info.list_categories),
    ):
        value = _enrich(key, fn)
        if value is not None:
            result[key] = value
    drift = _enrich("contract_drift", _type_mod().contract_drift, project=project)
    if isinstance(drift, dict) and drift.get("drifts"):
        result["contract_drift"] = drift
    _truncate_structure_lists(result)
    return result


def _structure_packages(info, project) -> dict:
    try:
        from .. import dependency_scanner
    except ImportError:
        import dependency_scanner
    return dependency_scanner.scan_dependencies(
        _structure_scan_path(info, project)
    ).to_dict()


def _structure_dependencies(project, symbol_id, path) -> dict:
    try:
        graph = _refs_mod().dependency_graph(
            file_path=path,
            symbol_id=symbol_id,
            project=project,
            direction="both",
            max_depth=2,
        )
        result = {"graph": graph}
        if path:
            relationships = _enrich(
                "framework_relationships",
                _framework_relationships_mod().analyze_framework_relationships,
                path=path,
                project=project,
            )
            if isinstance(relationships, dict) and relationships.get(
                "status"
            ) not in {"not_applicable", "skipped"}:
                result["framework_relationships"] = relationships
        return result
    except Exception as exc:
        logger.debug("dependency_graph failed: %s", exc)
        return {"graph_error": str(exc)}


def _structure_types(project, symbol_id) -> dict:
    result = {}
    contracts = _type_mod()
    if symbol_id:
        schema = _enrich(
            "extract_type_schema",
            contracts.extract_type_schema,
            symbol_id=symbol_id,
        )
        if schema is not None:
            result["schema"] = schema
    drift = _enrich("contract_drift", contracts.contract_drift, project=project)
    if drift is not None:
        result["contract_drift"] = drift
    return result


def _structure_profile(
    info,
    project,
    result_mode,
    limit,
    cursor,
    include_non_production,
) -> dict:
    if not project:
        try:
            projects = _bounded_profile_projects()
        except Exception as exc:
            logger.debug("bounded project overview failed: %s", exc)
            projects = {"error": str(exc), "projects": []}
        return {
            "profile_scope": "workspace_overview",
            "projects": projects,
            "next_action": {
                "tool": "structure",
                "arguments": {"focus": "profile", "project": "<project>"},
            },
        }
    try:
        from .. import project_profile as profile_module
    except ImportError:
        import project_profile as profile_module
    from pathlib import Path as _Path
    return profile_module.build_project_profile(
        _Path(_structure_scan_path(info, project)),
        result_mode=result_mode,
        limit=limit,
        cursor=cursor,
        include_non_production=include_non_production,
    )


def _structure_overview(info, project) -> dict:
    result = {}
    try:
        result["projects"] = _add_explicit_project_counts(info.list_projects())
    except Exception as exc:
        logger.debug("list_projects failed: %s", exc)
        result["projects_error"] = str(exc)
    if project:
        for key, fn in (
            ("apis", info.list_apis),
            ("categories", info.list_categories),
            ("index_status", _maint_mod().check_index_status),
        ):
            value = _enrich(key, fn)
            if value is not None:
                result[key] = value
    _truncate_structure_lists(result)
    return result


def smart_structure(
    project: str = None,
    focus: str = None,
    symbol_id: str = None,
    path: str = None,
    result_mode: str = "compact",
    limit: int = 20,
    cursor: int = 0,
    include_non_production: bool = False,
) -> dict:
    """Route structure queries to small focus-specific analyzers."""
    info = _info_mod()
    if focus == "apis":
        return _structure_apis(info, project)
    if focus == "packages":
        return _structure_packages(info, project)
    if focus == "dependencies":
        return _structure_dependencies(project, symbol_id, path)
    if focus == "types":
        return _structure_types(project, symbol_id)
    if focus == "conventions":
        result = _enrich(
            "conventions",
            _conventions_mod().extract_conventions,
            project=project,
        )
        return {"conventions": result} if result is not None else {}
    if focus == "change_patterns":
        result = _enrich(
            "change_clusters",
            _change_patterns_mod().discover_change_clusters,
            project=project,
        )
        return {"change_clusters": result} if result is not None else {}
    if focus == "profile":
        result = _structure_profile(
            info,
            project,
            result_mode,
            limit,
            cursor,
            include_non_production,
        )
        project_root = _structure_scan_path(info, project)
        result["continuity"] = _task_runs_mod().read_task_continuity(
            project_root,
            project=project,
        )
        return result
    return _structure_overview(info, project)
