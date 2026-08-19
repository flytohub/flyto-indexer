"""Tests for the security research priority ranking."""

import subprocess
import textwrap
from pathlib import Path

import pytest

from src.analyzer.research_priority import (
    DEFAULT_WEIGHTS,
    EVIDENCE_REACHABILITY,
    PROXIMITY_LINES,
    ResearchCandidate,
    _has_dynamic_sql,
    _is_attack_surface,
    _is_hidden_path,
    _looks_like_sql,
    _normalize,
    _score_candidate,
    _sink_present,
    rank_research_priority,
)


def _write(root: Path, name: str, code: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(code))
    return path


@pytest.fixture
def project(tmp_path):
    return tmp_path


# ── Proven flows ────────────────────────────────────────────────────────────


class TestProvenFlows:
    def test_in_function_flow_is_ranked_and_labelled_proven(self, project):
        _write(project, "app.py", """\
            from flask import request
            import os

            def handler():
                name = request.args.get("name")
                os.system("echo " + name)
        """)
        report = rank_research_priority(project)

        assert report.candidates, "an in-function source->sink flow must rank"
        top = report.candidates[0]
        assert top.file == "app.py"
        assert top.function == "handler"
        assert top.proven is True
        assert top.evidence == "proven_flow_in_function"
        assert top.category == "rce"

    def test_proven_flow_outranks_unproven_lead(self, project):
        _write(project, "proven.py", """\
            from flask import request
            import os

            def proven_handler():
                name = request.args.get("name")
                os.system("echo " + name)
        """)
        _write(project, "unproven.py", """\
            from flask import request
            import os

            def read_input():
                return request.args.get("name")

            def helper(target):
                os.system("echo " + target)
        """)
        report = rank_research_priority(project)
        by_function = {c.function: c for c in report.candidates}

        assert by_function["proven_handler"].score > by_function["helper"].score
        assert by_function["helper"].proven is False


# ── Unproven tiers ──────────────────────────────────────────────────────────


class TestUnprovenTiers:
    def test_sink_near_the_source_is_returned_but_marked_unproven(self, project):
        _write(project, "app.py", """\
            from flask import request
            import subprocess

            def read_input():
                return request.args.get("host")

            def ping(target):
                subprocess.run("ping " + target, shell=True)
        """)
        report = rank_research_priority(project)
        lead = next(c for c in report.candidates if c.function == "ping")

        assert lead.proven is False
        assert lead.evidence == "sink_with_nearby_source"
        assert any("NOT proven" in reason for reason in lead.reasons)

    def test_a_distant_source_in_a_big_file_ranks_below_a_near_one(self, project):
        """One request read at the top of a 1000-line file must not make every
        call in it a lead of equal weight."""
        head = (
            "from flask import request\n"
            "import subprocess\n"
            "\n"
            "def read_input():\n"
            "    return request.args.get('host')\n"
        )
        sink = (
            "def ping(target):\n"
            "    subprocess.run('ping ' + target, shell=True)\n"
        )
        (project / "near.py").write_text(head + "\n" + sink)
        padding = "".join(f"# padding {i}\n" for i in range(400))
        (project / "far.py").write_text(
            head + padding + sink.replace("def ping(", "def ping_far(")
        )

        report = rank_research_priority(project)
        by_function = {c.function: c for c in report.candidates}

        assert by_function["ping"].evidence == "sink_with_nearby_source"
        assert by_function["ping_far"].evidence == "sink_with_file_source"
        assert by_function["ping_far"].source_distance > PROXIMITY_LINES
        assert by_function["ping"].score > by_function["ping_far"].score

    def test_include_unproven_false_returns_only_proven(self, project):
        _write(project, "app.py", """\
            from flask import request
            import subprocess

            def read_input():
                return request.args.get("host")

            def ping(target):
                subprocess.run("ping " + target, shell=True)
        """)
        report = rank_research_priority(project, include_unproven=False)

        assert report.candidates == []
        assert "no source-to-sink flow" in report.coverage["truncation_note"]

    def test_sink_with_no_input_anywhere_is_not_a_lead(self, project):
        _write(project, "app.py", """\
            import subprocess

            def deploy():
                subprocess.run("systemctl restart app", shell=True)
        """)
        report = rank_research_priority(project)

        assert report.candidates == []

    def test_one_candidate_per_function(self, project):
        _write(project, "app.py", """\
            from flask import request
            import os

            def handler():
                a = request.args.get("a")
                b = request.args.get("b")
                os.system("echo " + a)
                os.system("echo " + b)
        """)
        report = rank_research_priority(project)

        assert len(report.candidates) == 1
        assert report.candidates[0].flow_count >= 2


