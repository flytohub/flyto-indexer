"""Project-scope regression tests for merged index consumers."""

from pathlib import Path

from src import index_store
from src.tools import code_info


def _multi_project_index():
    return {
        "projects": ["alpha", "beta"],
        "project_roots": {
            "alpha": "/workspace/alpha",
            "beta": "/workspace/beta",
        },
        "symbols": {
            "alpha:src/auth.py:function:login": {"path": "src/auth.py"},
            "alpha:tests/test_auth.py:function:test_login": {
                "path": "tests/test_auth.py"
            },
            "beta:src/auth.py:function:login": {"path": "src/auth.py"},
            "beta:spec/auth_test.py:function:test_login": {
                "path": "spec/auth_test.py"
            },
        },
        "dependencies": {},
    }


def test_discovery_skips_inaccessible_sibling_index(monkeypatch, tmp_path):
    """An unreadable sibling must not block otherwise valid index discovery."""
    project_index = tmp_path / "project" / ".flyto-index"
    sibling_index = tmp_path / "sibling" / ".flyto-index"
    blocked_index = tmp_path / "blocked" / ".flyto-index"
    project_index.mkdir(parents=True)
    sibling_index.mkdir(parents=True)
    blocked_index.parent.mkdir()

    original_exists = Path.exists

    def guarded_exists(path):
        if path == blocked_index:
            raise PermissionError("inaccessible system directory")
        return original_exists(path)

    monkeypatch.setattr(index_store, "INDEX_DIR", project_index)
    monkeypatch.setattr(index_store, "_EXPLICIT_INDEX_DIR", None)
    monkeypatch.setattr(Path, "exists", guarded_exists)

    discovered = index_store._discover_index_dirs()

    assert project_index.resolve() in discovered
    assert sibling_index.resolve() in discovered


def test_record_project_roots_preserves_explicit_and_single_index_roots():
    roots = {}

    index_store._record_project_roots(
        {
            "project": "alpha",
            "root_path": "/workspace/alpha",
            "project_roots": {"beta": "/workspace/beta"},
        },
        roots,
    )

    assert roots == {
        "alpha": "/workspace/alpha",
        "beta": "/workspace/beta",
    }


def test_list_projects_exposes_backward_compatible_name_and_root(monkeypatch):
    monkeypatch.setattr(code_info, "load_index", _multi_project_index)

    result = code_info.list_projects()
    alpha = next(item for item in result["projects"] if item["project"] == "alpha")

    assert alpha["name"] == "alpha"
    assert alpha["root"] == "/workspace/alpha"


def test_find_test_file_uses_project_scoped_mapper(monkeypatch):
    monkeypatch.setattr(index_store, "load_index", _multi_project_index)
    monkeypatch.setattr(index_store, "_test_mapper", None)
    monkeypatch.setattr(index_store, "_test_mappers", {})

    alpha = code_info.find_test_file("src/auth.py", project="alpha")
    beta = code_info.find_test_file("src/auth.py", project="beta")

    assert alpha["test_file"] == "tests/test_auth.py"
    assert beta["test_file"] == "spec/auth_test.py"
    assert alpha["project"] == "alpha"
    assert beta["project"] == "beta"
