"""Return-value taint: a function that returns untrusted input taints callers.

The intra-procedural pass only tainted a call result when one of the call's own
arguments was tainted. A function that reads a source itself and returns it —
the single most common shape in real code, e.g. a request accessor helper — had
no tainted argument, so `x = read()` stayed clean and every sink it reached was
missed. These tests pin the closure of that gap and its bounds.
"""

import textwrap
from pathlib import Path

import pytest

from src.analyzer.taint import TaintAnalyzer


def _analyze(tmp_path: Path, code: str):
    (tmp_path / "app.py").write_text(textwrap.dedent(code))
    return TaintAnalyzer(tmp_path).analyze()


class TestDirectReturnSource:
    def test_zero_arg_returner_taints_the_caller(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def read_input():
                return request.args.get("cmd")

            def handler():
                value = read_input()
                os.system(value)
        """)
        assert any(f.category == "rce" and f.line for f in flows)
        assert any("read_input" in f.source_expr for f in flows)

    def test_returned_source_reaching_sink_directly_is_flagged(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def read_input():
                return request.args.get("cmd")

            def handler():
                os.system(read_input())
        """)
        assert any(f.category == "rce" for f in flows)

    def test_local_chain_inside_the_returner_is_followed(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def read_input():
                a = request.headers.get("X-Cmd")
                b = a
                return b

            def handler():
                x = read_input()
                os.system("echo " + x)
        """)
        assert any(f.category == "rce" for f in flows)


class TestReturnChain:
    def test_two_hop_return_chain(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import subprocess

            def read_input():
                return request.args.get("cmd")

            def wrapper():
                return read_input()

            def handler():
                v = wrapper()
                subprocess.run(v, shell=True)
        """)
        assert any(f.category == "rce" for f in flows)

    def test_recursion_terminates_and_does_not_hang(self, tmp_path):
        # A function returning its own call must not spin the fixpoint.
        flows = _analyze(tmp_path, """\
            def recurse(n):
                if n <= 0:
                    return 0
                return recurse(n - 1)

            def handler():
                x = recurse(5)
        """)
        assert flows == [] or all(f.file_path == "app.py" for f in flows)


class TestPrecisionGuards:
    def test_constant_returner_does_not_taint(self, tmp_path):
        flows = _analyze(tmp_path, """\
            import os

            def safe_config():
                return "constant"

            def handler():
                y = safe_config()
                os.system(y)
        """)
        assert flows == []

    def test_env_returner_is_not_untrusted(self, tmp_path):
        # Operator-controlled input (os.environ) is not a remote source; a
        # function returning it must not become a return-source.
        flows = _analyze(tmp_path, """\
            import os

            def get_home():
                return os.environ.get("HOME")

            def handler():
                p = get_home()
                os.system(p)
        """)
        assert not [f for f in flows if "get_home" in f.source_expr]

    def test_returning_a_tainted_param_is_not_marked_unconditional(self, tmp_path):
        # `def echo(p): return p` returns untrusted input ONLY when the caller
        # passes it. It must not become an unconditional return-source that
        # fires on a clean argument.
        flows = _analyze(tmp_path, """\
            import os

            def echo(p):
                return p

            def handler():
                safe = echo("literal")
                os.system(safe)
        """)
        assert flows == []


class TestRegistryShape:
    def test_registry_is_name_based_and_populated(self, tmp_path):
        analyzer = TaintAnalyzer(tmp_path)
        (tmp_path / "app.py").write_text(textwrap.dedent("""\
            from flask import request

            def read_input():
                return request.args.get("x")

            def wrapper():
                return read_input()
        """))
        analyzer.analyze()
        assert "read_input" in analyzer._return_source_funcs
        assert "wrapper" in analyzer._return_source_funcs
