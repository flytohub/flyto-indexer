"""Tests for task analysis tools: analyze_task and task_gate_check."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import mcp_server
import index_store

# Shared mock index
MOCK_INDEX = {
    "symbols": {
        "proj-a:src/auth.py:function:login": {
            "name": "login",
            "type": "function",
            "path": "src/auth.py",
            "start_line": 10,
            "end_line": 50,
            "deps": ["proj-a:src/auth.py:function:validate_token"],
        },
        "proj-a:src/auth.py:function:validate_token": {
            "name": "validate_token",
            "type": "function",
            "path": "src/auth.py",
            "start_line": 55,
            "end_line": 80,
            "deps": [],
            "callers": [
                {"id": "proj-a:src/auth.py:function:login", "type": "call"},
                {"id": "proj-a:src/api.py:function:handle_request", "type": "call"},
                {"id": "proj-b:src/proxy.py:function:forward_auth", "type": "call"},
            ],
        },
        "proj-a:src/api.py:function:handle_request": {
            "name": "handle_request",
            "type": "function",
            "path": "src/api.py",
            "start_line": 1,
            "end_line": 30,
            "deps": ["proj-a:src/auth.py:function:validate_token"],
        },
        "proj-b:src/proxy.py:function:forward_auth": {
            "name": "forward_auth",
            "type": "function",
            "path": "src/proxy.py",
            "start_line": 1,
            "end_line": 20,
            "deps": ["proj-a:src/auth.py:function:validate_token"],
        },
        "proj-a:src/utils.py:function:helper": {
            "name": "helper",
            "type": "function",
            "path": "src/utils.py",
            "start_line": 1,
            "end_line": 10,
            "deps": [],
        },
    },
    "projects": ["proj-a", "proj-b"],
    "project_roots": {
        "proj-a": "/tmp/proj-a",
        "proj-b": "/tmp/proj-b",
    },
}


@pytest.fixture(autouse=True)
def setup_mock_index():
    old_cache = index_store._index_cache
    index_store._index_cache = MOCK_INDEX
    yield
    index_store._index_cache = old_cache


# =========================================================================
# analyze_task
# =========================================================================

class TestAnalyzeTask:
    """Test analyze_task function."""

    def test_invalid_intent(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(description="test", targets=["login"], intent="invalid")
        assert "error" in result

    def test_empty_targets(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(description="test", targets=[], intent="refactor")
        assert "error" in result

    def test_full_contract_structure(self):
        """analyze_task returns all 6 sections."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login function",
            targets=["login"],
            intent="refactor",
        )
        # 6 sections
        assert "task_profile" in result
        assert "project_signals" in result
        assert "dimensions" in result
        assert "constraints" in result
        assert "strategy" in result
        assert "human_summary" in result

    def test_task_profile_fields(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login function",
            targets=["login"],
            intent="refactor",
        )
        profile = result["task_profile"]
        assert profile["task_id"].startswith("task_refactor_")
        assert profile["title"] == "Refactor login function"
        assert profile["intent"] == "refactor"
        assert profile["version"] == "task-contract.v2"
        assert "generated_at" in profile
        assert profile["overall_risk"] in ("safe", "low", "moderate", "high")

    def test_all_dimensions_present(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Test", targets=["login"], intent="bugfix",
        )
        dims = result["dimensions"]
        expected = {"blast_radius", "breaking_risk", "test_risk",
                    "cross_coupling", "complexity", "rollback_difficulty"}
        assert set(dims.keys()) == expected

    def test_dimension_schema(self):
        """Each dimension has score, level, rationale, evidence."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Test", targets=["login"], intent="refactor",
        )
        for dim_name, dim_data in result["dimensions"].items():
            assert "score" in dim_data, f"{dim_name} missing score"
            assert "level" in dim_data, f"{dim_name} missing level"
            assert "rationale" in dim_data, f"{dim_name} missing rationale"
            assert "evidence" in dim_data, f"{dim_name} missing evidence"
            assert 0 <= dim_data["score"] <= 10, f"{dim_name} score out of range"
            assert dim_data["level"] in ("low", "medium", "high"), f"{dim_name} invalid level"

    def test_project_signals(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Test", targets=["login"], intent="refactor",
        )
        signals = result["project_signals"]
        assert "health_score" in signals
        assert "test_maturity" in signals
        assert "complexity_baseline" in signals
        for key, val in signals.items():
            assert "score" in val, f"project_signals.{key} missing score"
            assert ("basis" in val or "grade" in val), f"project_signals.{key} missing basis"

    def test_human_summary(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Test", targets=["login"], intent="refactor",
        )
        summary = result["human_summary"]
        assert "summary" in summary
        assert "top_risks" in summary
        assert "recommended_human_attention" in summary
        assert isinstance(summary["top_risks"], list)

    def test_strategy_modes(self):
        """Each intent maps to the correct strategy mode."""
        from tools.task_analysis import analyze_task
        cases = {
            "refactor": "safe_refactor",
            "bugfix": "minimal_bugfix",
            "feature": "contract_first_feature",
            "cleanup": "cautious_cleanup",
            "migration": "migration_mode",
        }
        for intent, expected_mode in cases.items():
            result = analyze_task(
                description=f"Test {intent}",
                targets=["helper"],
                intent=intent,
            )
            assert result["strategy"]["mode"] == expected_mode, \
                f"intent={intent} expected mode={expected_mode}, got={result['strategy']['mode']}"

    def test_strategy_fields(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Test", targets=["login"], intent="refactor",
        )
        strategy = result["strategy"]
        assert "mode" in strategy
        assert "risk_level" in strategy
        assert "editing_style" in strategy
        assert "verification_level" in strategy
        assert "preferred_patch_scope" in strategy

    def test_constraints_has_max_files(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Test", targets=["login"], intent="refactor",
        )
        assert "max_files_per_step" in result["constraints"]

    def test_cleanup_intent_constraints(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Remove helper", targets=["helper"], intent="cleanup",
        )
        assert result["constraints"].get("must_verify_no_live_callers") is True

    def test_migration_intent_constraints(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Migrate auth", targets=["login"], intent="migration",
        )
        assert result["constraints"].get("must_build_compatibility_layer") is True

    def test_symbol_id_target(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Modify validate_token",
            targets=["proj-a:src/auth.py:function:validate_token"],
            intent="refactor",
        )
        assert "error" not in result
        resolved = result["task_profile"]["resolved_targets"]
        assert any(t["symbol_id"] == "proj-a:src/auth.py:function:validate_token" for t in resolved)

    def test_unresolvable_target(self):
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Fix xyz",
            targets=["nonexistent_symbol_xyz"],
            intent="bugfix",
        )
        assert "error" not in result
        assert "dimensions" in result


# =========================================================================
# test_risk dimension (HIGH score = HIGH risk = low coverage)
# =========================================================================

class TestTestRisk:
    """test_risk checks callers too, not just target files."""

    def test_no_tests_high_risk(self):
        from tools.task_analysis import _score_test_risk
        resolved = [{"path": "src/auth.py", "symbol_id": "proj-a:src/auth.py:function:login"}]
        result = _score_test_risk(resolved)
        # No test file in mock → high risk score
        assert result["score"] >= 5.0
        assert "target_files_tested" in result["evidence"]
        assert "caller_files_tested" in result["evidence"]

    def test_all_dimensions_high_equals_risk(self):
        """Verify all dimensions follow high=risk convention."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Test", targets=["login"], intent="refactor",
        )
        # test_risk should be high (no real test files)
        assert result["dimensions"]["test_risk"]["score"] >= 5.0

    def test_evidence_has_caller_info(self):
        """test_risk evidence includes caller coverage stats."""
        from tools.task_analysis import _score_test_risk
        resolved = [{"path": "src/auth.py", "symbol_id": "proj-a:src/auth.py:function:login"}]
        result = _score_test_risk(resolved)
        ev = result["evidence"]
        assert "target_files_untested" in ev
        assert "caller_files_untested" in ev
        assert isinstance(ev["test_files"], list)


