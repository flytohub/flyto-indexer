"""Regression tests for workspace auto-reindex index isolation."""

from pathlib import Path

import src.engine as engine
import src.tools.maintenance as maintenance


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
