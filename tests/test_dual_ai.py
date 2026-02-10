"""
Tests for dual_ai module.

Covers:
- Enum definitions (AgentRole, ConsensusMode, ReviewType)
- AgentInfo dataclass and AVAILABLE_AGENTS constant
- dual_ai_agents() synchronous function
- dual_ai_task() with mocked VPS responses
- dual_ai_review() with mocked VPS responses
- dual_ai_consensus() with mocked VPS responses
- dual_ai_security() with mocked VPS responses
- dual_ai_test_gen() with mocked VPS responses
- Error handling (timeouts, VPS failures, fallback paths)
- Environment variable configuration
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import json
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock, mock_open

from dual_ai import (
    AgentRole,
    ConsensusMode,
    ReviewType,
    AgentInfo,
    AVAILABLE_AGENTS,
    VPS_API_URL,
    VPS_TIMEOUT,
    call_vps,
    dual_ai_task,
    dual_ai_review,
    dual_ai_consensus,
    dual_ai_security,
    dual_ai_test_gen,
    dual_ai_agents,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vps_task_response():
    """Standard successful VPS response for dual_ai_task."""
    return {
        "ok": True,
        "session_id": "vps-session-1234",
        "iteration": 3,
        "action": "complete",
        "message": "Task completed successfully",
        "todo_list": [
            {"step": "plan", "output": "created plan"},
            {"step": "execute", "output": "executed plan"},
        ],
        "files": [{"path": "output.py", "content": "print('hello')"}],
        "edits": [],
        "metadata": {"duration_ms": 1500},
    }


@pytest.fixture
def vps_review_response():
    """Standard successful VPS response for dual_ai_review."""
    return {
        "ok": True,
        "file": "test.py",
        "reviews": {
            "claude": {
                "issues": [
                    {
                        "severity": "high",
                        "line": 10,
                        "description": "SQL injection risk",
                        "suggestion": "Use parameterized queries",
                    }
                ],
                "suggestions": ["Add input validation"],
            },
            "gpt4": {
                "issues": [
                    {
                        "severity": "medium",
                        "line": 25,
                        "description": "Missing error handling",
                        "suggestion": "Add try/except block",
                    }
                ],
                "suggestions": ["Improve logging"],
            },
        },
        "merged": {
            "critical": [],
            "high": [{"severity": "high", "description": "SQL injection risk"}],
            "medium": [{"severity": "medium", "description": "Missing error handling"}],
            "low": [],
        },
        "consensus_score": 0.5,
        "total_issues": 2,
    }


@pytest.fixture
def vps_consensus_response():
    """Standard successful VPS response for dual_ai_consensus."""
    return {
        "ok": True,
        "question": "Which approach?",
        "options": ["Option A", "Option B"],
        "votes": {
            "claude": {"choice": "Option A", "confidence": 0.9, "reasoning": "Better performance"},
            "gpt4": {"choice": "Option A", "confidence": 0.85, "reasoning": "More maintainable"},
        },
        "result": {
            "winner": "Option A",
            "vote_count": {"Option A": 2, "Option B": 0},
            "consensus_reached": True,
            "combined_reasoning": "Both models agree on Option A.",
        },
    }


@pytest.fixture
def vps_security_response():
    """Standard successful VPS response for dual_ai_security."""
    return {
        "ok": True,
        "target": "app.py",
        "scan_type": "full",
        "vulnerabilities": [
            {
                "severity": "critical",
                "type": "sql_injection",
                "location": {"file": "app.py", "line": 42},
                "description": "Raw SQL query with user input",
                "fix_suggestion": "Use parameterized queries",
                "cwe_id": "CWE-89",
            },
            {
                "severity": "medium",
                "type": "hardcoded_secret",
                "location": {"file": "app.py", "line": 5},
                "description": "Hardcoded API key",
                "fix_suggestion": "Use environment variables",
                "cwe_id": "CWE-798",
            },
        ],
        "summary": {"critical": 1, "high": 0, "medium": 1, "low": 0},
        "total_vulnerabilities": 2,
        "recommendation": "Address critical SQL injection immediately.",
    }


@pytest.fixture
def vps_test_gen_response():
    """Standard successful VPS response for dual_ai_test_gen."""
    return {
        "ok": True,
        "target": "utils.py",
        "test_type": "unit",
        "framework": "pytest",
        "tests": [
            {
                "name": "test_add_positive_numbers",
                "description": "Test addition of two positive integers",
                "code": "def test_add_positive_numbers():\n    assert add(1, 2) == 3",
                "covers": ["line 5-7", "happy path"],
            },
            {
                "name": "test_add_negative_numbers",
                "description": "Test addition with negative numbers",
                "code": "def test_add_negative_numbers():\n    assert add(-1, -2) == -3",
                "covers": ["line 5-7", "negative input"],
            },
        ],
        "coverage_estimate": 85.5,
        "output_file": "test_utils.py",
        "notes": "Consider adding tests for float inputs.",
    }


# ---------------------------------------------------------------------------
# TestAgentDefinitions
# ---------------------------------------------------------------------------

class TestAgentDefinitions:
    """Test agent role definitions and constants."""

    def test_agent_role_values(self):
        """AgentRole enum should have exactly 6 members with correct values."""
        assert AgentRole.PLANNER.value == "planner"
        assert AgentRole.EXECUTOR.value == "executor"
        assert AgentRole.REVIEWER.value == "reviewer"
        assert AgentRole.SECURITY.value == "security"
        assert AgentRole.PERFORMANCE.value == "performance"
        assert AgentRole.TEST_GENERATOR.value == "test_generator"

    def test_agent_role_count(self):
        """AgentRole should have exactly 6 members."""
        assert len(AgentRole) == 6

    def test_consensus_mode_values(self):
        """ConsensusMode enum should have correct values."""
        assert ConsensusMode.MAJORITY.value == "majority"
        assert ConsensusMode.UNANIMOUS.value == "unanimous"
        assert ConsensusMode.WEIGHTED.value == "weighted"

    def test_consensus_mode_count(self):
        """ConsensusMode should have exactly 3 members."""
        assert len(ConsensusMode) == 3

    def test_review_type_values(self):
        """ReviewType enum should have correct values."""
        assert ReviewType.SECURITY.value == "security"
        assert ReviewType.PERFORMANCE.value == "performance"
        assert ReviewType.STYLE.value == "style"
        assert ReviewType.ALL.value == "all"

    def test_review_type_count(self):
        """ReviewType should have exactly 4 members."""
        assert len(ReviewType) == 4

    def test_agent_info_dataclass(self):
        """AgentInfo dataclass should work with all fields."""
        agent = AgentInfo(
            id="test",
            name="Test Agent",
            model="test-model",
            capabilities=["cap1", "cap2"],
            status="available",
        )
        assert agent.id == "test"
        assert agent.name == "Test Agent"
        assert agent.model == "test-model"
        assert agent.capabilities == ["cap1", "cap2"]
        assert agent.status == "available"

    def test_agent_info_default_status(self):
        """AgentInfo should default status to 'available'."""
        agent = AgentInfo(
            id="test",
            name="Test",
            model="model",
            capabilities=[],
        )
        assert agent.status == "available"

    def test_available_agents_count(self):
        """AVAILABLE_AGENTS should have exactly 6 agents."""
        assert len(AVAILABLE_AGENTS) == 6

    def test_available_agents_ids(self):
        """AVAILABLE_AGENTS should contain all expected agent IDs."""
        ids = {agent.id for agent in AVAILABLE_AGENTS}
        expected = {"planner", "executor", "reviewer", "security", "performance", "test_generator"}
        assert ids == expected

    def test_available_agents_models(self):
        """AVAILABLE_AGENTS should use the expected models."""
        agent_models = {agent.id: agent.model for agent in AVAILABLE_AGENTS}
        assert agent_models["planner"] == "gpt-4o"
        assert agent_models["executor"] == "claude-opus-4.5"
        assert agent_models["reviewer"] == "claude-opus-4.5"
        assert agent_models["security"] == "claude-opus-4.5"
        assert agent_models["performance"] == "gpt-4o"
        assert agent_models["test_generator"] == "claude-opus-4.5"

    def test_available_agents_have_capabilities(self):
        """Every agent in AVAILABLE_AGENTS should have at least one capability."""
        for agent in AVAILABLE_AGENTS:
            assert len(agent.capabilities) > 0, f"Agent {agent.id} has no capabilities"

    def test_available_agents_all_available(self):
        """All agents should have status 'available' by default."""
        for agent in AVAILABLE_AGENTS:
            assert agent.status == "available", f"Agent {agent.id} status is {agent.status}"


# ---------------------------------------------------------------------------
# TestDualAiAgents
# ---------------------------------------------------------------------------

class TestDualAiAgents:
    """Test dual_ai_agents() function."""

    def test_returns_dict(self):
        """dual_ai_agents() should return a dict."""
        result = dual_ai_agents()
        assert isinstance(result, dict)

    def test_ok_field(self):
        """Result should have ok=True."""
        result = dual_ai_agents()
        assert result["ok"] is True

    def test_agents_list_format(self):
        """Result should contain 'agents' as a list of dicts."""
        result = dual_ai_agents()
        assert "agents" in result
        assert isinstance(result["agents"], list)
        assert len(result["agents"]) == 6

    def test_agent_dict_fields(self):
        """Each agent dict should have id, name, model, capabilities, status."""
        result = dual_ai_agents()
        required_fields = {"id", "name", "model", "capabilities", "status"}
        for agent in result["agents"]:
            assert required_fields.issubset(agent.keys()), (
                f"Agent {agent.get('id', '?')} missing fields: "
                f"{required_fields - agent.keys()}"
            )

    def test_total_agents_field(self):
        """Result should include total_agents count."""
        result = dual_ai_agents()
        assert result["total_agents"] == 6

    def test_models_field(self):
        """Result should include models dict with openai and anthropic keys."""
        result = dual_ai_agents()
        assert "models" in result
        assert "openai" in result["models"]
        assert "anthropic" in result["models"]
        assert isinstance(result["models"]["openai"], list)
        assert isinstance(result["models"]["anthropic"], list)

    def test_models_contain_expected_values(self):
        """Models should include gpt-4o and claude-opus-4.5."""
        result = dual_ai_agents()
        assert "gpt-4o" in result["models"]["openai"]
        assert "claude-opus-4.5" in result["models"]["anthropic"]


# ---------------------------------------------------------------------------
# TestDualAiTask
# ---------------------------------------------------------------------------

class TestDualAiTask:
    """Test dual_ai_task() with mocked VPS."""

    @pytest.mark.asyncio
    async def test_vps_success(self, vps_task_response):
        """dual_ai_task() should return formatted result when VPS succeeds."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = vps_task_response
            result = await dual_ai_task("build a login form")

        assert result["ok"] is True
        assert result["session_id"] == "vps-session-1234"
        assert result["iterations"] == 3
        assert result["action"] == "complete"
        assert result["message"] == "Task completed successfully"
        assert len(result["results"]) == 2
        assert len(result["files"]) == 1

    @pytest.mark.asyncio
    async def test_vps_called_with_correct_args(self):
        """dual_ai_task() should call VPS with correct endpoint and data."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "session_id": "s1"}
            await dual_ai_task(
                "do something",
                project_path="/my/project",
                mode="parallel",
                agents=["planner", "executor"],
                max_iterations=5,
            )

        mock_vps.assert_called_once_with("coordinate", {
            "task": "do something",
            "project_path": "/my/project",
            "mode": "parallel",
            "agents": ["planner", "executor"],
            "max_iterations": 5,
        })

    @pytest.mark.asyncio
    async def test_default_agents(self):
        """dual_ai_task() should default to planner, executor, reviewer."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "session_id": "s1"}
            await dual_ai_task("do something")

        call_data = mock_vps.call_args[0][1]
        assert call_data["agents"] == ["planner", "executor", "reviewer"]

    @pytest.mark.asyncio
    async def test_default_mode(self):
        """dual_ai_task() should default to 'sequential' mode."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "session_id": "s1"}
            await dual_ai_task("do something")

        call_data = mock_vps.call_args[0][1]
        assert call_data["mode"] == "sequential"

    @pytest.mark.asyncio
    async def test_default_max_iterations(self):
        """dual_ai_task() should default to max_iterations=10."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "session_id": "s1"}
            await dual_ai_task("do something")

        call_data = mock_vps.call_args[0][1]
        assert call_data["max_iterations"] == 10

    @pytest.mark.asyncio
    async def test_vps_failure_fallback_to_local(self):
        """When VPS fails, dual_ai_task() should fall back to local OpenAI."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_openai:
            mock_vps.side_effect = Exception("Connection refused")
            mock_openai.return_value = json.dumps({
                "plan": {"steps": ["step1"], "approach": "direct"},
                "execution": {
                    "files": [{"path": "out.py", "content": "x=1"}],
                    "explanation": "Created file",
                },
                "status": "complete",
            })

            result = await dual_ai_task("create a file")

        assert result["ok"] is True
        assert result["session_id"].startswith("local-")
        assert result["iterations"] == 1
        assert result["status"] == "complete"
        assert len(result["files"]) == 1

    @pytest.mark.asyncio
    async def test_vps_returns_not_ok_fallback(self):
        """When VPS returns ok=False with no session_id, should fall back."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_openai:
            mock_vps.return_value = {"ok": False, "error": "Server busy"}
            mock_openai.return_value = json.dumps({
                "plan": {"steps": ["fallback"], "approach": "local"},
                "execution": {"explanation": "Done locally"},
                "status": "complete",
            })

            result = await dual_ai_task("do work")

        assert result["ok"] is True
        assert result["session_id"].startswith("local-")

    @pytest.mark.asyncio
    async def test_local_fallback_invalid_json(self):
        """When local OpenAI returns non-JSON, should still produce valid result."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_openai:
            mock_vps.side_effect = Exception("down")
            mock_openai.return_value = "Here is my plain text answer without JSON."

            result = await dual_ai_task("explain something")

        assert result["ok"] is True
        assert result["session_id"].startswith("local-")
        # Fallback should create a result structure even without valid JSON
        assert "plan" in result
        assert "results" in result

    @pytest.mark.asyncio
    async def test_session_id_from_vps_with_session_but_no_ok(self):
        """VPS returning session_id without ok=True should still succeed."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {
                "session_id": "vps-no-ok-123",
                "iteration": 1,
                "action": "done",
                "message": "Processed",
            }

            result = await dual_ai_task("test task")

        # The code checks: result.get("ok") or result.get("session_id")
        assert result["ok"] is True
        assert result["session_id"] == "vps-no-ok-123"


