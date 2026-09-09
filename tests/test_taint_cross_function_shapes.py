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