# =========================================================================
# Cross-coupling dimension (uses find_references, not name matching)
# =========================================================================

class TestCrossCoupling:
    """cross_coupling uses find_references(symbol_id) for precise results."""

    def test_no_coupling_for_isolated_symbol(self):
        from tools.task_analysis import _score_cross_coupling
        resolved = [{"symbol_id": "proj-a:src/utils.py:function:helper", "name": "helper"}]
        result = _score_cross_coupling(resolved)
        assert result["score"] == 0.0
        assert "source_projects" in result["evidence"]

    def test_evidence_has_source_projects(self):
        from tools.task_analysis import _score_cross_coupling
        resolved = [{"symbol_id": "proj-a:src/auth.py:function:login", "name": "login"}]
        result = _score_cross_coupling(resolved)
        assert "source_projects" in result["evidence"]
        assert "shared_by_projects" in result["evidence"]


# =========================================================================
# Constraint derivation (level-based + cross-dimension rules)
# =========================================================================

class TestConstraintDerivation:

    def test_high_blast_triggers_impact(self):
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 6.0, "level": "medium"},
            "breaking_risk": {"score": 2.0, "level": "low"},
            "test_risk": {"score": 2.0, "level": "low"},
            "cross_coupling": {"score": 1.0, "level": "low"},
            "complexity": {"score": 3.0, "level": "low"},
            "rollback_difficulty": {"score": 2.0, "level": "low"},
        }
        c = _derive_constraints(dims, "refactor")
        assert c.get("must_run_impact_analysis") is True

    def test_high_test_risk_triggers_tests(self):
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 2.0, "level": "low"},
            "breaking_risk": {"score": 2.0, "level": "low"},
            "test_risk": {"score": 7.0, "level": "high"},
            "cross_coupling": {"score": 1.0, "level": "low"},
            "complexity": {"score": 3.0, "level": "low"},
            "rollback_difficulty": {"score": 2.0, "level": "low"},
        }
        c = _derive_constraints(dims, "refactor")
        assert c.get("must_add_or_update_tests") is True

    def test_high_coupling_triggers_cross_check(self):
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 2.0, "level": "low"},
            "breaking_risk": {"score": 2.0, "level": "low"},
            "test_risk": {"score": 2.0, "level": "low"},
            "cross_coupling": {"score": 7.0, "level": "high"},
            "complexity": {"score": 3.0, "level": "low"},
            "rollback_difficulty": {"score": 2.0, "level": "low"},
        }
        c = _derive_constraints(dims, "refactor")
        assert c.get("must_check_cross_project_usage") is True

    def test_high_breaking_triggers_review(self):
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 2.0, "level": "low"},
            "breaking_risk": {"score": 8.0, "level": "high"},
            "test_risk": {"score": 2.0, "level": "low"},
            "cross_coupling": {"score": 1.0, "level": "low"},
            "complexity": {"score": 3.0, "level": "low"},
            "rollback_difficulty": {"score": 2.0, "level": "low"},
        }
        c = _derive_constraints(dims, "refactor")
        assert c.get("must_request_human_review_on_public_contract_change") is True

    def test_high_rollback_triggers_revert_plan(self):
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 2.0, "level": "low"},
            "breaking_risk": {"score": 2.0, "level": "low"},
            "test_risk": {"score": 2.0, "level": "low"},
            "cross_coupling": {"score": 1.0, "level": "low"},
            "complexity": {"score": 3.0, "level": "low"},
            "rollback_difficulty": {"score": 8.0, "level": "high"},
        }
        c = _derive_constraints(dims, "refactor")
        assert c.get("must_prepare_revert_plan") is True

    def test_blocked_actions_generated(self):
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 9.0, "level": "high"},
            "breaking_risk": {"score": 8.0, "level": "high"},
            "test_risk": {"score": 2.0, "level": "low"},
            "cross_coupling": {"score": 1.0, "level": "low"},
            "complexity": {"score": 3.0, "level": "low"},
            "rollback_difficulty": {"score": 8.0, "level": "high"},
        }
        c = _derive_constraints(dims, "refactor")
        assert "blocked_actions" in c
        assert "bulk_replace" in c["blocked_actions"]

    def test_safe_dimensions_minimal_constraints(self):
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 1.0, "level": "low"},
            "breaking_risk": {"score": 1.0, "level": "low"},
            "test_risk": {"score": 1.0, "level": "low"},
            "cross_coupling": {"score": 0.0, "level": "low"},
            "complexity": {"score": 2.0, "level": "low"},
            "rollback_difficulty": {"score": 1.0, "level": "low"},
        }
        c = _derive_constraints(dims, "bugfix")
        assert not c.get("must_run_impact_analysis")
        assert not c.get("must_add_or_update_tests")
        assert not c.get("must_check_cross_project_usage")

    def test_cross_rule_blast_plus_test_risk(self):
        """High blast + high test_risk → max_files_per_step = 1."""
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 8.0, "level": "high"},
            "breaking_risk": {"score": 2.0, "level": "low"},
            "test_risk": {"score": 8.0, "level": "high"},
            "cross_coupling": {"score": 1.0, "level": "low"},
            "complexity": {"score": 3.0, "level": "low"},
            "rollback_difficulty": {"score": 2.0, "level": "low"},
        }
        c = _derive_constraints(dims, "refactor")
        assert c["max_files_per_step"] == 1
        assert c.get("must_validate_before_wide_change") is True

    def test_cross_rule_breaking_plus_coupling(self):
        """High breaking + high coupling → max_files_per_step = 1 + blocked."""
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 2.0, "level": "low"},
            "breaking_risk": {"score": 8.0, "level": "high"},
            "test_risk": {"score": 2.0, "level": "low"},
            "cross_coupling": {"score": 8.0, "level": "high"},
            "complexity": {"score": 3.0, "level": "low"},
            "rollback_difficulty": {"score": 2.0, "level": "low"},
        }
        c = _derive_constraints(dims, "refactor")
        assert c["max_files_per_step"] == 1
        assert "multi_module_atomic_rewrite" in c.get("blocked_actions", [])

    def test_two_high_dims_tighter_step(self):
        """Any two high dimensions → max_files_per_step = 2."""
        from tools.task_analysis import _derive_constraints
        dims = {
            "blast_radius": {"score": 2.0, "level": "low"},
            "breaking_risk": {"score": 2.0, "level": "low"},
            "test_risk": {"score": 8.0, "level": "high"},
            "cross_coupling": {"score": 1.0, "level": "low"},
            "complexity": {"score": 8.0, "level": "high"},
            "rollback_difficulty": {"score": 2.0, "level": "low"},
        }
        c = _derive_constraints(dims, "refactor")
        assert c["max_files_per_step"] <= 2


