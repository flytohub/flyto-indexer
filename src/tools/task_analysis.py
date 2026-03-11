"""
Task Analysis — multi-dimensional task contract generation.

Analyzes a task across 6 dimensions using existing indexer tools,
derives constraints and execution strategy automatically.

All dimensions use unified scoring: HIGH score = HIGH risk (0.0–10.0).

Dimensions (all auto-computed from index):
  1. blast_radius        — how many symbols/files/projects are affected
  2. breaking_risk       — likelihood of breaking existing callers
  3. test_risk           — danger from insufficient test coverage on callers
  4. cross_coupling      — how many projects share the affected symbols
  5. complexity          — dependency depth + code complexity of targets
  6. rollback_difficulty — signal-based: public API, multi-project, many consumers

Output: 8-section task contract (task_profile, project_signals, dimensions,
        constraints, decision_metadata, execution_plan, strategy, human_summary).

Execution plan (data-driven cognitive guidance):
  - Concrete tool call sequences with pre-filled args from resolved targets
  - Step dependencies prevent skipping ahead
  - Reasoning modes (elimination, boundary_first, etc.) expressed through
    step ORDER and SELECTION — not text advice
  - Anti-patterns tracked in decision_metadata for audit/explainability
"""

import hashlib
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    from .references import (
        find_references,
        impact_analysis,
        edit_impact_preview,
        cross_project_impact,
        dependency_graph,
    )
    from .code_info import find_test_file, list_projects
    from .search import search_by_keyword
    from ..index_store import load_index
    from ..quality import code_health_score
except ImportError:
    from tools.references import (
        find_references,
        impact_analysis,
        edit_impact_preview,
        cross_project_impact,
        dependency_graph,
    )
    from tools.code_info import find_test_file, list_projects
    from tools.search import search_by_keyword
    from index_store import load_index
    from quality import code_health_score


# =========================================================================
# Constants
# =========================================================================

VALID_INTENTS = {"refactor", "bugfix", "feature", "cleanup", "migration"}

CONTRACT_VERSION = "task-contract.v1"

# =========================================================================
# Intent → default strategy
# =========================================================================

_INTENT_DEFAULTS = {
    "refactor": {
        "mode": "safe_refactor",
        "editing_style": "incremental",
        "verification_level": "high",
        "preferred_patch_scope": "narrow",
    },
    "bugfix": {
        "mode": "minimal_bugfix",
        "editing_style": "minimal",
        "verification_level": "medium",
        "preferred_patch_scope": "narrow",
    },
    "feature": {
        "mode": "contract_first_feature",
        "editing_style": "additive",
        "verification_level": "medium",
        "preferred_patch_scope": "medium",
    },
    "cleanup": {
        "mode": "cautious_cleanup",
        "editing_style": "subtractive",
        "verification_level": "low",
        "preferred_patch_scope": "narrow",
    },
    "migration": {
        "mode": "migration_mode",
        "editing_style": "incremental",
        "verification_level": "high",
        "preferred_patch_scope": "narrow",
    },
}

# =========================================================================
# Constraint derivation rules (level-based)
# =========================================================================

# (dimension, level, constraint_key)
# Triggers when dimension level >= the specified level
_CONSTRAINT_RULES_BY_LEVEL = [
    # medium+ triggers
    ("blast_radius",   "medium", "must_run_impact_analysis"),
    ("breaking_risk",  "medium", "must_use_edit_impact_preview"),
    ("test_risk",      "medium", "must_add_or_update_tests"),
    ("cross_coupling", "medium", "must_check_cross_project_usage"),
    # high triggers
    ("blast_radius",   "high", "must_use_small_steps"),
    ("breaking_risk",  "high", "must_request_human_review_on_public_contract_change"),
    ("test_risk",      "high", "must_validate_before_wide_change"),
    ("cross_coupling", "high", "must_list_affected_projects"),
    ("complexity",     "high", "must_review_dependency_chain"),
    ("rollback_difficulty", "high", "must_prepare_revert_plan"),
]

_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}

# blocked_actions derived from high-risk dimensions
_BLOCKED_ACTION_RULES = [
    ("breaking_risk",  "high", "rename_exported_symbol_without_review"),
    ("rollback_difficulty", "high", "multi_module_atomic_rewrite"),
    ("blast_radius",   "high", "bulk_replace"),
]

# =========================================================================
# Gate phases (fixed set for V1)
# =========================================================================

GATE_PHASES = ["inspect", "plan_changes", "apply_changes", "expand_changes", "finalize"]

# Strategy phase → gate phase mapping
# Allows task_gate_check to accept strategy-specific phase names
_STRATEGY_TO_GATE = {
    # inspect gate
    "inspect_references": "inspect",
    "inspect_tests": "inspect",
    "inspect_cross_project_usage": "inspect",
    "locate_root_cause": "inspect",
    # plan_changes gate
    "verify_fix_scope": "plan_changes",
    "prepare_minimal_change_set": "plan_changes",
    "define_interface_contract": "plan_changes",
    "scaffold_structure": "plan_changes",
    "confirm_no_live_callers": "plan_changes",
    "build_compatibility_adapter": "plan_changes",
    # apply_changes gate
    "apply_small_changes": "apply_changes",
    "apply_minimal_patch": "apply_changes",
    "implement_core_logic": "apply_changes",
    "remove_code": "apply_changes",
    "migrate_consumers_incrementally": "apply_changes",
    # expand_changes gate
    "add_tests": "expand_changes",
    "integrate_with_callers": "expand_changes",
    "remove_compatibility_layer": "expand_changes",
    # finalize gate
    "run_validation": "finalize",
}

# What must be true to enter each phase
_GATE_REQUIREMENTS = {
    "plan_changes": [
        ("impact_analysis_done", "must_run_impact_analysis", "Impact analysis must be completed first"),
    ],
    "apply_changes": [
        ("cross_project_check_done", "must_check_cross_project_usage",
         "Cross-project usage check required but not completed"),
        ("tests_reviewed", "must_add_or_update_tests",
         "Test review/addition required before applying changes"),
        ("human_review_completed", "must_request_human_review_on_public_contract_change",
         "Human review required for public contract change"),
    ],
    "expand_changes": [
        ("validation_passed", None,
         "Previous validation must pass before expanding changes"),
    ],
    "finalize": [
        ("validation_passed", None,
         "All validations must pass before finalizing"),
    ],
}