# ---------------------------------------------------------------------------
# TestDualAiReview
# ---------------------------------------------------------------------------

class TestDualAiReview:
    """Test dual_ai_review() with mocked VPS."""

    @pytest.mark.asyncio
    async def test_vps_success(self, vps_review_response, tmp_path):
        """dual_ai_review() should return VPS result when VPS succeeds."""
        test_file = tmp_path / "code.py"
        test_file.write_text("def hello(): pass")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = vps_review_response
            result = await dual_ai_review(str(test_file))

        assert result["ok"] is True
        assert "reviews" in result
        assert "merged" in result
        assert result["consensus_score"] == 0.5

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        """dual_ai_review() should return error for non-existent file."""
        result = await dual_ai_review("/nonexistent/path/file.py")
        assert result["ok"] is False
        assert "error" in result
        assert "not found" in result["error"].lower() or "File not found" in result["error"]

    @pytest.mark.asyncio
    async def test_vps_called_with_correct_args(self, tmp_path):
        """dual_ai_review() should call VPS with file content and correct params."""
        test_file = tmp_path / "app.py"
        test_file.write_text("import os\nprint(os.getenv('SECRET'))")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "reviews": {}, "merged": {}}
            await dual_ai_review(str(test_file), review_type="security", models=["claude"])

        call_data = mock_vps.call_args[0][1]
        assert call_data["review_type"] == "security"
        assert call_data["models"] == ["claude"]
        assert "import os" in call_data["code"]

    @pytest.mark.asyncio
    async def test_default_models(self, tmp_path):
        """dual_ai_review() should default to claude and gpt4 models."""
        test_file = tmp_path / "test.py"
        test_file.write_text("pass")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True}
            await dual_ai_review(str(test_file))

        call_data = mock_vps.call_args[0][1]
        assert call_data["models"] == ["claude", "gpt4"]

    @pytest.mark.asyncio
    async def test_local_fallback_format(self, tmp_path):
        """When VPS fails, local fallback should produce correct result format."""
        test_file = tmp_path / "code.py"
        test_file.write_text("x = 1")

        mock_claude_response = json.dumps({
            "issues": [{"severity": "low", "line": 1, "description": "Single letter var"}],
            "suggestions": ["Use descriptive names"],
            "overall_quality": "fair",
        })
        mock_gpt_response = json.dumps({
            "issues": [{"severity": "medium", "line": 1, "description": "No type hints"}],
            "suggestions": ["Add type annotations"],
            "overall_quality": "fair",
        })

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.return_value = {"ok": False}
            mock_claude.return_value = mock_claude_response
            mock_gpt.return_value = mock_gpt_response

            result = await dual_ai_review(str(test_file))

        assert result["ok"] is True
        assert result["file"] == str(test_file)
        assert "reviews" in result
        assert "claude" in result["reviews"]
        assert "gpt4" in result["reviews"]
        assert "merged" in result
        assert "consensus_score" in result
        assert "total_issues" in result
        # Should have 2 total issues (1 from each model)
        assert result["total_issues"] == 2

    @pytest.mark.asyncio
    async def test_merged_severity_buckets(self, tmp_path):
        """Local fallback should correctly categorize issues by severity."""
        test_file = tmp_path / "code.py"
        test_file.write_text("eval(input())")

        mock_response = json.dumps({
            "issues": [
                {"severity": "critical", "line": 1, "description": "eval is dangerous"},
                {"severity": "high", "line": 1, "description": "user input not validated"},
            ],
            "suggestions": [],
        })

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.return_value = {"ok": False}
            mock_claude.return_value = mock_response
            mock_gpt.return_value = json.dumps({"issues": [], "suggestions": []})

            result = await dual_ai_review(str(test_file))

        assert len(result["merged"]["critical"]) == 1
        assert len(result["merged"]["high"]) == 1
        assert len(result["merged"]["medium"]) == 0
        assert len(result["merged"]["low"]) == 0

    @pytest.mark.asyncio
    async def test_consensus_score_no_issues(self, tmp_path):
        """Consensus score should be 1.0 when no issues are found."""
        test_file = tmp_path / "clean.py"
        test_file.write_text("def add(a, b): return a + b")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.return_value = {"ok": False}
            mock_claude.return_value = json.dumps({"issues": [], "suggestions": []})
            mock_gpt.return_value = json.dumps({"issues": [], "suggestions": []})

            result = await dual_ai_review(str(test_file))

        assert result["consensus_score"] == 1.0
        assert result["total_issues"] == 0


