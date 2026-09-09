"""A function parameter must not hide a real source beside it.

Every composite branch of `_expr_is_tainted` returned the first tainted part
it found. When that part was a parameter, the finding it produced was
discarded later as param-only taint -- so the same expression reported an
injection or nothing at all depending on the order of its two interpolations.
"""

import os
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from analyzer.taint import TaintAnalyzer  # noqa: E402


def _analyze(code: str):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "app.py").write_text(textwrap.dedent(code))
        return TaintAnalyzer(root).analyze()


class TestOrderMustNotDecide:
    def test_an_fstring_reports_whichever_way_round_it_is_written(self):
        first = _analyze("""
            import flask, os
            def handler(table):
                value = flask.request.args.get("v")
                os.system(f"report {table} --name '{value}'")
        """)
        second = _analyze("""
            import flask, os
            def handler(table):
                value = flask.request.args.get("v")
                os.system(f"report --name '{value}' {table}")
        """)
        assert [f.category for f in first] == ["rce"]
        assert [f.category for f in second] == ["rce"]

    def test_concatenation_reports_either_way(self):
        for expr in (
            '"report " + table + " --name " + value',
            '"report --name " + value + " " + table',
        ):
            findings = _analyze(f"""
                import flask, os
                def handler(table):
                    value = flask.request.args.get("v")
                    os.system({expr})
            """)
            assert [f.category for f in findings] == ["rce"], expr

    def test_a_container_prefers_the_real_source_too(self):
        findings = _analyze("""
            import flask
            def handler(table, users):
                value = flask.request.args.get("v")
                users.find({"t": table, "n": value})
        """)
        assert [f.category for f in findings] == ["nosql_injection"]
        assert not findings[0].source_expr.startswith("param:")

    def test_the_reported_source_is_the_real_one(self):
        findings = _analyze("""
            import flask, os
            def handler(table):
                value = flask.request.args.get("v")
                os.system(f"report {table} --name '{value}'")
        """)
        assert findings[0].source_expr == "flask.request.args.get('v')"


class TestParameterTaintStillCrosses:
    def test_a_parameter_alone_still_carries_no_local_finding(self):
        # It is not a finding here; it makes the function dangerous to call,
        # which is Phase 2's job.
        findings = _analyze("""
            import os
            def handler(target):
                os.system("ping " + target)
        """)
        assert findings == []

    def test_a_parameter_alone_is_still_what_gets_reported_upward(self):
        from analyzer.taint import TaintAnalyzer as T

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "app.py").write_text(textwrap.dedent("""
                import os
                def handler(target):
                    os.system("ping " + target)
            """))
            analyzer = T(root)
            analyzer.analyze()
            assert ("app.py", "handler") in analyzer._dangerous_functions