# Reason codes for gate blockers
_REASON_CODES = {
    "impact_analysis_done": "IMPACT_ANALYSIS_REQUIRED",
    "cross_project_check_done": "CROSS_PROJECT_CHECK_REQUIRED",
    "tests_reviewed": "TEST_REVIEW_REQUIRED",
    "human_review_completed": "HUMAN_REVIEW_REQUIRED_FOR_PUBLIC_CONTRACT_CHANGE",
    "validation_passed": "VALIDATION_REQUIRED",
}


# =========================================================================
# Decision metadata + execution plan — data-driven cognitive guidance
# =========================================================================

# Reasoning modes: explain WHY the plan is structured a certain way
_REASONING_MODES = {
    "minimal_diff": "Smallest change that achieves the goal",
    "boundary_first": "Define interfaces and boundaries before implementation",
    "elimination": "Exclude known-safe areas first, focus on remainder",
    "narrow_then_widen": "Verify on smallest surface first, then expand",
    "decompose": "Break into independently verifiable sub-tasks",
    "follow_pattern": "Match existing codebase patterns",
}

# Anti-patterns: machine-readable forbidden reasoning patterns
_ANTI_PATTERNS = {
    "brute_force_enumerate", "guess_and_check", "copy_paste_modify",
    "big_bang_rewrite", "change_without_boundary_check",
}

# Intent → default reasoning mode + anti-patterns
_INTENT_REASONING = {
    "bugfix": {
        "mode": "minimal_diff",
        "anti_patterns": ["guess_and_check", "big_bang_rewrite"],
    },
    "refactor": {
        "mode": "minimal_diff",
        "anti_patterns": ["big_bang_rewrite", "copy_paste_modify"],
    },
    "feature": {
        "mode": "boundary_first",
        "anti_patterns": ["copy_paste_modify", "change_without_boundary_check"],
    },
    "cleanup": {
        "mode": "elimination",
        "anti_patterns": ["brute_force_enumerate", "big_bang_rewrite"],
    },
    "migration": {
        "mode": "boundary_first",
        "anti_patterns": ["big_bang_rewrite", "change_without_boundary_check"],
    },
}

# Risk posture: derived from overall risk level
_RISK_POSTURES = {
    "high": "conservative",
    "moderate": "cautious",
    "low": "standard",
    "safe": "standard",
}


# =========================================================================
# Helpers
# =========================================================================

def _score_to_level(score: float) -> str:
    """Convert 0-10 score to low/medium/high level."""
    if score >= 7.0:
        return "high"
    elif score >= 4.0:
        return "medium"
    return "low"


def _overall_risk(max_score: float) -> str:
    """Convert max dimension score to overall risk label."""
    if max_score >= 8.0:
        return "high"
    elif max_score >= 5.0:
        return "moderate"
    elif max_score >= 2.0:
        return "low"
    return "safe"


# =========================================================================
# Target resolution
# =========================================================================

def _resolve_targets(targets: List[str]) -> List[dict]:
    """Resolve target names to symbol IDs using search."""
    resolved = []
    seen_ids = set()
    index = load_index()
    symbols = index.get("symbols", {})

    for target in targets:
        # Try as exact symbol_id first
        if target in symbols:
            resolved.append({
                "input": target,
                "symbol_id": target,
                "name": symbols[target].get("name", ""),
                "type": symbols[target].get("type", ""),
                "path": symbols[target].get("path", ""),
            })
            seen_ids.add(target)
            continue

        # Search by keyword
        results = search_by_keyword(target, max_results=5)
        matched = False
        for item in results.get("results", []):
            sid = item.get("symbol_id", "")
            if sid and sid not in seen_ids:
                resolved.append({
                    "input": target,
                    "symbol_id": sid,
                    "name": item.get("name", ""),
                    "type": item.get("type", ""),
                    "path": item.get("path", ""),
                })
                seen_ids.add(sid)
                matched = True
                break

        if not matched:
            resolved.append({
                "input": target,
                "symbol_id": None,
                "name": target,
                "type": "unknown",
                "path": "",
            })

    return resolved


# =========================================================================
# Dimension scoring — all dimensions: HIGH score = HIGH risk
# =========================================================================

def _score_blast_radius(resolved: List[dict]) -> dict:
    """How many symbols/files/projects are affected."""
    total_affected = 0
    affected_files = set()
    affected_projects = set()
    evidence_items = []

    for target in resolved:
        sid = target.get("symbol_id")
        if not sid:
            continue
        result = impact_analysis(sid)
        count = result.get("affected_count", 0)
        total_affected += count
        for a in result.get("affected", []):
            path = a.get("path", "")
            if path:
                affected_files.add(path)
            aid = a.get("id", "")
            if ":" in aid:
                affected_projects.add(aid.split(":")[0])
        evidence_items.append({
            "symbol": sid,
            "affected_count": count,
        })

    # Scoring curve
    if total_affected == 0:
        score = 0.0
    elif total_affected <= 3:
        score = 2.0
    elif total_affected <= 10:
        score = 4.0 + (total_affected - 4) * 0.3
    elif total_affected <= 20:
        score = 6.0 + (total_affected - 11) * 0.2
    elif total_affected <= 50:
        score = 8.0 + (total_affected - 21) * 0.07
    else:
        score = 10.0
    score = round(min(score, 10.0), 1)

    rationale = f"{total_affected} affected symbols across {len(affected_files)} files"
    if affected_projects:
        rationale += f" in {len(affected_projects)} project(s)"

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "affected_symbols": total_affected,
            "affected_files": len(affected_files),
            "affected_projects": sorted(affected_projects),
        },
    }


