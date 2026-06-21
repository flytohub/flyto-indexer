import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import quality


def test_health_complexity_score_weights_severity_not_just_count():
    mild_many = quality._health_complexity_score(
        func_count=100,
        complex_count=25,
        complexity_burden=125,
        max_complexity_score=5,
    )
    severe_one = quality._health_complexity_score(
        func_count=10,
        complex_count=1,
        complexity_burden=80,
        max_complexity_score=80,
    )

    assert mild_many == 15
    assert severe_one == 11
    assert severe_one < mild_many


def test_health_complexity_score_keeps_release_pressure_on_dense_complexity():
    dense_complexity = quality._health_complexity_score(
        func_count=100,
        complex_count=40,
        complexity_burden=600,
        max_complexity_score=35,
    )
    mostly_simple = quality._health_complexity_score(
        func_count=100,
        complex_count=5,
        complexity_burden=25,
        max_complexity_score=5,
    )

    assert dense_complexity < 10
    assert mostly_simple > dense_complexity


def test_code_health_score_reports_weighted_complexity_detail(monkeypatch):
    project = "proj"
    complex_symbol_id = "proj:src/app.py:function:branchy"
    simple_symbol_id = "proj:src/app.py:function:simple"
    symbols = {
        complex_symbol_id: {
            "type": "function",
            "path": "src/app.py",
            "name": "branchy",
            "line": 1,
            "params": [],
            "summary": "Branch-heavy helper",
            "ref_count": 1,
        },
        simple_symbol_id: {
            "type": "function",
            "path": "src/app.py",
            "name": "simple",
            "line": 30,
            "params": [],
            "summary": "Simple helper",
            "ref_count": 1,
        },
    }
    branch_lines = ["def branchy():"]
    branch_lines.extend(f"    if value == {idx}: pass" for idx in range(15))
    contents = {
        complex_symbol_id: "\n".join(branch_lines),
        simple_symbol_id: "def simple():\n    return 1",
    }

    monkeypatch.setattr(quality, "load_index", lambda: {"symbols": symbols})
    monkeypatch.setattr(
        quality,
        "get_symbol_content_text",
        lambda sym_id, _sym: contents[sym_id],
    )

    from src.tools import maintenance

    monkeypatch.setattr(
        maintenance,
        "find_dead_code",
        lambda project=None, min_lines=5: {"total_dead": 0},
    )

    result = quality.code_health_score(project=project)

    detail = result["breakdown"]["complexity"]["detail"]
    assert result["breakdown"]["complexity"]["score"] == 5
    assert "1/2 functions with high composite complexity" in detail
    assert "burden 5" in detail
    assert "top hotspot 5" in detail
