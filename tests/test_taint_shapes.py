"""Argument-shape gates: a sink rule that names a method, not a receiver.

`collection.find(` only ever matched projects that happened to call their
Mongo handle `collection`. `.find(` matches every project and also matches
`str.find`. What separates them is the argument: a query is a mapping, a
string search is given a string.
"""

import ast
import os
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from analyzer.taint import TaintAnalyzer  # noqa: E402
from analyzer.taint_shapes import (  # noqa: E402
    call_satisfies,
    is_constant_literal,
    normalize_requirements,
    provable_shape,
)


def _call(source: str) -> tuple[ast.Call, str]:
    call = ast.parse(source, mode="eval").body
    assert isinstance(call, ast.Call)
    return call, ast.unparse(call.func)


def _analyze(code: str, rules: str | None = None):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "app.py").write_text(textwrap.dedent(code))
        if rules:
            (root / ".flyto-rules.yaml").write_text(textwrap.dedent(rules))
        return TaintAnalyzer(root).analyze()


# ---------------------------------------------------------------------------
# The shape vocabulary
# ---------------------------------------------------------------------------

class TestProvableShape:
    def test_literals_are_proven(self):
        assert provable_shape(ast.parse("{'a': 1}", mode="eval").body) == "mapping"
        assert provable_shape(ast.parse("[1, 2]", mode="eval").body) == "sequence"
        assert provable_shape(ast.parse("'x'", mode="eval").body) == "scalar"

    def test_builders_count_as_their_literal(self):
        assert provable_shape(ast.parse("dict(a=1)", mode="eval").body) == "mapping"
        assert provable_shape(ast.parse("list(x)", mode="eval").body) == "sequence"

    def test_an_unknown_expression_stays_unknown(self):
        # Not "scalar by default" -- a gate that guesses drops real flows.
        assert provable_shape(ast.parse("build_query(x)", mode="eval").body) is None
        assert provable_shape(ast.parse("value", mode="eval").body) is None

    def test_a_binding_is_followed(self):
        bindings = {"sep": ast.parse("','", mode="eval").body}
        assert provable_shape(ast.parse("sep", mode="eval").body, bindings) == "scalar"

    def test_an_fstring_is_a_string_but_not_a_constant(self):
        node = ast.parse('f"^{p}"', mode="eval").body
        assert provable_shape(node) == "scalar"
        assert is_constant_literal(node) is False


class TestRequirements:
    def test_a_shape_requirement_reads_the_named_argument(self):
        call, name = _call("users.find({'a': 1})")
        assert call_satisfies(call, name, [{"arg": 0, "shape": "mapping"}])
        call, name = _call("line.find(',')")
        assert not call_satisfies(call, name, [{"arg": 0, "shape": "mapping"}])

    def test_an_unknown_argument_passes_the_gate(self):
        call, name = _call("users.find(query)")
        assert call_satisfies(call, name, [{"arg": 0, "shape": "mapping"}])

    def test_a_missing_argument_fails_the_gate(self):
        call, name = _call("users.find()")
        assert not call_satisfies(call, name, [{"arg": 0, "shape": "mapping"}])

    def test_any_asks_whether_some_argument_matches(self):
        call, name = _call("merge('a', {'b': 1})")
        assert call_satisfies(call, name, [{"arg": "any", "shape": "mapping"}])
        call, name = _call("merge('a', 'b')")
        assert not call_satisfies(call, name, [{"arg": "any", "shape": "mapping"}])

    def test_after_first_asks_about_every_later_argument(self):
        call, name = _call("os.path.join(base, 'src', 'main.py')")
        assert call_satisfies(call, name, [{"arg": "after_first", "shape": "constant"}])
        call, name = _call("os.path.join(base, 'src', name)")
        assert not call_satisfies(call, name, [{"arg": "after_first", "shape": "constant"}])

    def test_callee_tail_respects_a_name_boundary(self):
        call, name = _call("re.search(p, s)")
        assert call_satisfies(call, name, [{"callee_tail": ["re.search"]}])
        # "re.search" is a substring of "sto|re.search"; the tail has to start
        # a name, not continue one.
        call, name = _call("store.search(p, s)")
        assert not call_satisfies(call, name, [{"callee_tail": ["re.search"]}])

    def test_a_keyword_requirement_reads_its_value(self):
        call, name = _call("run(cmd, shell=True)")
        assert call_satisfies(call, name, [{"keyword": "shell", "equals": True}])
        call, name = _call("run(cmd, shell=False)")
        assert not call_satisfies(call, name, [{"keyword": "shell", "equals": True}])
        call, name = _call("run(cmd)")
        assert not call_satisfies(call, name, [{"keyword": "shell", "equals": True}])

    def test_argument_counts(self):
        call, name = _call("execute(sql, params)")
        assert not call_satisfies(call, name, [{"max_args": 1}])
        assert call_satisfies(call, name, [{"min_args": 2}])

    def test_not_inverts_one_requirement(self):
        call, name = _call("re.compile('^a+$')")
        assert call_satisfies(call, name, [{"not": {"arg": 0, "shape": "dynamic"}}])
        call, name = _call("re.compile(pattern)")
        assert not call_satisfies(call, name, [{"not": {"arg": 0, "shape": "dynamic"}}])

    def test_every_requirement_has_to_hold(self):
        call, name = _call("users.find({'a': 1})")
        assert not call_satisfies(
            call, name, [{"arg": 0, "shape": "mapping"}, {"min_args": 2}],
        )

    def test_no_requirements_means_no_gate(self):
        call, name = _call("anything()")
        assert call_satisfies(call, name, ())
        assert call_satisfies(call, name, None)

    def test_normalization_accepts_a_bare_mapping(self):
        assert normalize_requirements({"min_args": 1}) == ({"min_args": 1},)
        assert normalize_requirements(None) == ()
        assert normalize_requirements(["not a rule", {"min_args": 1}]) == ({"min_args": 1},)