def _score_breaking_risk(resolved: List[dict], intent: str) -> dict:
    """Likelihood of breaking existing callers."""
    total_call_sites = 0
    max_risk = "safe"
    risk_order = {"safe": 0, "low": 1, "moderate": 2, "high": 3}
    has_public_api = False
    signature_changes = 0

    change_type_map = {
        "refactor": "signature_change",
        "bugfix": "modify",
        "feature": "modify",
        "cleanup": "delete",
        "migration": "rename",
    }
    change_type = change_type_map.get(intent, "modify")

    for target in resolved:
        sid = target.get("symbol_id")
        if not sid:
            continue
        result = edit_impact_preview(sid, change_type=change_type)
        sites = result.get("total_call_sites", 0)
        total_call_sites += sites
        risk = result.get("risk", "safe")
        if risk_order.get(risk, 0) > risk_order.get(max_risk, 0):
            max_risk = risk
        if sites > 0:
            has_public_api = True
        if change_type in ("signature_change", "rename", "delete"):
            signature_changes += 1

    # Score
    risk_scores = {"safe": 0.0, "low": 3.0, "moderate": 6.0, "high": 8.0}
    score = risk_scores.get(max_risk, 0.0)
    if total_call_sites > 20:
        score = min(score + 2.0, 10.0)
    elif total_call_sites > 10:
        score = min(score + 1.0, 10.0)
    score = round(score, 1)

    rationale_parts = []
    if has_public_api:
        rationale_parts.append("touches public API")
    if total_call_sites > 0:
        rationale_parts.append(f"{total_call_sites} call site(s)")
    if signature_changes > 0:
        rationale_parts.append(f"{signature_changes} signature change(s) detected")
    rationale = "; ".join(rationale_parts) if rationale_parts else "No breaking risk detected"

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "total_call_sites": total_call_sites,
            "max_risk_level": max_risk,
            "has_public_api": has_public_api,
            "signature_changes_detected": signature_changes,
        },
    }


def _score_test_risk(resolved: List[dict]) -> dict:
    """Risk from insufficient test coverage on callers.

    Checks both the target files AND their callers for test coverage.
    A symbol with tests is still risky if its callers lack tests.
    HIGH score = HIGH risk (low coverage).
    """
    targets_with_tests = 0
    targets_without_tests = 0
    callers_with_tests = 0
    callers_without_tests = 0
    test_files = []
    checked_paths = set()

    for target in resolved:
        sid = target.get("symbol_id")
        path = target.get("path", "")

        # Check target file test coverage
        if path and path not in checked_paths:
            checked_paths.add(path)
            result = find_test_file(path)
            test_file = result.get("test_file", "")
            if test_file:
                targets_with_tests += 1
                test_files.append(test_file)
            else:
                targets_without_tests += 1
        elif not path:
            targets_without_tests += 1

        # Check caller test coverage via find_references
        if sid:
            refs = find_references(sid)
            for ref in refs.get("references", []):
                caller_path = ref.get("from_path", "")
                if not caller_path or caller_path in checked_paths:
                    continue
                checked_paths.add(caller_path)
                caller_test = find_test_file(caller_path)
                if caller_test.get("test_file"):
                    callers_with_tests += 1
                    test_files.append(caller_test["test_file"])
                else:
                    callers_without_tests += 1

    # Combined coverage: target coverage + caller coverage
    total_targets = targets_with_tests + targets_without_tests
    total_callers = callers_with_tests + callers_without_tests

    if total_targets == 0:
        target_ratio = 0.0
    else:
        target_ratio = targets_with_tests / total_targets

    if total_callers == 0:
        caller_ratio = 1.0  # No callers = no caller risk
    else:
        caller_ratio = callers_with_tests / total_callers

    # Weighted: target coverage matters more, but untested callers are risky too
    # 60% target coverage + 40% caller coverage
    combined_ratio = target_ratio * 0.6 + caller_ratio * 0.4

    # INVERTED: high coverage → low risk score
    score = round((1.0 - combined_ratio) * 10.0, 1)

    # Build rationale
    parts = []
    if total_targets > 0:
        parts.append(f"{targets_with_tests}/{total_targets} target files have tests")
    if total_callers > 0:
        parts.append(f"{callers_with_tests}/{total_callers} caller files have tests")

    if not parts:
        rationale = "No targets or callers to assess"
    elif combined_ratio == 0:
        rationale = "No test files found — very high risk. " + "; ".join(parts)
    elif combined_ratio < 0.5:
        rationale = "Low test coverage. " + "; ".join(parts)
    elif combined_ratio < 1.0:
        rationale = "Partial test coverage. " + "; ".join(parts)
    else:
        rationale = "Full test coverage. " + "; ".join(parts)

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "target_files_tested": targets_with_tests,
            "target_files_untested": targets_without_tests,
            "caller_files_tested": callers_with_tests,
            "caller_files_untested": callers_without_tests,
            "test_files": sorted(set(test_files)),
        },
    }


def _score_cross_coupling(resolved: List[dict]) -> dict:
    """How many projects share the affected symbols.

    Uses find_references(symbol_id) to get precise cross-project callers,
    avoiding name-matching false positives from cross_project_impact(name).
    """
    all_affected_projects = set()
    cross_refs = []
    source_projects = set()

    for target in resolved:
        sid = target.get("symbol_id")
        if not sid:
            continue

        # Determine source project from symbol_id
        src_project = sid.split(":")[0] if ":" in sid else ""
        if src_project:
            source_projects.add(src_project)

        # Use find_references for precise symbol-level cross-project refs
        refs = find_references(sid)
        for ref in refs.get("references", []):
            caller_id = ref.get("from_symbol", "")
            if ":" not in caller_id:
                continue
            caller_project = caller_id.split(":")[0]

            # Only count cross-project references
            if caller_project and caller_project != src_project:
                all_affected_projects.add(caller_project)
                cross_refs.append({
                    "caller": caller_id,
                    "caller_project": caller_project,
                    "source_project": src_project,
                })

    num_projects = len(all_affected_projects)
    total_cross_refs = len(cross_refs)

    if num_projects == 0:
        score = 0.0
    elif num_projects == 1:
        score = 3.0 + min(total_cross_refs * 0.5, 2.0)
    elif num_projects == 2:
        score = 5.0 + min(total_cross_refs * 0.3, 2.0)
    else:
        score = 7.0 + min(num_projects * 0.5, 3.0)
    score = round(min(score, 10.0), 1)

    if num_projects == 0:
        rationale = "No cross-project coupling detected"
    else:
        rationale = f"Shared by {num_projects} project(s) with {total_cross_refs} cross-reference(s)"

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "shared_by_projects": sorted(all_affected_projects),
            "total_cross_refs": total_cross_refs,
            "source_projects": sorted(source_projects),
        },
    }


