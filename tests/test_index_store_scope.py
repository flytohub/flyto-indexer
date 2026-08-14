"""Project-scope regression tests for merged index consumers."""

from pathlib import Path

import pytest

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

    monkeypatch.delenv("FLYTO_INDEX_DIR", raising=False)
    monkeypatch.chdir(project_index.parent)
    monkeypatch.setattr(Path, "exists", guarded_exists)

    discovered = index_store._discover_index_dirs()

    assert project_index.resolve() in discovered
    assert sibling_index.resolve() in discovered


def test_git_root_discovery_never_crosses_into_sibling_worktrees(
    monkeypatch,
    tmp_path,
):
    project = tmp_path / "worktrees" / "current"
    sibling = tmp_path / "worktrees" / "sibling"
    project_index = project / ".flyto-index"
    sibling_index = sibling / ".flyto-index"
    project_index.mkdir(parents=True)
    sibling_index.mkdir(parents=True)
    (project / ".git").write_text(
        "gitdir: ../../.git/worktrees/current\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("FLYTO_INDEX_DIR", raising=False)
    monkeypatch.chdir(project)

    assert index_store._discover_index_dirs() == [project_index.resolve()]


def test_explicit_missing_index_is_authoritative(monkeypatch, tmp_path):
    index_dir = tmp_path / "not-created-yet"
    monkeypatch.setenv("FLYTO_INDEX_DIR", str(index_dir))

    identity = index_store.resolve_project_identity()

    assert identity.index_dir == index_dir.resolve()
    assert identity.explicit_index is True
    assert index_store._discover_index_dirs() == [index_dir.resolve()]


def test_invalid_explicit_index_fails_closed(monkeypatch):
    monkeypatch.setenv("FLYTO_INDEX_DIR", "")

    with pytest.raises(ValueError, match="FLYTO_INDEX_DIR"):
        index_store.resolve_project_identity()


def test_frozen_identity_ignores_later_env_and_cwd_changes(monkeypatch, tmp_path):
    first = tmp_path / "first-index"
    second = tmp_path / "second-index"
    monkeypatch.setenv("FLYTO_INDEX_DIR", str(first))
    identity = index_store.resolve_project_identity("display-name")

    with index_store.project_identity_scope(identity):
        monkeypatch.setenv("FLYTO_INDEX_DIR", str(second))
        monkeypatch.chdir(tmp_path)
        active = index_store.current_project_identity()

    assert active == identity
    assert active.project_label == "display-name"
    assert active.index_dir == first.resolve()


def test_cache_key_is_structured_and_collision_safe(tmp_path):
    first = index_store.resolve_project_identity(
        "a-b", index_dir=tmp_path / "index"
    )
    second = index_store.resolve_project_identity(
        "a_b", index_dir=tmp_path / "index"
    )

    assert first.cache_key != second.cache_key


def test_explicit_project_root_keeps_its_label_with_an_external_index(tmp_path):
    project_root = tmp_path / "validation-runner"
    project_root.mkdir()
    index_dir = tmp_path / "external-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        '{"project":"indexed-target","root_path":"/workspace/indexed-target"}',
        encoding="utf-8",
    )

    identity = index_store.resolve_project_identity(
        project_root=project_root,
        index_dir=index_dir,
    )

    assert identity.project_root == project_root.resolve()
    assert identity.index_dir == index_dir.resolve()
    assert identity.project_label == "validation-runner"


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
