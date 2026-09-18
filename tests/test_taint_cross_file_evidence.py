"""Exercise real index -> cross-function analysis, not a parallel test scanner."""
import ast
import json
import textwrap

import pytest

from src.analyzer.taint import TaintAnalyzer
from src.engine import IndexEngine


def analyze(tmp_path, files, *, repeat=False):
    for name, content in files.items():
        source = textwrap.dedent(content)
        if name.endswith(".py"):
            ast.parse(source)
        (tmp_path / name).write_text(source, encoding="utf-8")
    engine = IndexEngine("evidence", tmp_path, index_dir=tmp_path / ".flyto-index")
    engine.scan(incremental=False)
    index = json.loads((tmp_path / ".flyto-index/index.json").read_text())
    analyzer = TaintAnalyzer(tmp_path, index=index)
    first = analyzer.analyze_full()
    return (first, analyzer.analyze_full()) if repeat else first


SINK = textwrap.dedent("""\
    import os
    def run(value):
        os.system(value)
""")
CALLER = textwrap.dedent("""\
    from flask import request
    from worker import run
    def handler():
        value = request.args.get('value')
        return run(value)
""")


def test_cross_file_endpoints_are_source_and_actual_sink(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYTO_TAINT_LSP", "0")
    result = analyze(tmp_path, {"worker.py": SINK, "app.py": CALLER})
    assert len(result.taint_flows) == 1
    f = result.taint_flows[0].to_dict()
    assert (f["source_file"], f["source_line"]) == ("app.py", 4)
    assert (f["sink_file"], f["sink_line"]) == ("worker.py", 3)
    assert "os.system" in f["sink"]
    assert any("worker.py" in step for step in f["path"])
    assert "typed_or_cross_function_resolution" not in f["confidence"]["basis"]
    assert f["confidence"]["level"] == "medium"


def test_cross_file_multi_hop_preserves_sink_and_intermediate_path(tmp_path):
    result = analyze(tmp_path, {
        "worker.py": SINK,
        "service.py": "from worker import run\ndef forward(value):\n    return run(value)\n",
        "app.py": CALLER.replace("from worker import run", "from service import forward").replace("run(value)", "forward(value)"),
    })
    assert len(result.taint_flows) == 1
    f = result.taint_flows[0].to_dict()
    assert (f["sink_file"], f["sink_line"]) == ("worker.py", 3)
    assert any("service.py" in step for step in f["path"])


@pytest.mark.parametrize("clean", ["'constant'", "shlex.quote(value)"])
def test_cross_file_clean_overwrite_does_not_retain_previous_taint(tmp_path, clean):
    caller = CALLER.replace("    return", f"    value = {clean}\n    return")
    result = analyze(tmp_path, {"worker.py": SINK, "app.py": "import shlex\n" + caller})
    assert result.taint_flows == []


def test_cross_file_same_parameter_can_reach_distinct_sinks(tmp_path):
    result = analyze(tmp_path, {
        "worker.py": "import os\ndef run(value):\n    os.system(value)\n    eval(value)\n",
        "app.py": CALLER,
    })
    assert {(f.sink_file, f.sink_line) for f in result.taint_flows} == {("worker.py", 3), ("worker.py", 4)}


def test_repeated_analyze_does_not_lose_cross_file_flows(tmp_path):
    first, second = analyze(tmp_path, {"worker.py": SINK, "app.py": CALLER}, repeat=True)
    assert first.to_dict() == second.to_dict()


def test_explicit_index_target_does_not_bind_to_namesake(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYTO_TAINT_LSP", "0")
    result = analyze(tmp_path, {
        "worker.py": SINK,
        "safe.py": "def run(value):\n    return value\n",
        "app.py": "from flask import request\nimport safe, worker\ndef handler():\n    value = request.args.get('value')\n    safe.run(value)\n    worker.run(value)\n",
    })
    assert [f.line for f in result.taint_flows] == [6]


def test_regex_finding_budget_cannot_overflow_or_be_silent(tmp_path, monkeypatch):
    monkeypatch.setattr("src.analyzer.taint.MAX_FINDINGS", 2)
    result = analyze(tmp_path, {"app.js": "\n".join([f"eval(req.query.p{i});" for i in range(12)])})
    assert len(result.taint_flows) <= 2
    assert "finding_cap" in result.truncation


def test_cross_file_import_alias_and_named_argument(tmp_path):
    caller = CALLER.replace("from worker import run", "from worker import run as launch").replace("run(value)", "launch(value=value)")
    result = analyze(tmp_path, {"worker.py": SINK, "app.py": caller})
    assert len(result.taint_flows) == 1
    assert result.taint_flows[0].sink_file == "worker.py"


def test_ambiguous_source_coordinate_is_not_the_call_line(tmp_path):
    caller = CALLER.replace("    return", "    second = request.args.get('value')\n    return")
    result = analyze(tmp_path, {"worker.py": SINK, "app.py": caller})
    assert len(result.taint_flows) == 1
    assert result.taint_flows[0].to_dict()["source_line"] == 0