def _score_complexity(resolved: List[dict]) -> dict:
    """Dependency depth + code complexity of targets."""
    max_depth = 0
    total_deps = 0
    total_dependents = 0
    complex_functions_count = 0

    for target in resolved:
        sid = target.get("symbol_id")
        path = target.get("path", "")
        if not path and not sid:
            continue

        dep_result = dependency_graph(
            file_path=path if path else None,
            symbol_id=sid if not path else None,
            direction="both",
            max_depth=3,
        )
        summary = dep_result.get("summary", {})
        imports_count = summary.get("imports_count", 0)
        dependents_count = summary.get("dependents_count", 0)
        total_deps += imports_count
        total_dependents += dependents_count
        depth = min(imports_count, 10)
        if depth > max_depth:
            max_depth = depth

        # Check symbol line count
        if sid:
            index = load_index()
            sym = index.get("symbols", {}).get(sid, {})
            lines = sym.get("end_line", 0) - sym.get("start_line", 0)
            if lines > 50:
                complex_functions_count += 1

    dep_score = min(total_deps * 0.5, 5.0)
    depth_score = min(max_depth * 0.8, 3.0)
    complex_bonus = min(complex_functions_count * 1.0, 2.0)
    score = round(min(dep_score + depth_score + complex_bonus, 10.0), 1)

    rationale = f"Dependency depth {max_depth}, {total_deps} imports, {total_dependents} dependents"
    if complex_functions_count > 0:
        rationale += f", {complex_functions_count} complex function(s)"

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "dependency_depth": max_depth,
            "total_imports": total_deps,
            "total_dependents": total_dependents,
            "complex_functions": complex_functions_count,
        },
    }


def _score_rollback_difficulty(
    blast: dict, breaking: dict, coupling: dict, complexity: dict,
) -> dict:
    """Signal-based rollback difficulty scoring.

    Instead of a weighted average, counts discrete risk signals:
    - public_api_change: exported symbol with callers
    - multi_project: affects more than 1 project
    - many_consumers: >10 call sites
    - high_breaking: breaking_risk is high
    - high_coupling: cross_coupling is high
    - deep_dependency: complexity is high

    Each signal contributes to the score. More signals = harder rollback.
    """
    signals = []

    # Signal: public API change
    if breaking.get("evidence", {}).get("has_public_api", False):
        signals.append("public_api_change")

    # Signal: multi-project impact
    affected_projects = blast.get("evidence", {}).get("affected_projects", [])
    coupled_projects = coupling.get("evidence", {}).get("shared_by_projects", [])
    all_projects = set(affected_projects) | set(coupled_projects)
    if len(all_projects) > 1:
        signals.append("multi_project")

    # Signal: many consumers
    call_sites = breaking.get("evidence", {}).get("total_call_sites", 0)
    if call_sites > 10:
        signals.append("many_consumers")

    # Signal: high breaking risk
    if breaking.get("level") == "high":
        signals.append("high_breaking")

    # Signal: high cross-coupling
    if coupling.get("level") == "high":
        signals.append("high_coupling")

    # Signal: deep dependency chain
    if complexity.get("level") == "high":
        signals.append("deep_dependency")

    # Score: each signal adds ~2 points, capped at 10
    num_signals = len(signals)
    if num_signals == 0:
        score = 0.0
    elif num_signals == 1:
        score = 3.0
    elif num_signals == 2:
        score = 5.0
    elif num_signals == 3:
        score = 7.0
    elif num_signals == 4:
        score = 8.5
    else:
        score = min(9.0 + (num_signals - 5) * 0.5, 10.0)
    score = round(score, 1)

    # Rationale from signals
    signal_descriptions = {
        "public_api_change": "Public API change — consumers may have adapted",
        "multi_project": f"Affects {len(all_projects)} projects — coordinated rollback needed",
        "many_consumers": f"{call_sites} call sites — wide rollback surface",
        "high_breaking": "High breaking risk makes rollback dangerous",
        "high_coupling": "Cross-project coupling requires coordinated rollback",
        "deep_dependency": "Deep dependency chain complicates rollback",
    }

    rationale_parts = [signal_descriptions[s] for s in signals]
    if not rationale_parts:
        rationale_parts = ["Low rollback risk"]

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": "; ".join(rationale_parts),
        "evidence": {
            "signals": signals,
            "signal_count": num_signals,
        },
    }


# =========================================================================
# Project signals (reuses code_health_score — zero extra I/O)
# =========================================================================

