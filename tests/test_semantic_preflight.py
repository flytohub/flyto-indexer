"""Semantic refactor preflight tests."""

from unittest.mock import patch

from src.tools.references import edit_impact_preview


def test_preflight_reports_ambiguity_unresolved_refs_and_update_sites():
    selected_id = "proj:src/utils.py:function:helper"
    mock_index = {
        "symbols": {
            selected_id: {
                "path": "src/utils.py",
                "name": "helper",
                "type": "function",
                "params": ["value"],
            },
            "proj:src/other.py:function:helper": {
                "path": "src/other.py",
                "name": "helper",
                "type": "function",
                "params": [],
            },
            "proj:src/main.py:function:main": {
                "path": "src/main.py",
                "name": "main",
                "type": "function",
                "start_line": 10,
                "content": "return helper(value)",
            },
            "proj:tests/test_utils.py:function:test_helper": {
                "path": "tests/test_utils.py",
                "name": "test_helper",
                "type": "function",
                "start_line": 5,
                "content": "assert helper(1)",
            },
            "proj:src/plugin.py:function:load": {
                "path": "src/plugin.py",
                "name": "load",
                "type": "function",
            },
        },
        "reverse_index": {
            selected_id: [
                "proj:src/main.py:function:main",
                "proj:tests/test_utils.py:function:test_helper",
            ]
        },
        "dependencies": {
            "dynamic": {
                "source": "proj:src/plugin.py:function:load",
                "target": "registry.helper",
                "type": "calls",
                "line": 22,
                "metadata": {},
            }
        },
    }

    with patch("src.tools.references.load_index", return_value=mock_index):
        result = edit_impact_preview(selected_id, change_type="move")

    preflight = result["semantic_preflight"]
    assert result["change_type"] == "move"
    assert preflight["identity"]["ambiguous"] is True
    assert preflight["identity"]["candidate_count"] == 2
    assert preflight["reference_classes"] == {
        "direct_indexed": 2,
        "name_only": 0,
        "dynamic_or_unresolved": 1,
    }
    assert preflight["required_update_sites"]["production"] == ["src/main.py"]
    assert preflight["required_update_sites"]["tests"] == ["tests/test_utils.py"]
    assert preflight["required_update_sites"]["manual_review"] == ["src/plugin.py"]
    assert preflight["manual_review_required"] is True
