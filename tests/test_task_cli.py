"""Direct task-planning scope regressions shared by Python and CLI callers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import index_store as top_level_index_store
from src import index_store as src_index_store
from src.task_cli import configure_task_parser, execute_task_command
from src.tools import smart as src_smart
from tools import smart as top_level_smart


def _write_index(
    index_dir: Path,
    project: str,
    *,
    root_path: Path | None = None,
) -> None:
    index_dir.mkdir(parents=True)
    payload = {"project": project, "symbols": {}}
    if root_path is not None:
        payload["root_path"] = str(root_path)
    (index_dir / "index.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _assert_cli_plan_stays_in_selected_index(
    tmp_path: Path,
    monkeypatch,
    *,
    index_store,
    smart,
) -> None:
    selected = tmp_path / "selected"
    sibling = tmp_path / "ambient-sibling"
    selected.mkdir()
    sibling.mkdir()
    (selected / "target.py").write_text("value = 1\n", encoding="utf-8")
    selected_index = selected / ".flyto-index"
    sibling_index = sibling / ".flyto-index"
    _write_index(selected_index, str(selected))
    _write_index(sibling_index, str(sibling))

    loaded: list[Path] = []

    def load_single(index_dir: Path) -> dict:
        loaded.append(index_dir)
        if index_dir == sibling_index:
            raise AssertionError("ambient sibling index was loaded")
        return {"project": str(selected), "symbols": {}}

    index_store.invalidate_caches()
    monkeypatch.setenv(
        "FLYTO_INDEXER_TASK_DB",
        str(tmp_path / "task-runs.sqlite"),
    )
    monkeypatch.setattr(
        index_store,
        "_discover_index_dirs",
        lambda: [selected_index, sibling_index],
    )
    monkeypatch.setattr(index_store, "_load_single_index", load_single)

    parser = argparse.ArgumentParser()
    configure_task_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "task",
            "plan",
            "--description",
            "Refactor target",
            "--target",
            "target.py",
            "--project",
            str(selected),
        ]
    )

    try:
        result, should_fail = execute_task_command(
            args,
            smart_task=smart.smart_task,
        )
        assert index_store._current_project_scope() is None
        assert should_fail is False
        assert "task_profile" in result
        assert loaded == [selected_index]

        index_store.invalidate_caches()
        loaded.clear()
        with index_store.project_index_scope("outer-project"):
            result, should_fail = execute_task_command(
                args,
                smart_task=smart.smart_task,
            )
            assert index_store._current_project_scope() == "outer-project"

        assert should_fail is False
        assert "task_profile" in result
        # A caller-frozen identity is authoritative for the nested action. The
        # CLI project label must neither replace it nor load any sibling index.
        assert loaded == []
    finally:
        index_store.invalidate_caches()


def test_execute_task_plan_keeps_continuity_in_selected_index_for_src_import(
    tmp_path: Path, monkeypatch,
) -> None:
    _assert_cli_plan_stays_in_selected_index(
        tmp_path,
        monkeypatch,
        index_store=src_index_store,
        smart=src_smart,
    )


def test_execute_task_plan_keeps_continuity_in_selected_index_for_top_level_import(
    tmp_path: Path, monkeypatch,
) -> None:
    _assert_cli_plan_stays_in_selected_index(
        tmp_path,
        monkeypatch,
        index_store=top_level_index_store,
        smart=top_level_smart,
    )


def _assert_public_nonplan_action_stays_in_selected_index(
    tmp_path: Path,
    monkeypatch,
    *,
    action: str,
) -> None:
    project = "selected-project"
    selected = tmp_path / "selected"
    sibling = tmp_path / "ambient-sibling"
    selected.mkdir()
    sibling.mkdir()
    (selected / "target.py").write_text("value = 1\n", encoding="utf-8")
    selected_index = selected / ".flyto-index"
    sibling_index = sibling / ".flyto-index"
    _write_index(selected_index, project, root_path=selected)
    _write_index(sibling_index, "ambient-sibling", root_path=sibling)

    loaded: list[Path] = []

    def load_single(index_dir: Path) -> dict:
        loaded.append(index_dir)
        if index_dir == sibling_index:
            raise AssertionError("ambient sibling index was loaded")
        return {
            "project": project,
            "root_path": str(selected),
            "symbols": {},
        }

    src_index_store.invalidate_caches()
    monkeypatch.setenv(
        "FLYTO_INDEXER_TASK_DB",
        str(tmp_path / "task-runs.sqlite"),
    )
    monkeypatch.setattr(
        src_index_store,
        "_discover_index_dirs",
        lambda: [selected_index, sibling_index],
    )
    monkeypatch.setattr(src_index_store, "_load_single_index", load_single)

    try:
        contract = src_smart.smart_task(
            action="plan",
            description="Refactor target",
            targets=["target.py"],
            project=project,
        )
        assert sibling_index not in loaded

        src_index_store.invalidate_caches()
        loaded.clear()
        if action == "gate":
            src_smart.smart_task(
                action="gate",
                project=project,
                task_contract=contract,
                next_phase="assess",
                current_state={},
            )
        else:
            monkeypatch.setattr(
                src_smart,
                "_validation_mod",
                lambda: SimpleNamespace(
                    validate_changes=lambda **_kwargs: {"overall": "pass"},
                ),
            )
            src_smart.smart_task(
                action="validate",
                project=project,
                task_contract=contract,
                current_state={"changed_paths": []},
                run_tests=False,
            )

        assert src_index_store._current_project_scope() is None
        assert selected_index in loaded
        assert sibling_index not in loaded
    finally:
        src_index_store.invalidate_caches()


def test_public_task_gate_does_not_load_ambient_sibling_index(
    tmp_path: Path, monkeypatch,
) -> None:
    _assert_public_nonplan_action_stays_in_selected_index(
        tmp_path,
        monkeypatch,
        action="gate",
    )


def test_public_task_validate_does_not_load_ambient_sibling_index(
    tmp_path: Path, monkeypatch,
) -> None:
    _assert_public_nonplan_action_stays_in_selected_index(
        tmp_path,
        monkeypatch,
        action="validate",
    )
