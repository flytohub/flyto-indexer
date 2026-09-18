"""Real-index regressions for resolved, bounded Python interfile candidates."""
import json
import textwrap

import pytest

from src.analyzer.taint import TaintAnalyzer
from src.engine import IndexEngine

SINK = """\
import os
def handle(cmd):
    os.system(cmd)
"""


def analyze(tmp_path, files, monkeypatch):
    monkeypatch.setenv("FLYTO_TAINT_LSP", "0")
    root = tmp_path / "project"
    root.mkdir()
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
    index_dir = tmp_path / "index"
    IndexEngine("case", root, index_dir=index_dir).scan(incremental=False)
    index = json.loads((index_dir / "index.json").read_text(encoding="utf-8"))
    analyzer = TaintAnalyzer(root, index=index)
    return analyzer, analyzer.analyze_full()


@pytest.mark.parametrize("statement", [
    'handle(cmd=request.args.get("cmd"))',
    'handle(**{"cmd": request.args.get("cmd")})',
    'handle(*(request.args.get("cmd"),))',
])
def test_actual_argument_binding(tmp_path, monkeypatch, statement):
    _, result = analyze(tmp_path, {"sink.py": SINK, "api.py":
        'from flask import request\nfrom sink import handle\ndef endpoint():\n    ' + statement + '\n'}, monkeypatch)
    assert any(f.category == "rce" for f in result.taint_flows)


@pytest.mark.parametrize("signature", ["cmd, /", "*, cmd"])
def test_all_declared_parameter_kinds(tmp_path, monkeypatch, signature):
    call = 'handle(request.args.get("cmd"))' if '/' in signature else 'handle(cmd=request.args.get("cmd"))'
    _, result = analyze(tmp_path, {"sink.py": SINK.replace("handle(cmd)", "handle(" + signature + ")"),
        "api.py": 'from flask import request\nfrom sink import handle\ndef endpoint():\n    ' + call + '\n'}, monkeypatch)
    assert any(f.category == "rce" for f in result.taint_flows)


@pytest.mark.parametrize("imports,call", [
    ("from sink import handle as execute", "execute"),
    ("import sink as runner", "runner.handle"),
])
def test_import_alias_binding(tmp_path, monkeypatch, imports, call):
    _, result = analyze(tmp_path, {"sink.py": SINK, "api.py":
        'from flask import request\n' + imports + '\ndef endpoint():\n    ' + call + '(request.args.get("cmd"))\n'}, monkeypatch)
    flows = [f for f in result.taint_flows if f.category == "rce"]
    assert len(flows) == 1
    assert flows[0].sink_file == "sink.py"
    assert flows[0].sink_line == 3
    assert flows[0].source_file == "api.py" and flows[0].source_line == 4
    assert "python_import_binding" in flows[0].to_dict()["confidence"]["basis"]


def test_unrelated_same_named_function_is_not_a_sink(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"sink.py": SINK,
        "safe.py": 'def handle(cmd):\n    return "fixed"\n',
        "api.py": 'from flask import request\nfrom safe import handle\ndef endpoint():\n    handle(request.args.get("cmd"))\n'}, monkeypatch)
    assert result.taint_flows == []


def test_relative_alias_multihop_preserves_terminal_sink(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"pkg/__init__.py": "", "pkg/sink.py": SINK,
        "pkg/middle.py": 'from .sink import handle as run\ndef relay(cmd):\n    return run(cmd)\n',
        "pkg/api.py": 'from flask import request\nfrom .middle import relay as dispatch\ndef endpoint():\n    value = request.args.get("cmd")\n    return dispatch(value)\n'}, monkeypatch)
    flows = [f for f in result.taint_flows if f.category == "rce"]
    assert len(flows) == 1
    assert flows[0].sink_file == "pkg/sink.py" and flows[0].sink_line == 3
    assert flows[0].source_line == 4
    assert any("pkg/middle.py" in step for step in flows[0].path)
    assert any("pkg/sink.py" in step for step in flows[0].path)


def test_overwritten_source_is_not_stale_taint(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"sink.py": SINK,
        "api.py": 'from flask import request\nfrom sink import handle\ndef endpoint():\n    value = request.args.get("cmd")\n    value = "fixed"\n    handle(value)\n'}, monkeypatch)
    assert result.taint_flows == []


