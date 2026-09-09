"""Cross-function tracking has to see the call wherever the statement puts it.

Two shapes were invisible. A call whose result is returned or assigned was
never matched -- both cross-function passes tested for `ast.Expr` -- so
`return handle(request.args.get("x"))`, the ordinary shape of a web handler,
was followed nowhere. And a parameter reaching a *subscript* sink never made
its function dangerous to call, so `def echo(resp, origin): resp.headers[k] =
origin` was a finding in itself and invisible to every caller.
"""

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.engine import IndexEngine  # noqa: E402
from src.analyzer.taint import TaintAnalyzer  # noqa: E402


def _analyze(files: dict[str, str]):
    """Analyze a multi-file project the way the benchmark does: with an index."""
    with tempfile.TemporaryDirectory() as project:
        root = Path(project)
        for name, code in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(code))
        with tempfile.TemporaryDirectory() as index_dir:
            engine = IndexEngine("t", root, index_dir=Path(index_dir))
            engine.scan(incremental=False)
            index = json.loads(
                (Path(index_dir) / "index.json").read_text(encoding="utf-8")
            )
            return TaintAnalyzer(root, index=index).analyze()


SINK = """\
    import os

    def handle(cmd):
        os.system("run " + cmd)
"""


class TestWhereTheStatementPutsTheCall:
    def test_a_bare_call_is_followed(self):
        findings = _analyze({"sink.py": SINK, "api.py": """\
            from flask import request
            from sink import handle

            def lookup():
                handle(request.args.get("cmd"))
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_a_returned_call_is_followed(self):
        findings = _analyze({"sink.py": SINK, "api.py": """\
            from flask import request
            from sink import handle

            def lookup():
                return handle(request.args.get("cmd"))
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_an_assigned_call_is_followed(self):
        findings = _analyze({"sink.py": SINK, "api.py": """\
            from flask import request
            from sink import handle

            def lookup():
                result = handle(request.args.get("cmd"))
                return result
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_an_awaited_call_is_followed(self):
        findings = _analyze({"sink.py": """\
            import os

            async def handle(cmd):
                os.system("run " + cmd)
        """, "api.py": """\
            from flask import request
            from sink import handle

            async def lookup():
                return await handle(request.args.get("cmd"))
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_an_assignment_still_propagates_its_own_taint(self):
        # Surfacing the call must not cost the assignment its normal handling.
        findings = _analyze({"api.py": """\
            from flask import request
            import os

            def lookup():
                value = str(request.args.get("cmd"))
                os.system("run " + value)
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_an_unrelated_call_is_not_a_finding(self):
        findings = _analyze({"sink.py": SINK, "api.py": """\
            from flask import request
            from sink import handle

            def lookup():
                return handle("static")
        """})
        assert findings == []


class TestASubscriptSinkMakesItsFunctionDangerous:
    HEADERS = """\
        def echo_origin(resp, origin):
            resp.headers["Access-Control-Allow-Origin"] = origin
    """

    def test_a_caller_passing_a_tainted_value_is_reported(self):
        findings = _analyze({"headers.py": self.HEADERS, "api.py": """\
            from flask import request
            from headers import echo_origin

            def cors(resp):
                echo_origin(resp, request.headers.get("Origin"))
        """})
        assert [f.category for f in findings] == ["crlf_injection"]

    def test_a_caller_passing_a_constant_is_not(self):
        findings = _analyze({"headers.py": self.HEADERS, "api.py": """\
            from headers import echo_origin

            def cors(resp):
                echo_origin(resp, "https://example.com")
        """})
        assert findings == []


class TestEveryParameterThatReachesASink:
    """A callee registered on one parameter was unreachable through the others.

    A finding names one argument, and only that argument's parameter was
    recorded as making the function dangerous to call. So

        def handle(table, cmd):
            os.system(f"run {table} {cmd}")

    could be reached through `table` and not through `cmd`.
    """

    SINK = """\
        import os

        def handle(table, cmd):
            os.system(f"run {table} {cmd}")
    """

    def test_the_second_parameter_is_reachable(self):
        findings = _analyze({"sink.py": self.SINK, "api.py": """\
            from flask import request
            from sink import handle

            def lookup():
                handle("users", request.args.get("cmd"))
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_the_first_parameter_is_still_reachable(self):
        findings = _analyze({"sink.py": self.SINK, "api.py": """\
            from flask import request
            from sink import handle

            def lookup():
                handle(request.args.get("t"), "ls")
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_each_caller_is_reported_once(self):
        findings = _analyze({"sink.py": self.SINK, "api.py": """\
            from flask import request
            from sink import handle

            def first():
                handle(request.args.get("t"), "ls")

            def second():
                handle("users", request.args.get("cmd"))

            def neither():
                handle("users", "ls")
        """})
        assert [f.line for f in findings] == [5, 8]

    def test_constants_on_both_sides_report_nothing(self):
        findings = _analyze({"sink.py": self.SINK, "api.py": """\
            from sink import handle

            def lookup():
                handle("users", "ls")
        """})
        assert findings == []

    def test_a_sanitized_parameter_is_not_registered(self):
        findings = _analyze({"sink.py": """\
            import os, shlex

            def handle(table, cmd):
                os.system(f"run {table} " + shlex.quote(cmd))
        """, "api.py": """\
            from flask import request
            from sink import handle

            def lookup():
                handle("users", request.args.get("cmd"))
        """})
        assert findings == []


class TestTheCallerCanBeAMethod:
    """The index names a method `Class.method`; the AST node is called `method`.

    Comparing the two matched nothing, so a caller that was a method was never
    scanned -- which is most callers in code that uses classes. A six-shape
    class-based application scored 2 of 6 on this alone.
    """

    SINK = """\
        import os

        def run_cmd(host):
            os.system("ping " + host)
    """

    def test_a_method_calling_a_private_method_in_its_own_class(self):
        findings = _analyze({"app.py": """\
            import os
            from flask import request

            class Handler:
                def go(self):
                    self._run(request.args.get("host"))

                def _run(self, host):
                    os.system("ping " + host)
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_a_method_calling_through_an_attribute(self):
        findings = _analyze({"app.py": """\
            import os
            from flask import request

            class Repo:
                def run(self, cmd):
                    os.system("x " + cmd)

            class Handler:
                def __init__(self):
                    self.repo = Repo()

                def go(self):
                    self.repo.run(request.args.get("v"))
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_a_plain_function_caller_still_works(self):
        findings = _analyze({"sink.py": self.SINK, "app.py": """\
            from flask import request
            from sink import run_cmd

            def go():
                run_cmd(request.args.get("host"))
        """})
        assert [f.category for f in findings] == ["rce"]


class TestTheChainDoesNotStopAfterOneHop:
    """Tracing a caller can find the caller dangerous, one hop further out.

    That was recorded and never traced: the name map was built once, before
    the round that grows it, so a three-function chain stopped at the second
    no matter what MAX_CROSS_DEPTH said.
    """

    def test_two_hops(self):
        findings = _analyze({"app.py": """\
            import os
            from flask import request

            def go():
                outer(request.args.get("v"))

            def outer(v):
                inner(v)

            def inner(v):
                os.system("echo " + v)
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_three_hops(self):
        findings = _analyze({"app.py": """\
            import os
            from flask import request

            def go():
                first(request.args.get("v"))

            def first(v):
                second(v)

            def second(v):
                third(v)

            def third(v):
                os.system("echo " + v)
        """})
        assert [f.category for f in findings] == ["rce"]

    def test_a_clean_chain_reports_nothing(self):
        findings = _analyze({"app.py": """\
            import os

            def go():
                first("static")

            def first(v):
                second(v)

            def second(v):
                os.system("echo " + v)
        """})
        assert findings == []
