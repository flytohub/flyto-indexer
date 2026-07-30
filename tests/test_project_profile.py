"""Focused tests for bounded, classification-aware project profiles."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from profile.builder import shape_profile
from profile.filesystem import classify_path, scan_filesystem
from profile.index_extract import extract_from_index
from tools.smart import _add_explicit_project_counts


def test_classify_path_uses_specific_non_production_classes():
    assert classify_path("src/app.py") == "source"
    assert classify_path("tests/test_app.py") == "test"
    assert classify_path("tests/fixtures/vulnerable_api.py") == "fixture"
    assert classify_path("examples/demo.py") == "example"
    assert classify_path("src/generated/client.py") == "generated"
    assert classify_path("package-lock.json") == "generated"


def test_filesystem_counts_have_explicit_semantics(tmp_path):
    files = {
        "src/app.py": "",
        "README.md": "",
        "tests/test_app.py": "",
        "tests/fixtures/vulnerable_api.py": "",
        "examples/demo.py": "",
        "src/generated/client.py": "",
        "package-lock.json": "{}",
    }
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    result = scan_filesystem(tmp_path)

    assert result["file_count"] == result["total_file_count"] == 7
    assert result["source_file_count"] == 2
    assert result["non_production_file_count"] == 5
    assert result["file_class_counts"] == {
        "source": 2,
        "test": 1,
        "fixture": 1,
        "example": 1,
        "generated": 2,
    }
    assert "indexed_file_count" in result["file_count_semantics"]


def _write_index(root: Path) -> None:
    index_dir = root / ".flyto-index"
    index_dir.mkdir()
    symbols = {
        "demo:src/api.py:api:GET /prod": {
            "name": "GET /prod",
            "type": "api",
            "path": "src/api.py",
            "metadata": {"method": "GET"},
        },
        "demo:tests/fixtures/vuln.py:api:GET /fixture": {
            "name": "GET /fixture",
            "type": "api",
            "path": "tests/fixtures/vuln.py",
            "metadata": {"method": "GET"},
        },
        "demo:src/models.py:class:Order": {
            "name": "Order",
            "type": "class",
            "path": "src/models.py",
            "metadata": {"fields": ["id"]},
        },
        "demo:tests/fixtures/models.py:class:FakeOrder": {
            "name": "FakeOrder",
            "type": "class",
            "path": "tests/fixtures/models.py",
            "metadata": {"fields": ["id"]},
        },
    }
    (index_dir / "index.json").write_text(
        json.dumps({
            "symbols": symbols,
            "dependencies": {},
            "reverse_index": {},
        }),
        encoding="utf-8",
    )


def test_index_extract_excludes_fixtures_from_production_signals(tmp_path):
    _write_index(tmp_path)

    production = extract_from_index(tmp_path)
    all_signals = extract_from_index(tmp_path, include_non_production=True)

    assert production["indexed_file_count"] == 4
    assert production["indexed_source_file_count"] == 2
    assert production["indexed_file_class_counts"]["fixture"] == 2
    assert [route["path"] for route in production["api_definitions"]] == ["/prod"]
    assert [model["name"] for model in production["models"]] == ["Order"]
    assert len(all_signals["api_definitions"]) == 2
    assert len(all_signals["models"]) == 2


def test_shape_profile_is_bounded_and_paginated():
    profile = {
        "name": "demo",
        "file_count": 100,
        "api_definitions": [{"path": f"/v1/{i}"} for i in range(100)],
        "models": [{"name": f"Model{i}"} for i in range(30)],
        "lenses": {"security": {"findings": list(range(40))}},
    }

    compact = shape_profile(
        profile,
        result_mode="compact",
        limit=5,
        cursor=10,
    )
    paged = shape_profile(profile, result_mode="paged", limit=5, cursor=10)
    full = shape_profile(profile, result_mode="full")

    assert [item["path"] for item in compact["api_definitions"]] == [
        f"/v1/{i}" for i in range(10, 15)
    ]
    assert compact["pagination"]["fields"]["api_definitions"]["total"] == 100
    assert compact["pagination"]["has_more"] is True
    assert "lenses" not in compact
    assert len(paged["lenses"]["security"]["findings"]) == 5
    assert len(full["api_definitions"]) == 100
    assert "pagination" not in full


def test_compact_profile_has_small_runtime_and_wire_budget():
    profile = {
        "name": "large",
        "file_count": 5000,
        "api_definitions": [
            {"path": f"/api/{i}", "summary": "x" * 200}
            for i in range(5000)
        ],
        "models": [{"name": f"Model{i}"} for i in range(5000)],
    }

    started = time.perf_counter()
    compact = shape_profile(profile, result_mode="compact", limit=10)
    elapsed = time.perf_counter() - started
    wire_size = len(json.dumps(compact).encode("utf-8"))

    assert elapsed < 0.25
    assert wire_size < 10_000


def test_project_overview_labels_indexed_file_counts():
    result = _add_explicit_project_counts({
        "projects": [
            {"name": "one", "files": 3},
            {"name": "two", "files": 5},
        ],
    })

    assert result["total_indexed_files"] == 8
    assert result["projects"][0]["indexed_file_count"] == 3
    assert "unique file paths" in (
        result["projects"][0]["file_count_semantics"]["files"]
    )