def test_safe_branch_does_not_erase_other_branch(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"sink.py": SINK,
        "api.py": 'from flask import request\nfrom sink import handle\ndef endpoint(flag):\n    value = request.args.get("cmd")\n    if flag:\n        value = int(value)\n    handle(value)\n'}, monkeypatch)
    assert any(f.category == "rce" for f in result.taint_flows)


def test_try_call_reaches_existing_cross_function_pass(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"sink.py": SINK,
        "api.py": 'from flask import request\nfrom sink import handle\ndef endpoint():\n    try:\n        handle(request.args.get("cmd"))\n    except ValueError:\n        pass\n'}, monkeypatch)
    assert any(f.category == "rce" for f in result.taint_flows)


def test_same_instance_does_not_reuse_previous_scan_ast(tmp_path, monkeypatch):
    analyzer, result = analyze(tmp_path, {"sink.py": SINK,
        "api.py": 'from flask import request\nfrom sink import handle\ndef endpoint():\n    handle(request.args.get("cmd"))\n'}, monkeypatch)
    assert result.taint_flows
    (analyzer.project_root / "sink.py").write_text('def handle(cmd):\n    return "safe"\n', encoding="utf-8")
    assert analyzer.analyze_full().taint_flows == []

@pytest.mark.parametrize("body,expected", [
    ('value = request.args.get("cmd")\nvalue = shlex.quote(value)\nhandle(value)', False),
    ('value = request.args.get("cmd")\nvalue = sanitize_print(value)\nhandle(value)', True),
    ('value = request.args.get("cmd")\nvalue = html.escape(value)\nhandle(value)', True),
    ('value = request.args.get("cmd")\nif flag:\n    value = "safe"\nelse:\n    value = "also-safe"\nhandle(value)', False),
    ('value = request.args.get("cmd")\nfor _ in []:\n    value = "safe"\nhandle(value)', True),
    ('value = request.args.get("cmd")\ntry:\n    value = "safe"\nexcept ValueError:\n    pass\nhandle(value)', True),
])
def test_cross_flow_sanitizers_and_branch_join(tmp_path, monkeypatch, body, expected):
    code = 'from flask import request\nimport shlex, html\nfrom sink import handle\ndef endpoint(flag):\n'
    code += textwrap.indent(body, "    ") + "\n"
    _, result = analyze(tmp_path, {"sink.py": SINK, "api.py": code}, monkeypatch)
    assert bool(result.taint_flows) is expected


def test_dynamic_unpack_is_a_gap_not_a_guessed_argument(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"sink.py": SINK, "api.py":
        'from flask import request\nfrom sink import handle\ndef endpoint():\n    args = request.get_json()\n    handle(**args)\n'}, monkeypatch)
    assert not result.taint_flows
    assert "dynamic_argument_binding" in result.truncation


def test_multiple_terminal_sinks_survive_a_relay(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"one.py": SINK,
        "two.py": SINK,
        "relay.py": 'from one import handle as one\nfrom two import handle as two\ndef relay(cmd):\n    one(cmd)\n    two(cmd)\n',
        "api.py": 'from flask import request\nfrom relay import relay\ndef endpoint():\n    relay(request.args.get("cmd"))\n'}, monkeypatch)
    assert {f.sink_file for f in result.taint_flows} == {"one.py", "two.py"}


def test_recursive_edges_terminate_and_keep_sink(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"sink.py": SINK,
        "relay.py": 'from sink import handle\ndef relay(cmd):\n    relay(cmd)\n    handle(cmd)\n',
        "api.py": 'from flask import request\nfrom relay import relay\ndef endpoint():\n    relay(request.args.get("cmd"))\n'}, monkeypatch)
    assert any(f.sink_file == "sink.py" for f in result.taint_flows)
    assert len(result.taint_flows) <= 2


def test_caller_budget_is_disclosed(tmp_path, monkeypatch):
    import src.analyzer.taint as taint
    monkeypatch.setattr(taint, "MAX_CALLERS", 0)
    _, result = analyze(tmp_path, {"sink.py": SINK, "api.py":
        'from flask import request\nfrom sink import handle\ndef endpoint():\n    handle(request.args.get("cmd"))\n'}, monkeypatch)
    assert not result.taint_flows
    assert "cross_caller_cap" in result.truncation


