"""Constraint, strategy, and execution-plan construction for task analysis.

Planning consumes resolved targets and risk evidence. It never performs edits or
runtime execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

try:
    from .governance import evaluate_task_governance
    from ..index_store import load_index
except ImportError:
    from tools.governance import evaluate_task_governance
    from index_store import load_index

from .task_risk import (
    _is_test_path, _overall_risk, _score_blast_radius, _score_breaking_risk,
    _score_test_risk, _score_cross_coupling, _score_complexity,
    _score_rollback_difficulty,
)

CONTRACT_VERSION = "task-contract.v2"

MAX_INDIVIDUAL_INSPECT = 10  # Bound detailed inspect searches before assess

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

_BLOCKED_ACTION_RULES = [
    ("breaking_risk",  "high", "rename_exported_symbol_without_review"),
    ("rollback_difficulty", "high", "multi_module_atomic_rewrite"),
    ("blast_radius",   "high", "bulk_replace"),
]

_REASONING_MODES = {
    "minimal_diff": "Smallest change that achieves the goal",
    "boundary_first": "Define interfaces and boundaries before implementation",
    "elimination": "Exclude known-safe areas first, focus on remainder",
    "narrow_then_widen": "Verify on smallest surface first, then expand",
    "decompose": "Break into independently verifiable sub-tasks",
    "follow_pattern": "Match existing codebase patterns",
}

_ANTI_PATTERNS = {
    "brute_force_enumerate", "guess_and_check", "copy_paste_modify",
    "big_bang_rewrite", "change_without_boundary_check",
}

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

_RISK_POSTURES = {
    "high": "conservative",
    "moderate": "cautious",
    "low": "standard",
    "safe": "standard",
}

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

def _plan_inspect_steps(symbol_ids, file_paths, first_sid, first_path,
                        coupling_level, complexity_level, test_level, intent, _add):
    """Phase 1: INSPECT — understand the landscape."""
    # Collect all inspect step IDs for gate dependencies
    inspect_step_ids = []

    # Step(s): scope callers — one per symbol, up to MAX_INDIVIDUAL_INSPECT
    ref_steps = []
    if symbol_ids and intent != "feature":
        individual_sids = symbol_ids[:MAX_INDIVIDUAL_INSPECT]
        for idx, sid in enumerate(individual_sids):
            # Single target: use plain purpose for V1 compatibility
            purpose = "scope_callers" if len(symbol_ids) == 1 else f"scope_callers_{idx}"
            sid_step = _add(
                "impact",
                {"target": sid, "change_type": "modify"},
                purpose,
            )
            ref_steps.append(sid_step)
        inspect_step_ids.extend(ref_steps)

    # Back-compat alias for downstream deps (single-target case)
    ref_step = ref_steps[0] if ref_steps else None

    # Step(s): verify test coverage — one per unique file path
    test_steps = []
    if file_paths:
        unique_test_paths = [
            path for path in dict.fromkeys(file_paths) if not _is_test_path(path)
        ]
        individual_paths = unique_test_paths[:MAX_INDIVIDUAL_INSPECT]
        for idx, fpath in enumerate(individual_paths):
            purpose = "verify_test_coverage" if len(unique_test_paths) == 1 else f"verify_test_coverage_{idx}"
            t_step = _add(
                "search", {"query": f"tests covering {fpath}"}, purpose,
                required=test_level in ("medium", "high"),
            )
            test_steps.append(t_step)
        inspect_step_ids.extend(test_steps)

    # Back-compat alias
    test_step = test_steps[0] if test_steps else None

    # Step: check cross-project usage (if coupling is a concern)
    cross_step = None
    if coupling_level in ("medium", "high") and first_sid and not ref_step:
        cross_step = _add(
            "impact",
            {"target": first_sid, "change_type": "modify"},
            "check_cross_project",
        )
        inspect_step_ids.append(cross_step)

    # Step: map dependency graph (if complexity is a concern) — first path only
    dep_step = None
    if complexity_level in ("medium", "high") and first_path:
        dep_step = _add(
            "structure",
            {"focus": "dependencies", "path": first_path},
            "map_dependencies",
            required=complexity_level == "high",
        )
        inspect_step_ids.append(dep_step)

    return inspect_step_ids, ref_steps, ref_step, test_steps, test_step

def _plan_assess_steps(symbol_ids, first_sid, blast_level, breaking_level,
                       intent, inspect_steps, inspect_step_ids, _add):
    """Phase 2: ASSESS — quantify risk before making changes."""
    assess_step_ids = []

    inspect_ids = set(inspect_step_ids)
    inspect_impact_by_target = {
        step["args"]["target"]: step["id"]
        for step in inspect_steps
        if step.get("id") in inspect_ids
        and step.get("tool") == "impact"
        and step.get("args", {}).get("change_type") == "modify"
        and step.get("args", {}).get("target")
    }

    # Step(s): exact impact analysis — one per symbol
    impact_steps = []
    if symbol_ids:
        for idx, sid in enumerate(symbol_ids):
            purpose = "assess_blast_radius" if len(symbol_ids) == 1 else f"assess_blast_radius_{idx}"
            i_step = inspect_impact_by_target.get(sid)
            if i_step is None:
                i_step = _add(
                    "impact",
                    {"target": sid, "change_type": "modify"},
                    purpose,
                    required=blast_level in ("medium", "high"),
                )
                assess_step_ids.append(i_step)
            impact_steps.append(i_step)

    # Reused inspect IDs already participate in the inspect gate dependency set;
    # only newly emitted assess calls need to be added to assess_step_ids.

    # Back-compat alias
    impact_step = impact_steps[0] if impact_steps else None

    # Step: edit impact preview (required when breaking is medium+) — first sid only
    preview_step = None
    if first_sid and breaking_level in ("medium", "high"):
        change_type_map = {
            "refactor": "signature_change", "bugfix": "modify",
            "feature": "modify", "cleanup": "delete", "migration": "rename",
        }
        preview_change_type = change_type_map.get(intent, "modify")
        if preview_change_type == "modify":
            preview_step = impact_step
        else:
            preview_step = _add(
                "impact",
                {
                    "target": first_sid,
                    "change_type": preview_change_type,
                },
                "preview_change_risk",
                depends_on=[impact_step] if impact_step else [],
            )
            assess_step_ids.append(preview_step)

    return assess_step_ids, impact_step

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
    - tool: public MCP tool name to call
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
    file_paths = list(dict.fromkeys(
        t["path"] for t in resolved if t.get("path")
    ))
    first_sid = symbol_ids[0] if symbol_ids else None
    first_path = file_paths[0] if file_paths else None

    # Dimension levels
    blast_level = dimensions.get("blast_radius", {}).get("level", "low")
    breaking_level = dimensions.get("breaking_risk", {}).get("level", "low")
    coupling_level = dimensions.get("cross_coupling", {}).get("level", "low")
    test_level = dimensions.get("test_risk", {}).get("level", "low")
    complexity_level = dimensions.get("complexity", {}).get("level", "low")

    # Fast path: minimal plan for all-low-risk cleanup/bugfix tasks
    all_low = all(level == "low" for level in [
        blast_level, breaking_level, coupling_level, test_level, complexity_level,
    ])
    if all_low and intent in ("cleanup", "bugfix"):
        # Only verify no live callers if the constraint exists
        if constraints.get("must_verify_no_live_callers") and first_sid:
            _add(
                "impact",
                {"target": first_sid, "change_type": "modify"},
                "scope_callers",
            )
        _add(
            "task",
            {"action": "gate", "next_phase": "implement"},
            "gate_before_apply",
        )
        return steps

    # =================================================================
    # Phase 1: INSPECT — understand the landscape
    # =================================================================

    inspect_step_ids, ref_steps, ref_step, test_steps, test_step = _plan_inspect_steps(
        symbol_ids, file_paths, first_sid, first_path,
        coupling_level, complexity_level, test_level, intent, _add,
    )

    # =================================================================
    # Phase 2: ASSESS — quantify risk before making changes
    # =================================================================

    assess_step_ids, impact_step = _plan_assess_steps(
        symbol_ids, first_sid, blast_level, breaking_level,
        intent, steps, inspect_step_ids, _add,
    )

    # =================================================================
    # Phase 3: GATE — verify ready to proceed
    # =================================================================

    gate_deps = inspect_step_ids + assess_step_ids
    gate_step = _add(
        "task",
        {"action": "gate", "next_phase": "assess"},
        "gate_before_plan",
        depends_on=gate_deps,
    )

    # Step: final gate before applying changes
    _add(
        "task",
        {"action": "gate", "next_phase": "implement"},
        "gate_before_apply",
        depends_on=[gate_step],
    )

    return steps

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

def _classify_target_intent(resolved: List[dict], caller_intent: str) -> Dict[str, str]:
    """Classify each target's natural intent based on index data.

    Returns dict mapping symbol_id -> classified intent.
    Dead code (no references) -> 'cleanup'
    Complex functions (>50 lines with callers) -> 'refactor'
    Otherwise -> caller_intent
    """
    index = load_index()
    reverse_index = index.get("reverse_index", {})
    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})

    # Build set of referenced names (same logic as find_dead_code)
    referenced_names = set()
    for dep in dependencies.values():
        dep_type = dep.get("type", "")
        if dep_type == "imports":
            for name in dep.get("metadata", {}).get("names", []):
                referenced_names.add(name)
        elif dep_type == "calls":
            target = dep.get("target", "")
            if target:
                referenced_names.add(target)
                for part in target.split("."):
                    if len(part) > 2:
                        referenced_names.add(part)

    result = {}
    for t in resolved:
        sid = t.get("symbol_id")
        if not sid:
            result[t.get("input", "")] = caller_intent
            continue

        name = t.get("name", "")
        has_callers = bool(reverse_index.get(sid, [])) or name in referenced_names

        if not has_callers:
            result[sid] = "cleanup"
        else:
            sym = symbols.get(sid, {})
            lines = sym.get("end_line", 0) - sym.get("start_line", 0)
            if lines > 50:
                result[sid] = "refactor"
            else:
                result[sid] = caller_intent

    return result

def _generate_compound_summary(sub_tasks: list) -> dict:
    """Generate human summary for compound contract."""
    parts = []
    for st in sub_tasks:
        intent = st["intent"]
        count = len(st["targets"])
        risk = st["overall_risk"]
        parts.append(f"{intent}: {count} targets ({risk} risk)")

    return {
        "summary": f"Compound task with {len(sub_tasks)} sub-tasks: " + ", ".join(parts),
        "sub_task_summaries": parts,
        "recommended_execution_order": [
            st["intent"] for st in sorted(
                sub_tasks,
                key=lambda s: {"cleanup": 0, "bugfix": 1, "refactor": 2, "feature": 3, "migration": 4}.get(s["intent"], 5),
            )
        ],
    }

def _build_compound_contract(
    description: str,
    resolved: List[dict],
    classified: Dict[str, str],
    original_intent: str,
    project: str,
    task_id: str,
    opts: dict,
) -> dict:
    """Build a compound contract for mixed-intent tasks."""
    # Group by classified intent
    groups = {}  # intent -> [resolved_targets]
    for t in resolved:
        key = t.get("symbol_id") or t.get("input", "")
        target_intent = classified.get(key, original_intent)
        groups.setdefault(target_intent, []).append(t)

    sub_tasks = []
    max_risk_score = 0

    for sub_intent, sub_resolved in sorted(groups.items()):
        # Score dimensions for this sub-group
        blast = _score_blast_radius(sub_resolved)
        breaking = _score_breaking_risk(sub_resolved, sub_intent)
        test = _score_test_risk(sub_resolved)
        coupling = _score_cross_coupling(sub_resolved)
        complexity = _score_complexity(sub_resolved)
        rollback = _score_rollback_difficulty(blast, breaking, coupling, complexity)

        dims = {
            "blast_radius": blast,
            "breaking_risk": breaking,
            "test_risk": test,
            "cross_coupling": coupling,
            "complexity": complexity,
            "rollback_difficulty": rollback,
        }

        constraints = _derive_constraints(dims, sub_intent)
        strategy = _derive_strategy(dims, sub_intent, constraints)
        plan = _build_execution_plan(sub_resolved, dims, sub_intent, constraints)

        sub_max = max(d.get("score", 0) for d in dims.values())
        max_risk_score = max(max_risk_score, sub_max)

        sub_tasks.append({
            "intent": sub_intent,
            "targets": [t.get("input", t.get("symbol_id", "")) for t in sub_resolved],
            "resolved_targets": sub_resolved,
            "overall_risk": _overall_risk(sub_max),
            "dimensions": dims,
            "constraints": constraints,
            "strategy": strategy,
            "execution_plan": plan,
        })

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    contract = {
        "task_profile": {
            "task_id": task_id,
            "title": description[:120],
            "description": description,
            "original_intent": original_intent,
            "compound": True,
            "sub_task_count": len(sub_tasks),
            "project": project,
            "overall_risk": _overall_risk(max_risk_score),
            "version": CONTRACT_VERSION,
            "generated_at": now,
        },
        "sub_tasks": sub_tasks,
        "human_summary": _generate_compound_summary(sub_tasks),
    }
    contract["governance"] = evaluate_task_governance(
        description=description,
        targets=[t.get("input", "") for t in resolved],
        resolved_targets=resolved,
        project=project,
        options=opts,
    )
    return contract

