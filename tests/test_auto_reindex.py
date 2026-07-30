"""Regression tests for workspace auto-reindex index isolation."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.engine as engine
import src.index_store as index_store
import src.tools.maintenance as maintenance
import src.watcher as watcher


def _write_index(root: Path, project: str, symbol_name: str) -> Path:
    index_dir = root / project / ".flyto-index"
    index_dir.mkdir(parents=True)
    payload = {
        "project": project,
        "projects": [project],
        "symbols": {
            f"{project}:src/example.py:{symbol_name}": {
                "name": symbol_name,
                "path": "src/example.py",
                "project": project,
            },
        },
        "files": {},
        "reverse_index": {},
        "dependencies": {},
    }
    (index_dir / "index.json").write_text(json.dumps(payload), encoding="utf-8")
    return index_dir


def _run_reindex(monkeypatch, roots):
    captured = []

    class FakeIndexEngine:
        def __init__(self, project, root, index_dir):
            captured.append((project, Path(root), Path(index_dir)))

        def scan(self, incremental):
            assert incremental is True
            return {
                "files_scanned": 1,
                "symbols_found": 1,
                "timing": {},
            }

    monkeypatch.setattr(
        maintenance,
        "load_index",
        lambda: {
            "projects": list(roots),
            "project_roots": {name: str(root) for name, root in roots.items()},
        },
    )
    monkeypatch.setattr(engine, "IndexEngine", FakeIndexEngine)
    monkeypatch.setattr(maintenance, "_invalidate_caches_unlocked", lambda: None)

    result = maintenance._perform_live_reindex_unlocked()

    assert result["reindexed"] == len(roots)
    assert result["errors"] == 0
    return captured


def test_workspace_reindex_writes_each_project_to_its_own_index(
    monkeypatch,
    tmp_path,
):
    active_root = tmp_path / "active"
    sibling_root = tmp_path / "sibling"
    active_root.mkdir()
    sibling_root.mkdir()
    roots = {"active": active_root, "sibling": sibling_root}

    monkeypatch.setattr(maintenance, "_EXPLICIT_INDEX_DIR", None)
    monkeypatch.setattr(
        maintenance,
        "INDEX_DIR",
        active_root / ".flyto-index",
    )

    captured = _run_reindex(monkeypatch, roots)

    assert captured == [
        ("active", active_root, active_root / ".flyto-index"),
        ("sibling", sibling_root, sibling_root / ".flyto-index"),
    ]


def test_explicit_index_dir_remains_authoritative(monkeypatch, tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    roots = {"first": first_root, "second": second_root}
    explicit_index_dir = tmp_path / "shared-index"

    monkeypatch.setattr(
        maintenance,
        "_EXPLICIT_INDEX_DIR",
        str(explicit_index_dir),
    )
    monkeypatch.setattr(maintenance, "INDEX_DIR", explicit_index_dir)

    captured = _run_reindex(monkeypatch, roots)

    assert captured == [
        ("first", first_root, explicit_index_dir),
        ("second", second_root, explicit_index_dir),
    ]


def test_project_scope_loads_only_the_requested_index(monkeypatch, tmp_path):
    alpha_dir = _write_index(tmp_path, "alpha", "alpha_symbol")
    beta_dir = _write_index(tmp_path, "beta", "beta_symbol")
    monkeypatch.setattr(
        index_store,
        "_discover_index_dirs",
        lambda: [alpha_dir, beta_dir],
    )
    index_store.invalidate_caches()

    with index_store.project_index_scope("alpha"):
        alpha_index = index_store.load_index()
    with index_store.project_index_scope("beta"):
        beta_index = index_store.load_index()

    assert alpha_index["projects"] == ["alpha"]
    assert beta_index["projects"] == ["beta"]
    assert {symbol["name"] for symbol in alpha_index["symbols"].values()} == {
        "alpha_symbol",
    }
    assert {symbol["name"] for symbol in beta_index["symbols"].values()} == {
        "beta_symbol",
    }


def test_project_auto_reindex_filters_sibling_changes(monkeypatch):
    detected_projects = []
    reindexed_projects = []

    class FakeChange:
        def __init__(self, project):
            self.project = project

    class FakeWatcher:
        def __init__(self, index):
            assert index["project"] == "alpha"

        def detect_changes(self, project=None):
            detected_projects.append(project)
            return [FakeChange("alpha"), FakeChange("beta")]

    monkeypatch.setattr(index_store, "_AUTO_REINDEX_ENABLED", True)
    monkeypatch.setattr(index_store, "_project_reindex_checks", {})
    monkeypatch.setattr(index_store, "_project_full_checks", {})
    monkeypatch.setattr(
        index_store,
        "load_index",
        lambda: {"project": "alpha"},
    )
    monkeypatch.setattr(watcher, "FileWatcher", FakeWatcher)
    monkeypatch.setattr(
        maintenance,
        "_perform_live_reindex_unlocked",
        lambda project=None: reindexed_projects.append(project)
        or {"reindexed": 1},
    )

    index_store._maybe_auto_reindex(project="alpha")

    assert detected_projects == ["alpha"]
    assert reindexed_projects == ["alpha"]