# ── Parameterized SQL suppression ───────────────────────────────────────────


class TestParameterizedSqlGate:
    def test_orm_query_is_not_a_sql_lead(self, project):
        _write(project, "api.py", """\
            from fastapi import Query

            async def list_logs(limit: int = Query(100), db=None):
                query = select(ProcessLog).where(ProcessLog.id == limit)
                result = await db.execute(query)
                return result.scalars().all()
        """)
        report = rank_research_priority(project)

        assert not any(c.category == "sql_injection" for c in report.candidates)

    def test_fstring_sql_is_a_lead(self, project):
        _write(project, "api.py", """\
            from fastapi import Query

            async def list_logs(name: str = Query("x"), db=None):
                await db.execute(f"SELECT * FROM logs WHERE name = '{name}'")
        """)
        report = rank_research_priority(project)

        assert any(c.category == "sql_injection" for c in report.candidates)

    def test_prose_containing_from_is_not_dynamic_sql(self):
        import ast

        tree = ast.parse(textwrap.dedent("""\
            def transition(state, event, db):
                if not ok:
                    raise ValueError(f"Cannot transition from {state} on {event}")
                return db.execute(select(X).where(X.id == state))
        """))
        func = tree.body[0]

        assert _has_dynamic_sql(func) is False

    @pytest.mark.parametrize("text,expected", [
        ("SELECT * FROM users", True),
        ("insert into users values", True),
        ("delete from users", True),
        ("Cannot transition from pending", False),
        ("data received from upstream", False),
    ])
    def test_looks_like_sql(self, text, expected):
        assert _looks_like_sql(text) is expected


# ── Honesty guarantees ──────────────────────────────────────────────────────


class TestUnavailableSignalsAreNotZero:
    def test_missing_signals_are_named_not_scored(self, project):
        _write(project, "app.py", """\
            from flask import request
            import os

            def handler():
                name = request.args.get("name")
                os.system("echo " + name)
        """)
        report = rank_research_priority(project)
        top = report.candidates[0]

        # No git repo, no index in a bare tmp dir.
        assert top.signals["churn"] is None
        assert top.signals["test_gap"] is None
        assert top.signals["entry_exposure"] is None
        unavailable = " ".join(report.coverage["signals_unavailable"])
        assert "git_churn" in unavailable
        assert "test_gap" in unavailable
        assert "entry_exposure" in unavailable

    def test_unmeasured_signal_does_not_drag_the_score_down(self):
        """Renormalization, not zero-filling: the same evidence must score the
        same whether or not the repo happens to have git history."""
        signals = {"reachability": 1.0, "sink_severity": 1.0}
        with_git = ResearchCandidate(
            file="a.py", function="f", line=1, category="rce", severity="critical",
            signals={**signals, "churn": None, "test_gap": None},
        )
        without = ResearchCandidate(
            file="a.py", function="f", line=1, category="rce", severity="critical",
            signals=dict(signals),
        )

        assert _score_candidate(with_git, DEFAULT_WEIGHTS) == pytest.approx(100.0)
        assert _score_candidate(without, DEFAULT_WEIGHTS) == pytest.approx(100.0)

    def test_empty_result_reports_why(self, project):
        _write(project, "safe.py", """\
            def add(a, b):
                return a + b
        """)
        report = rank_research_priority(project)

        assert report.candidates == []
        assert report.coverage["signals_unavailable"]
        assert report.coverage["truncated"] is False

    def test_every_candidate_carries_reasons(self, project):
        _write(project, "app.py", """\
            from flask import request
            import os

            def handler():
                name = request.args.get("name")
                os.system("echo " + name)
        """)
        report = rank_research_priority(project)

        for candidate in report.candidates:
            assert candidate.reasons
            assert candidate.evidence in EVIDENCE_REACHABILITY