# =========================================================================
# Strategy derivation
# =========================================================================

class TestStrategyDerivation:

    def test_high_risk_high_verification(self):
        from tools.task_analysis import _derive_strategy
        dims = {
            "blast_radius": {"score": 9.0},
            "breaking_risk": {"score": 8.0},
            "cross_coupling": {"score": 7.0},
            "test_risk": {"score": 5.0},
        }
        s = _derive_strategy(dims, "refactor", {})
        assert s["verification_level"] == "high"
        assert s["risk_level"] in ("high", "moderate")

    def test_migration_has_compatibility_phase(self):
        from tools.task_analysis import _derive_strategy
        dims = {"blast_radius": {"score": 3.0}, "breaking_risk": {"score": 3.0},
                "cross_coupling": {"score": 3.0}, "test_risk": {"score": 3.0}}
        s = _derive_strategy(dims, "migration", {})
        assert s["mode"] == "migration_mode"

    def test_bugfix_upgrades_to_safe_refactor_on_high_blast(self):
        """High blast_radius bugfix → safe_refactor mode."""
        from tools.task_analysis import _derive_strategy
        dims = {
            "blast_radius": {"score": 8.0, "level": "high"},
            "breaking_risk": {"score": 3.0, "level": "low"},
            "cross_coupling": {"score": 2.0, "level": "low"},
            "test_risk": {"score": 3.0, "level": "low"},
        }
        s = _derive_strategy(dims, "bugfix", {})
        assert s["mode"] == "safe_refactor"
        assert s["original_mode"] == "minimal_bugfix"
        assert "blast_radius" in s["mode_overridden_by"]

    def test_cleanup_upgrades_to_migration_on_high_coupling(self):
        """High cross_coupling cleanup → migration_mode."""
        from tools.task_analysis import _derive_strategy
        dims = {
            "blast_radius": {"score": 3.0, "level": "low"},
            "breaking_risk": {"score": 3.0, "level": "low"},
            "cross_coupling": {"score": 8.0, "level": "high"},
            "test_risk": {"score": 3.0, "level": "low"},
        }
        s = _derive_strategy(dims, "cleanup", {})
        assert s["mode"] == "migration_mode"
        assert s["original_mode"] == "cautious_cleanup"

    def test_three_high_dims_upgrades_to_safe_refactor(self):
        """3+ high dimensions → safe_refactor regardless of intent."""
        from tools.task_analysis import _derive_strategy
        dims = {
            "blast_radius": {"score": 8.0, "level": "high"},
            "breaking_risk": {"score": 8.0, "level": "high"},
            "cross_coupling": {"score": 8.0, "level": "high"},
            "test_risk": {"score": 8.0, "level": "high"},
            "complexity": {"score": 8.0, "level": "high"},
            "rollback_difficulty": {"score": 8.0, "level": "high"},
        }
        s = _derive_strategy(dims, "feature", {})
        assert s["mode"] == "safe_refactor"
        assert s["original_mode"] == "contract_first_feature"

    def test_no_override_when_low_risk(self):
        """Low-risk bugfix stays minimal_bugfix."""
        from tools.task_analysis import _derive_strategy
        dims = {
            "blast_radius": {"score": 2.0, "level": "low"},
            "breaking_risk": {"score": 1.0, "level": "low"},
            "cross_coupling": {"score": 0.0, "level": "low"},
            "test_risk": {"score": 2.0, "level": "low"},
        }
        s = _derive_strategy(dims, "bugfix", {})
        assert s["mode"] == "minimal_bugfix"
        assert "original_mode" not in s

    def test_refactor_not_overridden(self):
        """safe_refactor is already the safest — no override."""
        from tools.task_analysis import _derive_strategy
        dims = {
            "blast_radius": {"score": 9.0, "level": "high"},
            "breaking_risk": {"score": 9.0, "level": "high"},
            "cross_coupling": {"score": 9.0, "level": "high"},
            "test_risk": {"score": 9.0, "level": "high"},
        }
        s = _derive_strategy(dims, "refactor", {})
        assert s["mode"] == "safe_refactor"
        assert "original_mode" not in s