# ---------------------------------------------------------------------------
# What the gate buys on real code
# ---------------------------------------------------------------------------

class TestReceiverFreeNoSqlRules:
    def test_a_collection_the_project_named_itself_is_found(self):
        # The old rule read `collection.find(`, so this was invisible.
        findings = _analyze("""
            import flask
            def handler(users):
                return users.find(flask.request.json)
        """)
        assert [f.category for f in findings] == ["nosql_injection"]

    def test_a_nested_handle_is_found(self):
        findings = _analyze("""
            import flask
            def handler(db):
                name = flask.request.args.get("n")
                return db.orders.find({"name": name})
        """)
        assert [f.category for f in findings] == ["nosql_injection"]

    def test_str_find_is_not_a_database(self):
        findings = _analyze("""
            import flask
            def handler():
                line = flask.request.args.get("l")
                return line.find(",")
        """)
        assert findings == []

    def test_str_find_through_a_bound_separator_is_not_a_database(self):
        findings = _analyze("""
            import flask
            def handler(text):
                sep = ","
                query = flask.request.args.get("q")
                return text.find(sep) + len(query)
        """)
        assert findings == []

    def test_dict_update_is_still_left_alone(self):
        # `.update(` cannot be gated: dict.update takes a mapping too, so that
        # rule deliberately keeps its receiver.
        findings = _analyze("""
            import flask
            def handler(settings):
                settings.update(flask.request.json)
        """)
        assert findings == []


class TestReceiverFreeHeaderRules:
    def test_any_response_object_counts(self):
        findings = _analyze("""
            import flask
            def handler(resp):
                resp.headers["X-Thing"] = flask.request.args.get("v")
        """)
        assert [f.category for f in findings] == ["crlf_injection"]

    def test_a_gated_rule_does_not_apply_to_a_subscript(self):
        # A subscript assignment has no arguments, so a rule that judges
        # arguments cannot be treated as satisfied there.
        findings = _analyze(
            """
            import flask
            def handler(store):
                store["k"] = flask.request.args.get("v")
            """,
            rules="""
            taint:
              sinks:
                - pattern: "store["
                  vuln_type: custom
                  severity: high
                  requires:
                    - {arg: 0, shape: mapping}
            """,
        )
        assert findings == []


class TestProjectDeclaredGates:
    def test_a_yaml_rule_can_state_an_argument_shape(self):
        code = """
            import flask
            def handler(client):
                client.send({"body": flask.request.json})
                client.send("static")
        """
        rules = """
            taint:
              sinks:
                - pattern: ".send("
                  vuln_type: custom
                  severity: high
                  recommendation: "Validate the payload"
                  requires:
                    - {arg: 0, shape: mapping}
        """
        findings = _analyze(code, rules)
        assert [f.category for f in findings] == ["custom"]
        assert findings[0].line == 4  # the mapping call, not the string one

    def test_a_yaml_rule_without_requires_is_ungated(self):
        rules = """
            taint:
              sinks:
                - pattern: ".send("
                  vuln_type: custom
                  severity: high
        """
        findings = _analyze("""
            import flask
            def handler(client):
                client.send(flask.request.args.get("v"))
        """, rules)
        assert [f.category for f in findings] == ["custom"]


class TestWritingAGatedRule:
    def test_the_dsl_round_trips_requires(self, tmp_path):
        from analyzer.taint_dsl import add_taint_sink, list_taint_rules

        add_taint_sink(
            tmp_path, ".find(", vuln_type="nosql_injection",
            requires=[{"arg": 0, "shape": "mapping"}],
        )
        assert list_taint_rules(tmp_path)["sinks"][0]["requires"] == [
            {"arg": 0, "shape": "mapping"},
        ]

    def test_a_rule_written_by_the_dsl_gates_the_scan(self, tmp_path):
        from analyzer.taint_dsl import add_taint_sink

        add_taint_sink(
            tmp_path, ".dispatch(", vuln_type="custom",
            requires=[{"arg": 0, "shape": "mapping"}],
        )
        (tmp_path / "app.py").write_text(textwrap.dedent("""
            import flask
            def handler(bus):
                bus.dispatch({"body": flask.request.json})
                bus.dispatch("ping")
        """))
        findings = TaintAnalyzer(tmp_path).analyze()
        assert [(f.category, f.line) for f in findings] == [("custom", 4)]


