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
from pathlib import Path
from typing import Dict, List, Optional

from .task_resolution import (
    _PATH_EXTENSIONS, _append_resolved_target, _is_exact_task_target_match,
    _looks_like_path, _normalize_path_text, _relative_target_path,
    _resolve_exact_search_target as _resolve_exact_search_target_impl, _resolve_targets as _resolve_targets_impl, _symbol_path_matches_target,
    _symbol_sort_key,
)
from .task_risk import (
    _compute_index_confidence, _compute_project_signals, _is_test_path,
    _overall_risk, _score_blast_radius, _score_breaking_risk, _score_complexity,
    _score_cross_coupling, _score_rollback_difficulty, _score_test_risk as _score_test_risk_impl,
    _score_to_level,
)
from .task_planning import (
    MAX_INDIVIDUAL_INSPECT, _ANTI_PATTERNS, _BLOCKED_ACTION_RULES,
    _CONSTRAINT_RULES_BY_LEVEL, _INTENT_DEFAULTS, _INTENT_REASONING,
    _LEVEL_ORDER, _REASONING_MODES, _RISK_POSTURES, _build_compound_contract,
    _build_decision_metadata, _build_execution_plan, _classify_target_intent,
    _derive_constraints, _derive_strategy, _generate_compound_summary,
    _generate_human_summary, _plan_assess_steps, _plan_inspect_steps,
)

try:
    from .code_info import find_test_file, list_projects
    from .references import find_references
    from .search import search_by_keyword
    from ..index_store import load_index
    from .governance import evaluate_task_governance
    from .task_gate import (
        GATE_PHASES,
        _GATE_REQUIREMENTS,
        _REASON_CODES,
        _STRATEGY_TO_GATE,
        _apply_governance_gate,
        _collect_gate_blockers,
        _gate_check_compound,
        task_gate_check,
    )
except ImportError:
    from tools.code_info import find_test_file, list_projects
    from tools.references import find_references
    from tools.search import search_by_keyword
    from index_store import load_index
    from tools.governance import evaluate_task_governance
    from tools.task_gate import (
        GATE_PHASES,
        _GATE_REQUIREMENTS,
        _REASON_CODES,
        _STRATEGY_TO_GATE,
        _apply_governance_gate,
        _collect_gate_blockers,
        _gate_check_compound,
        task_gate_check,
    )


# =========================================================================
# Constants
# =========================================================================



def _resolve_exact_search_target(
    target: str,
    project: str | None,
    seen_ids: set[str],
) -> dict | None:
    """Compatibility wrapper preserving the historical facade signature."""
    return _resolve_exact_search_target_impl(
        target,
        project,
        seen_ids,
        search_fn=search_by_keyword,
    )


def _resolve_targets(targets: List[str], project: str = None) -> List[dict]:
    """Compatibility wrapper preserving task_analysis dependency patch points."""
    return _resolve_targets_impl(
        targets,
        project,
        load_index_fn=load_index,
        search_fn=search_by_keyword,
    )

def _score_test_risk(resolved: List[dict]) -> dict:
    """Compatibility wrapper preserving task_analysis dependency patch points."""
    return _score_test_risk_impl(
        resolved,
        find_references_fn=find_references,
        find_test_file_fn=find_test_file,
    )


VALID_INTENTS = {"refactor", "bugfix", "feature", "cleanup", "migration"}

CONTRACT_VERSION = "task-contract.v2"




# =========================================================================
# Intent → default strategy
# =========================================================================


# =========================================================================
# Constraint derivation rules (level-based)
# =========================================================================

# (dimension, level, constraint_key)
# Triggers when dimension level >= the specified level


# blocked_actions derived from high-risk dimensions

# =========================================================================
# Gate phases (fixed set for V1)
# =========================================================================


# Strategy phase → gate phase mapping
# Allows task_gate_check to accept strategy-specific phase names

# What must be true to enter each phase

# Reason codes for gate blockers


# =========================================================================
# Decision metadata + execution plan — data-driven cognitive guidance
# =========================================================================

# Reasoning modes: explain WHY the plan is structured a certain way

# Anti-patterns: machine-readable forbidden reasoning patterns

# Intent → default reasoning mode + anti-patterns

# Risk posture: derived from overall risk level


# =========================================================================
# Helpers
# =========================================================================





# =========================================================================
# Target resolution
# =========================================================================





















# =========================================================================
# Dimension scoring — all dimensions: HIGH score = HIGH risk
# =========================================================================













# =========================================================================
# Project signals (reuses code_health_score — zero extra I/O)
# =========================================================================



# =========================================================================
# Index confidence
# =========================================================================



# =========================================================================
# Constraint + strategy derivation
# =========================================================================





# =========================================================================
# Thinking hints derivation
# =========================================================================









# =========================================================================
# Human summary
# =========================================================================



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
    resolved = _resolve_targets(targets, project=project)

    # Classify each target's natural intent
    classified = _classify_target_intent(resolved, intent)
    unique_intents = set(classified.values())

    # If mixed intents detected, split into sub-tasks
    if len(unique_intents) > 1:
        return _build_compound_contract(
            description, resolved, classified, intent, project, task_id, opts
        )

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
    contract["governance"] = evaluate_task_governance(
        description=description,
        targets=targets,
        resolved_targets=resolved,
        project=project,
        options=opts,
    )

    return contract