# =========================================================================
# task_gate_check
# =========================================================================

class TestTaskGateCheck:

    def _make_contract(self, **constraint_overrides):
        constraints = {"max_files_per_step": 5}
        constraints.update(constraint_overrides)
        return {
            "constraints": constraints,
            "strategy": {
                "mode": "safe_refactor",
            },
            "dimensions": {},
        }

    def test_empty_contract(self):
        from tools.task_analysis import task_gate_check
        result = task_gate_check(task_contract={})
        assert isinstance(result, dict)

    def test_pass_when_no_constraints(self):
        from tools.task_analysis import task_gate_check
        contract = self._make_contract()
        result = task_gate_check(
            task_contract=contract,
            next_phase="apply_changes",
            current_state={},
        )
        assert result["pass"] is True
        assert result["decision"] == "pass"

    def test_blocked_without_tests(self):
        from tools.task_analysis import task_gate_check
        contract = self._make_contract(must_add_or_update_tests=True)
        result = task_gate_check(
            task_contract=contract,
            next_phase="apply_changes",
            current_state={"tests_reviewed": False},
        )
        assert result["pass"] is False
        assert result["decision"] == "blocked"
        assert "TEST_REVIEW_REQUIRED" in result["reason_codes"]
        assert "tests_reviewed" in result["required_actions"]

    def test_pass_with_tests_reviewed(self):
        from tools.task_analysis import task_gate_check
        contract = self._make_contract(must_add_or_update_tests=True)
        result = task_gate_check(
            task_contract=contract,
            next_phase="apply_changes",
            current_state={"tests_reviewed": True},
        )
        assert result["pass"] is True

    def test_blocked_without_impact_analysis(self):
        from tools.task_analysis import task_gate_check
        contract = self._make_contract(must_run_impact_analysis=True)
        result = task_gate_check(
            task_contract=contract,
            next_phase="plan_changes",
            current_state={"impact_analysis_done": False},
        )
        assert result["pass"] is False
        assert "IMPACT_ANALYSIS_REQUIRED" in result["reason_codes"]

    def test_blocked_public_contract_change(self):
        from tools.task_analysis import task_gate_check
        contract = self._make_contract(
            must_request_human_review_on_public_contract_change=True,
        )
        result = task_gate_check(
            task_contract=contract,
            next_phase="apply_changes",
            current_state={
                "public_contract_change_detected": True,
                "human_review_completed": False,
            },
        )
        assert result["pass"] is False
        assert "HUMAN_REVIEW_REQUIRED_FOR_PUBLIC_CONTRACT_CHANGE" in result["reason_codes"]

    def test_pass_public_contract_after_review(self):
        from tools.task_analysis import task_gate_check
        contract = self._make_contract(
            must_request_human_review_on_public_contract_change=True,
        )
        result = task_gate_check(
            task_contract=contract,
            next_phase="apply_changes",
            current_state={
                "public_contract_change_detected": True,
                "human_review_completed": True,
            },
        )
        assert result["pass"] is True

    def test_inspect_phase_always_passes(self):
        """inspect phase has no gate requirements."""
        from tools.task_analysis import task_gate_check
        contract = self._make_contract(
            must_run_impact_analysis=True,
            must_add_or_update_tests=True,
        )
        result = task_gate_check(
            task_contract=contract,
            next_phase="inspect",
            current_state={},
        )
        assert result["pass"] is True

    def test_multiple_blockers(self):
        from tools.task_analysis import task_gate_check
        contract = self._make_contract(
            must_add_or_update_tests=True,
            must_check_cross_project_usage=True,
        )
        result = task_gate_check(
            task_contract=contract,
            next_phase="apply_changes",
            current_state={
                "tests_reviewed": False,
                "cross_project_check_done": False,
            },
        )
        assert result["pass"] is False
        assert len(result["reason_codes"]) >= 2

    def test_strategy_phase_maps_to_gate(self):
        """Strategy phase 'apply_small_changes' maps to 'apply_changes' gate."""
        from tools.task_analysis import task_gate_check
        contract = self._make_contract(must_add_or_update_tests=True)
        result = task_gate_check(
            task_contract=contract,
            next_phase="apply_small_changes",
            current_state={"tests_reviewed": False},
        )
        assert result["pass"] is False
        assert result["phase"] == "apply_changes"
        assert result["strategy_phase"] == "apply_small_changes"
        assert result["mapped_to_gate"] == "apply_changes"

    def test_strategy_phase_inspect_passes(self):
        """Strategy phase 'inspect_references' maps to 'inspect' (no requirements)."""
        from tools.task_analysis import task_gate_check
        contract = self._make_contract(must_run_impact_analysis=True)
        result = task_gate_check(
            task_contract=contract,
            next_phase="inspect_references",
            current_state={},
        )
        assert result["pass"] is True
        assert result["strategy_phase"] == "inspect_references"
        assert result["mapped_to_gate"] == "inspect"

    def test_strategy_phase_run_validation_maps_to_finalize(self):
        """Strategy phase 'run_validation' maps to 'finalize' gate."""
        from tools.task_analysis import task_gate_check
        contract = self._make_contract()
        result = task_gate_check(
            task_contract=contract,
            next_phase="run_validation",
            current_state={"validation_passed": True},
        )
        assert result["pass"] is True
        assert result["mapped_to_gate"] == "finalize"

    def test_gate_phase_no_mapping_info(self):
        """Direct gate phase names don't include mapping info."""
        from tools.task_analysis import task_gate_check
        contract = self._make_contract()
        result = task_gate_check(
            task_contract=contract,
            next_phase="apply_changes",
            current_state={},
        )
        assert result["pass"] is True
        assert "strategy_phase" not in result
        assert "mapped_to_gate" not in result