def _compute_project_signals(resolved: List[dict], project: str = None) -> dict:
    """Compute project signals: health score + real test file sampling.

    - health_score: from code_health_score (zero extra I/O)
    - test_maturity: actual find_test_file sampling on unique project paths (max 30)
    - complexity_baseline: from code_health_score complexity dimension
    """
    index = load_index()
    symbols = index.get("symbols", {})

    # Determine project from resolved targets
    target_project = project
    if not target_project:
        for t in resolved:
            sid = t.get("symbol_id") or ""
            if ":" in sid:
                target_project = sid.split(":")[0]
                break

    # --- Health score (zero extra I/O) ---
    health = code_health_score(project=target_project)

    if health.get("error"):
        health_signal = {"score": 0, "grade": "N/A", "basis": "No data available"}
        complexity_baseline = {"score": 0.0, "basis": "No data available"}
    else:
        total_score = health.get("score", 0)
        grade = health.get("grade", "N/A")
        health_signal = {
            "score": total_score,
            "grade": grade,
            "basis": f"Code health {total_score}/100 (grade {grade})",
        }
        breakdown = health.get("breakdown", {})
        complexity_raw = breakdown.get("complexity", {}).get("score", 25)
        complexity_detail = breakdown.get("complexity", {}).get("detail", "")
        # Invert: health score gives 25=good, 0=bad → we want 0=simple, 10=complex
        complexity_baseline = {
            "score": round((25 - complexity_raw) / 25 * 10, 1),
            "basis": complexity_detail or f"Complexity score {complexity_raw}/25",
        }

    # --- Test maturity: real sampling with path dedup ---
    tested = 0
    sampled = 0
    seen_paths = set()
    SAMPLE_CAP = 30

    for sid, sym in symbols.items():
        if target_project and not sid.startswith(target_project + ":"):
            continue
        path = sym.get("path", "")
        if not path or path in seen_paths:
            continue
        # Skip test files themselves
        if "/test" in path.lower() or path.lower().startswith("test"):
            continue
        seen_paths.add(path)
        sampled += 1
        if sampled > SAMPLE_CAP:
            break
        result = find_test_file(path)
        if result.get("test_file"):
            tested += 1

    if sampled > 0:
        maturity_score = round(tested / sampled * 10, 1)
        basis = f"{tested}/{sampled} sampled source files have test files"
    else:
        maturity_score = 0.0
        basis = "No source files to sample"

    return {
        "health_score": health_signal,
        "test_maturity": {
            "score": maturity_score,
            "basis": basis,
        },
        "complexity_baseline": complexity_baseline,
    }


# =========================================================================
# Index confidence
# =========================================================================