def test_name_floor_remains_explicitly_heuristic(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"sink.py": SINK, "api.py":
        'from flask import request\ndef endpoint():\n    handle(request.args.get("cmd"))\n'}, monkeypatch)
    assert result.taint_flows
    assert result.callee_resolution["name_only_calls"] > 0
    for flow in result.taint_flows:
        assert flow.to_dict()["confidence"]["level"] == "medium"
        assert "name_only_callee" in flow.to_dict()["confidence"]["basis"]


def test_binding_only_resolves_unambiguous_imports():
    import ast
    from src.analyzer.taint_bindings import PythonCallBindings
    trees = {"pkg/sink.py": ast.parse(SINK),
             "pkg/__init__.py": ast.parse('from .sink import handle as exported'),
             "api.py": ast.parse('from pkg import exported as run\ndef endpoint():\n    run(1)\n')}
    bindings = PythonCallBindings(trees)
    call = next(n for n in ast.walk(trees["api.py"]) if isinstance(n, ast.Call))
    target = bindings.resolve("api.py", call)
    assert target is not None and target.file == "pkg/sink.py"
    trees["src/pkg/sink.py"] = ast.parse(SINK)
    assert PythonCallBindings(trees).resolve("api.py", call) is None


@pytest.mark.parametrize("code", [
    'from pkg.sink import handle\ndef endpoint(handle):\n    handle(1)',
    'from pkg.sink import handle\ndef endpoint():\n    handle = lambda x: x\n    handle(1)',
    'from pkg.sink import handle\nf = lambda handle: handle(1)',
    'from pkg.sink import handle\nf = [handle(1) for handle in []]',
])
def test_shadowed_names_are_not_proven_import_bindings(code):
    import ast
    from src.analyzer.taint_bindings import PythonCallBindings
    trees = {"pkg/sink.py": ast.parse(SINK), "api.py": ast.parse(code)}
    bindings = PythonCallBindings(trees)
    call = next(n for n in ast.walk(trees["api.py"]) if isinstance(n, ast.Call))
    assert bindings.resolve("api.py", call) is None


def test_reexport_cycle_and_call_cap_are_bounded(monkeypatch):
    import ast
    import src.analyzer.taint_bindings as tb
    trees = {"a.py": ast.parse('from b import f'), "b.py": ast.parse('from a import f'),
             "api.py": ast.parse('from a import f\ndef api():\n    f(1)\n    f(2)')}
    bindings = tb.PythonCallBindings(trees)
    assert bindings.callers() == {}
    monkeypatch.setattr(tb, "MAX_BOUND_CALLS", 1)
    limited = tb.PythonCallBindings(trees)
    assert limited.callers() == {} and limited.exhausted


def test_source_parse_and_byte_limits_are_reported(tmp_path, monkeypatch):
    import src.analyzer.taint as taint
    monkeypatch.setattr(taint, "MAX_TAINT_FILE_BYTES", 256)
    _, result = analyze(tmp_path, {"bad.py": "def broken(\n", "large.py": "#" + "x" * 400,
        "app.py": 'def safe():\n    return 1\n'}, monkeypatch)
    assert "python_parse_error" in result.truncation
    assert "source_byte_cap" in result.truncation


def test_source_snapshot_budget_is_explicit(tmp_path, monkeypatch):
    import src.analyzer.taint as taint
    monkeypatch.setattr(taint, "MAX_TAINT_SOURCE_FILES", 1)
    _, result = analyze(tmp_path, {"one.py": SINK, "two.py": SINK}, monkeypatch)
    assert "source_snapshot_cap" in result.truncation


def test_symlink_source_is_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYTO_TAINT_LSP", "0")
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text(SINK)
    try:
        (root / "linked.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    result = TaintAnalyzer(root, index={}).analyze_full()
    assert result.functions_analyzed == 0
    assert "source_symlink" in result.truncation


def test_regex_fallback_does_not_claim_ast_proof(tmp_path, monkeypatch):
    _, result = analyze(tmp_path, {"server.js": 'app.get("/", (req, res) => { res.send(req.query.name); });'}, monkeypatch)
    for flow in result.taint_flows:
        assert "intraprocedural_ast" not in flow.to_dict()["confidence"]["basis"]
        assert flow.to_dict()["confidence"]["level"] == "medium"