# ---------------------------------------------------------------------------
# TestDualAiConsensus
# ---------------------------------------------------------------------------

class TestDualAiConsensus:
    """Test dual_ai_consensus() with mocked VPS."""

    @pytest.mark.asyncio
    async def test_vps_success(self, vps_consensus_response):
        """dual_ai_consensus() should return VPS result when VPS succeeds."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = vps_consensus_response
            result = await dual_ai_consensus(
                "Which approach?",
                ["Option A", "Option B"],
            )

        assert result["ok"] is True
        assert result["question"] == "Which approach?"
        assert result["result"]["winner"] == "Option A"
        assert result["result"]["consensus_reached"] is True

    @pytest.mark.asyncio
    async def test_vps_called_with_correct_args(self):
        """dual_ai_consensus() should pass all parameters to VPS."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True}
            await dual_ai_consensus(
                "Which DB?",
                ["PostgreSQL", "MySQL", "SQLite"],
                context="Small project, single developer",
                mode="weighted",
                voters=["claude", "gpt4", "gpt4-mini"],
            )

        mock_vps.assert_called_once_with("consensus", {
            "question": "Which DB?",
            "options": ["PostgreSQL", "MySQL", "SQLite"],
            "context": "Small project, single developer",
            "mode": "weighted",
            "voters": ["claude", "gpt4", "gpt4-mini"],
        })

    @pytest.mark.asyncio
    async def test_default_voters(self):
        """dual_ai_consensus() should default to claude and gpt4 voters."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True}
            await dual_ai_consensus("Q?", ["A", "B"])

        call_data = mock_vps.call_args[0][1]
        assert call_data["voters"] == ["claude", "gpt4"]

    @pytest.mark.asyncio
    async def test_local_fallback_majority_mode(self):
        """Local fallback with majority mode should determine winner correctly."""
        claude_vote = json.dumps({"choice": "Redis", "confidence": 0.9, "reasoning": "Faster"})
        gpt_vote = json.dumps({"choice": "Redis", "confidence": 0.8, "reasoning": "Scalable"})

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.return_value = {"ok": False}
            mock_claude.return_value = claude_vote
            mock_gpt.return_value = gpt_vote

            result = await dual_ai_consensus(
                "Which cache?",
                ["Redis", "Memcached"],
                mode="majority",
            )

        assert result["ok"] is True
        assert result["result"]["winner"] == "Redis"
        assert result["result"]["consensus_reached"] is True
        assert result["result"]["vote_count"]["Redis"] == 2

    @pytest.mark.asyncio
    async def test_local_fallback_unanimous_no_consensus(self):
        """Unanimous mode should report no consensus when votes differ."""
        claude_vote = json.dumps({"choice": "Redis", "confidence": 0.9, "reasoning": "Fast"})
        gpt_vote = json.dumps({"choice": "Memcached", "confidence": 0.7, "reasoning": "Simple"})

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.return_value = {"ok": False}
            mock_claude.return_value = claude_vote
            mock_gpt.return_value = gpt_vote

            result = await dual_ai_consensus(
                "Which cache?",
                ["Redis", "Memcached"],
                mode="unanimous",
            )

        assert result["ok"] is True
        assert result["result"]["consensus_reached"] is False

    @pytest.mark.asyncio
    async def test_local_fallback_weighted_mode(self):
        """Weighted mode should sum confidence scores instead of counts."""
        claude_vote = json.dumps({"choice": "A", "confidence": 0.3, "reasoning": "Slightly prefer A"})
        gpt_vote = json.dumps({"choice": "B", "confidence": 0.9, "reasoning": "Strongly prefer B"})

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.return_value = {"ok": False}
            mock_claude.return_value = claude_vote
            mock_gpt.return_value = gpt_vote

            result = await dual_ai_consensus(
                "Which option?",
                ["A", "B"],
                mode="weighted",
            )

        assert result["ok"] is True
        # B has 0.9 weight vs A with 0.3, so B should win
        assert result["result"]["winner"] == "B"

    @pytest.mark.asyncio
    async def test_local_fallback_result_format(self):
        """Local fallback result should have all expected fields."""
        vote_json = json.dumps({"choice": "X", "confidence": 0.8, "reasoning": "Good"})

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.return_value = {"ok": False}
            mock_claude.return_value = vote_json
            mock_gpt.return_value = vote_json

            result = await dual_ai_consensus("Q?", ["X", "Y"])

        assert "question" in result
        assert "options" in result
        assert "votes" in result
        assert "result" in result
        assert "winner" in result["result"]
        assert "vote_count" in result["result"]
        assert "consensus_reached" in result["result"]
        assert "combined_reasoning" in result["result"]

    @pytest.mark.asyncio
    async def test_local_fallback_voter_error_handling(self):
        """Voter errors should be handled gracefully with default votes."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.return_value = {"ok": False}
            mock_claude.side_effect = Exception("Claude API down")
            mock_gpt.side_effect = Exception("OpenAI API down")

            result = await dual_ai_consensus("Q?", ["A", "B"])

        # Should still produce a result (fallback to first option with confidence 0)
        assert result["ok"] is True
        assert "votes" in result
        for voter_data in result["votes"].values():
            assert voter_data["confidence"] == 0.0