# =========================================================================
# Rollback difficulty (signal-based)
# =========================================================================

class TestRollbackDifficulty:

    def test_signal_based_scoring(self):
        from tools.task_analysis import _score_rollback_difficulty
        blast = {"score": 5.0, "level": "medium", "evidence": {"affected_projects": ["proj-a", "proj-b"]}}
        breaking = {"score": 8.0, "level": "high", "evidence": {"has_public_api": True, "total_call_sites": 15}}
        coupling = {"score": 9.0, "level": "high", "evidence": {"shared_by_projects": ["proj-b"]}}
        complexity = {"score": 4.0, "level": "medium"}
        result = _score_rollback_difficulty(blast, breaking, coupling, complexity)
        assert result["score"] > 5.0
        assert result["level"] in ("low", "medium", "high")
        assert "signals" in result["evidence"]
        assert "signal_count" in result["evidence"]
        # Should detect: public_api_change, multi_project, many_consumers, high_breaking, high_coupling
        assert result["evidence"]["signal_count"] >= 3

    def test_no_signals_low_score(self):
        from tools.task_analysis import _score_rollback_difficulty
        blast = {"score": 1.0, "level": "low", "evidence": {"affected_projects": []}}
        breaking = {"score": 1.0, "level": "low", "evidence": {"has_public_api": False, "total_call_sites": 0}}
        coupling = {"score": 0.0, "level": "low", "evidence": {"shared_by_projects": []}}
        complexity = {"score": 1.0, "level": "low"}
        result = _score_rollback_difficulty(blast, breaking, coupling, complexity)
        assert result["score"] == 0.0
        assert result["evidence"]["signal_count"] == 0

    def test_public_api_signal(self):
        from tools.task_analysis import _score_rollback_difficulty
        blast = {"score": 2.0, "level": "low", "evidence": {"affected_projects": []}}
        breaking = {"score": 4.0, "level": "medium", "evidence": {"has_public_api": True, "total_call_sites": 5}}
        coupling = {"score": 0.0, "level": "low", "evidence": {"shared_by_projects": []}}
        complexity = {"score": 2.0, "level": "low"}
        result = _score_rollback_difficulty(blast, breaking, coupling, complexity)
        assert "public_api_change" in result["evidence"]["signals"]
        assert result["score"] >= 3.0


