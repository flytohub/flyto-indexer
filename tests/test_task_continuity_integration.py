"""Existing smart task and structure surfaces carry continuity automatically."""

from src.task_runs import TaskRunStore, default_task_db
from src.tools import smart


def test_smart_task_lifecycle_updates_one_shared_continuity_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYTO_INDEXER_TASK_TRACKING", "1")
    monkeypatch.setattr(smart, "_structure_scan_path", lambda info, project: str(tmp_path))
    monkeypatch.setattr(
        smart,
        "_task_plan",
        lambda *args: {
            "task_profile": {
                "task_id": "smart-task",
                "title": "Smart task",
                "intent_fingerprint": "fingerprint",
            },
            "execution_plan": [
                {"id": "step_01_impact", "purpose": "scope_callers", "required": True}
            ],
        },
    )
    plan = smart.smart_task(
        action="plan",
        description="Ship continuity",
        project=tmp_path.name,
    )
    run_id = plan["task_profile"]["run_id"]
    contract = {"task_profile": {"run_id": run_id}}
    monkeypatch.setattr(
        smart,
        "_task_gate",
        lambda *args: {"pass": True, "phase": "apply_changes"},
    )
    gate = smart.smart_task(
        action="gate",
        project=tmp_path.name,
        task_contract=contract,
        next_phase="implement",
        current_state={
            "completed_steps": ["impact reviewed"],
            "remaining_steps": ["verify"],
            "changed_paths": ["src/task_runs.py"],
        },
    )
    monkeypatch.setattr(
        smart,
        "_task_validate",
        lambda *args: {"pass": True, "tests_passed": True, "lint_passed": True},
    )
    validate = smart.smart_task(
        action="validate",
        project=tmp_path.name,
        task_contract=contract,
    )

    store = TaskRunStore(default_task_db(tmp_path), readonly=True)
    assert plan["continuity"]["status"] == "needs_handoff"
    assert plan["continuity"]["remaining"] == ["scope_callers"]
    assert gate["continuity"]["status"] == "needs_handoff"
    assert validate["continuity"]["status"] == "closed"
    assert store.get_run(run_id)["status"] == "passed"


def test_structure_profile_reads_continuity_without_modifying_database(tmp_path, monkeypatch):
    store = TaskRunStore(default_task_db(tmp_path))
    store.start_task(
        "profile-task",
        project=tmp_path.name,
        objective="Resume from another AI",
        project_root=tmp_path,
        base_commit="abc123",
    )
    database = default_task_db(tmp_path)
    before = database.stat().st_mtime_ns
    monkeypatch.setattr(smart, "_structure_scan_path", lambda info, project: str(tmp_path))
    monkeypatch.setattr(smart, "_structure_profile", lambda *args: {"project": tmp_path.name})

    result = smart.smart_structure(focus="profile", project=tmp_path.name)

    assert result["continuity"]["task_id"] == "profile-task"
    assert result["continuity"]["handoff_required"] is False
    assert database.stat().st_mtime_ns == before
