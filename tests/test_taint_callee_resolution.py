"""Tests for type-aware callee resolution in the cross-function taint pass.

The pass used to match callees by name, so `run(...)` flows were attributed to
any call whose name merely contained it, and two same-named functions in
different modules were one function. These tests pin the tightened matching and
the three-state verifier contract (True / False / None), including the case
that matters most on real machines: no language server installed, where
verification must return None and leave the name-based result standing.
"""

import ast
import textwrap
from pathlib import Path

import pytest

from src.analyzer.taint import TaintAnalyzer
from src.analyzer.taint_lsp import MAX_LSP_CHECKS, CalleeVerifier, _call_position


def _first_call(code: str) -> ast.Call:
    tree = ast.parse(textwrap.dedent(code))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            return node
    raise AssertionError("no call in fixture")


# ── Call-site positions ─────────────────────────────────────────────────────


class TestCallPosition:
    def test_plain_name_points_at_the_name(self):
        call = _first_call("handler(value)")
        assert _call_position(call) == (0, 0)

    def test_attribute_points_at_the_attribute_not_the_receiver(self):
        call = _first_call("db.execute(value)")
        # `execute` starts at column 3, after `db.`
        assert _call_position(call) == (0, 3)

    def test_multiline_attribute_uses_the_attribute_line(self):
        call = _first_call("""\
            (session
                .execute(value))
        """)
        line, col = _call_position(call)
        assert line == 1
        assert col == 5

    def test_unsupported_callee_shape_is_none(self):
        call = _first_call("factory()(value)")
        assert _call_position(call) is None


# ── Verifier contract ───────────────────────────────────────────────────────


