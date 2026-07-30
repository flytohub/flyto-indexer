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


def test_canonical_health_cache_is_index_versioned_and_copy_safe(monkeypatch):
    index = {
        "root_path": "/tmp/project",
        "indexed_at": "2026-07-30T00:00:00Z",
        "symbols": {
            "proj:src/app.py:function:run": {
                "type": "function",
                "path": "src/app.py",
                "name": "run",
                "summary": "Run the app.",
                "ref_count": 1,
            },
        },
    }
    calls = {"complexity": 0, "dead": 0}

    def complexity(*_args, **_kwargs):
        calls["complexity"] += 1
        return {
            "total_analyzed": 1,
            "complex_count": 0,
            "complexity_burden": 0,
            "max_complexity_score": 0,
            "avg_complexity": 0.0,
            "functions": [],
        }

    def dead(*_args, **_kwargs):
        calls["dead"] += 1
        return {"total_dead": 0, "total_dead_lines": 0, "dead_symbols": []}

    quality._HEALTH_CACHE.clear()
    monkeypatch.setattr(quality, "_find_complex_functions_from_index", complexity)
    monkeypatch.setattr(quality, "_dead_code_from_index", dead)

    first = quality._code_health_score_from_index(index, project="proj")
    first["score"] = -1
    second = quality._code_health_score_from_index(index, project="proj")

    assert second["score"] != -1
    assert calls == {"complexity": 1, "dead": 1}


def test_health_snapshot_and_cache_change_with_symbol_content_hash(monkeypatch):
    index = {
        "root_path": "/tmp/project",
        "indexed_at": "2026-07-30T00:00:00Z",
        "symbols": {
            "proj:src/app.py:function:run": {
                "type": "function",
                "path": "src/app.py",
                "name": "run",
                "summary": "Run.",
                "content_hash": "before",
                "ref_count": 1,
            },
        },
    }
    monkeypatch.setattr(
        quality,
        "_find_complex_functions_from_index",
        lambda *_args, **_kwargs: {
            "total_analyzed": 1,
            "complex_count": 0,
            "complexity_burden": 0,
            "max_complexity_score": 0,
            "avg_complexity": 0.0,
            "functions": [],
        },
    )
    monkeypatch.setattr(
        quality,
        "_dead_code_from_index",
        lambda *_args, **_kwargs: {
            "total_dead": 0,
            "total_dead_lines": 0,
            "dead_symbols": [],
        },
    )

    quality._HEALTH_CACHE.clear()
    before = quality._code_health_score_from_index(index, project="proj")
    index["symbols"]["proj:src/app.py:function:run"]["content_hash"] = "after"
    after = quality._code_health_score_from_index(index, project="proj")

    assert before["snapshot"]["schema"] == "health-snapshot.v2"
    assert before["snapshot"]["id"] != after["snapshot"]["id"]
