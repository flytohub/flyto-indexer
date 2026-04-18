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
            "breakdown": {
                "complexity": {"score": 15, "max": 25, "detail": "5 complex functions"},
                "dead_code": {"score": 23, "max": 25, "detail": "2 unused symbols"},
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

    def test_always_includes_health(self, mock_quality, mock_git, mock_maint):
        result = smart_audit()
        assert "health" in result
        assert result["health"]["score"] == 72

    def test_auto_expands_weak_dimensions(self, mock_quality, mock_git, mock_maint):
        """Score < 80 for security and complexity → auto-expand both."""
        result = smart_audit()
        # security=60 → should have security_findings
        assert "security_findings" in result
        # complexity=65 → should have complex_functions
        assert "complex_functions" in result
        # dead_code=90 → should NOT auto-expand
        assert "dead_code" not in result

    def test_focus_overrides(self, mock_quality, mock_git, mock_maint):
        result = smart_audit(focus="dead_code")
        assert "dead_code" in result

    def test_low_score_suggests_refactoring(self, mock_quality, mock_git, mock_maint):
        """Overall score 72 < 80 → includes refactoring suggestions."""
        result = smart_audit()
        assert "refactoring_suggestions" in result

    def test_always_includes_hotspots(self, mock_quality, mock_git, mock_maint):
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

    def test_gate_action(self, mock_task):
        result = smart_task(action="gate", task_contract={"id": "t1"}, next_phase="implement")
        assert result["pass"] is True
        mock_task.task_gate_check.assert_called_once()

    def test_validate_action(self, mock_validation):
        result = smart_task(action="validate")
        assert result["tests_passed"] is True

    def test_unknown_action(self):
        result = smart_task(action="unknown")
        assert "error" in result


# ---------------------------------------------------------------------------
# Tests: smart_structure
# ---------------------------------------------------------------------------

class TestSmartStructure:

    def test_default_overview(self, mock_info, mock_maint):
        result = smart_structure()
        assert "projects" in result

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
        result = smart_structure(focus="dependencies", path="src/pay.py")
        assert "graph" in result
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
        assert len(SMART_TOOLS) == 9
        assert SMART_TOOL_NAMES == {"search", "impact", "audit", "task", "structure", "project_profile", "scan_secrets", "scan_licenses", "scan_documentation"}

    def test_smart_tools_in_dispatch(self):
        from tool_registry import execute_tool
        # Verify smart tools are registered (will fail at execution since no index, but shouldn't KeyError)
        for name in ["search", "impact", "audit", "task", "structure"]:
            try:
                execute_tool(name, {"query": "test"} if name == "search" else {})
            except KeyError:
                pytest.fail(f"Smart tool '{name}' not found in dispatch")
            except Exception:
                pass  # Expected — no index loaded

    def test_legacy_tools_still_in_dispatch(self):
        from tool_registry import execute_tool
        # Old tools should still work via dispatch
        for name in ["search_code", "find_references", "code_health_score", "analyze_task"]:
            try:
                execute_tool(name, {"query": "test"} if "search" in name else {})
            except KeyError:
                pytest.fail(f"Legacy tool '{name}' missing from dispatch")
            except Exception:
                pass  # Expected — no index loaded