class TestVerifierWithoutLanguageServer:
    """No server installed is the common case; it must degrade to None."""

    def test_unavailable_verifier_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLYTO_TAINT_LSP", "0")
        verifier = CalleeVerifier(tmp_path)
        call = _first_call("db.execute(value)")

        assert verifier.available is False
        assert verifier.verify_call("app.py", call, "db.py", "execute") is None

    def test_unavailable_stats_explain_the_limitation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLYTO_TAINT_LSP", "0")
        verifier = CalleeVerifier(tmp_path)

        stats = verifier.stats()
        assert stats["mode"] == "name_only"
        assert "matched by name" in stats["reason"]

    def test_env_opt_out_disables_verification(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLYTO_TAINT_LSP", "0")
        assert CalleeVerifier(tmp_path).available is False


class TestVerifierVerdicts:
    """The resolver is stubbed: these pin the comparison, not the server."""

    @pytest.fixture
    def verifier(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1\n")
        (tmp_path / "db.py").write_text("def execute(q): ...\n")
        v = CalleeVerifier(tmp_path)
        v._probed = True
        return v

    def test_same_definition_is_verified(self, verifier, tmp_path):
        verifier._resolve = lambda root, src, line, col: (
            str(tmp_path / "db.py"), 1, "execute",
        )
        call = _first_call("db.execute(value)")

        assert verifier.verify_call("app.py", call, "db.py", "execute") is True
        assert verifier.stats()["verified"] == 1

    def test_same_name_different_module_is_rejected(self, verifier, tmp_path):
        verifier._resolve = lambda root, src, line, col: (
            str(tmp_path / "logging_helpers.py"), 4, "execute",
        )
        call = _first_call("logger.execute(value)")

        assert verifier.verify_call("app.py", call, "db.py", "execute") is False
        assert verifier.stats()["rejected"] == 1

    def test_different_name_is_rejected(self, verifier, tmp_path):
        verifier._resolve = lambda root, src, line, col: (
            str(tmp_path / "db.py"), 1, "execute_many",
        )
        call = _first_call("db.execute(value)")

        assert verifier.verify_call("app.py", call, "db.py", "execute") is False

    def test_server_with_no_answer_is_unknown_not_no(self, verifier):
        verifier._resolve = lambda root, src, line, col: None
        call = _first_call("db.execute(value)")

        assert verifier.verify_call("app.py", call, "db.py", "execute") is None
        assert verifier.stats()["unknown"] == 1

    def test_resolver_exception_is_unknown_not_no(self, verifier):
        def boom(*args):
            raise RuntimeError("server died")

        verifier._resolve = boom
        call = _first_call("db.execute(value)")

        assert verifier.verify_call("app.py", call, "db.py", "execute") is None

    def test_missing_caller_file_is_unknown(self, verifier, tmp_path):
        verifier._resolve = lambda root, src, line, col: (str(tmp_path / "db.py"), 1, "execute")
        call = _first_call("db.execute(value)")

        assert verifier.verify_call("gone.py", call, "db.py", "execute") is None

    def test_budget_is_bounded(self, verifier, tmp_path):
        verifier._resolve = lambda root, src, line, col: (str(tmp_path / "db.py"), 1, "execute")
        call = _first_call("db.execute(value)")
        for _ in range(MAX_LSP_CHECKS):
            verifier.verify_call("app.py", call, "db.py", "execute")

        assert verifier.verify_call("app.py", call, "db.py", "execute") is None
        assert verifier.stats()["budget_exhausted"] is True
        assert verifier.checks == MAX_LSP_CHECKS


# ── Engine behaviour ────────────────────────────────────────────────────────


def _index_for(root: Path, entries):
    """Minimal index shaped like the real one: dependencies of type 'calls'."""
    dependencies = {}
    symbols = {}
    for i, (caller_file, caller_func, callee, line) in enumerate(entries):
        sym_id = f"proj:{caller_file}:function:{caller_func}"
        dependencies[f"dep{i}"] = {
            "source": sym_id, "target": callee, "type": "calls", "source_line": line,
        }
        symbols[sym_id] = {
            "project": "proj", "path": caller_file, "type": "function",
            "name": caller_func,
        }
    return {"dependencies": dependencies, "symbols": symbols}


class TestSubstringAttribution:
    def test_similar_name_no_longer_absorbs_the_flow(self, tmp_path):
        """`prerun_hook` must not be treated as a call to `run`."""
        (tmp_path / "sink.py").write_text(textwrap.dedent("""\
            import os

            def run(cmd):
                os.system(cmd)
        """))
        (tmp_path / "app.py").write_text(textwrap.dedent("""\
            from flask import request
            from sink import run

            def handler():
                value = request.args.get("v")
                prerun_hook(value)
        """))
        index = _index_for(tmp_path, [("app.py", "handler", "run", 6)])

        flows = TaintAnalyzer(tmp_path, index=index).analyze()
        cross = [f for f in flows if f.file_path == "app.py"]

        assert cross == [], "a call to prerun_hook is not a call to run"

    def test_real_call_still_produces_the_flow(self, tmp_path):
        (tmp_path / "sink.py").write_text(textwrap.dedent("""\
            import os

            def run(cmd):
                os.system(cmd)
        """))
        (tmp_path / "app.py").write_text(textwrap.dedent("""\
            from flask import request
            from sink import run

            def handler():
                value = request.args.get("v")
                run(value)
        """))
        index = _index_for(tmp_path, [("app.py", "handler", "run", 6)])

        flows = TaintAnalyzer(tmp_path, index=index).analyze()

        assert any(f.file_path == "app.py" and f.category == "rce" for f in flows)


class TestResolutionIsReported:
    def test_result_states_how_callees_were_resolved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLYTO_TAINT_LSP", "0")
        (tmp_path / "app.py").write_text("x = 1\n")

        result = TaintAnalyzer(tmp_path).analyze_full()

        assert result.callee_resolution["mode"] == "name_only"
        assert result.to_dict()["callee_resolution"]["mode"] == "name_only"

    def test_rejections_are_counted_in_the_report(self, tmp_path):
        (tmp_path / "sink.py").write_text(textwrap.dedent("""\
            import os

            def run(cmd):
                os.system(cmd)
        """))
        (tmp_path / "app.py").write_text(textwrap.dedent("""\
            from flask import request

            def handler():
                value = request.args.get("v")
                run(value)
        """))
        index = _index_for(tmp_path, [("app.py", "handler", "run", 5)])

        analyzer = TaintAnalyzer(tmp_path, index=index)
        verifier = analyzer._callee_verifier()
        verifier._probed = True
        # Pretend a server resolved this call somewhere else entirely.
        verifier._resolve = lambda root, src, line, col: (
            str(tmp_path / "unrelated.py"), 9, "run",
        )

        result = analyzer.analyze_full()

        assert result.callee_resolution["rejected"] >= 1
        assert not [f for f in result.taint_flows if f.file_path == "app.py"]