class TestDuplicateFlows:
    def test_two_rules_naming_one_call_report_it_once(self, tmp_path):
        (tmp_path / ".flyto-rules.yaml").write_text(textwrap.dedent("""
            taint:
              sinks:
                - pattern: ".find("
                  vuln_type: nosql_injection
                  severity: high
        """))
        (tmp_path / "app.py").write_text(textwrap.dedent("""
            import flask
            def handler(users):
                return users.find({"n": flask.request.json})
        """))
        findings = TaintAnalyzer(tmp_path).analyze()
        assert len(findings) == 1


class TestSinkCounting:
    def test_a_gated_rule_is_not_counted_as_a_text_match(self, tmp_path):
        # `total_sinks` is what verify prints. Counting `.find(` textually
        # reported every `str.find` in the project as a sink -- 1554 -> 1890
        # on this repository -- none of which the analysis would report.
        (tmp_path / "app.py").write_text(textwrap.dedent("""
            def offsets(line):
                a = line.find(",")
                b = line.find(";")
                c = line.find(":")
                return a, b, c
        """))
        result = TaintAnalyzer(tmp_path).analyze_full()
        assert result.total_sinks == 0

    def test_an_ungated_rule_is_still_counted(self, tmp_path):
        (tmp_path / "app.py").write_text(textwrap.dedent("""
            def lookup(users, q):
                return users.findOne(q)
        """))
        assert TaintAnalyzer(tmp_path).analyze_full().total_sinks == 1


class TestReceiverRequirements:
    def test_receiver_root_reads_the_leftmost_name(self):
        from analyzer.taint_shapes import receiver_root

        def expr(src):
            return ast.parse(src, mode="eval").body

        assert receiver_root(expr("request.headers")) == "request"
        assert receiver_root(expr("self.session.headers")) == "self"
        assert receiver_root(expr("clients[0].headers")) == "clients"
        assert receiver_root(expr("make_response().headers")) == "make_response"
        assert receiver_root(expr("'literal'")) == ""
        assert receiver_root(None) == ""

    def test_a_call_can_refuse_its_receiver(self):
        call, name = _call("res.setHeader('X', v)")
        rule = [{"not": {"receiver_root": ["request", "req"]}}]
        assert call_satisfies(call, name, rule)
        call, name = _call("req.setHeader('X', v)")
        assert not call_satisfies(call, name, rule)

    def test_a_sink_with_no_call_is_judged_on_its_receiver(self):
        from analyzer.taint_shapes import receiver_satisfies

        rule = [{"not": {"receiver_root": ["request", "req"]}}]
        assert receiver_satisfies(ast.parse("resp.headers", mode="eval").body, rule)
        assert not receiver_satisfies(
            ast.parse("request.headers", mode="eval").body, rule,
        )

    def test_an_argument_requirement_still_does_not_reach_a_subscript(self):
        from analyzer.taint_shapes import receiver_satisfies

        # There are no arguments to judge, and treating it as satisfied would
        # let the rule through unchecked.
        assert not receiver_satisfies(
            ast.parse("resp.headers", mode="eval").body,
            [{"arg": 0, "shape": "mapping"}],
        )
        assert not receiver_satisfies(
            ast.parse("resp.headers", mode="eval").body,
            [{"not": {"arg": 0, "shape": "mapping"}}],
        )


class TestHeaderRulesMeanTheResponse:
    def test_a_response_header_is_still_reported(self):
        findings = _analyze("""
            import flask
            def handler(resp):
                resp.headers["X-Thing"] = flask.request.args.get("v")
        """)
        assert [f.category for f in findings] == ["crlf_injection"]

    def test_a_client_building_its_own_request_is_not(self):
        # aiohttp's digest-auth middleware has exactly this shape, and the
        # receiver-free rule read it as a response header injection.
        findings = _analyze("""
            import flask
            def sign(request):
                request.headers["Authorization"] = flask.request.args.get("v")
        """)
        assert findings == []

    def test_req_set_header_is_not_a_response_either(self):
        findings = _analyze("""
            import flask
            def handler(req):
                req.setHeader("X-Thing", flask.request.args.get("v"))
        """)
        assert findings == []

    def test_a_receiver_with_no_name_to_read_still_reports(self):
        findings = _analyze("""
            import flask
            def handler():
                make_response().headers["X"] = flask.request.args.get("v")
        """)
        assert [f.category for f in findings] == ["crlf_injection"]