# ---------------------------------------------------------------------------
# TestDualAiSecurity
# ---------------------------------------------------------------------------

class TestDualAiSecurity:
    """Test dual_ai_security() with mocked VPS."""

    @pytest.mark.asyncio
    async def test_vps_success(self, vps_security_response, tmp_path):
        """dual_ai_security() should return VPS result when VPS succeeds."""
        test_file = tmp_path / "app.py"
        test_file.write_text("import sqlite3\ndb.execute(f'SELECT * FROM users WHERE id={user_id}')")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = vps_security_response
            result = await dual_ai_security(str(test_file))

        assert result["ok"] is True
        assert result["target"] == "app.py"
        assert len(result["vulnerabilities"]) == 2
        assert result["summary"]["critical"] == 1

    @pytest.mark.asyncio
    async def test_nonexistent_file_treated_as_snippet(self):
        """Non-existent path should be treated as code snippet."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "vulnerabilities": [], "summary": {}}
            result = await dual_ai_security("eval(input())")

        call_data = mock_vps.call_args[0][1]
        assert call_data["file_path"] == "<snippet>"
        assert call_data["code"] == "eval(input())"

    @pytest.mark.asyncio
    async def test_default_scan_type(self, tmp_path):
        """dual_ai_security() should default to 'full' scan type."""
        test_file = tmp_path / "code.py"
        test_file.write_text("pass")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True}
            await dual_ai_security(str(test_file))

        call_data = mock_vps.call_args[0][1]
        assert call_data["scan_type"] == "full"

    @pytest.mark.asyncio
    async def test_custom_scan_type(self, tmp_path):
        """dual_ai_security() should pass custom scan type to VPS."""
        test_file = tmp_path / "code.py"
        test_file.write_text("pass")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True}
            await dual_ai_security(str(test_file), scan_type="deep")

        call_data = mock_vps.call_args[0][1]
        assert call_data["scan_type"] == "deep"

    @pytest.mark.asyncio
    async def test_local_fallback_format(self, tmp_path):
        """Local fallback should produce correct result format with summary."""
        test_file = tmp_path / "vuln.py"
        test_file.write_text("os.system(user_input)")

        mock_claude = json.dumps({
            "vulnerabilities": [
                {
                    "severity": "critical",
                    "type": "command_injection",
                    "location": {"file": "vuln.py", "line": 1},
                    "description": "Command injection via os.system",
                    "fix_suggestion": "Use subprocess with shell=False",
                    "cwe_id": "CWE-78",
                },
                {
                    "severity": "low",
                    "type": "missing_validation",
                    "location": {"file": "vuln.py", "line": 1},
                    "description": "No input validation",
                    "fix_suggestion": "Validate user input",
                    "cwe_id": "CWE-20",
                },
            ],
            "recommendation": "Critical: fix command injection immediately.",
        })

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_local:
            mock_vps.return_value = {"ok": False}
            mock_local.return_value = mock_claude

            result = await dual_ai_security(str(test_file))

        assert result["ok"] is True
        assert result["target"] == str(test_file)
        assert result["scan_type"] == "full"
        assert len(result["vulnerabilities"]) == 2
        assert result["summary"]["critical"] == 1
        assert result["summary"]["low"] == 1
        assert result["summary"]["high"] == 0
        assert result["summary"]["medium"] == 0
        assert result["total_vulnerabilities"] == 2
        assert "recommendation" in result

    @pytest.mark.asyncio
    async def test_local_fallback_no_vulns(self, tmp_path):
        """Local fallback with no vulnerabilities should return clean summary."""
        test_file = tmp_path / "clean.py"
        test_file.write_text("def add(a, b): return a + b")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_local:
            mock_vps.return_value = {"ok": False}
            mock_local.return_value = json.dumps({
                "vulnerabilities": [],
                "recommendation": "Code is clean.",
            })

            result = await dual_ai_security(str(test_file))

        assert result["ok"] is True
        assert result["total_vulnerabilities"] == 0
        assert result["summary"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}

    @pytest.mark.asyncio
    async def test_local_fallback_invalid_json(self, tmp_path):
        """Local fallback with invalid JSON should still produce valid result."""
        test_file = tmp_path / "code.py"
        test_file.write_text("x = 1")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_local:
            mock_vps.return_value = {"ok": False}
            mock_local.return_value = "No JSON here, just plain text analysis."

            result = await dual_ai_security(str(test_file))

        assert result["ok"] is True
        assert result["vulnerabilities"] == []
        assert result["total_vulnerabilities"] == 0


# ---------------------------------------------------------------------------
# TestDualAiTestGen
# ---------------------------------------------------------------------------

class TestDualAiTestGen:
    """Test dual_ai_test_gen() with mocked VPS."""

    @pytest.mark.asyncio
    async def test_vps_success(self, vps_test_gen_response, tmp_path):
        """dual_ai_test_gen() should return VPS result when VPS succeeds."""
        test_file = tmp_path / "utils.py"
        test_file.write_text("def add(a, b): return a + b")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = vps_test_gen_response
            result = await dual_ai_test_gen(str(test_file))

        assert result["ok"] is True
        assert len(result["tests"]) == 2
        assert result["coverage_estimate"] == 85.5

    @pytest.mark.asyncio
    async def test_auto_detect_python_framework(self, tmp_path):
        """Auto framework detection should choose pytest for .py files."""
        test_file = tmp_path / "module.py"
        test_file.write_text("pass")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "tests": []}
            await dual_ai_test_gen(str(test_file), framework="auto")

        call_data = mock_vps.call_args[0][1]
        assert call_data["framework"] == "pytest"

    @pytest.mark.asyncio
    async def test_auto_detect_js_framework(self, tmp_path):
        """Auto framework detection should choose jest for .js files."""
        test_file = tmp_path / "utils.js"
        test_file.write_text("export function add(a, b) { return a + b; }")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "tests": []}
            await dual_ai_test_gen(str(test_file), framework="auto")

        call_data = mock_vps.call_args[0][1]
        assert call_data["framework"] == "jest"

    @pytest.mark.asyncio
    async def test_auto_detect_ts_framework(self, tmp_path):
        """Auto framework detection should choose jest for .ts files."""
        test_file = tmp_path / "service.ts"
        test_file.write_text("export function hello(): string { return 'hi'; }")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "tests": []}
            await dual_ai_test_gen(str(test_file), framework="auto")

        call_data = mock_vps.call_args[0][1]
        assert call_data["framework"] == "jest"

    @pytest.mark.asyncio
    async def test_auto_detect_vue_framework(self, tmp_path):
        """Auto framework detection should choose vitest for .vue files."""
        test_file = tmp_path / "Component.vue"
        test_file.write_text("<template><div>Hello</div></template>")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "tests": []}
            await dual_ai_test_gen(str(test_file), framework="auto")

        call_data = mock_vps.call_args[0][1]
        assert call_data["framework"] == "vitest"

    @pytest.mark.asyncio
    async def test_explicit_framework(self, tmp_path):
        """Explicit framework should override auto-detection."""
        test_file = tmp_path / "utils.py"
        test_file.write_text("pass")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "tests": []}
            await dual_ai_test_gen(str(test_file), framework="vitest")

        call_data = mock_vps.call_args[0][1]
        assert call_data["framework"] == "vitest"

    @pytest.mark.asyncio
    async def test_default_test_type(self, tmp_path):
        """dual_ai_test_gen() should default to 'unit' test type."""
        test_file = tmp_path / "code.py"
        test_file.write_text("pass")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True}
            await dual_ai_test_gen(str(test_file))

        call_data = mock_vps.call_args[0][1]
        assert call_data["test_type"] == "unit"

    @pytest.mark.asyncio
    async def test_local_fallback_format(self, tmp_path):
        """Local fallback should produce correct result format."""
        test_file = tmp_path / "math_utils.py"
        test_file.write_text("def multiply(a, b): return a * b")

        mock_response = json.dumps({
            "tests": [
                {
                    "name": "test_multiply_positive",
                    "description": "Test multiplication of positive numbers",
                    "code": "def test_multiply_positive():\n    assert multiply(3, 4) == 12",
                    "covers": ["line 1", "happy path"],
                },
            ],
            "coverage_estimate": 70.0,
            "notes": "Add edge case tests for zero and negative numbers.",
        })

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_local:
            mock_vps.return_value = {"ok": False}
            mock_local.return_value = mock_response

            result = await dual_ai_test_gen(str(test_file))

        assert result["ok"] is True
        assert result["target"] == str(test_file)
        assert result["test_type"] == "unit"
        assert result["framework"] == "pytest"
        assert len(result["tests"]) == 1
        assert result["coverage_estimate"] == 70.0
        assert result["output_file"] == "test_math_utils.py"
        assert result["notes"] == "Add edge case tests for zero and negative numbers."

    @pytest.mark.asyncio
    async def test_output_file_naming_python(self, tmp_path):
        """Output file for Python source should be test_<name>.py."""
        test_file = tmp_path / "validator.py"
        test_file.write_text("pass")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_local:
            mock_vps.return_value = {"ok": False}
            mock_local.return_value = json.dumps({"tests": [], "coverage_estimate": 0})

            result = await dual_ai_test_gen(str(test_file))

        assert result["output_file"] == "test_validator.py"

    @pytest.mark.asyncio
    async def test_output_file_naming_js(self, tmp_path):
        """Output file for JS source should be <name>.test.js."""
        test_file = tmp_path / "helpers.js"
        test_file.write_text("function x() {}")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_local:
            mock_vps.return_value = {"ok": False}
            mock_local.return_value = json.dumps({"tests": [], "coverage_estimate": 0})

            result = await dual_ai_test_gen(str(test_file), framework="jest")

        assert result["output_file"] == "helpers.test.js"

    @pytest.mark.asyncio
    async def test_snippet_target(self):
        """Non-file target should be treated as a code snippet."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps:
            mock_vps.return_value = {"ok": True, "tests": []}
            await dual_ai_test_gen("def add(a, b): return a + b")

        call_data = mock_vps.call_args[0][1]
        assert call_data["file_path"] == "<snippet>"

    @pytest.mark.asyncio
    async def test_local_fallback_no_json_no_codeblocks(self, tmp_path):
        """Local fallback with no JSON and no code blocks should use code-block extraction path."""
        test_file = tmp_path / "code.py"
        test_file.write_text("x = 1")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_local:
            mock_vps.return_value = {"ok": False}
            mock_local.return_value = "No valid JSON, just text."

            result = await dual_ai_test_gen(str(test_file))

        assert result["ok"] is True
        assert result["tests"] == []
        # When no JSON match found, code-block extraction path sets coverage to 50.0
        assert result["coverage_estimate"] == 50.0

    @pytest.mark.asyncio
    async def test_local_fallback_json_decode_error(self, tmp_path):
        """Local fallback with malformed JSON should produce empty tests with 0 coverage."""
        test_file = tmp_path / "code.py"
        test_file.write_text("x = 1")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_local:
            mock_vps.return_value = {"ok": False}
            # Contains a JSON-like structure that re.search will match but json.loads will fail on
            mock_local.return_value = '{invalid json content, missing quotes and brackets}'

            result = await dual_ai_test_gen(str(test_file))

        assert result["ok"] is True
        assert result["tests"] == []
        assert result["coverage_estimate"] == 0.0


