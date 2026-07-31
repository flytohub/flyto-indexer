"""Local, privacy-preserving feedback for AI-assisted development sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    _fcntl = None


FEEDBACK_SCHEMA = "development-feedback.v1"
FEEDBACK_SUMMARY_SCHEMA = "development-feedback-summary.v1"
VALID_CATEGORIES = {
    "bad_recommendation",
    "false_negative",
    "false_positive",
    "framework_gap",
    "gate_friction",
    "missing_context",
    "runtime_mismatch",
    "slow_scan",
    "validation_failure",
    "other",
}
VALID_SEVERITIES = {"low", "medium", "high", "critical"}
_SEVERITY_WEIGHT = {"low": 1, "medium": 3, "high": 7, "critical": 12}
_MAX_RECORDS = 5000
_MAX_TEXT = 600
_LOCK = threading.RLock()
_SPACE_RE = re.compile(r"\s+")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/[^/\s]+/")
_TOKEN_RE = re.compile(
    r"(?i)(?:bearer\s+)?(?:gh[ps]_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{16,}|"
    r"sk_(?:live|test)_[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AIza[0-9A-Za-z_-]{20,})"
)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _project_identity(project: str | None) -> str:
    if not project:
        return "unknown"
    return Path(project).name or str(project)


def _default_root() -> Path:
    configured = os.environ.get("FLYTO_INDEXER_FEEDBACK_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".flyto-indexer" / "feedback"


def sanitize_feedback_text(value: Any) -> str:
    """Keep a useful bounded note while removing common code and secret material."""
    text = str(value or "")
    text = _CODE_FENCE_RE.sub("[code omitted]", text)
    text = _HOME_PATH_RE.sub("$HOME/", text)
    text = _TOKEN_RE.sub("[redacted-secret]", text)
    text = _ASSIGNMENT_SECRET_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    return _SPACE_RE.sub(" ", text).strip()[:_MAX_TEXT]


class FeedbackStore:
    """Append-only local event store with restrictive permissions and bounded reads."""

    def __init__(self, root: Path | None = None):
        self.root = (root or _default_root()).resolve()
        self.path = self.root / "feedback.jsonl"
        self.lock_path = self.root / ".feedback.lock"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def read(self) -> list[dict[str, Any]]:
        with _LOCK:
            if not self.path.is_file():
                return []
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return []
            records = []
            for line in lines[-_MAX_RECORDS:]:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("event_id"):
                    records.append(record)
            return records

    def append(self, event: dict[str, Any]) -> bool:
        """Append once by event_id; return False for an idempotent replay."""
        with _LOCK:
            self._ensure_root()
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_EX)
                if any(
                    record.get("event_id") == event.get("event_id")
                    for record in self.read()
                ):
                    return False
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(_canonical_json(event) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
                return True
            finally:
                if _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_UN)
                os.close(descriptor)


def _normalized_category(category: str) -> str:
    value = str(category or "other").strip().casefold().replace("-", "_")
    if value not in VALID_CATEGORIES:
        raise ValueError(f"Unsupported feedback category: {category}")
    return value


def _normalized_severity(severity: str) -> str:
    value = str(severity or "medium").strip().casefold()
    if value not in VALID_SEVERITIES:
        raise ValueError(f"Unsupported feedback severity: {severity}")
    return value


def record_feedback(
    *,
    project: str | None,
    category: str,
    summary: str,
    severity: str = "medium",
    tool_name: str = "",
    finding_id: str = "",
    rule_id: str = "",
    framework: str = "",
    duration_ms: float | None = None,
    expected: str = "",
    actual: str = "",
    session_id: str = "",
    request_id: str = "",
    source: str = "explicit",
    store: FeedbackStore | None = None,
) -> dict[str, Any]:
    """Record one compact issue occurrence without persisting prompts or source code."""
    category_value = _normalized_category(category)
    severity_value = _normalized_severity(severity)
    summary_value = sanitize_feedback_text(summary)
    if not summary_value:
        raise ValueError("Feedback summary is required")
    issue_material = {
        "project": _project_identity(project),
        "category": category_value,
        "summary": summary_value,
        "tool": sanitize_feedback_text(tool_name),
        "finding_id": sanitize_feedback_text(finding_id),
        "rule_id": sanitize_feedback_text(rule_id),
        "framework": sanitize_feedback_text(framework),
    }
    # A scanner finding already has a stable identity. Keep the latest human wording
    # as event evidence, but do not split one finding into multiple improvement groups
    # just because two LLM sessions described it differently.
    fingerprint_material = dict(issue_material)
    if fingerprint_material["finding_id"]:
        fingerprint_material["summary"] = ""
    feedback_id = "feedback-" + hashlib.sha256(
        _canonical_json(fingerprint_material).encode("utf-8")
    ).hexdigest()[:24]
    recorded_at = _now()
    replay_key = sanitize_feedback_text(request_id) or recorded_at
    event_id = hashlib.sha256(
        f"{feedback_id}\0{replay_key}".encode("utf-8")
    ).hexdigest()
    event = {
        "schema": FEEDBACK_SCHEMA,
        "event_id": event_id,
        "event": "observed",
        "feedback_id": feedback_id,
        "recorded_at": recorded_at,
        **issue_material,
        "severity": severity_value,
        "expected": sanitize_feedback_text(expected),
        "actual": sanitize_feedback_text(actual),
        "session_id": sanitize_feedback_text(session_id),
        "source": sanitize_feedback_text(source) or "explicit",
    }
    if duration_ms is not None:
        event["duration_ms"] = max(0.0, round(float(duration_ms), 3))
    target_store = store or FeedbackStore()
    appended = target_store.append(event)
    return {
        "status": "recorded" if appended else "already_recorded",
        "feedback_id": feedback_id,
        "event_id": event_id,
        "category": category_value,
        "severity": severity_value,
        "privacy": "local_only_no_prompts_or_source_code",
        "policy_effect": "none_until_human_review",
    }


def resolve_feedback(
    feedback_id: str,
    *,
    resolution: str,
    resolved_by: str = "",
    request_id: str = "",
    store: FeedbackStore | None = None,
) -> dict[str, Any]:
    """Append a resolution event without rewriting history."""
    target_store = store or FeedbackStore()
    if not any(
        event.get("feedback_id") == feedback_id and event.get("event") == "observed"
        for event in target_store.read()
    ):
        return {"status": "not_found", "feedback_id": feedback_id}
    resolution_value = sanitize_feedback_text(resolution)
    if not resolution_value:
        raise ValueError("Resolution is required")
    replay_key = sanitize_feedback_text(request_id) or _now()
    event_id = hashlib.sha256(
        f"resolve\0{feedback_id}\0{replay_key}".encode("utf-8")
    ).hexdigest()
    event = {
        "schema": FEEDBACK_SCHEMA,
        "event_id": event_id,
        "event": "resolved",
        "feedback_id": feedback_id,
        "recorded_at": _now(),
        "resolution": resolution_value,
        "resolved_by": sanitize_feedback_text(resolved_by),
    }
    appended = target_store.append(event)
    return {
        "status": "resolved" if appended else "already_resolved",
        "feedback_id": feedback_id,
        "event_id": event_id,
    }


def summarize_feedback(
    project: str | None = None,
    *,
    since_days: int = 90,
    include_resolved: bool = False,
    limit: int = 10,
    store: FeedbackStore | None = None,
) -> dict[str, Any]:
    """Aggregate repeated problems into bounded, human-reviewable improvement candidates."""
    target_store = store or FeedbackStore()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(since_days, 3650)))
    identity = _project_identity(project) if project else None
    events = target_store.read()
    resolved = {
        event.get("feedback_id")
        for event in events
        if event.get("event") == "resolved"
    }
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event") != "observed":
            continue
        if identity and event.get("project") != identity:
            continue
        try:
            recorded_at = datetime.fromisoformat(
                str(event.get("recorded_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if recorded_at < cutoff:
            continue
        feedback_id = str(event.get("feedback_id") or "")
        if not include_resolved and feedback_id in resolved:
            continue
        item = grouped.setdefault(
            feedback_id,
            {
                "feedback_id": feedback_id,
                "project": event.get("project"),
                "category": event.get("category"),
                "summary": event.get("summary"),
                "tool": event.get("tool"),
                "rule_id": event.get("rule_id"),
                "framework": event.get("framework"),
                "severity": event.get("severity"),
                "occurrences": 0,
                "last_seen": event.get("recorded_at"),
                "max_duration_ms": 0.0,
                "status": "resolved" if feedback_id in resolved else "open",
            },
        )
        item["occurrences"] += 1
        item["last_seen"] = max(str(item["last_seen"]), str(event.get("recorded_at")))
        item["max_duration_ms"] = max(
            float(item["max_duration_ms"]), float(event.get("duration_ms") or 0.0)
        )
        if _SEVERITY_WEIGHT.get(str(event.get("severity")), 0) > _SEVERITY_WEIGHT.get(
            str(item.get("severity")), 0
        ):
            item["severity"] = event.get("severity")
    for item in grouped.values():
        latency_bonus = min(float(item["max_duration_ms"]) / 10_000, 5)
        item["priority_score"] = round(
            item["occurrences"] * _SEVERITY_WEIGHT.get(str(item["severity"]), 1)
            + latency_bonus,
            2,
        )
    candidates = sorted(
        grouped.values(),
        key=lambda item: (item["priority_score"], item["last_seen"]),
        reverse=True,
    )
    bounded_limit = max(1, min(int(limit), 50))
    visible = candidates[:bounded_limit]
    by_category: Counter[str] = Counter()
    by_tool: Counter[str] = Counter()
    for item in candidates:
        by_category[str(item["category"])] += int(item["occurrences"])
        if item.get("tool"):
            by_tool[str(item["tool"])] += int(item["occurrences"])
    return {
        "schema": FEEDBACK_SUMMARY_SCHEMA,
        "project": identity or "all",
        "since_days": max(1, min(since_days, 3650)),
        "total_issue_groups": len(candidates),
        "total_occurrences": sum(item["occurrences"] for item in candidates),
        "by_category": dict(by_category),
        "by_tool": dict(by_tool),
        "improvement_candidates": visible,
        "has_more": len(candidates) > len(visible),
        "governance": {
            "automatic_policy_changes": False,
            "required_next_step": "human_review_and_benchmark_before_rule_change",
        },
        "privacy": "local_only_no_prompts_or_source_code",
    }


def record_validation_feedback(
    result: dict[str, Any],
    *,
    project: str | None,
    task_id: str = "",
    store: FeedbackStore | None = None,
) -> dict[str, Any]:
    """Convert a failed task validation into one compact automatic observation."""
    if result.get("overall") != "fail" and result.get("pass") is not False:
        return {"status": "skipped", "reason": "validation_passed"}
    reason_codes = sorted(set(result.get("reason_codes") or ["CODE_VALIDATION_FAILED"]))
    summary = "Task validation failed: " + ", ".join(reason_codes[:8])
    category = (
        "missing_context"
        if any("CONTEXT" in code or "LEDGER" in code for code in reason_codes)
        else "runtime_mismatch"
        if any("PROOF" in code or "CONFORMANCE" in code for code in reason_codes)
        else "validation_failure"
    )
    return record_feedback(
        project=project,
        category=category,
        summary=summary,
        severity="high",
        tool_name="task.validate",
        session_id=task_id,
        request_id=f"validation:{task_id}:{'|'.join(reason_codes)}" if task_id else "",
        source="automatic_validation",
        store=store,
    )
