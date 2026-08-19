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


class TestContextManagerSinks:
    """Sinks inside `with`/`async with` were dropped; the context expression is
    exactly where file/db/subprocess sinks live (`with open(tainted) as f`)."""

    def test_sync_with_open_sink(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request

            def handler():
                name = request.args.get("f")
                with open("/data/" + name) as fh:
                    return fh.read()
        """)
        assert any(f.category == "path_traversal" for f in flows)

    def test_async_with_open_sink(self, tmp_path):
        # `async with` is a distinct AST node that was never matched.
        flows = _analyze(tmp_path, """\
            from fastapi import Body

            async def handler(name: str = Body(...)):
                async with open("/data/" + name) as fh:
                    return await fh.read()
        """)
        assert any(f.category == "path_traversal" for f in flows)

    def test_taint_binds_from_context_manager(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def read_source():
                return request.args.get("cmd")

            def handler():
                with read_source() as cmd:
                    os.system(cmd)
        """)
        assert any(f.category == "rce" for f in flows)


class TestSelfAttributeTaint:
    """Untrusted input stored on `self` in one method, used in a sink in
    another — the field sensitivity every mature taint tool has."""

    def test_self_attr_across_methods(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            class Handler:
                def load(self):
                    self.cmd = request.args.get("cmd")
                def run(self):
                    os.system(self.cmd)
        """)
        assert any(f.category == "rce" and f.source_expr == "self.cmd" for f in flows)

    def test_untainted_self_attr_is_not_a_source(self, tmp_path):
        flows = _analyze(tmp_path, """\
            import os

            class Handler:
                def load(self):
                    self.name = "constant"
                def run(self):
                    os.system(self.name)
        """)
        assert flows == []

    def test_self_attr_is_class_scoped(self, tmp_path):
        # `cmd` tainted on class A must not taint `self.cmd` read in class B.
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            class A:
                def load(self):
                    self.cmd = request.args.get("cmd")

            class B:
                def run(self):
                    os.system(self.cmd)
        """)
        assert flows == []


class TestPropagators:
    """Taint through in-place mutation — Semgrep's propagator concept. The
    tainted data never appears on the left of an assignment, so value-flow
    taint cannot see it."""

    def test_list_append_taints_the_container(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def handler():
                items = []
                items.append(request.args.get("x"))
                for it in items:
                    os.system(it)
        """)
        assert any(f.category == "rce" for f in flows)

    def test_dict_subscript_assignment_taints_the_dict(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def handler():
                d = {}
                d["k"] = request.args.get("y")
                os.system(d["k"])
        """)
        assert any(f.category == "rce" for f in flows)

    def test_parse_dict_taints_the_destination(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import os
            from google.protobuf.json_format import parse_dict

            def handler():
                proto = Msg()
                parse_dict(request.get_json(), proto)
                os.system(proto.cmd)
        """)
        assert any(f.category == "rce" for f in flows)

    def test_multi_hop_return_source_via_parse_dict(self, tmp_path):
        # read() -> normalize() -> parse_dict(json, proto); return proto,
        # then message.field -> sink. The global fixpoint must converge.
        flows = _analyze(tmp_path, """\
            from flask import request
            import os
            from google.protobuf.json_format import parse_dict

            def _normalize(flask_request=request):
                return flask_request.get_json(force=True)

            def _get_message(msg, flask_request=request):
                body = _normalize(flask_request)
                parse_dict(body, msg)
                return msg

            def handler():
                message = _get_message(Msg())
                os.system("run " + message.cmd)
        """)
        assert any(f.category == "rce" for f in flows)


class TestMethodFormSources:
    def test_get_json_method_form(self, tmp_path):
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def handler():
                data = request.get_json()
                os.system(data["cmd"])
        """)
        assert any(f.category == "rce" for f in flows)


class TestYamlConfigurablePropagators:
    """Propagators are declared in .flyto-rules.yaml like sources/sinks/
    sanitizers — not hardcoded in the engine. A project's own mutation helper
    is addable without touching the source."""

    def test_positional_propagator_from_yaml(self, tmp_path):
        (tmp_path / ".flyto-rules.yaml").write_text(textwrap.dedent("""\
            taint:
              propagators:
                - name: my_populate
                  from: 0
                  to: 1
        """))
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def handler():
                dst = {}
                my_populate(request.args.get("a"), dst)
                os.system(dst["cmd"])
        """)
        assert any(f.category == "rce" for f in flows)

    def test_receiver_propagator_from_yaml(self, tmp_path):
        (tmp_path / ".flyto-rules.yaml").write_text(textwrap.dedent("""\
            taint:
              propagators:
                - name: stash
                  receiver: true
        """))
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def handler():
                box = []
                box.stash(request.args.get("b"))
                os.system(box[0])
        """)
        assert any(f.category == "rce" for f in flows)

    def test_unknown_method_is_not_a_propagator_without_yaml(self, tmp_path):
        # `stash` is not a built-in propagator; without YAML it must not fire.
        flows = _analyze(tmp_path, """\
            from flask import request
            import os

            def handler():
                box = []
                box.stash(request.args.get("b"))
                os.system(box[0])
        """)
        assert flows == []