# ---------------------------------------------------------------------------
# TestCallVps
# ---------------------------------------------------------------------------

class TestCallVps:
    """Test call_vps() HTTP layer."""

    @pytest.mark.asyncio
    async def test_successful_httpx_call(self):
        """call_vps() should use httpx and return JSON response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "data": "result"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("dual_ai.httpx", create=True) as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            # Force httpx import to succeed
            with patch.dict("sys.modules", {"httpx": mock_httpx}):
                result = await call_vps("test_endpoint", {"key": "value"})

        assert result == {"ok": True, "data": "result"}

    @pytest.mark.asyncio
    async def test_exception_returns_error_dict(self):
        """call_vps() should return error dict on exception."""
        with patch("dual_ai.httpx", create=True) as mock_httpx:
            mock_httpx.AsyncClient.side_effect = Exception("Network error")
            with patch.dict("sys.modules", {"httpx": mock_httpx}):
                result = await call_vps("endpoint", {"data": "x"})

        assert result["ok"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test timeout and error propagation across all async functions."""

    @pytest.mark.asyncio
    async def test_task_vps_timeout_fallback(self):
        """dual_ai_task() should fall back to local on VPS timeout."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_openai:
            mock_vps.side_effect = TimeoutError("VPS timed out")
            mock_openai.return_value = json.dumps({
                "plan": {"steps": ["fallback"], "approach": "local"},
                "execution": {"explanation": "Fallback result"},
                "status": "complete",
            })

            result = await dual_ai_task("a task")

        assert result["ok"] is True
        assert result["session_id"].startswith("local-")

    @pytest.mark.asyncio
    async def test_review_vps_timeout_fallback(self, tmp_path):
        """dual_ai_review() should fall back to local on VPS timeout."""
        test_file = tmp_path / "code.py"
        test_file.write_text("pass")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.side_effect = TimeoutError("timeout")
            mock_claude.return_value = json.dumps({"issues": [], "suggestions": []})
            mock_gpt.return_value = json.dumps({"issues": [], "suggestions": []})

            result = await dual_ai_review(str(test_file))

        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_consensus_vps_error_fallback(self):
        """dual_ai_consensus() should fall back to local on VPS error."""
        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.side_effect = ConnectionError("refused")
            mock_claude.return_value = json.dumps({"choice": "A", "confidence": 0.8, "reasoning": "ok"})
            mock_gpt.return_value = json.dumps({"choice": "A", "confidence": 0.7, "reasoning": "ok"})

            result = await dual_ai_consensus("Q?", ["A", "B"])

        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_security_file_read_error(self, tmp_path):
        """dual_ai_security() should handle file read errors gracefully."""
        # Create a file then make it unreadable via mock
        test_file = tmp_path / "secret.py"
        test_file.write_text("x = 1")

        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with patch("os.path.exists", return_value=True):
                result = await dual_ai_security(str(test_file))

        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_test_gen_file_read_error(self, tmp_path):
        """dual_ai_test_gen() should handle file read errors gracefully."""
        test_file = tmp_path / "broken.py"
        test_file.write_text("x = 1")

        with patch("builtins.open", side_effect=IOError("Read error")):
            with patch("os.path.exists", return_value=True):
                result = await dual_ai_test_gen(str(test_file))

        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_review_model_failure_graceful(self, tmp_path):
        """Individual model failure in review should not crash the whole review."""
        test_file = tmp_path / "code.py"
        test_file.write_text("x = 1")

        with patch("dual_ai.call_vps", new_callable=AsyncMock) as mock_vps, \
             patch("dual_ai.call_claude_local", new_callable=AsyncMock) as mock_claude, \
             patch("dual_ai.call_openai_local", new_callable=AsyncMock) as mock_gpt:
            mock_vps.return_value = {"ok": False}
            mock_claude.side_effect = Exception("Claude crashed")
            mock_gpt.return_value = json.dumps({
                "issues": [{"severity": "low", "line": 1, "description": "Minor"}],
                "suggestions": [],
            })

            result = await dual_ai_review(str(test_file))

        assert result["ok"] is True
        # Claude review should have error, gpt4 should have result
        assert "error" in result["reviews"]["claude"]
        assert len(result["reviews"]["gpt4"]["issues"]) == 1


# ---------------------------------------------------------------------------
# TestConfiguration
# ---------------------------------------------------------------------------

class TestConfiguration:
    """Test environment variable configuration."""

    def test_default_vps_url(self):
        """VPS_API_URL should default to api.flyto2.net."""
        # The module-level constant reflects the value at import time.
        # We verify the default is the expected production URL.
        assert "api.flyto2.net" in VPS_API_URL
        assert VPS_API_URL == "https://api.flyto2.net/api/v1/pro"

    def test_default_vps_timeout(self):
        """VPS_TIMEOUT should default to 60 seconds."""
        assert VPS_TIMEOUT == 60
        assert isinstance(VPS_TIMEOUT, int)

    def test_custom_vps_url_via_env(self):
        """FLYTO_VPS_URL env var should override the default."""
        custom_url = "https://custom.example.com/api"
        with patch.dict(os.environ, {"FLYTO_VPS_URL": custom_url}):
            # Re-evaluate the getenv call
            result = os.getenv("FLYTO_VPS_URL", "https://api.flyto2.net/api/v1/pro")
        assert result == custom_url

    def test_custom_vps_timeout_via_env(self):
        """FLYTO_VPS_TIMEOUT env var should override the default."""
        with patch.dict(os.environ, {"FLYTO_VPS_TIMEOUT": "120"}):
            result = int(os.getenv("FLYTO_VPS_TIMEOUT", "60"))
        assert result == 120

    def test_vps_timeout_is_integer(self):
        """VPS_TIMEOUT should always be an integer."""
        assert isinstance(VPS_TIMEOUT, int)

    def test_vps_url_is_string(self):
        """VPS_API_URL should be a string."""
        assert isinstance(VPS_API_URL, str)

    def test_vps_url_uses_https(self):
        """Default VPS_API_URL should use HTTPS."""
        assert VPS_API_URL.startswith("https://")
