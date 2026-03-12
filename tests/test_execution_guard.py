"""Tests for execution_guard module — both class and module-level wrappers."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from execution_guard import (
    ExecutionGuard,
    register_task,
    clear_task,
    record_tool_call,
    check_enforcement,
    get_status,
    TASK_EXPIRY_SECONDS,
    _default_guard,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_contract(steps=None, compound=False, sub_tasks=None):
    """Build a minimal task contract for testing."""
    if compound and sub_tasks:
        return {"sub_tasks": sub_tasks}
    return {"execution_plan": steps or []}


def _step(step_id, tool, args=None):
    """Build a minimal execution plan step."""
    return {"id": step_id, "tool": tool, "args": args or {}}


@pytest.fixture(autouse=True)
def _reset_guard():
    """Reset the default guard before each test."""
    _default_guard.clear_task()
    yield
    _default_guard.clear_task()


# ===========================================================================
# ExecutionGuard class tests
# ===========================================================================

class TestExecutionGuardClass:

    def test_initial_state(self):
        g = ExecutionGuard()
        assert g._active_task is None
        assert g._completed_steps == set()
        assert g._task_registered_at == 0.0

    def test_register_task(self):
        g = ExecutionGuard()
        contract = _make_contract([_step("s1", "find_references")])
        g.register_task(contract)
        assert g._active_task is contract
        assert g._completed_steps == set()
        assert g._task_registered_at > 0

    def test_register_task_ignores_non_dict(self):
        g = ExecutionGuard()
        g.register_task("not a dict")
        assert g._active_task is None

    def test_register_task_ignores_error_contract(self):
        g = ExecutionGuard()
        g.register_task({"error": "something went wrong"})
        assert g._active_task is None

    def test_clear_task(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([_step("s1", "find_references")]))
        g.clear_task()
        assert g._active_task is None
        assert g._completed_steps == set()

    def test_record_tool_call_matches_step(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([
            _step("s1", "find_references", {"symbol_id": "foo"}),
        ]))
        result = g.record_tool_call("find_references", {"symbol_id": "foo"})
        assert result == "s1"
        assert "s1" in g._completed_steps

    def test_record_tool_call_no_active_task(self):
        g = ExecutionGuard()
        assert g.record_tool_call("find_references", {}) is None

    def test_record_tool_call_no_match(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([
            _step("s1", "find_references", {"symbol_id": "foo"}),
        ]))
        result = g.record_tool_call("impact_analysis", {"symbol_id": "bar"})
        assert result is None

    def test_record_tool_call_skips_completed(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([
            _step("s1", "find_references", {"symbol_id": "foo"}),
            _step("s2", "find_references", {"symbol_id": "bar"}),
        ]))
        g.record_tool_call("find_references", {"symbol_id": "foo"})
        result = g.record_tool_call("find_references", {"symbol_id": "bar"})
        assert result == "s2"

    def test_check_enforcement_no_task(self):
        g = ExecutionGuard()
        assert g.check_enforcement("find_references", {}) is None

    def test_check_enforcement_allows_non_plan_tools(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([
            _step("s1", "find_references"),
            _step("g1", "task_gate_check"),
        ]))
        # A tool not in the plan should not be blocked
        assert g.check_enforcement("search_code", {"query": "test"}) is None

    def test_check_enforcement_blocks_after_gate(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([
            _step("s1", "find_references", {"symbol_id": "foo"}),
            _step("g1", "task_gate_check", {"next_phase": "plan_changes"}),
            _step("s2", "impact_analysis", {"symbol_id": "bar"}),
        ]))
        # Try to call impact_analysis without completing gate
        warning = g.check_enforcement("impact_analysis", {"symbol_id": "bar"})
        assert warning is not None
        assert "BLOCKED" in warning["enforcement_warning"]
        assert warning["blocked_by_gate"] == "g1"
        assert len(warning["recovery_plan"]) >= 1

    def test_check_enforcement_allows_before_gate(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([
            _step("s1", "find_references", {"symbol_id": "foo"}),
            _step("g1", "task_gate_check"),
        ]))
        # Steps before gate should be allowed
        assert g.check_enforcement("find_references", {"symbol_id": "foo"}) is None

    def test_check_enforcement_allows_after_completed_gate(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([
            _step("s1", "find_references", {"symbol_id": "foo"}),
            _step("g1", "task_gate_check", {"next_phase": "plan_changes"}),
            _step("s2", "impact_analysis", {"symbol_id": "bar"}),
        ]))
        # Complete the gate
        g.record_tool_call("find_references", {"symbol_id": "foo"})
        g.record_tool_call("task_gate_check", {"next_phase": "plan_changes"})
        # Now impact_analysis should be allowed
        assert g.check_enforcement("impact_analysis", {"symbol_id": "bar"}) is None

    def test_check_enforcement_auto_expires(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([
            _step("s1", "find_references"),
            _step("g1", "task_gate_check"),
            _step("s2", "impact_analysis"),
        ]))
        # Force expiry
        g._task_registered_at = time.monotonic() - g.TASK_EXPIRY_SECONDS - 1
        assert g.check_enforcement("impact_analysis", {}) is None
        assert g._active_task is None

    def test_get_status_no_task(self):
        g = ExecutionGuard()
        status = g.get_status()
        assert status == {"active": False}

    def test_get_status_active(self):
        g = ExecutionGuard()
        g.register_task(_make_contract([
            _step("s1", "find_references"),
            _step("s2", "impact_analysis"),
        ]))
        g.record_tool_call("find_references", {})
        status = g.get_status()
        assert status["active"] is True
        assert "s1" in status["completed_steps"]
        assert "s2" in status["remaining_steps"]
        assert status["elapsed_seconds"] >= 0
        assert status["expires_in_seconds"] > 0

    def test_compound_contract(self):
        g = ExecutionGuard()
        contract = _make_contract(compound=True, sub_tasks=[
            {"intent": "cleanup", "targets": ["a"], "execution_plan": [
                _step("c1", "find_dead_code"),
                _step("cg", "task_gate_check"),
            ]},
            {"intent": "refactor", "targets": ["b"], "execution_plan": [
                _step("r1", "find_references"),
                _step("rg", "task_gate_check"),
            ]},
        ])
        g.register_task(contract)
        plan = g._get_plan()
        assert len(plan) == 4
        # Gate enforcement should work across sub-tasks
        g.record_tool_call("find_dead_code", {})
        assert g.check_enforcement("find_references", {}) is not None  # blocked by cg

    def test_args_match_empty(self):
        g = ExecutionGuard()
        assert g._args_match({}, {"anything": "value"}) is True

    def test_args_match_hit(self):
        g = ExecutionGuard()
        assert g._args_match({"key": "val"}, {"key": "val", "other": "x"}) is True

    def test_args_match_miss(self):
        g = ExecutionGuard()
        assert g._args_match({"key": "val"}, {"key": "other"}) is False

    def test_multiple_guards_independent(self):
        g1 = ExecutionGuard()
        g2 = ExecutionGuard()
        g1.register_task(_make_contract([_step("s1", "find_references")]))
        assert g1._active_task is not None
        assert g2._active_task is None


# ===========================================================================
# Module-level wrapper tests (backward compat)
# ===========================================================================

class TestModuleLevelWrappers:

    def test_register_task_wrapper(self):
        contract = _make_contract([_step("s1", "find_references")])
        register_task(contract)
        assert _default_guard._active_task is contract

    def test_clear_task_wrapper(self):
        register_task(_make_contract([_step("s1", "find_references")]))
        clear_task()
        assert _default_guard._active_task is None

    def test_record_tool_call_wrapper(self):
        register_task(_make_contract([
            _step("s1", "find_references", {"symbol_id": "foo"}),
        ]))
        result = record_tool_call("find_references", {"symbol_id": "foo"})
        assert result == "s1"

    def test_check_enforcement_wrapper(self):
        register_task(_make_contract([
            _step("s1", "find_references"),
            _step("g1", "task_gate_check"),
            _step("s2", "impact_analysis"),
        ]))
        warning = check_enforcement("impact_analysis", {})
        assert warning is not None

    def test_get_status_wrapper(self):
        register_task(_make_contract([_step("s1", "find_references")]))
        status = get_status()
        assert status["active"] is True

    def test_task_expiry_constant(self):
        assert TASK_EXPIRY_SECONDS == 1800
        assert TASK_EXPIRY_SECONDS == ExecutionGuard.TASK_EXPIRY_SECONDS