# =========================================================================
# Index confidence
# =========================================================================

class TestIndexConfidence:

    def test_confidence_in_contract(self):
        """analyze_task includes index_confidence in task_profile."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Test confidence",
            targets=["login"],
            intent="refactor",
        )
        ic = result["task_profile"]["index_confidence"]
        assert "score" in ic
        assert "level" in ic
        assert 0 <= ic["score"] <= 10
        assert ic["level"] in ("low", "medium", "high")

    def test_confidence_for_known_symbol(self):
        """Known symbol with complete metadata should get good confidence."""
        from tools.task_analysis import _compute_index_confidence
        resolved = [{"symbol_id": "proj-a:src/auth.py:function:login", "name": "login"}]
        result = _compute_index_confidence(resolved)
        assert result["score"] >= 5.0
        assert result["checks_passed"] > 0

    def test_confidence_for_unknown_symbol(self):
        """Unknown symbol should lower confidence."""
        from tools.task_analysis import _compute_index_confidence
        resolved = [{"symbol_id": "proj-a:nonexistent:function:foo", "name": "foo"}]
        result = _compute_index_confidence(resolved)
        assert result["score"] < 10.0
        assert len(result["warnings"]) > 0

    def test_confidence_no_targets(self):
        """No targets should return low confidence."""
        from tools.task_analysis import _compute_index_confidence
        resolved = [{"symbol_id": None, "name": "x"}]
        result = _compute_index_confidence(resolved)
        assert result["level"] in ("low", "medium", "high")


# =========================================================================
# Decision metadata
# =========================================================================

class TestDecisionMetadata:

    def test_in_contract(self):
        """analyze_task includes decision_metadata section."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login",
            targets=["login"],
            intent="refactor",
        )
        assert "decision_metadata" in result
        dm = result["decision_metadata"]
        assert "reasoning_mode" in dm
        assert "risk_posture" in dm
        assert "anti_patterns" in dm

    def test_bugfix_mode(self):
        from tools.task_analysis import _build_decision_metadata
        dims = {k: {"score": 2.0, "level": "low"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        dm = _build_decision_metadata(dims, "bugfix")
        assert dm["reasoning_mode"] == "minimal_diff"
        assert "guess_and_check" in dm["anti_patterns"]

    def test_cleanup_mode(self):
        from tools.task_analysis import _build_decision_metadata
        dims = {k: {"score": 2.0, "level": "low"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        dm = _build_decision_metadata(dims, "cleanup")
        assert dm["reasoning_mode"] == "elimination"

    def test_high_breaking_adds_boundary_anti(self):
        from tools.task_analysis import _build_decision_metadata
        dims = {k: {"score": 2.0, "level": "low"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        dims["breaking_risk"] = {"score": 8.0, "level": "high"}
        dm = _build_decision_metadata(dims, "bugfix")
        assert "change_without_boundary_check" in dm["anti_patterns"]

    def test_three_high_dims_narrow_mode(self):
        dims = {k: {"score": 8.0, "level": "high"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        from tools.task_analysis import _build_decision_metadata
        dm = _build_decision_metadata(dims, "feature")
        assert dm["reasoning_mode"] == "narrow_then_widen"

    def test_risk_posture_conservative(self):
        dims = {k: {"score": 9.0, "level": "high"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        from tools.task_analysis import _build_decision_metadata
        dm = _build_decision_metadata(dims, "refactor")
        assert dm["risk_posture"] == "conservative"

    def test_risk_posture_standard(self):
        dims = {k: {"score": 1.0, "level": "low"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        from tools.task_analysis import _build_decision_metadata
        dm = _build_decision_metadata(dims, "refactor")
        assert dm["risk_posture"] == "standard"


# =========================================================================
# Execution plan — concrete tool call sequences
# =========================================================================

class TestExecutionPlan:

    def test_in_contract(self):
        """analyze_task includes execution_plan as a list of steps."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login",
            targets=["login"],
            intent="refactor",
        )
        assert "execution_plan" in result
        plan = result["execution_plan"]
        assert isinstance(plan, list)
        assert len(plan) >= 1

    def test_step_schema(self):
        """Each step has id, tool, args, purpose, required, depends_on."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login",
            targets=["login"],
            intent="refactor",
        )
        for step in result["execution_plan"]:
            assert "id" in step, f"Step missing id: {step}"
            assert "tool" in step, f"Step missing tool: {step}"
            assert "args" in step, f"Step missing args: {step}"
            assert "purpose" in step, f"Step missing purpose: {step}"
            assert "required" in step, f"Step missing required: {step}"
            assert "depends_on" in step, f"Step missing depends_on: {step}"
            assert isinstance(step["args"], dict)
            assert isinstance(step["depends_on"], list)

    def test_first_step_scopes_callers(self):
        """First step for refactor should scope callers via find_references."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login",
            targets=["login"],
            intent="refactor",
        )
        plan = result["execution_plan"]
        first = plan[0]
        assert first["tool"] == "find_references"
        assert first["purpose"] == "scope_callers"
        assert "symbol_id" in first["args"]

    def test_args_prefilled_with_target(self):
        """Step args contain the resolved target's symbol_id."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Modify validate_token",
            targets=["proj-a:src/auth.py:function:validate_token"],
            intent="refactor",
        )
        plan = result["execution_plan"]
        ref_steps = [s for s in plan if s["purpose"] == "scope_callers"]
        assert len(ref_steps) >= 1
        assert ref_steps[0]["args"]["symbol_id"] == "proj-a:src/auth.py:function:validate_token"

    def test_has_gate_steps(self):
        """Plan includes task_gate_check steps."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login",
            targets=["login"],
            intent="refactor",
        )
        gate_steps = [s for s in result["execution_plan"]
                      if s["tool"] == "task_gate_check"]
        assert len(gate_steps) >= 1

    def test_gate_depends_on_prior_steps(self):
        """Gate steps depend on earlier inspection steps."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login",
            targets=["login"],
            intent="refactor",
        )
        gate_steps = [s for s in result["execution_plan"]
                      if s["purpose"] == "gate_before_plan"]
        assert len(gate_steps) == 1
        assert len(gate_steps[0]["depends_on"]) >= 1

    def test_high_coupling_adds_cross_project_step(self):
        """High cross_coupling adds a check_cross_project step."""
        from tools.task_analysis import _build_execution_plan
        resolved = [{"symbol_id": "proj-a:src/auth.py:function:login",
                      "path": "src/auth.py", "name": "login"}]
        dims = {k: {"score": 2.0, "level": "low"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        dims["cross_coupling"] = {"score": 8.0, "level": "high"}
        plan = _build_execution_plan(resolved, dims, "refactor", {})
        purposes = [s["purpose"] for s in plan]
        assert "check_cross_project" in purposes

    def test_high_breaking_adds_preview_step(self):
        """High breaking_risk adds edit_impact_preview step."""
        from tools.task_analysis import _build_execution_plan
        resolved = [{"symbol_id": "proj-a:src/auth.py:function:login",
                      "path": "src/auth.py", "name": "login"}]
        dims = {k: {"score": 2.0, "level": "low"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        dims["breaking_risk"] = {"score": 8.0, "level": "high"}
        plan = _build_execution_plan(resolved, dims, "refactor", {})
        purposes = [s["purpose"] for s in plan]
        assert "preview_change_risk" in purposes

    def test_high_complexity_adds_dependency_step(self):
        """High complexity adds dependency_graph step."""
        from tools.task_analysis import _build_execution_plan
        resolved = [{"symbol_id": "proj-a:src/auth.py:function:login",
                      "path": "src/auth.py", "name": "login"}]
        dims = {k: {"score": 2.0, "level": "low"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        dims["complexity"] = {"score": 8.0, "level": "high"}
        plan = _build_execution_plan(resolved, dims, "refactor", {})
        purposes = [s["purpose"] for s in plan]
        assert "map_dependencies" in purposes

    def test_step_ids_unique(self):
        """All step IDs in a plan are unique."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login",
            targets=["login"],
            intent="refactor",
        )
        ids = [s["id"] for s in result["execution_plan"]]
        assert len(ids) == len(set(ids))

    def test_depends_on_references_valid_steps(self):
        """All depends_on values reference existing step IDs."""
        from tools.task_analysis import analyze_task
        result = analyze_task(
            description="Refactor login",
            targets=["login"],
            intent="refactor",
        )
        all_ids = {s["id"] for s in result["execution_plan"]}
        for step in result["execution_plan"]:
            for dep in step["depends_on"]:
                assert dep in all_ids, f"Step {step['id']} depends on unknown step {dep}"

    def test_feature_skips_scope_callers(self):
        """Feature intent doesn't scope existing callers (additive)."""
        from tools.task_analysis import _build_execution_plan
        resolved = [{"symbol_id": "proj-a:src/utils.py:function:helper",
                      "path": "src/utils.py", "name": "helper"}]
        dims = {k: {"score": 2.0, "level": "low"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        plan = _build_execution_plan(resolved, dims, "feature", {})
        purposes = [s["purpose"] for s in plan]
        assert "scope_callers" not in purposes

    def test_multi_target_generates_per_target_steps(self):
        """V2: Multiple targets generate inspect/assess steps for ALL targets."""
        from tools.task_analysis import _build_execution_plan
        resolved = [
            {"symbol_id": "proj-a:src/auth.py:function:login",
             "path": "src/auth.py", "name": "login"},
            {"symbol_id": "proj-a:src/auth.py:function:validate_token",
             "path": "src/auth.py", "name": "validate_token"},
        ]
        dims = {k: {"score": 2.0, "level": "low"} for k in
                ["blast_radius", "breaking_risk", "test_risk",
                 "cross_coupling", "complexity", "rollback_difficulty"]}
        plan = _build_execution_plan(resolved, dims, "refactor", {})
        # Both targets should have find_references steps
        ref_steps = [s for s in plan if s["tool"] == "find_references"]
        assert len(ref_steps) >= 2
