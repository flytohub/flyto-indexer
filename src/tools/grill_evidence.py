"""Repository evidence snapshots, freshness checks, and Grill artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Callable


MAX_SNAPSHOT_FILES = 256
MAX_FILES_PER_DECISION = 64
MAX_HASH_BYTES = 8 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def resolve_project_root(
    project: str | None,
    index_loader: Callable[[], dict] | None = None,
) -> Path | None:
    """Resolve an indexed project name or explicit directory without guessing."""
    if project:
        direct = Path(project).expanduser()
        if direct.is_dir():
            return direct.resolve()
        relative = (Path.cwd() / direct).resolve()
        if relative.is_dir():
            return relative
    store = None
    if index_loader is None:
        try:
            store = import_module("..index_store", package=__package__)
        except ImportError:  # pragma: no cover - direct module execution
            store = import_module("index_store")
        identity = store.current_project_identity()
        if not project or project.strip() == identity.project_label:
            return identity.project_root
    try:
        if store is not None:
            with store.project_index_scope(project):
                index = store.load_index()
        else:
            assert index_loader is not None
            index = index_loader()
        roots = (index or {}).get("project_roots", {})
    except Exception:
        return None
    root = roots.get(project) if project else None
    if root and Path(root).is_dir():
        return Path(root).resolve()
    if not project and len(roots) == 1:
        only_root = next(iter(roots.values()))
        if Path(only_root).is_dir():
            return Path(only_root).resolve()
    return None


def _scoped_path(root: Path, raw_path: Any) -> tuple[str, Path] | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path.strip())
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return None
    return relative, resolved


def _file_state(path: Path) -> dict:
    if not path.is_file():
        return {"exists": False}
    stat = path.stat()
    digest = hashlib.sha256()
    remaining = MAX_HASH_BYTES
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
        "hash_truncated": stat.st_size > MAX_HASH_BYTES,
    }


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    head = result.stdout.strip()
    return head if result.returncode == 0 and len(head) == 40 else None


def _decision_evidence_paths(node: dict) -> list[str]:
    paths = []
    for item in node.get("evidence") or []:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            path = item["path"].strip()
            if path and path not in paths:
                paths.append(path)
    return paths[:MAX_FILES_PER_DECISION]


def capture_evidence_snapshot(project: str | None, decisions: list[dict]) -> dict:
    """Capture content-addressed state only for files used as decision evidence."""
    root = resolve_project_root(project)
    if root is None:
        return {
            "status": "unavailable",
            "captured_at": _now(),
            "project": project,
            "reason": "project_root_not_resolved",
            "decisions": {},
        }
    decision_states = {}
    file_count = 0
    for node in decisions:
        states = {}
        rejected_paths = []
        for raw_path in _decision_evidence_paths(node):
            if file_count >= MAX_SNAPSHOT_FILES:
                break
            scoped = _scoped_path(root, raw_path)
            if scoped is None:
                rejected_paths.append(raw_path)
                continue
            relative, resolved = scoped
            if relative in states:
                continue
            states[relative] = _file_state(resolved)
            file_count += 1
        material = {"paths": states, "rejected_paths": rejected_paths}
        decision_states[node["id"]] = {
            **material,
            "fingerprint": _fingerprint(material),
        }
    snapshot = {
        "status": "captured",
        "captured_at": _now(),
        "project": project,
        "project_root": str(root),
        "git_head": _git_head(root),
        "decisions": decision_states,
        "file_count": file_count,
    }
    snapshot["fingerprint"] = _fingerprint(
        {key: value for key, value in snapshot.items() if key != "captured_at"}
    )
    return snapshot


def check_evidence_freshness(contract: dict, project: str | None = None) -> dict:
    """Compare current repository evidence with the frozen snapshot."""
    snapshot = contract.get("evidence_snapshot")
    if not isinstance(snapshot, dict):
        return {
            "pass": True,
            "status": "legacy_contract_without_snapshot",
            "stale_decision_ids": [],
            "changes": [],
        }
    if snapshot.get("status") != "captured":
        return {
            "pass": True,
            "status": snapshot.get("status", "unavailable"),
            "stale_decision_ids": [],
            "changes": [],
            "warning": snapshot.get("reason", "evidence snapshot unavailable"),
        }
    rejected = [
        (decision_id, path)
        for decision_id, state in snapshot.get("decisions", {}).items()
        for path in state.get("rejected_paths", [])
    ]
    if rejected:
        stale_ids = list(dict.fromkeys(decision_id for decision_id, _ in rejected))
        return {
            "pass": False,
            "status": "invalid_evidence_scope",
            "stale_decision_ids": stale_ids,
            "changes": [
                {
                    "decision_id": decision_id,
                    "path": path,
                    "reason": "evidence_path_outside_project_root",
                }
                for decision_id, path in rejected
            ],
        }
    root = resolve_project_root(project or contract.get("project"))
    if root is None:
        stored_root = Path(snapshot.get("project_root", ""))
        root = stored_root.resolve() if stored_root.is_dir() else None
    if root is None:
        decision_ids = [
            decision_id
            for decision_id, state in snapshot.get("decisions", {}).items()
            if state.get("paths")
        ]
        return {
            "pass": not decision_ids,
            "status": "project_root_unavailable",
            "stale_decision_ids": decision_ids,
            "changes": [],
        }
    changes = []
    stale_ids = []
    for decision_id, state in snapshot.get("decisions", {}).items():
        for relative, previous in state.get("paths", {}).items():
            scoped = _scoped_path(root, relative)
            current = _file_state(scoped[1]) if scoped else {"exists": False}
            comparable_previous = {
                key: previous.get(key)
                for key in ("exists", "size", "sha256", "hash_truncated")
            }
            comparable_current = {
                key: current.get(key)
                for key in ("exists", "size", "sha256", "hash_truncated")
            }
            if comparable_current != comparable_previous:
                if decision_id not in stale_ids:
                    stale_ids.append(decision_id)
                changes.append(
                    {
                        "decision_id": decision_id,
                        "path": relative,
                        "before": comparable_previous,
                        "after": comparable_current,
                    }
                )
    return {
        "pass": not stale_ids,
        "status": "fresh" if not stale_ids else "stale",
        "stale_decision_ids": stale_ids,
        "changes": changes,
        "current_git_head": _git_head(root),
        "snapshot_git_head": snapshot.get("git_head"),
    }


def selective_reopen_plan(contract: dict, freshness: dict) -> list[dict]:
    """Build an amendment plan containing only decisions with stale evidence."""
    stale = set(freshness.get("stale_decision_ids") or [])
    changes = freshness.get("changes") or []
    return [
        {
            "decision_id": node.get("id"),
            "status": "open",
            "previous_answer": node.get("answer"),
            "reason": "repository_evidence_changed",
            "changed_paths": sorted(
                {
                    change["path"]
                    for change in changes
                    if change.get("decision_id") == node.get("id")
                }
            ),
        }
        for node in contract.get("decisions", [])
        if node.get("id") in stale
    ]


def render_adr(contract: dict) -> str:
    """Render a deterministic Markdown ADR from a frozen decision contract."""
    title = str(contract.get("description") or "Decision contract").strip()
    lines = [
        f"# ADR: {title}",
        "",
        "- Status: Accepted",
        f"- Decision session: `{contract.get('session_id', '')}`",
        f"- Contract version: `{contract.get('version', '')}`",
        "",
        "## Decisions",
        "",
    ]
    for node in contract.get("decisions", []):
        lines.extend(
            [
                f"### {node.get('id', 'decision')}",
                "",
                f"- Severity: {node.get('severity', 'unknown')}",
                f"- Decision: {node.get('answer') or node.get('recommendation') or ''}",
                f"- Rationale: {node.get('rationale') or 'Not provided.'}",
                (
                    "- Evidence: "
                    f"{len(node.get('evidence') or [])} repository item(s)"
                ),
                (
                    "- Confidence: "
                    f"{(node.get('confidence') or {}).get('recommendation', 'unknown')}"
                ),
                (
                    "- Strongest objection: "
                    f"{(node.get('adversarial_review') or {}).get('strongest_objection', '')}"
                ),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def decision_audit_artifact(contract: dict) -> dict:
    """Create a compact, machine-readable audit record."""
    snapshot = contract.get("evidence_snapshot") or {}
    return {
        "session_id": contract.get("session_id"),
        "contract_version": contract.get("version"),
        "decision_count": len(contract.get("decisions") or []),
        "readiness": contract.get("readiness"),
        "evidence_snapshot_fingerprint": snapshot.get("fingerprint"),
        "evidence_file_count": snapshot.get("file_count", 0),
        "generated_at": _now(),
    }
