"""Local task continuity and efficiency evidence backed by SQLite."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .task_usage import TokenUsage

SCHEMA_VERSION = 1
DEFAULT_TTL_DAYS = 7
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_RUNS = 1000
MAX_SUMMARY = 600
MAX_LIST_ITEMS = 100
ACTIVE_STATUSES = {"active", "needs_attention"}
TERMINAL_STATUSES = {"passed", "closed", "superseded"}
REQUIRED_COMPARISON_KEYS = {
    "experiment_id",
    "task_fingerprint",
    "repo_commit",
    "provider",
    "model",
    "tool_policy",
    "verification_policy",
    "sample_count",
}
_SPACE_RE = re.compile(r"\s+")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/[^/\s]+/")
_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+)?(?:gh[ps]_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{16,}|"
    r"sk_(?:live|test)_[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AIza[0-9A-Za-z_-]{20,})"
)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)


def _now() -> datetime:
    """Return the current timezone-aware UTC instant."""
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    """Serialize an instant as a stable UTC timestamp."""
    return (value or _now()).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    """Parse the store's UTC timestamp representation."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_json(value: Any) -> str:
    """Serialize evidence deterministically for storage and identities."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sanitize_summary(value: Any, limit: int = MAX_SUMMARY) -> str:
    """Retain a bounded task fact while removing code blocks, paths, and secrets."""
    text = _CODE_FENCE_RE.sub("[code omitted]", str(value or ""))
    text = _HOME_PATH_RE.sub("$HOME/", text)
    text = _SECRET_RE.sub("[redacted-secret]", text)
    text = _ASSIGNMENT_SECRET_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    return _SPACE_RE.sub(" ", text).strip()[:limit]


def _bounded_summaries(values: list[Any] | None) -> list[str]:
    """Sanitize and cap a list of human-readable task facts."""
    return [
        summary
        for summary in (sanitize_summary(value) for value in (values or [])[:MAX_LIST_ITEMS])
        if summary
    ]


def _relative_paths(values: list[Any] | None) -> list[str]:
    """Validate and deduplicate repository-relative paths."""
    paths: list[str] = []
    for value in (values or [])[:MAX_LIST_ITEMS]:
        raw = str(value or "").replace("\\", "/").strip()
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            raise ValueError("continuity paths must be repository-relative")
        normalized = path.as_posix().lstrip("./")
        if normalized and normalized not in paths:
            paths.append(normalized)
    return paths


def _safe_project(value: Any) -> str:
    """Reduce a project selector to a bounded local name."""
    project = sanitize_summary(value, 120) or "unknown"
    return Path(project).name if "/" in project else project


def _git_head(root: Path) -> str:
    """Read the current commit without changing repository state."""
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else "unversioned"


def default_task_db(project_root: str | Path = ".") -> Path:
    """Keep continuity with the local ignored index unless explicitly overridden."""
    configured = os.environ.get("FLYTO_INDEXER_TASK_DB", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(project_root).resolve() / ".flyto-index" / "task-runs.sqlite"


def _verification_summary(value: dict[str, Any] | None) -> dict[str, Any]:
    """Keep statuses and counts, never command output, source, or prompts."""
    result: dict[str, Any] = {}
    for key, item in list((value or {}).items())[:MAX_LIST_ITEMS]:
        lowered = key.lower()
        if any(term in lowered for term in ("output", "content", "prompt", "response")):
            continue
        if isinstance(item, (bool, int, float)) or item is None:
            result[str(key)[:80]] = item
        elif isinstance(item, str) and key in {
            "overall",
            "decision",
            "status",
            "phase",
            "summary",
        }:
            result[key] = sanitize_summary(item, 240)
        elif isinstance(item, dict) and key in {"summary", "metrics"}:
            nested = _verification_summary(item)
            if nested:
                result[key] = nested
    return result


def _comparison_context(value: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only bounded scalar experiment identity, never arbitrary provider data."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("comparison_context must be an object")
    result: dict[str, Any] = {}
    for key in REQUIRED_COMPARISON_KEYS:
        if key not in value:
            continue
        item = value[key]
        if key == "sample_count":
            if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                raise ValueError("comparison sample_count must be a positive integer")
            result[key] = item
        elif isinstance(item, (str, int, float)) and not isinstance(item, bool):
            normalized = sanitize_summary(item, 160)
            result[key] = normalized.lower() if key == "provider" else normalized
        else:
            raise ValueError(f"comparison {key} must be a scalar value")
    return result


def _verification_passes(value: dict[str, Any] | None) -> bool:
    """Require explicit positive proof and reject any failing signal."""
    evidence = _verification_summary(value)
    if not evidence:
        return False
    if any(evidence.get(key) is False for key in ("pass", "lint_passed", "tests_passed")):
        return False
    if str(evidence.get("overall", "")).lower() in {"fail", "failed", "error"}:
        return False
    if str(evidence.get("status", "")).lower() in {"fail", "failed", "error"}:
        return False
    if any(evidence.get(key) is True for key in ("pass", "lint_passed", "tests_passed")):
        return True
    return str(evidence.get("overall", evidence.get("status", ""))).lower() in {
        "pass",
        "passed",
        "success",
    }


class TaskRunStore:
    """One local database for resumable task state and normalized usage events."""

    def __init__(self, path: str | Path, *, readonly: bool = False):
        """Open a writable store or an existing database in read-only mode."""
        self.path = Path(path).expanduser().resolve()
        self.readonly = readonly
        if readonly:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            os.chmod(self.path.parent, 0o700)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Yield one configured SQLite connection and close it safely."""
        if self.readonly:
            connection = sqlite3.connect(
                f"file:{self.path.as_posix()}?mode=ro",
                uri=True,
                timeout=10,
            )
        else:
            connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        """Create the versioned normalized-count schema."""
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    base_commit TEXT NOT NULL,
                    task_fingerprint TEXT NOT NULL,
                    variant TEXT NOT NULL DEFAULT '',
                    comparison_context TEXT NOT NULL DEFAULT '{}',
                    completed_steps TEXT NOT NULL DEFAULT '[]',
                    remaining_steps TEXT NOT NULL DEFAULT '[]',
                    changed_paths TEXT NOT NULL DEFAULT '[]',
                    blockers TEXT NOT NULL DEFAULT '[]',
                    next_action TEXT NOT NULL DEFAULT '',
                    verification TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_runs_project_updated
                    ON task_runs(project, updated_at DESC);
                CREATE INDEX IF NOT EXISTS task_runs_task_updated
                    ON task_runs(task_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS usage_events (
                    event_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES task_runs(run_id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    source TEXT NOT NULL,
                    estimator TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cached_input_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    tool_calls INTEGER NOT NULL,
                    duration_ms REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, event_id),
                    CHECK (input_tokens >= 0),
                    CHECK (output_tokens >= 0),
                    CHECK (cached_input_tokens >= 0),
                    CHECK (reasoning_tokens >= 0),
                    CHECK (tool_calls >= 0),
                    CHECK (duration_ms >= 0)
                );
                CREATE INDEX IF NOT EXISTS usage_events_run
                    ON usage_events(run_id, created_at);
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        with suppress(OSError):
            os.chmod(self.path, 0o600)

    def schema_columns(self) -> dict[str, list[str]]:
        """Expose the privacy-reviewable storage surface."""
        with self._connection() as connection:
            return {
                table: [
                    str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")
                ]
                for table in ("task_runs", "usage_events")
            }

    def start_task(
        self,
        task_id: str,
        *,
        project: str,
        objective: str,
        project_root: str | Path = ".",
        base_commit: str = "",
        task_fingerprint: str = "",
        ttl_days: int = DEFAULT_TTL_DAYS,
        comparison_context: dict[str, Any] | None = None,
        variant: str = "",
    ) -> dict[str, Any]:
        """Start or resume one task while superseding other active work."""
        if not task_id.strip():
            raise ValueError("task_id is required")
        if ttl_days < 1 or ttl_days > 90:
            raise ValueError("ttl_days must be between 1 and 90")
        root = Path(project_root).resolve()
        commit = sanitize_summary(base_commit, 64) or _git_head(root)
        project_name = _safe_project(project)
        fingerprint = (
            sanitize_summary(task_fingerprint, 128)
            or hashlib.sha256(f"{task_id}\0{commit}".encode()).hexdigest()
        )
        context = _comparison_context(comparison_context)
        variant_name = sanitize_summary(variant, 40)
        context_identity = _canonical_json(context)
        run_identity = f"{task_id}\0{commit}\0{project_name}\0{variant_name}\0{context_identity}"
        run_id = f"run_{hashlib.sha256(run_identity.encode()).hexdigest()[:20]}"
        now = _now()
        expires = now + timedelta(days=ttl_days)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE task_runs
                SET status = 'superseded', updated_at = ?, completed_at = ?
                WHERE project = ? AND status IN ('active', 'needs_attention')
                  AND run_id != ?
                """,
                (_timestamp(now), _timestamp(now), project_name, run_id),
            )
            existing = connection.execute(
                "SELECT status FROM task_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO task_runs (
                        run_id, task_id, project, objective, status, base_commit,
                        task_fingerprint, variant, comparison_context, started_at,
                        updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        sanitize_summary(task_id, 160),
                        project_name,
                        sanitize_summary(objective, 360),
                        commit,
                        fingerprint,
                        variant_name,
                        _canonical_json(context),
                        _timestamp(now),
                        _timestamp(now),
                        _timestamp(expires),
                    ),
                )
            elif str(existing["status"]) in ACTIVE_STATUSES:
                connection.execute(
                    """
                    UPDATE task_runs SET objective = ?, updated_at = ?, expires_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        sanitize_summary(objective, 360),
                        _timestamp(now),
                        _timestamp(expires),
                        run_id,
                    ),
                )
        self.prune()
        return self.get_run(run_id) or {}

    def prune(
        self,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_runs: int = DEFAULT_MAX_RUNS,
    ) -> dict[str, int]:
        """Bound terminal evidence while never deleting an active task."""
        if retention_days < 1 or retention_days > 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        if max_runs < 1 or max_runs > 100_000:
            raise ValueError("max_runs must be between 1 and 100000")
        cutoff = _timestamp(_now() - timedelta(days=retention_days))
        with self._connection() as connection:
            old_cursor = connection.execute(
                """
                DELETE FROM task_runs
                WHERE status NOT IN ('active', 'needs_attention') AND updated_at < ?
                """,
                (cutoff,),
            )
            overflow_cursor = connection.execute(
                """
                DELETE FROM task_runs WHERE run_id IN (
                    SELECT run_id FROM task_runs
                    WHERE status NOT IN ('active', 'needs_attention')
                    ORDER BY updated_at DESC LIMIT -1 OFFSET ?
                )
                """,
                (max_runs,),
            )
        return {
            "expired_runs_deleted": max(old_cursor.rowcount, 0),
            "overflow_runs_deleted": max(overflow_cursor.rowcount, 0),
        }

    def update_continuity(
        self,
        identifier: str,
        *,
        completed_steps: list[Any] | None = None,
        remaining_steps: list[Any] | None = None,
        changed_paths: list[Any] | None = None,
        blockers: list[Any] | None = None,
        next_action: str | None = None,
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Replace the bounded resumable facts for one task run."""
        run = self.get_run(identifier)
        if not run:
            raise ValueError(f"unknown task run: {identifier}")
        completed = _bounded_summaries(completed_steps)
        remaining = _bounded_summaries(remaining_steps)
        paths = _relative_paths(changed_paths)
        blocker_items = _bounded_summaries(blockers)
        evidence = _verification_summary(verification)
        failed = (
            evidence.get("pass") is False
            or evidence.get("overall") == "fail"
            or evidence.get("status") == "fail"
        )
        status = "needs_attention" if blocker_items or failed else "active"
        now = _timestamp()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE task_runs SET status = ?, completed_steps = ?,
                    remaining_steps = ?, changed_paths = ?, blockers = ?,
                    next_action = ?, verification = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    _canonical_json(completed),
                    _canonical_json(remaining),
                    _canonical_json(paths),
                    _canonical_json(blocker_items),
                    sanitize_summary(next_action, 360),
                    _canonical_json(evidence),
                    now,
                    run["run_id"],
                ),
            )
        return self.get_run(str(run["run_id"])) or {}

    def finish_task(
        self,
        identifier: str,
        *,
        success: bool,
        verification: dict[str, Any] | None = None,
        blockers: list[Any] | None = None,
        next_action: str = "",
    ) -> dict[str, Any]:
        """Close a verified task or retain actionable failure state."""
        run = self.get_run(identifier)
        if not run:
            raise ValueError(f"unknown task run: {identifier}")
        evidence = _verification_summary(verification)
        if success and not _verification_passes(evidence):
            raise ValueError("a passed task requires positive verification evidence")
        now = _timestamp()
        status = "passed" if success else "needs_attention"
        blocker_items = [] if success else _bounded_summaries(blockers)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE task_runs SET status = ?, blockers = ?, next_action = ?,
                    remaining_steps = ?, verification = ?, updated_at = ?,
                    completed_at = ? WHERE run_id = ?
                """,
                (
                    status,
                    _canonical_json(blocker_items),
                    "" if success else sanitize_summary(next_action, 360),
                    "[]" if success else run["remaining_steps_json"],
                    _canonical_json(evidence),
                    now,
                    now if success else None,
                    run["run_id"],
                ),
            )
        return self.get_run(str(run["run_id"])) or {}

    def record_usage(
        self,
        identifier: str,
        usage: TokenUsage,
        *,
        provider: str,
        model: str,
        event_id: str = "",
        tool_calls: int = 0,
        duration_ms: float = 0,
        comparison_context: dict[str, Any] | None = None,
        variant: str = "",
    ) -> dict[str, Any]:
        """Append one idempotent normalized usage event."""
        run = self.get_run(identifier)
        if not run:
            raise ValueError(f"unknown task run: {identifier}")
        counts = (
            usage.input_tokens,
            usage.output_tokens,
            usage.cached_input_tokens,
            usage.reasoning_tokens,
        )
        if any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts
        ):
            raise ValueError("normalized token counts must be non-negative integers")
        if isinstance(tool_calls, bool) or not isinstance(tool_calls, int) or tool_calls < 0:
            raise ValueError("tool_calls must be a non-negative integer")
        if (
            isinstance(duration_ms, bool)
            or not math.isfinite(float(duration_ms))
            or duration_ms < 0
        ):
            raise ValueError("tool_calls and duration_ms must be non-negative")
        source = sanitize_summary(usage.source, 40).lower()
        if source not in {"reported", "estimated"}:
            raise ValueError("usage source must be reported or estimated")
        estimator = sanitize_summary(usage.estimator, 120)
        payload = {
            "run_id": run["run_id"],
            "provider": (sanitize_summary(provider, 80) or "unknown").lower(),
            "model": sanitize_summary(model, 120) or "unknown",
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "total_tokens": usage.total_tokens,
            "source": source,
            "estimator": estimator,
            "tool_calls": int(tool_calls),
            "duration_ms": float(duration_ms),
        }
        identity = (
            sanitize_summary(event_id, 160)
            or hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
        )
        context = (
            _comparison_context(comparison_context) if comparison_context is not None else None
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO usage_events (
                    event_id, run_id, provider, model, source, estimator,
                    input_tokens, output_tokens, cached_input_tokens,
                    reasoning_tokens, tool_calls, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity,
                    run["run_id"],
                    payload["provider"],
                    payload["model"],
                    source,
                    estimator,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cached_input_tokens,
                    usage.reasoning_tokens,
                    int(tool_calls),
                    float(duration_ms),
                    _timestamp(),
                ),
            )
            if context is not None or variant:
                connection.execute(
                    """
                    UPDATE task_runs SET comparison_context = ?, variant = ?,
                        updated_at = ? WHERE run_id = ?
                    """,
                    (
                        _canonical_json(context or run["comparison_context"]),
                        sanitize_summary(variant, 40) or run["variant"],
                        _timestamp(),
                        run["run_id"],
                    ),
                )
        return {
            "event_id": identity,
            "recorded": cursor.rowcount == 1,
            "run_id": run["run_id"],
            "usage": TokenUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                source=source,
                estimator=estimator,
            ).to_dict(),
        }

    def _row_to_run(self, row: sqlite3.Row) -> dict[str, Any]:
        """Decode stored JSON fields while preserving their raw form internally."""
        result = dict(row)
        for column in (
            "comparison_context",
            "completed_steps",
            "remaining_steps",
            "changed_paths",
            "blockers",
            "verification",
        ):
            result[f"{column}_json"] = result[column]
            result[column] = json.loads(result[column])
        return result

    def get_run(self, identifier: str) -> dict[str, Any] | None:
        """Return a run by run ID or the latest matching task ID."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM task_runs
                WHERE run_id = ? OR task_id = ?
                ORDER BY CASE WHEN run_id = ? THEN 0 ELSE 1 END, updated_at DESC
                LIMIT 1
                """,
                (identifier, identifier, identifier),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, *, project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List a bounded recent run history, optionally by project."""
        bounded_limit = max(1, min(int(limit), 200))
        with self._connection() as connection:
            if project:
                rows = connection.execute(
                    "SELECT * FROM task_runs WHERE project = ? ORDER BY updated_at DESC LIMIT ?",
                    (_safe_project(project), bounded_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM task_runs ORDER BY updated_at DESC LIMIT ?",
                    (bounded_limit,),
                ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def aggregate_usage(self, identifier: str) -> dict[str, Any]:
        """Aggregate counters and measurement identities for one run."""
        run = self.get_run(identifier)
        if not run:
            raise ValueError(f"unknown task run: {identifier}")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS events,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                    COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                    COALESCE(SUM(tool_calls), 0) AS tool_calls,
                    COALESCE(SUM(duration_ms), 0) AS duration_ms,
                    GROUP_CONCAT(DISTINCT source) AS sources,
                    GROUP_CONCAT(DISTINCT estimator) AS estimators,
                    GROUP_CONCAT(DISTINCT provider) AS providers,
                    GROUP_CONCAT(DISTINCT model) AS models
                FROM usage_events WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()
        result = dict(row) if row else {}
        result["total_tokens"] = int(result.get("input_tokens", 0)) + int(
            result.get("output_tokens", 0)
        )
        for key in ("sources", "estimators", "providers", "models"):
            result[key] = sorted(value for value in str(result.get(key) or "").split(",") if value)
        return result

    def continuity(self, *, project: str | None = None) -> dict[str, Any]:
        """Return only the active state needed to resume or hand off work."""
        runs = self.list_runs(project=project, limit=20)
        active = next((run for run in runs if run["status"] in ACTIVE_STATUSES), None)
        if not active:
            return {"status": "closed", "handoff_required": False}
        if _parse_timestamp(active["expires_at"]) <= _now():
            return {
                "status": "expired",
                "handoff_required": False,
                "run_id": active["run_id"],
                "task_id": active["task_id"],
            }
        reasons = []
        if active["status"] == "needs_attention":
            reasons.append("validation_or_blocker_requires_attention")
        if active["remaining_steps"]:
            reasons.append("remaining_steps")
        if active["changed_paths"]:
            reasons.append("unclosed_changed_paths")
        if active["blockers"]:
            reasons.append("blockers")
        if active["next_action"]:
            reasons.append("next_action")
        required = bool(reasons)
        return {
            "status": "needs_handoff" if required else "active",
            "handoff_required": required,
            "reasons": reasons,
            "run_id": active["run_id"],
            "task_id": active["task_id"],
            "project": active["project"],
            "objective": active["objective"],
            "base_commit": active["base_commit"],
            "completed": active["completed_steps"],
            "remaining": active["remaining_steps"],
            "changed_paths": active["changed_paths"],
            "blockers": active["blockers"],
            "next_action": active["next_action"],
            "verification": active["verification"],
            "expires_at": active["expires_at"],
        }

    def _measurement_identity(self, usage: dict[str, Any]) -> tuple[Any, ...]:
        """Build an equality key for reported versus estimated measurements."""
        return (
            tuple(usage.get("sources") or []),
            tuple(usage.get("estimators") or []),
            tuple(usage.get("providers") or []),
            tuple(usage.get("models") or []),
        )

    def compare_runs(self, baseline_id: str, current_id: str) -> dict[str, Any]:
        """Compare only verified runs with identical experiment conditions."""
        baseline = self.get_run(baseline_id)
        current = self.get_run(current_id)
        if not baseline or not current:
            return {"available": False, "reason": "task_run_not_found"}
        baseline_context = baseline["comparison_context"]
        current_context = current["comparison_context"]
        if not REQUIRED_COMPARISON_KEYS.issubset(baseline_context):
            return {"available": False, "reason": "baseline_context_incomplete"}
        if baseline_context != current_context:
            return {"available": False, "reason": "comparison_context_mismatch"}
        if not baseline["variant"] or baseline["variant"] == current["variant"]:
            return {"available": False, "reason": "paired_variants_required"}
        if baseline["status"] != "passed" or current["status"] != "passed":
            return {"available": False, "reason": "both_runs_must_pass_verification"}
        if not _verification_passes(baseline["verification"]) or not _verification_passes(
            current["verification"]
        ):
            return {"available": False, "reason": "passing_verification_evidence_missing"}
        baseline_usage = self.aggregate_usage(str(baseline["run_id"]))
        current_usage = self.aggregate_usage(str(current["run_id"]))
        if not baseline_usage["events"] or not current_usage["events"]:
            return {"available": False, "reason": "usage_evidence_missing"}
        if self._measurement_identity(baseline_usage) != self._measurement_identity(current_usage):
            return {"available": False, "reason": "measurement_method_mismatch"}
        declared_provider = str(current_context["provider"]).lower()
        declared_model = str(current_context["model"])
        if baseline_usage["providers"] != [declared_provider] or current_usage["providers"] != [
            declared_provider
        ]:
            return {"available": False, "reason": "declared_measurement_mismatch"}
        if baseline_usage["models"] != [declared_model] or current_usage["models"] != [
            declared_model
        ]:
            return {"available": False, "reason": "declared_measurement_mismatch"}
        before = int(baseline_usage["total_tokens"])
        after = int(current_usage["total_tokens"])
        if before <= 0:
            return {"available": False, "reason": "baseline_tokens_must_be_positive"}
        saved = before - after
        source = (baseline_usage["sources"] or ["unknown"])[0]
        return {
            "available": True,
            "claim": "measured_reduction" if source == "reported" else "estimated_reduction",
            "measurement": source,
            "baseline_run_id": baseline["run_id"],
            "current_run_id": current["run_id"],
            "before_tokens": before,
            "after_tokens": after,
            "saved_tokens": saved,
            "reduction_percent": round((saved / before) * 100, 2),
            "quality_regression": False,
            "comparison_context": current_context,
        }

    def report(self, identifier: str, *, compare_to: str = "") -> dict[str, Any]:
        """Build a privacy-safe task evidence report."""
        run = self.get_run(identifier)
        if not run:
            raise ValueError(f"unknown task run: {identifier}")
        usage = self.aggregate_usage(str(run["run_id"]))
        result = {
            "schema_version": 1,
            "run": {
                key: run[key]
                for key in (
                    "run_id",
                    "task_id",
                    "project",
                    "objective",
                    "status",
                    "base_commit",
                    "variant",
                    "started_at",
                    "updated_at",
                    "completed_at",
                )
            },
            "usage": usage,
            "verification": run["verification"],
            "efficiency": {
                "verified_successes_per_1000_tokens": (
                    round(1000 / usage["total_tokens"], 6)
                    if run["status"] == "passed" and usage["total_tokens"]
                    else 0.0
                )
            },
            "comparison": {"available": False, "reason": "not_requested"},
            "privacy": "normalized_counts_only_no_prompts_responses_or_source",
        }
        if compare_to:
            result["comparison"] = self.compare_runs(compare_to, str(run["run_id"]))
        return result


def _extract_task_id(contract: dict[str, Any] | None) -> str:
    """Resolve the local run or task identity carried by a task contract."""
    return str(
        (contract or {}).get("task_profile", {}).get("run_id")
        or (contract or {}).get("task_profile", {}).get("task_id")
        or ""
    )


def read_task_continuity(
    project_root: str | Path,
    *,
    project: str | None = None,
) -> dict[str, Any]:
    """Read continuity without creating or modifying any project file."""
    path = default_task_db(project_root)
    if not path.is_file():
        return {"status": "closed", "handoff_required": False}
    try:
        return TaskRunStore(path, readonly=True).continuity(project=project)
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        return {
            "status": "unavailable",
            "handoff_required": False,
            "reason": sanitize_summary(exc, 160),
        }


def _task_passed(result: dict[str, Any]) -> bool:
    """Interpret the established task validation result fields."""
    if result.get("overall") == "fail" or result.get("pass") is False:
        return False
    return not any(result.get(key) is False for key in ("lint_passed", "tests_passed"))


def observe_task_action(
    action: str,
    result: dict[str, Any],
    *,
    project: str | None,
    project_root: str | Path,
    description: str = "",
    task_contract: dict[str, Any] | None = None,
    current_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach local continuity to existing task actions without a new MCP tool."""
    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        and os.environ.get("FLYTO_INDEXER_TASK_TRACKING") != "1"
    ):
        return result
    if not isinstance(result, dict) or result.get("error"):
        return result
    store = TaskRunStore(default_task_db(project_root))
    project_name = project or Path(project_root).resolve().name
    if action == "plan":
        profile = result.get("task_profile") or {}
        task_id = str(profile.get("task_id") or "")
        if task_id:
            run = store.start_task(
                task_id,
                project=project_name,
                objective=description or profile.get("title") or task_id,
                project_root=project_root,
                task_fingerprint=str(profile.get("intent_fingerprint") or ""),
            )
            profile["run_id"] = run["run_id"]
            result["task_profile"] = profile
            plan_steps = list(result.get("execution_plan") or [])
            if not plan_steps:
                for subtask in result.get("sub_tasks") or []:
                    plan_steps.extend(subtask.get("execution_plan") or [])
            remaining = [
                step.get("purpose") or step.get("id")
                for step in plan_steps
                if step.get("required", True) and (step.get("purpose") or step.get("id"))
            ]
            if remaining:
                store.update_continuity(
                    run["run_id"],
                    remaining_steps=remaining,
                    next_action=f"Complete plan step: {remaining[0]}",
                )
    elif action == "gate":
        identifier = _extract_task_id(task_contract)
        if identifier and store.get_run(identifier):
            state = current_state or {}
            store.update_continuity(
                identifier,
                completed_steps=state.get("completed_steps") or state.get("completed_subtasks"),
                remaining_steps=state.get("remaining_steps"),
                changed_paths=state.get("changed_paths"),
                blockers=[] if result.get("pass") else result.get("required_actions"),
                next_action=(
                    f"Proceed to {result.get('phase')}"
                    if result.get("pass")
                    else (result.get("required_actions") or ["Complete gate remediation"])[0]
                ),
                verification={"pass": result.get("pass"), "phase": result.get("phase")},
            )
    elif action == "validate":
        identifier = _extract_task_id(task_contract)
        if identifier and store.get_run(identifier):
            passed = _task_passed(result)
            store.finish_task(
                identifier,
                success=passed,
                verification=result,
                blockers=[] if passed else result.get("required_actions") or ["Validation failed"],
                next_action="Fix validation failures and rerun task(validate)",
            )
    result["continuity"] = store.continuity(project=project_name)
    return result