# ── Path handling ───────────────────────────────────────────────────────────


class TestPathHandling:
    def test_normalize_keeps_leading_dot_directory(self):
        assert _normalize("./src/app.py") == "src/app.py"
        assert _normalize(".claude/worktrees/x/app.py") == ".claude/worktrees/x/app.py"

    def test_hidden_directories_are_skipped(self):
        assert _is_hidden_path(".claude/worktrees/x/app.py") is True
        assert _is_hidden_path("src/app.py") is False
        assert _is_hidden_path(".hidden.py") is False  # a file, not a directory

    def test_worktree_copies_do_not_produce_duplicate_leads(self, project):
        code = """\
            from flask import request
            import subprocess

            def read_input():
                return request.args.get("host")

            def ping(target):
                subprocess.run("ping " + target, shell=True)
        """
        _write(project, "app.py", code)
        _write(project, ".claude/worktrees/copy/app.py", code)
        report = rank_research_priority(project)

        files = [c.file for c in report.candidates]
        assert files == ["app.py"]


# ── Ordering ────────────────────────────────────────────────────────────────


class TestOrdering:
    def test_churn_breaks_ties_between_equal_evidence(self, project):
        code = """\
            from flask import request
            import subprocess

            def read_input():
                return request.args.get("host")

            def ping(target):
                subprocess.run("ping " + target, shell=True)
        """
        _write(project, "hot.py", code)
        _write(project, "cold.py", code)

        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
        subprocess.run(["git", "add", "."], cwd=project, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=project, check=True)
        for i in range(3):
            (project / "hot.py").write_text(
                (project / "hot.py").read_text() + f"\n# touch {i}\n"
            )
            subprocess.run(["git", "add", "hot.py"], cwd=project, check=True)
            subprocess.run(["git", "commit", "-qm", f"edit {i}"], cwd=project, check=True)

        report = rank_research_priority(project)
        scores = {c.file: c.score for c in report.candidates}

        assert scores["hot.py"] > scores["cold.py"]

    def test_top_n_is_capped(self, project):
        _write(project, "app.py", """\
            from flask import request
            import os

            def handler():
                name = request.args.get("name")
                os.system("echo " + name)
        """)
        report = rank_research_priority(project, top_n=0)

        assert len(report.candidates) <= 1


# ── Tool adapter ────────────────────────────────────────────────────────────


class TestToolAdapter:
    def test_unknown_project_is_an_explicit_error(self):
        from src.tools.research_priority import research_priority

        result = research_priority(project="definitely-not-an-indexed-project")

        assert "error" in result
        assert "available_projects" in result

    def test_registered_in_dispatch_and_schema(self):
        from src.tool_registry import MCP_TOOLS, has_tool

        assert has_tool("research_priority")
        names = {tool["name"] for tool in MCP_TOOLS}
        assert "research_priority" in names

    def test_reachable_from_the_audit_tool_without_a_new_tool(self):
        from src.tool_registry import SMART_TOOLS

        audit = next(tool for tool in SMART_TOOLS if tool["name"] == "audit")
        focus = audit["inputSchema"]["properties"]["focus"]["enum"]

        assert "research_priority" in focus
        assert len(SMART_TOOLS) == 20


# ── Precision fixes found by scanning a real project ────────────────────────


