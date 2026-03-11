"""
Execution Guard — server-side enforcement for task execution plans.

When analyze_task returns a contract with an execution_plan, this module
tracks step completion and blocks tool calls that skip required gates.

State is in-memory only. MCP server restart clears enforcement.
"""

import time
from typing import Optional

# ── Module-level state ──────────────────────────────────────────────

_active_task: Optional[dict] = None
_completed_steps: set = set()
_task_registered_at: float = 0.0

TASK_EXPIRY_SECONDS = 1800  # 30 minutes


# ── Public API ──────────────────────────────────────────────────────

def register_task(contract: dict) -> None:
    """Store a task contract from analyze_task. Ignore error contracts."""
    global _active_task, _completed_steps, _task_registered_at

    if not isinstance(contract, dict):
        return
    if "error" in contract:
        return

    _active_task = contract
    _completed_steps = set()
    _task_registered_at = time.monotonic()


def clear_task() -> None:
    """Clear active task and all tracking state."""
    global _active_task, _completed_steps, _task_registered_at
    _active_task = None
    _completed_steps = set()
    _task_registered_at = 0.0


def record_tool_call(tool_name: str, args: dict) -> Optional[str]:
    """
    Match a tool call to an uncompleted execution plan step.

    Returns the step_id if matched, None otherwise.
    """
    if _active_task is None:
        return None

    plan = _get_plan()
    if not plan:
        return None

    for step in plan:
        step_id = step.get("id", "")
        if step_id in _completed_steps:
            continue
        if step.get("tool") != tool_name:
            continue
        if _args_match(step.get("args", {}), args):
            _completed_steps.add(step_id)
            return step_id

    return None


def check_enforcement(tool_name: str, args: dict) -> Optional[dict]:
    """
    Check if a tool call is allowed under the current execution plan.

    Returns None if allowed, or a warning dict if blocked by a gate.
    """
    global _active_task, _completed_steps, _task_registered_at

    if _active_task is None:
        return None

    # Auto-expire stale tasks
    if time.monotonic() - _task_registered_at > TASK_EXPIRY_SECONDS:
        clear_task()
        return None

    plan = _get_plan()
    if not plan:
        return None

    # Find the step(s) matching this tool call
    matched_step = _find_matching_step(plan, tool_name, args)
    if matched_step is None:
        # Tool not in plan — don't block exploration tools
        return None

    # Find all gate steps
    gate_steps = [s for s in plan if s.get("tool") == "task_gate_check"]

    # Check if this step comes after an uncompleted gate
    matched_idx = _step_index(plan, matched_step["id"])
    for gate in gate_steps:
        gate_id = gate.get("id", "")
        if gate_id in _completed_steps:
            continue
        gate_idx = _step_index(plan, gate_id)
        if gate_idx < matched_idx:
            # This step is after an uncompleted gate — block it
            # Build recovery plan: uncompleted steps before this gate, in order
            remaining_steps = []
            for s in plan:
                s_idx = _step_index(plan, s["id"])
                if s["id"] not in _completed_steps and s_idx <= gate_idx:
                    remaining_steps.append({
                        "step_id": s["id"],
                        "tool": s.get("tool", ""),
                        "args": s.get("args", {}),
                    })

            # Build human-readable next action
            if remaining_steps:
                next_step = remaining_steps[0]
                next_action = (
                    f"Call {next_step['tool']} with args {next_step['args']} "
                    f"(step {next_step['step_id']})"
                )
            else:
                next_action = f"Call task_gate_check for gate '{gate_id}'"

            return {
                "enforcement_warning": (
                    f"⛔ BLOCKED: '{matched_step['id']}' requires gate "
                    f"'{gate_id}' to pass first."
                ),
                "blocked_by_gate": gate_id,
                "next_action": next_action,
                "recovery_plan": remaining_steps,
                "completed_steps": sorted(_completed_steps),
                "message": (
                    f"You skipped ahead. Complete these {len(remaining_steps)} "
                    f"step(s) first, then retry your original call."
                ),
            }

    return None


def get_status() -> dict:
    """Return current enforcement state for debugging."""
    if _active_task is None:
        return {"active": False}

    plan = _get_plan()
    step_ids = [s.get("id", "") for s in plan] if plan else []
    remaining = [sid for sid in step_ids if sid not in _completed_steps]

    elapsed = time.monotonic() - _task_registered_at
    return {
        "active": True,
        "task_id": _active_task.get("task_id", _active_task.get("id", "unknown")),
        "completed_steps": sorted(_completed_steps),
        "remaining_steps": remaining,
        "elapsed_seconds": round(elapsed, 1),
        "expires_in_seconds": round(max(0, TASK_EXPIRY_SECONDS - elapsed), 1),
    }


# ── Internal helpers ────────────────────────────────────────────────

def _get_plan() -> list:
    """Extract execution plan from active task, including sub_tasks."""
    if _active_task is None:
        return []

    plan = _active_task.get("execution_plan", [])
    if plan:
        return plan

    # Support compound contracts with sub_tasks
    sub_tasks = _active_task.get("sub_tasks", [])
    combined = []
    for sub in sub_tasks:
        if isinstance(sub, dict):
            combined.extend(sub.get("execution_plan", []))
    return combined


def _args_match(plan_args: dict, call_args: dict) -> bool:
    """
    Approximate match: at least one plan arg value appears in call args.

    Empty plan args always match (step has no required args).
    """
    if not plan_args:
        return True
    for key, val in plan_args.items():
        call_val = call_args.get(key)
        if call_val is not None and str(call_val) == str(val):
            return True
    return False


def _find_matching_step(plan: list, tool_name: str, args: dict) -> Optional[dict]:
    """Find the first uncompleted step matching tool_name and args."""
    for step in plan:
        if step.get("tool") != tool_name:
            continue
        step_id = step.get("id", "")
        if step_id in _completed_steps:
            continue
        if _args_match(step.get("args", {}), args):
            return step
    # Also check completed steps — caller might re-run a tool
    for step in plan:
        if step.get("tool") == tool_name and _args_match(step.get("args", {}), args):
            return step
    return None


def _step_index(plan: list, step_id: str) -> int:
    """Return index of a step in the plan, or len(plan) if not found."""
    for i, step in enumerate(plan):
        if step.get("id") == step_id:
            return i
    return len(plan)