def _compute_index_confidence(resolved: List[dict]) -> dict:
    """Assess how trustworthy the index data is for the resolved targets.

    Checks:
    - reverse_index coverage: do resolved symbols have entries?
    - dependency resolution: are deps resolved or just name-based?
    - symbol completeness: do symbols have path, start_line, end_line?

    Returns score 0-10 (10 = fully reliable) and list of warnings.
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    total_checks = 0
    passed_checks = 0
    warnings = []

    for target in resolved:
        sid = target.get("symbol_id")
        if not sid:
            continue

        # Check 1: symbol exists in index
        total_checks += 1
        sym = symbols.get(sid)
        if sym:
            passed_checks += 1
        else:
            warnings.append(f"Symbol {sid} not found in index")
            continue

        # Check 2: symbol has complete metadata (path + lines)
        total_checks += 1
        if sym.get("path") and sym.get("start_line") is not None:
            passed_checks += 1
        else:
            warnings.append(f"Symbol {sid} has incomplete metadata")

        # Check 3: reverse_index has entry for this symbol
        total_checks += 1
        if sid in reverse_index or sym.get("name", "") in reverse_index:
            passed_checks += 1
        else:
            # Not having reverse_index entries could mean 0 callers (ok)
            # or missing data (bad). Check if deps reference this symbol.
            has_dep_refs = any(
                dep.get("metadata", {}).get("resolved_target") == sid
                for dep in dependencies.values()
            )
            if has_dep_refs:
                # Deps reference it but reverse_index doesn't — data gap
                warnings.append(f"Symbol {sid} referenced in deps but missing from reverse_index")
            else:
                passed_checks += 1  # Likely just has no callers

    # Check overall index stats
    total_checks += 1
    total_syms = len(symbols)
    reverse_entries = len(reverse_index)
    if total_syms > 0 and reverse_entries / total_syms > 0.1:
        passed_checks += 1
    elif total_syms > 0:
        warnings.append(f"Low reverse_index coverage: {reverse_entries}/{total_syms} symbols")
    else:
        warnings.append("No symbols in index")

    if total_checks == 0:
        return {"score": 0.0, "level": "low", "warnings": ["No targets to assess"]}

    score = round(passed_checks / total_checks * 10, 1)
    level = "high" if score >= 8 else "medium" if score >= 5 else "low"

    return {
        "score": score,
        "level": level,
        "checks_passed": passed_checks,
        "checks_total": total_checks,
        "warnings": warnings,
    }


# =========================================================================
# Constraint + strategy derivation
# =========================================================================

def _derive_constraints(dimensions: Dict[str, dict], intent: str) -> dict:
    """Derive constraints from dimension levels with cross-dimension rules."""
    constraints = {}

    # Level-based constraint rules
    for dim_name, required_level, constraint_key in _CONSTRAINT_RULES_BY_LEVEL:
        dim_level = dimensions.get(dim_name, {}).get("level", "low")
        if _LEVEL_ORDER.get(dim_level, 0) >= _LEVEL_ORDER.get(required_level, 0):
            constraints[constraint_key] = True

    # blocked_actions (level-based)
    blocked = []
    for dim_name, required_level, action in _BLOCKED_ACTION_RULES:
        dim_level = dimensions.get(dim_name, {}).get("level", "low")
        if _LEVEL_ORDER.get(dim_level, 0) >= _LEVEL_ORDER.get(required_level, 0):
            blocked.append(action)

    # Intent-specific
    if intent == "cleanup":
        constraints["must_verify_no_live_callers"] = True
    if intent == "migration":
        constraints["must_build_compatibility_layer"] = True

    # ---------------------------------------------------------------
    # Cross-dimension rules: combined risk escalation
    # ---------------------------------------------------------------
    blast_level = dimensions.get("blast_radius", {}).get("level", "low")
    test_level = dimensions.get("test_risk", {}).get("level", "low")
    breaking_level = dimensions.get("breaking_risk", {}).get("level", "low")
    coupling_level = dimensions.get("cross_coupling", {}).get("level", "low")

    # High blast + high test_risk → very strict step size
    if blast_level == "high" and test_level == "high":
        constraints["max_files_per_step"] = 1
        constraints["must_validate_before_wide_change"] = True
    # High breaking + high coupling → block wide changes
    elif breaking_level == "high" and coupling_level == "high":
        constraints["max_files_per_step"] = 1
        if "multi_module_atomic_rewrite" not in blocked:
            blocked.append("multi_module_atomic_rewrite")
    # Any two high dimensions → tighter step size
    elif sum(1 for d in dimensions.values() if d.get("level") == "high") >= 2:
        constraints.setdefault("max_files_per_step", 2)
    else:
        # Normal max_files_per_step based on blast_radius alone
        blast_score = dimensions.get("blast_radius", {}).get("score", 0)
        if blast_level == "high":
            constraints.setdefault("max_files_per_step", 2)
        elif blast_level == "medium" or constraints.get("must_use_small_steps"):
            constraints.setdefault("max_files_per_step", 3)
        else:
            constraints.setdefault("max_files_per_step", 5)

    if blocked:
        constraints["blocked_actions"] = blocked

    return constraints


def _derive_strategy(dimensions: Dict[str, dict], intent: str, constraints: dict) -> dict:
    """Derive execution strategy from intent + dimensions.

    Mode override rules (dimensions can upgrade the intent-based mode):
    - bugfix/cleanup/feature with blast_radius=high → safe_refactor
    - bugfix/cleanup with cross_coupling=high → migration_mode
    - Any intent with 3+ high dimensions → safe_refactor
    The original intent mode is preserved in 'original_mode' when overridden.
    """
    defaults = _INTENT_DEFAULTS.get(intent, _INTENT_DEFAULTS["refactor"])
    mode = defaults["mode"]
    original_mode = None

    # --- Mode override by dimensions ---
    blast_level = dimensions.get("blast_radius", {}).get("level", "low")
    coupling_level = dimensions.get("cross_coupling", {}).get("level", "low")
    high_count = sum(1 for d in dimensions.values() if d.get("level") == "high")

    # 3+ high dimensions → always safe_refactor (most cautious)
    if high_count >= 3 and mode != "safe_refactor":
        original_mode = mode
        mode = "safe_refactor"
    # High cross_coupling on bugfix/cleanup → migration_mode (need coordinated approach)
    elif coupling_level == "high" and intent in ("bugfix", "cleanup"):
        original_mode = mode
        mode = "migration_mode"
    # High blast_radius on bugfix/cleanup/feature → safe_refactor (need inspection phases)
    elif blast_level == "high" and intent in ("bugfix", "cleanup", "feature"):
        original_mode = mode
        mode = "safe_refactor"

    # Risk level from max dimension score
    max_score = max(
        dimensions.get("blast_radius", {}).get("score", 0),
        dimensions.get("breaking_risk", {}).get("score", 0),
        dimensions.get("cross_coupling", {}).get("score", 0),
        dimensions.get("test_risk", {}).get("score", 0),
    )
    risk_level = _overall_risk(max_score)

    # Verification level
    if max_score >= 8:
        verification_level = "high"
    elif max_score >= 5:
        verification_level = "medium"
    else:
        verification_level = defaults["verification_level"]

    result = {
        "mode": mode,
        "risk_level": risk_level,
        "editing_style": defaults["editing_style"],
        "verification_level": verification_level,
        "preferred_patch_scope": defaults["preferred_patch_scope"],
    }
    if original_mode:
        result["original_mode"] = original_mode
        result["mode_overridden_by"] = (
            "3+ high dimensions" if high_count >= 3
            else f"{coupling_level} cross_coupling" if coupling_level == "high"
            else f"{blast_level} blast_radius"
        )
    return result


# =========================================================================
# Thinking hints derivation
# =========================================================================

def _build_decision_metadata(dimensions: Dict[str, dict], intent: str) -> dict:
    """Build decision metadata explaining WHY the plan is structured this way.

    This is for explainability, audit, and human_summary — NOT for driving AI behavior.
    """
    defaults = _INTENT_REASONING.get(intent, _INTENT_REASONING["refactor"])
    reasoning_mode = defaults["mode"]
    anti_patterns = list(defaults["anti_patterns"])

    # Dimension-based anti-pattern upgrades
    if dimensions.get("breaking_risk", {}).get("level") == "high":
        if "change_without_boundary_check" not in anti_patterns:
            anti_patterns.append("change_without_boundary_check")
    if dimensions.get("test_risk", {}).get("level") == "high":
        if "guess_and_check" not in anti_patterns:
            anti_patterns.append("guess_and_check")

    # Override reasoning_mode for extreme risk
    high_count = sum(1 for d in dimensions.values() if d.get("level") == "high")
    if high_count >= 3:
        reasoning_mode = "narrow_then_widen"

    # Risk posture
    max_score = max((d.get("score", 0) for d in dimensions.values()), default=0)
    risk_label = _overall_risk(max_score)
    risk_posture = _RISK_POSTURES.get(risk_label, "standard")

    return {
        "reasoning_mode": reasoning_mode,
        "risk_posture": risk_posture,
        "anti_patterns": anti_patterns,
    }


def _build_execution_plan(
    resolved: List[dict],
    dimensions: Dict[str, dict],
    intent: str,
    constraints: dict,
) -> list:
    """Build a concrete, ordered sequence of tool calls with pre-filled args.

    The execution plan is the compiled result of intent × dimensions × constraints.
    Each step has:
    - id: unique step identifier
    - tool: MCP tool name to call
    - args: pre-filled arguments from resolved targets
    - purpose: machine-readable tag (scope_callers, verify_tests, etc.)
    - required: whether this step is mandatory
    - depends_on: list of step IDs that must complete first

    The reasoning mode (elimination, boundary_first, etc.) is expressed through
    the ORDER and SELECTION of steps — not as text advice.
    """
    steps = []
    step_num = [0]  # mutable counter for closures

    def _add(tool: str, args: dict, purpose: str,
             required: bool = True, depends_on: list = None):
        step_num[0] += 1
        step_id = f"step_{step_num[0]:02d}_{purpose}"
        steps.append({
            "id": step_id,
            "tool": tool,
            "args": args,
            "purpose": purpose,
            "required": required,
            "depends_on": depends_on or [],
        })
        return step_id

    # Extract target data for pre-filling args
    symbol_ids = [t["symbol_id"] for t in resolved if t.get("symbol_id")]
    file_paths = list({t["path"] for t in resolved if t.get("path")})
    first_sid = symbol_ids[0] if symbol_ids else None
    first_path = file_paths[0] if file_paths else None

    # Dimension levels
    blast_level = dimensions.get("blast_radius", {}).get("level", "low")
    breaking_level = dimensions.get("breaking_risk", {}).get("level", "low")
    coupling_level = dimensions.get("cross_coupling", {}).get("level", "low")
    test_level = dimensions.get("test_risk", {}).get("level", "low")
    complexity_level = dimensions.get("complexity", {}).get("level", "low")

    # =================================================================
    # Phase 1: INSPECT — understand the landscape
    # =================================================================

    # Step: scope callers (always first for anything that touches existing code)
    ref_step = None
    if first_sid and intent != "feature":
        ref_step = _add("find_references", {"symbol_id": first_sid}, "scope_callers")

    # Step: verify test coverage
    test_step = None
    if first_path:
        test_step = _add(
            "find_test_file", {"file_path": first_path}, "verify_test_coverage",
            required=test_level in ("medium", "high"),
        )

    # Step: check cross-project usage (if coupling is a concern)
    cross_step = None
    if coupling_level in ("medium", "high") and first_sid:
        cross_step = _add(
            "find_references", {"symbol_id": first_sid}, "check_cross_project",
            depends_on=[ref_step] if ref_step else [],
        )

    # Step: map dependency graph (if complexity is a concern)
    dep_step = None
    if complexity_level in ("medium", "high") and first_path:
        dep_step = _add(
            "dependency_graph",
            {"file_path": first_path, "direction": "both", "max_depth": 3},
            "map_dependencies",
            required=complexity_level == "high",
        )

    # =================================================================
    # Phase 2: ASSESS — quantify risk before making changes
    # =================================================================

    # Step: impact analysis (required when blast is medium+)
    impact_step = None
    if first_sid:
        impact_deps = []
        if ref_step:
            impact_deps.append(ref_step)
        impact_step = _add(
            "impact_analysis", {"symbol_id": first_sid}, "assess_blast_radius",
            required=blast_level in ("medium", "high"),
            depends_on=impact_deps,
        )

    # Step: edit impact preview (required when breaking is medium+)
    preview_step = None
    if first_sid and breaking_level in ("medium", "high"):
        change_type_map = {
            "refactor": "signature_change", "bugfix": "modify",
            "feature": "modify", "cleanup": "delete", "migration": "rename",
        }
        preview_step = _add(
            "edit_impact_preview",
            {"symbol_id": first_sid, "change_type": change_type_map.get(intent, "modify")},
            "preview_change_risk",
            depends_on=[impact_step] if impact_step else [],
        )

    # =================================================================
    # Phase 3: GATE — verify ready to proceed
    # =================================================================

    gate_deps = [s for s in [ref_step, test_step, impact_step] if s]
    gate_step = _add(
        "task_gate_check",
        {"next_phase": "plan_changes"},
        "gate_before_plan",
        depends_on=gate_deps,
    )

    # =================================================================
    # Phase 4: Additional steps for multi-target tasks
    # =================================================================

    # For each additional target beyond the first, add scoping steps
    if len(symbol_ids) > 1:
        for extra_sid in symbol_ids[1:]:
            extra_ref = _add(
                "find_references", {"symbol_id": extra_sid},
                "scope_callers_additional",
                required=False,
                depends_on=[gate_step],
            )

    # Step: final gate before applying changes
    _add(
        "task_gate_check",
        {"next_phase": "apply_changes"},
        "gate_before_apply",
        depends_on=[gate_step],
    )

    return steps


# =========================================================================
# Human summary
# =========================================================================

def _generate_human_summary(dimensions: Dict[str, dict], constraints: dict) -> dict:
    """Generate human-readable summary, top risks, and attention items."""
    # Top risks
    top_risks = []
    attention = []

    blast = dimensions.get("blast_radius", {})
    breaking = dimensions.get("breaking_risk", {})
    test_risk = dimensions.get("test_risk", {})
    coupling = dimensions.get("cross_coupling", {})

    if coupling.get("level") == "high":
        top_risks.append("Cross-project dependency is high")
        projects = coupling.get("evidence", {}).get("shared_by_projects", [])
        if projects:
            attention.append(f"Confirm impact on: {', '.join(projects)}")

    if breaking.get("level") == "high":
        top_risks.append("Public API may be affected")
        attention.append("Confirm exported API is allowed to change")

    if test_risk.get("level") in ("medium", "high"):
        top_risks.append("Test coverage is insufficient")
        attention.append("Add or review tests before making changes")

    if blast.get("level") == "high":
        count = blast.get("evidence", {}).get("affected_symbols", 0)
        top_risks.append(f"Large blast radius ({count} affected symbols)")

    rollback = dimensions.get("rollback_difficulty", {})
    if rollback.get("level") == "high":
        top_risks.append("Rollback will be difficult if something goes wrong")
        attention.append("Prepare revert plan before starting")

    # Summary text
    high_dims = [k for k, v in dimensions.items() if v.get("level") == "high"]
    if high_dims:
        summary = f"High-risk task. Key concerns: {', '.join(high_dims)}. Proceed with caution — follow constraints and strategy phases."
    elif any(v.get("level") == "medium" for v in dimensions.values()):
        summary = "Moderate-risk task. Some dimensions require attention. Follow the recommended execution order."
    else:
        summary = "Low-risk task. Safe to proceed with standard workflow."

    if not top_risks:
        top_risks.append("No significant risks detected")

    return {
        "summary": summary,
        "top_risks": top_risks,
        "recommended_human_attention": attention,
    }


# =========================================================================
# Main entry points
# =========================================================================

def analyze_task(
    description: str,
    targets: List[str],
    intent: str = "refactor",
    project: str = None,
    options: dict = None,
) -> dict:
    """Analyze a task and produce a multi-dimensional task contract.

    Args:
        description: What the task is about (human-readable)
        targets: List of symbol names, symbol IDs, or file paths
        intent: refactor | bugfix | feature | cleanup | migration
        project: Filter to a specific project
        options: Optional dict with include_evidence (bool), include_human_summary (bool)

    Returns:
        Task contract with 8 sections:
          task_profile, project_signals, dimensions, constraints,
          decision_metadata, execution_plan, strategy, human_summary
    """
    if intent not in VALID_INTENTS:
        return {"error": f"Invalid intent: {intent}. Use: {', '.join(sorted(VALID_INTENTS))}"}
    if not targets:
        return {"error": "At least one target is required (symbol name, ID, or file path)"}

    opts = options or {}

    # Generate task ID
    task_hash = hashlib.sha256(
        f"{description}:{','.join(targets)}:{time.time()}".encode()
    ).hexdigest()[:12]
    task_id = f"task_{intent}_{task_hash}"

    # Resolve targets
    resolved = _resolve_targets(targets)

    # Score all 6 dimensions (all HIGH = HIGH RISK)
    blast_radius = _score_blast_radius(resolved)
    breaking_risk = _score_breaking_risk(resolved, intent)
    test_risk = _score_test_risk(resolved)
    cross_coupling = _score_cross_coupling(resolved)
    complexity = _score_complexity(resolved)
    rollback_difficulty = _score_rollback_difficulty(
        blast_radius, breaking_risk, cross_coupling, complexity,
    )

    dimensions = {
        "blast_radius": blast_radius,
        "breaking_risk": breaking_risk,
        "test_risk": test_risk,
        "cross_coupling": cross_coupling,
        "complexity": complexity,
        "rollback_difficulty": rollback_difficulty,
    }

    # Derive constraints and strategy
    constraints = _derive_constraints(dimensions, intent)
    strategy = _derive_strategy(dimensions, intent, constraints)
    decision_metadata = _build_decision_metadata(dimensions, intent)
    execution_plan = _build_execution_plan(resolved, dimensions, intent, constraints)

    # Overall risk
    max_score = max(d.get("score", 0) for d in dimensions.values())
    risk_level = _overall_risk(max_score)

    # Build contract
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Index confidence
    index_confidence = _compute_index_confidence(resolved)

    contract = {
        "task_profile": {
            "task_id": task_id,
            "title": description[:120],
            "description": description,
            "intent": intent,
            "targets": [t.get("input", "") for t in resolved],
            "resolved_targets": resolved,
            "project": project,
            "overall_risk": risk_level,
            "index_confidence": index_confidence,
            "version": CONTRACT_VERSION,
            "generated_at": now,
        },
        "project_signals": _compute_project_signals(resolved, project),
        "dimensions": dimensions,
        "constraints": constraints,
        "decision_metadata": decision_metadata,
        "execution_plan": execution_plan,
        "strategy": strategy,
        "human_summary": _generate_human_summary(dimensions, constraints),
    }

    return contract


def task_gate_check(
    task_contract: dict,
    next_phase: str = None,
    current_state: dict = None,
) -> dict:
    """Check whether a task can proceed to the next phase.

    Accepts both gate phase names (inspect, plan_changes, apply_changes,
    expand_changes, finalize) AND strategy-specific phase names
    (e.g., apply_small_changes → apply_changes gate).

    Args:
        task_contract: The task contract from analyze_task()
        next_phase: Phase to check entry for. Can be a gate phase name or
            a strategy phase name (auto-mapped to the corresponding gate).
        current_state: Dict of boolean flags for what has been completed, e.g.:
            {
                "impact_analysis_done": true,
                "cross_project_check_done": false,
                "tests_reviewed": false,
                "human_review_completed": false,
                "validation_passed": false,
                "public_contract_change_detected": false
            }

    Returns:
        Gate check result with decision, reason_codes, message, and required_actions.
        When a strategy phase is mapped, includes strategy_phase and mapped_to_gate.
    """
    if not task_contract:
        return {"error": "task_contract is required"}

    state = current_state or {}
    constraints = task_contract.get("constraints", {})

    # Default next_phase: inspect
    if not next_phase:
        next_phase = "inspect"

    # Map strategy phase to gate phase (if it's a strategy-specific name)
    original_phase = next_phase
    if next_phase not in GATE_PHASES and next_phase in _STRATEGY_TO_GATE:
        next_phase = _STRATEGY_TO_GATE[next_phase]

    # Check gate requirements
    blockers = []
    reason_codes = []
    required_actions = []

    requirements = _GATE_REQUIREMENTS.get(next_phase, [])
    for state_key, constraint_key, message in requirements:
        # Only check if the constraint is active (or constraint_key is None = always check)
        if constraint_key is not None and not constraints.get(constraint_key):
            continue

        # Check if the state condition is met
        if not state.get(state_key, False):
            blockers.append(message)
            code = _REASON_CODES.get(state_key, state_key.upper())
            reason_codes.append(code)
            required_actions.append(state_key)

    # Special: check for public contract change needing human review
    if (state.get("public_contract_change_detected")
            and constraints.get("must_request_human_review_on_public_contract_change")
            and not state.get("human_review_completed")
            and next_phase in ("apply_changes", "expand_changes", "finalize")):
        msg = "Human review required for public contract change"
        if msg not in blockers:
            blockers.append(msg)
            reason_codes.append("HUMAN_REVIEW_REQUIRED_FOR_PUBLIC_CONTRACT_CHANGE")
            required_actions.append("complete_human_review_for_public_contract_change")

    # Determine decision
    if blockers:
        decision = "blocked"
        message = f"Cannot enter {next_phase}. " + " ".join(blockers)
    else:
        decision = "pass"
        message = f"Clear to proceed to {next_phase}."

    result = {
        "pass": decision == "pass",
        "decision": decision,
        "phase": next_phase,
        "reason_codes": reason_codes,
        "message": message,
        "required_actions": required_actions,
    }
    # Include mapping info when a strategy phase was mapped to a gate phase
    if original_phase != next_phase:
        result["strategy_phase"] = original_phase
        result["mapped_to_gate"] = next_phase
    return result