class TestSinkTokenBoundary:
    """`exec(` must not match `create_subprocess_exec(`."""

    @pytest.mark.parametrize("text,pattern,expected", [
        ("os.system(cmd)", "os.system(", True),
        ("await asyncio.create_subprocess_exec(*cmd)", "exec(", False),
        ("exec(payload)", "exec(", True),
        ("types.ResourceTemplate(uri)", "Template(", False),
        ("Template(source).render(x)", "Template(", True),
        ("el.innerHTML = x", ".innerHTML", True),
    ])
    def test_boundary(self, text, pattern, expected):
        assert _sink_present(text, pattern) is expected

    def test_subprocess_exec_is_not_an_rce_lead(self, project):
        _write(project, "media.py", """\
            import asyncio
            from flask import request

            def read_input():
                return request.args.get("f")

            async def convert(path):
                await asyncio.create_subprocess_exec("ffmpeg", "-i", path)
        """)
        report = rank_research_priority(project)

        assert not [c for c in report.candidates if c.category == "rce"]


class TestAttackSurfaceScoping:
    @pytest.mark.parametrize("path,expected", [
        ("gradio/routes.py", True),
        ("demo/gif_maker/run.py", False),
        ("examples/basic/app.py", False),
        ("scripts/profile/analyze.py", False),
        ("docs/conf.py", False),
        ("src/app/handlers.py", True),
    ])
    def test_is_attack_surface(self, path, expected):
        assert _is_attack_surface(path) is expected

    def test_demo_code_does_not_crowd_out_library_code(self, project):
        lead = """\
            from flask import request
            import subprocess

            def read_input():
                return request.args.get("host")

            def ping(target):
                subprocess.run("ping " + target, shell=True)
        """
        _write(project, "app/service.py", lead)
        for i in range(5):
            _write(project, f"demo/sample{i}/run.py", lead)
        report = rank_research_priority(project)

        assert [c.file for c in report.candidates] == ["app/service.py"]


class TestOperatorInputTier:
    def test_argv_fed_sink_is_labelled_and_outranked(self, project):
        _write(project, "web.py", """\
            from flask import request
            import subprocess

            def read_input():
                return request.args.get("host")

            def ping(target):
                subprocess.run("ping " + target, shell=True)
        """)
        _write(project, "cli.py", """\
            import subprocess
            import sys

            def read_args():
                return sys.argv[1]

            def deploy(target):
                subprocess.run("deploy " + target, shell=True)
        """)
        report = rank_research_priority(project)
        by_file = {c.file: c for c in report.candidates}

        assert by_file["cli.py"].evidence == "operator_input_and_sink"
        assert any("not a remote request" in r for r in by_file["cli.py"].reasons)
        assert by_file["web.py"].score > by_file["cli.py"].score


class TestJavaScriptSinksInPython:
    def test_innerhtml_in_a_python_string_is_not_an_xss_lead(self, project):
        _write(project, "page.py", """\
            from flask import request

            def read_input():
                return request.args.get("q")

            def render(value):
                return f"<script>el.innerHTML = '{value}'</script>"
        """)
        report = rank_research_priority(project)

        assert not [c for c in report.candidates if c.category == "xss"]


class TestProvenFlowDemotion:
    def test_proven_flow_in_demo_code_is_demoted_not_dropped(self, project):
        lead = """\
            from flask import request
            import os

            def h():
                c = request.args.get("c")
                os.system(c)
        """
        _write(project, "app/svc.py", lead)
        _write(project, "demo/run.py", lead)
        report = rank_research_priority(project)
        by_file = {c.file: c for c in report.candidates}

        # both proven and present...
        assert "app/svc.py" in by_file
        assert "demo/run.py" in by_file
        # ...but library code outranks the demo copy
        assert by_file["app/svc.py"].score > by_file["demo/run.py"].score
        assert any("demo/example" in r for r in by_file["demo/run.py"].reasons)

    def test_operator_sourced_proven_flow_ranks_below_remote(self, project):
        _write(project, "web.py", """\
            from flask import request
            import os

            def h():
                c = request.args.get("c")
                os.system(c)
        """)
        _write(project, "tool.py", """\
            import os
            import sys

            def main():
                os.system("run " + sys.argv[1])
        """)
        report = rank_research_priority(project)
        by_file = {c.file: c for c in report.candidates}

        assert by_file["web.py"].score > by_file["tool.py"].score
        assert any("operator input" in r for r in by_file["tool.py"].reasons)
