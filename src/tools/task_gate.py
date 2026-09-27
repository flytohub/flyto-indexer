"""Task phase-gate policy for task-analysis contracts.

This module decides whether an already-built task contract may enter a phase.
It does not resolve targets, score risk, or build execution plans.
"""

from __future__ import annotations

try:
    from .governance import validate_governance_diff
except ImportError:
    from tools.governance import validate_governance_diff


GATE_PHASES = ["inspect", "plan_changes", "apply_changes", "expand_changes", "finalize"]

_STRATEGY_TO_GATE = {
    # Public MCP phase names
    "assess": "plan_changes",
    "implement": "apply_changes",
    "verify": "finalize",
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

_GATE_REQUIREMENTS = {
    "plan_changes": [
        ("impact_analysis_done", "must_run_impact_analysis", "Impact analysis must be completed first"),
    ],
    "apply_changes": [
        ("cross_project_check_done", "must_check_cross_project_usage",
         "Cross-project usage check required but not completed"),
        ("tests_reviewed", "must_add_or_update_tests",
         "Test review/addition required before applying changes"),
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

_REASON_CODES = {
    "impact_analysis_done": "IMPACT_ANALYSIS_REQUIRED",
    "cross_project_check_done": "CROSS_PROJECT_CHECK_REQUIRED",
    "tests_reviewed": "TEST_REVIEW_REQUIRED",
    "human_review_completed": "HUMAN_REVIEW_REQUIRED_FOR_PUBLIC_CONTRACT_CHANGE",
    "validation_passed": "VALIDATION_REQUIRED",
}

def _apply_governance_gate(
    result: dict,
    task_contract: dict,
    current_state: dict | None,
) -> dict:
    """Merge opt-in governance blockers into an existing gate result."""
    governance = task_contract.get("governance")
    if not governance:
        return result
    state = current_state or {}
    gate = validate_governance_diff(
        governance,
        changed_paths=state.get("changed_paths") or [],
        state=state,
    )
    result["governance"] = gate
    if gate.get("pass"):
        return result
    codes = [
        f"GOVERNANCE_{item.get('code', 'VIOLATION').upper()}"
        for item in gate.get("blocking") or []
    ]
    result["pass"] = False
    result["decision"] = "blocked"
    result["reason_codes"] = list(dict.fromkeys(
        list(result.get("reason_codes") or []) + codes
    ))
    result["required_actions"] = list(dict.fromkeys(
        list(result.get("required_actions") or [])
        + list(gate.get("required_actions") or [])
    ))
    result.setdefault("required_state", {})
    result["message"] = (
        f"Cannot enter {result.get('phase')}. "
        f"Governance blocked {len(gate.get('blocking') or [])} finding(s)."
    )
    return result

def _gate_check_compound(task_contract, next_phase, current_state):
    """Gate one compound sub-task at a time in the recommended order."""
    sub_tasks = task_contract.get("sub_tasks", [])
    if not sub_tasks:
        return {"error": "Compound contract has no sub_tasks"}

    state = current_state or {}
    completed = set(state.get("completed_subtasks") or [])
    pending = [
        (index, sub_task)
        for index, sub_task in enumerate(sub_tasks)
        if f"subtask_{index + 1}" not in completed
    ]
    if not pending:
        result = {
            "pass": True,
            "decision": "pass",
            "phase": next_phase,
            "reason_codes": [],
            "message": (
                f"Clear to proceed to {next_phase}; "
                f"all {len(sub_tasks)} sub-tasks are complete."
            ),
            "required_actions": [],
            "required_state": {},
            "completed_subtasks": sorted(completed),
        }
        return _apply_governance_gate(result, task_contract, current_state)

    index, active = pending[0]
    subtask_id = f"subtask_{index + 1}"
    result = task_gate_check(active, next_phase, state)
    result["active_subtask"] = {
        "id": subtask_id,
        "index": index,
        "intent": active.get("intent"),
        "targets": active.get("targets", []),
    }
    if result.get("pass"):
        result["message"] = (
            f"{result.get('message', '')} Complete {subtask_id} before "
            "adding it to current_state.completed_subtasks."
        )
    else:
        prefix = f"[{subtask_id}:{active.get('intent', 'unknown')}] "
        result["reason_codes"] = [
            prefix + code for code in result.get("reason_codes", [])
        ]
    return _apply_governance_gate(result, task_contract, current_state)

def _collect_gate_blockers(next_phase, state, constraints):
    """Collect blockers for a gate phase based on requirements and constraints."""
    blockers = []
    reason_codes = []
    required_actions = []
    required_state = {}

    for state_key, constraint_key, message in _GATE_REQUIREMENTS.get(next_phase, []):
        if constraint_key is not None and not constraints.get(constraint_key):
            continue
        if not state.get(state_key, False):
            blockers.append(message)
            reason_codes.append(_REASON_CODES.get(state_key, state_key.upper()))
            required_actions.append(state_key)
            required_state[state_key] = True

    # Public contract change requires human review for later phases
    if (state.get("public_contract_change_detected")
            and constraints.get("must_request_human_review_on_public_contract_change")
            and not state.get("human_review_completed")
            and next_phase in ("apply_changes", "expand_changes", "finalize")):
        msg = "Human review required for public contract change"
        if msg not in blockers:
            blockers.append(msg)
            reason_codes.append("HUMAN_REVIEW_REQUIRED_FOR_PUBLIC_CONTRACT_CHANGE")
            required_actions.append("complete_human_review_for_public_contract_change")
            required_state["human_review_completed"] = True

    return blockers, reason_codes, required_actions, required_state

def task_gate_check(
    task_contract: dict,
    next_phase: str = None,
    current_state: dict = None,
) -> dict:
    """Check whether a task can proceed to the next phase.

    Accepts both gate phase names (inspect, plan_changes, apply_changes,
    expand_changes, finalize) AND strategy-specific phase names
    (e.g., apply_small_changes → apply_changes gate).
    """
    if not task_contract:
        return {"error": "task_contract is required"}

    # Handle compound contracts
    if task_contract.get("task_profile", {}).get("compound") or task_contract.get("compound"):
        return _gate_check_compound(task_contract, next_phase, current_state)

    state = current_state or {}
    constraints = task_contract.get("constraints", {})

    if not next_phase:
        next_phase = "inspect"

    # Map strategy phase to gate phase
    original_phase = next_phase
    if next_phase not in GATE_PHASES and next_phase in _STRATEGY_TO_GATE:
        next_phase = _STRATEGY_TO_GATE[next_phase]

    blockers, reason_codes, required_actions, required_state = _collect_gate_blockers(
        next_phase,
        state,
        constraints,
    )

    passed = not blockers
    result = {
        "pass": passed,
        "decision": "pass" if passed else "blocked",
        "phase": next_phase,
        "reason_codes": reason_codes,
        "message": f"Clear to proceed to {next_phase}." if passed
                   else f"Cannot enter {next_phase}. " + " ".join(blockers),
        "required_actions": required_actions,
        "required_state": required_state,
    }
    if original_phase != next_phase:
        result["strategy_phase"] = original_phase
        result["mapped_to_gate"] = next_phase
    return _apply_governance_gate(result, task_contract, state)

