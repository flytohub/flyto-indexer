"""Tests for smart tools — consolidated entry points with association triggers."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tools.smart import smart_search, smart_impact, smart_audit, smart_task, smart_structure


# ---------------------------------------------------------------------------
# Fixtures: mock the underlying tool modules
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_search():
    with patch("tools.smart._search_mod") as m:
        mod = MagicMock()
        mod.search_by_keyword.return_value = {
            "results": [
                {"symbol_id": "proj:src/pay.py:function:process_refund", "name": "process_refund", "path": "src/pay.py", "score": 0.95},
                {"symbol_id": "proj:src/pay.py:function:charge", "name": "charge", "path": "src/pay.py", "score": 0.7},
            ],
            "count": 2,
        }
        mod.semantic_search.return_value = {
            "results": [
                {"symbol_id": "proj:src/pay.py:function:process_refund", "name": "process_refund", "path": "src/pay.py", "score": 0.9},
                {"symbol_id": "proj:src/err.py:function:handle_error", "name": "handle_error", "path": "src/err.py", "score": 0.5},
            ],
            "concept_expansion": ["refund", "payment", "charge"],
            "count": 2,
        }
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_grill():
    with patch("tools.smart._grill_mod") as m:
        mod = MagicMock()
        mod.run_grill.return_value = {
            "session_id": "grill_aaaaaaaaaaaaaaaaaaaaaaaa",
            "next_question": {"id": "scope"},
        }
        mod.validate_decision_contract.return_value = {"pass": True}
        mod.export_decision_contract.return_value = {
            "version": "flyto.decision-contract.v1",
            "status": "frozen",
            "fingerprint": "abc",
        }
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_refs():
    with patch("tools.smart._refs_mod") as m:
        mod = MagicMock()
        mod.find_references.return_value = {
            "symbol_id": "proj:src/pay.py:function:process_refund",
            "target_file": "src/pay.py",
            "references_count": 3,
            "references": [
                {"caller_id": "proj:src/api.py:function:handle_api", "path": "src/api.py", "line": 42},
                {"caller_id": "proj:tests/test_pay.py:function:test_refund", "path": "tests/test_pay.py", "line": 10},
                {"caller_id": "proj:src/batch.py:function:batch_process", "path": "src/batch.py", "line": 88},
            ],
        }
        mod.impact_analysis.return_value = {
            "symbol_id": "proj:src/pay.py:function:process_refund",
            "target_file": "src/pay.py",
            "affected_count": 5,
            "risk": "medium",
        }
        mod.cross_project_impact.return_value = {"impacts": []}
        mod.edit_impact_preview.return_value = {"call_sites": []}
        mod.dependency_graph.return_value = {"nodes": [], "edges": []}
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_info():
    with patch("tools.smart._info_mod") as m:
        mod = MagicMock()
        mod.get_file_symbols.return_value = {
            "symbols": [
                {"name": "process_refund", "type": "function"},
                {"name": "charge", "type": "function"},
                {"name": "validate_amount", "type": "function"},
            ],
        }
        mod.find_test_file.return_value = {"test_file": "tests/test_pay.py"}
        mod.list_projects.return_value = {"count": 2, "projects": ["proj-a", "proj-b"]}
        mod.list_apis.return_value = {"apis": [], "count": 0}
        mod.list_categories.return_value = {"categories": []}
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_quality():
    with patch("tools.smart._quality_mod") as m:
        mod = MagicMock()
        mod.code_health_score.return_value = {
            "score": 72,
            "grade": "C",
            "snapshot": {
                "schema": "health-snapshot.v2",
                "id": "snapshot-1",
            },
            "breakdown": {
                "complexity": {
                    "score": 15,
                    "max": 25,
                    "detail": "5 complex functions",
                    "metrics": {
                        "total_functions": 20,
                        "complex_functions": 5,
                        "complexity_burden": 35,
                        "max_complexity_score": 12,
                        "avg_complexity": 2.5,
                    },
                    "hotspots": [{"name": "branchy", "score": 12}],
                },
                "dead_code": {
                    "score": 23,
                    "max": 25,
                    "detail": "2 unused symbols",
                    "metrics": {"dead_count": 2, "dead_lines": 10},
                    "symbols": [{"name": "unused"}],
                },
                "security": {"score": 12, "max": 25, "detail": "3 findings"},
                "documentation": {"score": 22, "max": 25, "detail": "ok"},
            },
        }
        mod.security_scan.return_value = {"findings": [{"severity": "high"}], "count": 1}
        mod.find_complex_functions.return_value = {"results": [], "count": 0}
        mod.find_duplicates.return_value = {"results": [], "count": 0}
        mod.suggest_refactoring.return_value = {"suggestions": []}
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_git():
    with patch("tools.smart._git_mod") as m:
        mod = MagicMock()
        mod.git_hotspots.return_value = {"hotspots": [], "count": 0}
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_evidence():
    with patch("tools.smart._evidence_mod") as m:
        mod = MagicMock()
        mod.build_evidence_portfolio.return_value = {
            "schema": "evidence-portfolio.v1",
            "status": "captured",
            "summary": {"selected_commits": 1},
            "commits": [{"id": "E001"}],
        }
        mod.build_audit_verdict.return_value = {
            "schema": "evidence-verdict.v1",
            "status": "attention",
            "findings": [{"refs": ["audit.health"]}],
        }
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_staleness():
    with patch("tools.smart._staleness_mod") as m:
        mod = MagicMock()
        mod.find_stale_symbols.return_value = {"stale_symbols": [], "count": 0}
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_maint():
    with patch("tools.smart._maint_mod") as m:
        mod = MagicMock()
        mod.find_dead_code.return_value = {"results": [], "count": 0}
        mod.check_index_status.return_value = {"status": "fresh"}
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_diff():
    with patch("tools.smart._diff_mod") as m:
        mod = MagicMock()
        mod.impact_from_diff.return_value = {
            "changes": [
                {"file": "src/pay.py", "symbols": ["process_refund"], "type": "body_change"},
            ],
        }
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_task():
    with patch("tools.smart._task_mod") as m:
        mod = MagicMock()
        mod.analyze_task.return_value = {
            "task_id": "t1",
            "execution_plan": [{"id": "s1", "tool": "find_references", "args": {}}],
        }
        mod.task_gate_check.return_value = {"pass": True}
        m.return_value = mod
        yield mod


@pytest.fixture
def mock_validation():
    with patch("tools.smart._validation_mod") as m:
        mod = MagicMock()
        mod.validate_changes.return_value = {"tests_passed": True, "lint_passed": True}
        m.return_value = mod
        yield mod


# ---------------------------------------------------------------------------
# Tests: smart_search
# ---------------------------------------------------------------------------

class TestSmartSearch:

    def test_empty_query(self):
        result = smart_search("")
        assert result["results"] == []

    def test_merges_bm25_and_semantic(self, mock_search, mock_refs, mock_info):
        result = smart_search("refund")
        assert result["result_count"] == 3  # 2 bm25 + 1 unique from semantic
        assert result["search_modes"] == ["bm25", "semantic"]

    def test_deduplicates_by_symbol_id(self, mock_search, mock_refs, mock_info):
        result = smart_search("refund")
        ids = [r["symbol_id"] for r in result["results"]]
        assert len(ids) == len(set(ids))

    def test_auto_attaches_callers(self, mock_search, mock_refs, mock_info):
        result = smart_search("refund")
        top = result["results"][0]
        assert "callers" in top
        assert len(top["callers"]) <= 5
        assert "caller_count" in top

    def test_auto_attaches_file_siblings(self, mock_search, mock_refs, mock_info):
        result = smart_search("refund")
        top = result["results"][0]
        assert "file_siblings" in top
        # Should not include the symbol itself
        assert top["name"] not in top["file_siblings"]

    def test_concept_expansion_passed_through(self, mock_search, mock_refs, mock_info):
        result = smart_search("refund")
        assert result["concept_expansion"] == ["refund", "payment", "charge"]


# ---------------------------------------------------------------------------
# Tests: smart_impact
# ---------------------------------------------------------------------------

class TestSmartImpact:

    def test_no_target_no_mode(self):
        result = smart_impact()
        assert "error" in result

    def test_symbol_mode(self, mock_refs, mock_info):
        result = smart_impact(target="proj:src/pay.py:function:process_refund")
        assert "references" in result
        assert "impact" in result
        assert result["target"] == "proj:src/pay.py:function:process_refund"

    def test_auto_cross_project(self, mock_refs, mock_info):
        """With >1 project, auto-runs cross_project_impact."""
        smart_impact(target="proj:src/pay.py:function:process_refund")
        mock_refs.cross_project_impact.assert_called_once()

    def test_auto_test_file(self, mock_refs, mock_info):
        result = smart_impact(target="proj:src/pay.py:function:process_refund")
        assert result.get("test_file") == "tests/test_pay.py"

    def test_diff_mode(self, mock_diff, mock_info):
        result = smart_impact(mode="unstaged")
        assert result["mode"] == "diff"
        assert result["diff_mode"] == "unstaged"
        mock_diff.impact_from_diff.assert_called_once_with(mode="unstaged", project=None)

    def test_diff_auto_test_file(self, mock_diff, mock_info):
        result = smart_impact(mode="unstaged")
        changes = result["result"]["changes"]
        assert changes[0].get("test_file") == "tests/test_pay.py"

    def test_change_type_triggers_edit_preview(self, mock_refs, mock_info):
        smart_impact(target="process_refund", change_type="rename")
        mock_refs.edit_impact_preview.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: smart_audit
# ---------------------------------------------------------------------------

class TestSmartAudit:

    def test_always_includes_health(
        self, mock_quality, mock_git, mock_evidence, mock_staleness, mock_maint
    ):
        result = smart_audit()
        assert "health" in result
        assert result["health"]["score"] == 72
        assert result["evidence_portfolio"]["schema"] == "evidence-portfolio.v1"
        assert result["verdict"]["schema"] == "evidence-verdict.v1"
        assert result["evidence_integrity"]["pass"] is True

    def test_auto_expands_weak_dimensions(
        self, mock_quality, mock_git, mock_evidence, mock_staleness, mock_maint
    ):
        """Score < 80 for security and complexity → auto-expand both."""
        result = smart_audit()
        # security=60 → should have security_findings
        assert "security_findings" in result
        # complexity=65 → should have complex_functions
        assert "complex_functions" in result
        # dead_code=90 → should NOT auto-expand
        assert "dead_code" not in result

        detail = result["complex_functions"]
        metrics = result["health"]["breakdown"]["complexity"]["metrics"]
        assert detail["complex_count"] == metrics["complex_functions"]
        assert detail["complexity_burden"] == metrics["complexity_burden"]
        assert detail["snapshot"] == result["health"]["snapshot"]

    def test_focus_overrides(
        self, mock_quality, mock_git, mock_evidence, mock_staleness, mock_maint
    ):
        result = smart_audit(focus="dead_code")
        assert "dead_code" in result
        assert result["dead_code"]["total_dead"] == 2
        assert result["dead_code"]["snapshot"] == result["health"]["snapshot"]

    def test_audit_integrity_fails_closed_on_divergent_expansion(
        self, mock_quality, mock_git, mock_evidence, mock_staleness, mock_maint,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "tools.smart._canonical_complexity_detail",
            lambda *_args, **_kwargs: {
                "total_analyzed": 20,
                "complex_count": 99,
                "complexity_burden": 35,
                "max_complexity_score": 12,
                "snapshot": {"id": "wrong"},
            },
        )

        result = smart_audit(focus="complexity")

        assert result["blocked"] is True
        assert result["reason_codes"] == ["EVIDENCE_SNAPSHOT_DIVERGED"]
        assert "verdict" not in result

    def test_low_score_suggests_refactoring(
        self, mock_quality, mock_git, mock_evidence, mock_staleness, mock_maint
    ):
        """Overall score 72 < 80 → includes refactoring suggestions."""
        result = smart_audit()
        assert "refactoring_suggestions" in result

    def test_always_includes_hotspots(
        self, mock_quality, mock_git, mock_evidence, mock_staleness, mock_maint
    ):
        result = smart_audit()
        assert "git_hotspots" in result


# ---------------------------------------------------------------------------
# Tests: smart_task
# ---------------------------------------------------------------------------

class TestSmartTask:

    def test_plan_action(self, mock_task):
        result = smart_task(action="plan", description="refactor auth", targets=["src/auth.py"])
        assert result["task_id"] == "t1"
        mock_task.analyze_task.assert_called_once()

    def test_recovery_context_without_parent_never_falls_back_to_fresh_plan(
        self,
        mock_task,
    ):
        result = smart_task(
            action="plan",
            description="continue",
            targets=["src/auth.py"],
            recovery_context={
                "version": "task-rework-recovery.request.v1",
                "source_parent_contract_digest": "0" * 64,
                "prior_scope": ["src/prior.py"],
                "requested_targets": ["src/auth.py"],
            },
        )

        assert result["pass"] is False
        assert result["reason_codes"] == ["AMENDMENT_PARENT_NOT_A_CONTRACT"]
        mock_task.analyze_task.assert_not_called()

    def test_recovery_context_is_plan_only(self, mock_task):
        result = smart_task(
            action="gate",
            task_contract={"id": "t1"},
            next_phase="implement",
            recovery_context={"hostile": [{}]},
        )

        assert result["pass"] is False
        assert result["reason_codes"] == ["AMENDMENT_RECOVERY_CONTEXT_INVALID"]
        mock_task.task_gate_check.assert_not_called()

    def test_gate_action(self, mock_task):
        result = smart_task(action="gate", task_contract={"id": "t1"}, next_phase="implement")
        assert result["pass"] is True
        mock_task.task_gate_check.assert_called_once()

    def test_validate_action(self, mock_validation):
        result = smart_task(action="validate")
        assert result["tests_passed"] is True

    def test_validate_contract_scopes_lint_to_declared_python_paths(
        self,
        mock_validation,
    ):
        contract = {
            "task_profile": {"project": "demo"},
            "intent_ledger": {
                "allowed_paths": [
                    "src/app.py",
                    "tests/test_app.py",
                    "README.md",
                    "deleted.py",
                ],
            },
        }

        smart_task(
            action="validate",
            project="demo",
            task_contract=contract,
            run_tests=False,
        )

        mock_validation.validate_changes.assert_called_once_with(
            project="demo",
            run_tests=False,
            test_path=None,
            lint_paths=["deleted.py", "src/app.py", "tests/test_app.py"],
        )

    def test_validate_scopes_diff_to_host_attributable_changed_paths(
        self,
        mock_validation,
    ):
        contract = {
            "task_profile": {"project": "demo"},
            "intent_ledger": {"allowed_paths": ["src/app.py"]},
        }
        context = MagicMock()
        context.validate_intent_ledger.return_value = {
            "pass": True,
            "status": "pass",
            "change_set": {
                "status": "captured",
                "changed_paths": ["src/app.py"],
            },
        }
        context.validate_instruction_context.return_value = {
            "pass": True,
            "status": "not_required",
        }

        with patch("tools.smart._task_context_mod", return_value=context):
            smart_task(
                action="validate",
                project="demo",
                task_contract=contract,
                run_tests=False,
                current_state={"changed_paths": ["src/app.py"]},
            )

        context.validate_intent_ledger.assert_called_once_with(
            contract,
            project="demo",
            validation=mock_validation.validate_changes.return_value,
            change_set={
                "status": "captured",
                "changed_paths": ["src/app.py"],
            },
        )

    def test_validate_can_require_external_proof(self, mock_validation):
        with (
            patch("tools.smart._proof_receipts_mod") as proof_mod,
            patch("tools.smart._feedback_mod") as feedback_mod,
        ):
            proof_mod.return_value.validate_proof_receipts.return_value = {
                "pass": False,
                "reason_codes": ["EXTERNAL_PROOF_NONCONFORMANT"],
                "required_actions": ["attach browser receipt"],
            }
            feedback_mod.return_value.record_validation_feedback.return_value = {
                "status": "recorded"
            }
            result = smart_task(
                action="validate",
                project="demo",
                required_proof_kinds=["browser"],
            )

        assert result["pass"] is False
        assert "EXTERNAL_PROOF_NONCONFORMANT" in result["reason_codes"]
        assert "external_proof_validation" in result

    def test_feedback_action_uses_local_learning_branch(self):
        with patch("tools.smart._feedback_mod") as feedback_mod:
            feedback_mod.return_value.record_feedback.return_value = {
                "status": "recorded",
                "feedback_id": "feedback-1",
            }
            result = smart_task(
                action="feedback",
                project="demo",
                feedback_category="false_positive",
                feedback_summary="Demo value was noisy",
            )

        assert result["feedback_id"] == "feedback-1"
        feedback_mod.return_value.record_feedback.assert_called_once()

    def test_unknown_action(self):
        result = smart_task(action="unknown")
        assert "error" in result

    def test_grill_action_keeps_one_high_level_tool_surface(
        self, mock_grill, mock_search
    ):
        decisions = [
            {
                "id": "scope",
                "question": "Which scope?",
                "recommendation": "Use the smallest safe scope.",
            }
        ]
        result = smart_task(
            action="grill",
            grill_action="start",
            description="robot adapter",
            project="flyto-robotics",
            decisions=decisions,
            locale="zh-TW",
        )

        assert result["next_question"]["id"] == "scope"
        kwargs = mock_grill.run_grill.call_args.kwargs
        assert kwargs["operation"] == "start"
        assert kwargs["decisions"] == decisions
        assert kwargs["locale"] == "zh-TW"
        evidence = kwargs["fact_resolver"]("adapter registry", "flyto-robotics")
        assert evidence["results"]
        mock_search.search_by_keyword.assert_called_once_with(
            query="adapter registry",
            max_results=5,
            project="flyto-robotics",
            include_content=False,
        )

    def test_plan_attaches_frozen_decision_contract(self, mock_task, mock_grill):
        result = smart_task(
            action="plan",
            description="add adapter",
            targets=["src/adapter.py"],
            grill_session_id="grill_aaaaaaaaaaaaaaaaaaaaaaaa",
        )

        assert result["decision_contract"]["status"] == "frozen"
        assert result["task_profile"]["decision_session_id"].startswith("grill_")
        mock_grill.export_decision_contract.assert_called_once()

    def test_gate_fails_before_legacy_gate_when_decision_contract_is_invalid(
        self, mock_task, mock_grill
    ):
        mock_grill.validate_decision_contract.return_value = {
            "pass": False,
            "reason_codes": ["DECISION_CONTRACT_TAMPERED"],
        }

        result = smart_task(
            action="gate",
            task_contract={"decision_contract": {"status": "frozen"}},
            next_phase="apply_changes",
        )

        assert result["pass"] is False
        mock_task.task_gate_check.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: smart_structure
# ---------------------------------------------------------------------------

class TestSmartStructure:

    def test_default_overview(self, mock_info, mock_maint):
        with patch("tools.smart._framework_relationships_mod") as relationships:
            result = smart_structure()
        assert "projects" in result
        relationships.assert_not_called()

    def test_project_detail(self, mock_info, mock_maint):
        result = smart_structure(project="proj-a")
        assert "projects" in result
        assert "apis" in result
        assert "index_status" in result

    def test_apis_focus(self, mock_info):
        with patch("tools.smart._type_mod") as tm:
            tm.return_value.contract_drift.return_value = {"drifts": []}
            result = smart_structure(focus="apis")
            assert "apis" in result
            assert "categories" in result

    def test_dependencies_focus(self, mock_refs):
        with patch("tools.smart._framework_relationships_mod") as relationships:
            relationships.return_value.analyze_framework_relationships.return_value = {
                "status": "analyzed",
                "relationships": [{"kind": "react_lazy_import"}],
            }
            result = smart_structure(focus="dependencies", path="src/pay.py")
        assert "graph" in result
        assert result["framework_relationships"]["relationships"][0]["kind"] == "react_lazy_import"
        mock_refs.dependency_graph.assert_called_once()

    def test_types_focus(self):
        with patch("tools.smart._type_mod") as tm:
            tm.return_value.extract_type_schema.return_value = {"fields": []}
            tm.return_value.contract_drift.return_value = {"drifts": []}
            result = smart_structure(focus="types", symbol_id="proj:src/model.py:class:User")
            assert "schema" in result


# ---------------------------------------------------------------------------
# Tests: tool_registry integration
# ---------------------------------------------------------------------------

class TestToolRegistryIntegration:

    def test_smart_tools_in_registry(self):
        from tool_registry import SMART_TOOLS, SMART_TOOL_NAMES
        assert len(SMART_TOOLS) == 20
        expected_names = {
            "search", "impact", "audit", "task", "structure",
            "verify", "verify_workspace",
            "project_profile", "scan_secrets", "scan_licenses",
            "scan_documentation", "analyze_pr_risk", "detect_frameworks",
            "call_hierarchy", "check_layers",
            "add_layer", "add_taint_source", "add_taint_sink",
            "add_taint_sanitizer", "list_taint_rules",
        }
        assert expected_names == SMART_TOOL_NAMES

    def test_smart_tools_in_dispatch(self):
        """Verify smart tools are registered. Uses has_tool() not
        execute_tool() — invoking handlers in CI was flaky because
        they could hang on partially-loaded module state from
        earlier tests in the suite."""
        from tool_registry import has_tool
        for name in ["search", "impact", "audit", "task", "structure", "verify", "verify_workspace"]:
            assert has_tool(name), f"Smart tool '{name}' not in registered dispatch"

    def test_tool_names_stay_in_sync_with_dispatch(self):
        """Drift guard: declared names equal the cached runtime registry."""
        from tool_registry import _TOOL_NAMES
        from tool_registry.dispatch import _dispatch_table

        dispatch_keys = set(_dispatch_table())

        names_set = set(_TOOL_NAMES)
        only_in_names = names_set - dispatch_keys
        only_in_dispatch = dispatch_keys - names_set
        assert only_in_names == set(), (
            f"_TOOL_NAMES has entries dispatch doesn't: {sorted(only_in_names)}"
        )
        assert only_in_dispatch == set(), (
            f"dispatch has entries _TOOL_NAMES doesn't: {sorted(only_in_dispatch)}. "
            f"Add them to _TOOL_NAMES so has_tool() reports correctly."
        )

    def test_legacy_tools_still_in_dispatch(self):
        """Old tools must keep their dispatch entries. Uses has_tool()
        for the same flakiness reason as the smart-tools test."""
        from tool_registry import has_tool
        for name in ["search_code", "find_references", "code_health_score", "analyze_task"]:
            assert has_tool(name), f"Legacy tool '{name}' missing from dispatch"
