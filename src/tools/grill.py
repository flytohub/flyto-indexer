"""Evidence-backed decision interrogation for the task workflow.

The engine is deliberately model and language neutral.  Callers may provide
questions in any language, while the persisted contract uses stable canonical
IDs and enums.  Repository-owned facts are resolved through an injected search
function; human decisions are never guessed from code search results.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .grill_intelligence import (
    DecisionIntelligenceError,
    enrich_decision,
    finalize_adversarial_review,
)
from .grill_evidence import (
    capture_evidence_snapshot,
    check_evidence_freshness,
    decision_audit_artifact,
    render_adr,
    selective_reopen_plan,
)
from .grill_outcomes import OutcomeStore, load_outcome_priors

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    _fcntl = None


SCHEMA_VERSION = "flyto.grill-session.v2"
CONTRACT_VERSION = "flyto.decision-contract.v2"
LEGACY_SCHEMA_VERSIONS = {"flyto.grill-session.v1"}
LEGACY_CONTRACT_VERSIONS = {"flyto.decision-contract.v1"}
VALID_OPERATIONS = {"start", "answer", "status", "freeze", "discard"}
VALID_MODES = {"interactive", "batch"}
VALID_KINDS = {"decision", "fact"}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_STATUSES = {"open", "resolved", "discarded"}
VALID_RESOLUTION_POLICIES = {"exact_match", "all_terms", "evidence_present"}
SEVERITY_WEIGHT = {"critical": 8, "high": 5, "medium": 3, "low": 1}
SESSION_ID_RE = re.compile(r"^grill_[a-f0-9]{24}$")
DECISION_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
MAX_DECISIONS = 64
MAX_HISTORY = 512
MAX_BATCH_QUESTIONS = 20

FactResolver = Callable[[str, str | None], dict]

_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


class GrillError(ValueError):
    """Fail-closed error returned by the public runner."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_text(value: Any, field: str, *, required: bool = False, limit: int = 4000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise GrillError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise GrillError(f"{field} is required")
    if len(value) > limit:
        raise GrillError(f"{field} exceeds {limit} characters")
    return value


def _state_dir() -> Path:
    configured = os.environ.get("FLYTO_INDEXER_GRILL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".flyto-indexer" / "grill"


def _store_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


def _default_decisions(description: str) -> list[dict]:
    outcome = description[:240] or "the requested change"
    return [
        {
            "id": "outcome",
            "kind": "decision",
            "severity": "critical",
            "blocking": True,
            "question": "What is the smallest observable outcome that proves this task is complete?",
            "recommendation": f"Define one externally observable success criterion for: {outcome}",
            "rationale": "Implementation cannot be verified without an observable outcome.",
        },
        {
            "id": "compatibility",
            "kind": "decision",
            "severity": "high",
            "blocking": True,
            "prerequisites": ["outcome"],
            "question": "Which existing public behavior must remain backward compatible?",
            "recommendation": "Use additive contracts and preserve every existing caller by default.",
            "rationale": "Compatibility decisions determine the safe implementation boundary.",
        },
        {
            "id": "failure_policy",
            "kind": "decision",
            "severity": "high",
            "blocking": True,
            "prerequisites": ["outcome"],
            "question": "What should happen when the new flow cannot safely complete?",
            "recommendation": "Fail closed, preserve evidence, and return an explicit remediation action.",
            "rationale": "Failure behavior must be deterministic before implementation.",
        },
        {
            "id": "verification",
            "kind": "decision",
            "severity": "high",
            "blocking": True,
            "prerequisites": ["compatibility", "failure_policy"],
            "question": "What evidence must the final verification gate require?",
            "recommendation": (
                "Require focused unit tests, real dispatch integration, subprocess end-to-end "
                "coverage, regression tests, and a tamper check."
            ),
            "rationale": "The gate needs proof requirements fixed before code is written.",
        },
    ]


def _normalize_option(raw: Any, decision_id: str, index: int) -> dict:
    if isinstance(raw, str):
        return {"id": f"option_{index + 1}", "label": _bounded_text(raw, "option", required=True)}
    if not isinstance(raw, dict):
        raise GrillError(f"decision {decision_id} options must be strings or objects")
    option_id = _bounded_text(raw.get("id"), "option.id", required=True, limit=80)
    if not DECISION_ID_RE.fullmatch(option_id):
        raise GrillError(f"invalid option id: {option_id}")
    conflicts = raw.get("conflicts_with", [])
    if not isinstance(conflicts, list) or not all(isinstance(item, str) for item in conflicts):
        raise GrillError(f"decision {decision_id} option conflicts_with must be a string array")
    return {
        "id": option_id,
        "label": _bounded_text(raw.get("label"), "option.label", required=True),
        "conflicts_with": sorted(set(conflicts)),
    }


def _normalize_decisions(
    raw_decisions: Any,
    description: str,
    *,
    project: str | None = None,
    outcome_store: OutcomeStore | None = None,
) -> list[dict]:
    using_defaults = raw_decisions is None
    if raw_decisions is None:
        raw_decisions = _default_decisions(description)
    if not isinstance(raw_decisions, list) or not raw_decisions:
        raise GrillError("decisions must be a non-empty array")
    if len(raw_decisions) > MAX_DECISIONS:
        raise GrillError(f"decisions exceeds the limit of {MAX_DECISIONS}")

    normalized = []
    ids = set()
    learned_priors = load_outcome_priors(project, store=outcome_store)
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            raise GrillError("each decision must be an object")
        decision_id = _bounded_text(raw.get("id"), "decision.id", required=True, limit=80)
        if not DECISION_ID_RE.fullmatch(decision_id):
            raise GrillError(f"invalid decision id: {decision_id}")
        if decision_id in ids:
            raise GrillError(f"duplicate decision id: {decision_id}")
        ids.add(decision_id)

        kind = raw.get("kind", "decision")
        severity = raw.get("severity", "medium")
        if kind not in VALID_KINDS:
            raise GrillError(f"invalid decision kind: {kind}")
        if severity not in VALID_SEVERITIES:
            raise GrillError(f"invalid decision severity: {severity}")
        prerequisites = raw.get("prerequisites", [])
        evidence_queries = raw.get("evidence_queries", [])
        if not isinstance(prerequisites, list) or not all(
            isinstance(item, str) for item in prerequisites
        ):
            raise GrillError(f"decision {decision_id} prerequisites must be a string array")
        if not isinstance(evidence_queries, list) or not all(
            isinstance(item, str) for item in evidence_queries
        ):
            raise GrillError(f"decision {decision_id} evidence_queries must be a string array")
        options = [
            _normalize_option(option, decision_id, index)
            for index, option in enumerate(raw.get("options", []))
        ]
        option_ids = [option["id"] for option in options]
        if len(option_ids) != len(set(option_ids)):
            raise GrillError(f"decision {decision_id} has duplicate option ids")
        resolution_policy = raw.get("resolution_policy", "exact_match")
        if resolution_policy not in VALID_RESOLUTION_POLICIES:
            raise GrillError(
                f"decision {decision_id} has invalid resolution_policy: {resolution_policy}"
            )

        intelligence_input = raw
        learning_prior = learned_priors.get(decision_id)
        if learning_prior and kind == "decision" and "confidence" not in raw:
            intelligence_input = deepcopy(raw)
            intelligence_input["confidence"] = {
                "recommendation": learning_prior["recommendation_confidence"]
            }
        node = {
                "id": decision_id,
                "kind": kind,
                "owner": "repository" if kind == "fact" else "human",
                "severity": severity,
                "blocking": bool(raw.get("blocking", severity in {"critical", "high"})),
                "question": _bounded_text(raw.get("question"), "decision.question", required=True),
                "recommendation": _bounded_text(
                    raw.get("recommendation"), "decision.recommendation", required=True
                ),
                "rationale": _bounded_text(raw.get("rationale"), "decision.rationale"),
                "prerequisites": list(dict.fromkeys(prerequisites)),
                "evidence_queries": [
                    _bounded_text(query, "evidence_query", required=True, limit=500)
                    for query in evidence_queries
                ],
                "resolution_policy": resolution_policy,
                "options": options,
                "status": "open",
                "answer": None,
                "selected_option": None,
                "evidence": [],
                "source": raw.get("source", "default" if using_defaults else "client"),
        }
        try:
            node.update(enrich_decision(intelligence_input, node))
        except DecisionIntelligenceError as exc:
            raise GrillError(f"decision {decision_id}: {exc}") from exc
        node["learning_prior"] = deepcopy(learning_prior)
        normalized.append(node)

    _validate_graph(normalized)
    return normalized


def _validate_graph(decisions: list[dict]) -> None:
    ids = {node["id"] for node in decisions}
    for node in decisions:
        missing = [item for item in node["prerequisites"] if item not in ids]
        if missing:
            raise GrillError(f"decision {node['id']} has missing prerequisites: {missing}")

    graph = {node["id"]: node["prerequisites"] for node in decisions}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise GrillError(f"decision dependency cycle detected at {node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for parent in graph[node_id]:
            visit(parent)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in graph:
        visit(node_id)


def _find_node(session: dict, decision_id: str) -> dict:
    for node in session["decisions"]:
        if node["id"] == decision_id:
            return node
    raise GrillError(f"unknown decision_id: {decision_id}")


def _resolved_ids(session: dict) -> set[str]:
    return {node["id"] for node in session["decisions"] if node["status"] == "resolved"}


def _frontier(session: dict) -> list[dict]:
    resolved = _resolved_ids(session)
    frontier = [
        node
        for node in session["decisions"]
        if node["status"] == "open"
        and node["owner"] == "human"
        and set(node["prerequisites"]).issubset(resolved)
    ]
    declaration_order = {
        node["id"]: index for index, node in enumerate(session["decisions"])
    }
    return sorted(
        frontier,
        key=lambda node: (
            -float(node.get("value_of_information", 0.0)),
            declaration_order[node["id"]],
        ),
    )


def _repository_blockers(session: dict) -> list[dict]:
    resolved = _resolved_ids(session)
    return [
        node
        for node in session["decisions"]
        if node["status"] == "open"
        and node["owner"] == "repository"
        and set(node["prerequisites"]).issubset(resolved)
    ]


def _normalized_search_text(value: Any) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _evidence_matches(item: dict, query: str, resolution_policy: str) -> bool:
    if resolution_policy == "evidence_present":
        return True
    fields = [
        item.get("name", ""),
        item.get("symbol_id", ""),
        item.get("path", ""),
        item.get("summary", ""),
    ]
    if resolution_policy == "exact_match":
        needle = _normalized_search_text(query)
        return bool(needle) and any(needle in _normalized_search_text(field) for field in fields)
    corpus = _normalized_search_text(" ".join(str(field) for field in fields))
    terms = [
        _normalized_search_text(term)
        for term in re.findall(r"[^\W_]+", query, flags=re.UNICODE)
    ]
    return bool(terms) and all(term in corpus for term in terms)


def _compact_evidence(result: dict, query: str, resolution_policy: str) -> list[dict]:
    evidence = []
    for item in result.get("results") or []:
        if not isinstance(item, dict) or not _evidence_matches(
            item, query, resolution_policy
        ):
            continue
        evidence.append(
            {
                "query": query,
                "symbol_id": item.get("symbol_id"),
                "name": item.get("name"),
                "type": item.get("type"),
                "path": item.get("path"),
                "line": item.get("line"),
                "score": item.get("score", 0),
                "summary": item.get("summary", ""),
                "match": item.get("match", ""),
            }
        )
        if len(evidence) == 5:
            break
    return evidence


def _resolve_facts(session: dict, fact_resolver: FactResolver | None) -> None:
    if fact_resolver is None:
        return
    for node in _repository_blockers(session):
        evidence = []
        errors = []
        queries = node["evidence_queries"] or [node["question"]]
        for query in queries:
            try:
                result = fact_resolver(query, session.get("project"))
                if isinstance(result, dict):
                    evidence.extend(
                        _compact_evidence(result, query, node["resolution_policy"])
                    )
            except Exception as exc:  # repository lookup must fail closed
                errors.append(str(exc)[:300])
        node["evidence"] = evidence
        if evidence:
            node["status"] = "resolved"
            node["answer"] = {
                "source": "repository",
                "summary": f"Resolved from {len(evidence)} indexed evidence item(s).",
            }
        elif errors:
            node["resolution_errors"] = errors


def _contradictions(session: dict) -> list[dict]:
    selected = {
        f"{node['id']}:{node['selected_option']}"
        for node in session["decisions"]
        if node.get("selected_option")
    }
    found = []
    for node in session["decisions"]:
        selected_option = node.get("selected_option")
        if not selected_option:
            continue
        option = next(
            (item for item in node["options"] if item["id"] == selected_option),
            None,
        )
        if not option:
            continue
        for target in option.get("conflicts_with", []):
            if target in selected:
                found.append(
                    {
                        "decision_id": node["id"],
                        "selected_option": selected_option,
                        "conflicts_with": target,
                    }
                )
    return sorted(found, key=lambda item: _canonical_json(item))


def _readiness(session: dict) -> dict:
    total = sum(SEVERITY_WEIGHT[node["severity"]] for node in session["decisions"])
    resolved = sum(
        SEVERITY_WEIGHT[node["severity"]]
        for node in session["decisions"]
        if node["status"] == "resolved"
    )
    unresolved = [node for node in session["decisions"] if node["status"] == "open"]
    blocking = [node for node in unresolved if node["blocking"]]
    contradictions = _contradictions(session)
    score = round((resolved / total) * 100) if total else 100
    confidence_total = sum(
        SEVERITY_WEIGHT[node["severity"]] for node in session["decisions"]
    )
    confidence_weighted = sum(
        SEVERITY_WEIGHT[node["severity"]]
        * float(
            node.get("confidence", {}).get(
                "evidence" if node["kind"] == "fact" else "recommendation",
                0.5,
            )
        )
        for node in session["decisions"]
    )
    confidence_score = (
        round((confidence_weighted / confidence_total) * 100)
        if confidence_total
        else 100
    )
    return {
        "score": score,
        "confidence_score": confidence_score,
        "low_confidence_decision_ids": [
            node["id"]
            for node in session["decisions"]
            if float(
                node.get("confidence", {}).get(
                    "evidence" if node["kind"] == "fact" else "recommendation",
                    0.5,
                )
            )
            < 0.5
        ],
        "resolved": len(session["decisions"]) - len(unresolved),
        "total": len(session["decisions"]),
        "blocking_count": len(blocking),
        "blocking_decision_ids": [node["id"] for node in blocking],
        "repository_fact_blockers": [
            node["id"] for node in blocking if node["owner"] == "repository"
        ],
        "contradictions": contradictions,
        "ready_to_freeze": not blocking and not contradictions,
    }


def _question_view(node: dict) -> dict:
    return {
        key: deepcopy(node[key])
        for key in (
            "id",
            "kind",
            "owner",
            "severity",
            "blocking",
            "question",
            "recommendation",
            "rationale",
            "prerequisites",
            "options",
            "evidence",
            "confidence",
            "decision_cost",
            "reversibility",
            "value_of_information",
            "acceptance",
            "adversarial_review",
            "learning_prior",
        )
    }


def _response(session: dict) -> dict:
    readiness = _readiness(session)
    frontier = _frontier(session)
    next_question = _question_view(frontier[0]) if frontier else None
    questions = [next_question] if next_question else []
    if session["mode"] == "batch":
        limit = min(session["max_questions"], MAX_BATCH_QUESTIONS)
        questions = [_question_view(node) for node in frontier[:limit]]
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session["session_id"],
        "status": session["status"],
        "revision": session["revision"],
        "mode": session["mode"],
        "locale": session["locale"],
        "readiness": readiness,
        "next_question": next_question,
        "questions": questions,
        "frontier_ids": [node["id"] for node in frontier],
        "repository_actions": [
            {
                "decision_id": node["id"],
                "required_action": "resolve_repository_fact",
                "queries": node["evidence_queries"] or [node["question"]],
                "errors": node.get("resolution_errors", []),
            }
            for node in _repository_blockers(session)
        ],
        "resolved_from_code": [
            node["id"]
            for node in session["decisions"]
            if node["owner"] == "repository" and node["status"] == "resolved"
        ],
        "decisions": deepcopy(session["decisions"]),
        "contract": deepcopy(session.get("contract")),
    }


class GrillSessionStore:
    """Atomic JSON persistence for resumable decision sessions."""

    def __init__(self, root: Path | None = None):
        self.root = (root or _state_dir()).resolve()
        self._lock = _store_lock(self.root)

    def _path(self, session_id: str) -> Path:
        if not SESSION_ID_RE.fullmatch(session_id or ""):
            raise GrillError("invalid grill session_id")
        return self.root / f"{session_id}.json"

    def _ensure_private_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    @contextmanager
    def transaction(self, session_id: str) -> Iterator[None]:
        """Serialize a session update across threads and POSIX processes."""
        self._path(session_id)
        with self._lock:
            self._ensure_private_root()
            lock_path = self.root / f".{session_id}.lock"
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                os.chmod(lock_path, 0o600)
                if _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_EX)
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_UN)
                os.close(descriptor)

    def load(self, session_id: str) -> dict:
        path = self._path(session_id)
        with self._lock:
            if not path.is_file():
                raise GrillError(f"grill session not found: {session_id}")
            try:
                session = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise GrillError(f"grill session is unreadable: {session_id}") from exc
        if session.get("schema_version") not in {SCHEMA_VERSION, *LEGACY_SCHEMA_VERSIONS}:
            raise GrillError("unsupported grill session schema")
        if session.get("session_id") != session_id:
            raise GrillError("grill session identity mismatch")
        if session.get("schema_version") in LEGACY_SCHEMA_VERSIONS:
            session["schema_version"] = SCHEMA_VERSION
            for node in session.get("decisions", []):
                try:
                    intelligence = enrich_decision(node, node)
                except DecisionIntelligenceError as exc:
                    raise GrillError(f"cannot upgrade grill session: {exc}") from exc
                for key, value in intelligence.items():
                    node.setdefault(key, value)
                node.setdefault("learning_prior", None)
                if node.get("status") == "resolved":
                    node["adversarial_review"] = finalize_adversarial_review(node)
        return session

    def save(self, session: dict) -> None:
        path = self._path(session["session_id"])
        payload = _canonical_json(session) + "\n"
        with self._lock:
            self._ensure_private_root()
            fd, tmp_name = tempfile.mkstemp(prefix=f".{session['session_id']}.", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(tmp_name, 0o600)
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)


def _new_session(
    description: str,
    project: str | None,
    decisions: Any,
    mode: str,
    locale: str,
    max_questions: int,
    outcome_root: Path | None = None,
) -> dict:
    if mode not in VALID_MODES:
        raise GrillError(f"invalid grill mode: {mode}")
    if not isinstance(max_questions, int) or not 1 <= max_questions <= MAX_BATCH_QUESTIONS:
        raise GrillError(f"max_questions must be between 1 and {MAX_BATCH_QUESTIONS}")
    description = _bounded_text(description, "description", required=True, limit=12000)
    project = _bounded_text(project, "project", limit=200) or None
    locale = _bounded_text(locale, "locale", limit=40) or "und"
    timestamp = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": f"grill_{uuid.uuid4().hex[:24]}",
        "status": "active",
        "revision": 1,
        "description": description,
        "project": project,
        "mode": mode,
        "locale": locale,
        "max_questions": max_questions,
        "created_at": timestamp,
        "updated_at": timestamp,
        "decisions": _normalize_decisions(
            decisions,
            description,
            project=project,
            outcome_store=OutcomeStore(outcome_root) if outcome_root else None,
        ),
        "history": [{"event": "started", "at": timestamp}],
        "processed_request_ids": [],
        "contract": None,
    }


def _append_history(session: dict, event: dict) -> None:
    event = {"at": _now(), **event}
    session["history"].append(event)
    session["history"] = session["history"][-MAX_HISTORY:]
    session["updated_at"] = event["at"]
    session["revision"] += 1


def _answer(
    session: dict,
    decision_id: str,
    answer: Any,
    selected_option: str | None,
    accept_recommendation: bool,
    request_id: str | None,
) -> None:
    if session["status"] != "active":
        raise GrillError(f"cannot answer a {session['status']} grill session")
    if request_id:
        request_id = _bounded_text(request_id, "request_id", required=True, limit=128)
        if request_id in session["processed_request_ids"]:
            return
    node = _find_node(session, decision_id)
    if node["owner"] != "human":
        raise GrillError("repository facts cannot be answered by the user")
    frontier_ids = {item["id"] for item in _frontier(session)}
    if node["status"] == "resolved":
        existing = _canonical_json(
            {
                "answer": node["answer"],
                "selected_option": node["selected_option"],
            }
        )
        proposed_answer = node["recommendation"] if accept_recommendation else answer
        proposed = _canonical_json(
            {"answer": proposed_answer, "selected_option": selected_option}
        )
        if existing == proposed:
            return
        raise GrillError(f"decision {decision_id} is already resolved with a different answer")
    if decision_id not in frontier_ids:
        raise GrillError(f"decision {decision_id} is not on the current frontier")

    if accept_recommendation:
        answer = node["recommendation"]
    answer = _bounded_text(answer, "answer", required=True, limit=12000)
    if selected_option is not None:
        selected_option = _bounded_text(
            selected_option, "selected_option", required=True, limit=80
        )
        option_ids = {option["id"] for option in node["options"]}
        if selected_option not in option_ids:
            raise GrillError(f"unknown selected_option for {decision_id}: {selected_option}")

    node["answer"] = answer
    node["selected_option"] = selected_option
    node["status"] = "resolved"
    node["adversarial_review"] = finalize_adversarial_review(node)
    if request_id:
        session["processed_request_ids"].append(request_id)
        session["processed_request_ids"] = session["processed_request_ids"][-MAX_HISTORY:]
    _append_history(
        session,
        {
            "event": "answered",
            "decision_id": decision_id,
            "accepted_recommendation": bool(accept_recommendation),
            "selected_option": selected_option,
        },
    )


def _contract_material(session: dict) -> dict:
    material = {
        "version": CONTRACT_VERSION,
        "session_id": session["session_id"],
        "description": session["description"],
        "project": session["project"],
        "status": "frozen",
        "decisions": [
            {
                key: deepcopy(node.get(key))
                for key in (
                    "id",
                    "kind",
                    "owner",
                    "severity",
                    "blocking",
                    "question",
                    "recommendation",
                    "rationale",
                    "prerequisites",
                    "answer",
                    "selected_option",
                    "evidence",
                    "confidence",
                    "decision_cost",
                    "reversibility",
                    "value_of_information",
                    "acceptance",
                    "adversarial_review",
                    "learning_prior",
                    "source",
                )
            }
            for node in session["decisions"]
        ],
        "readiness": _readiness(session),
        "evidence_snapshot": capture_evidence_snapshot(
            session.get("project"), session["decisions"]
        ),
    }
    material["artifacts"] = {
        "adr_markdown": render_adr(material),
        "decision_audit": decision_audit_artifact(material),
    }
    return material


def _freeze(session: dict) -> None:
    if session["status"] == "frozen":
        return
    if session["status"] != "active":
        raise GrillError(f"cannot freeze a {session['status']} grill session")
    readiness = _readiness(session)
    if not readiness["ready_to_freeze"]:
        return
    material = _contract_material(session)
    material["fingerprint"] = _fingerprint(material)
    material["frozen_at"] = _now()
    session["contract"] = material
    session["status"] = "frozen"
    _append_history(session, {"event": "frozen", "fingerprint": material["fingerprint"]})


def validate_decision_contract(
    task_contract: dict,
    *,
    project: str | None = None,
    check_freshness: bool = True,
) -> dict:
    """Validate an embedded frozen decision contract without trusting caller state."""
    contract = task_contract.get("decision_contract") if isinstance(task_contract, dict) else None
    if not contract:
        return {"pass": True, "decision": "pass", "required_actions": []}
    supported_versions = {CONTRACT_VERSION, *LEGACY_CONTRACT_VERSIONS}
    if contract.get("version") not in supported_versions or contract.get("status") != "frozen":
        return {
            "pass": False,
            "decision": "blocked",
            "reason_codes": ["DECISION_CONTRACT_NOT_FROZEN"],
            "required_actions": ["freeze_decision_contract"],
            "message": "Decision contract must be frozen before implementation.",
        }
    fingerprint = contract.get("fingerprint", "")
    material = {key: deepcopy(value) for key, value in contract.items() if key not in {"fingerprint", "frozen_at"}}
    if not fingerprint or _fingerprint(material) != fingerprint:
        return {
            "pass": False,
            "decision": "blocked",
            "reason_codes": ["DECISION_CONTRACT_TAMPERED"],
            "required_actions": ["restore_or_refreeze_decision_contract"],
            "message": "Decision contract fingerprint does not match its contents.",
        }
    readiness = contract.get("readiness", {})
    if not readiness.get("ready_to_freeze"):
        return {
            "pass": False,
            "decision": "blocked",
            "reason_codes": ["DECISIONS_INCOMPLETE"],
            "required_actions": list(readiness.get("blocking_decision_ids", [])),
            "message": "Critical decisions remain unresolved.",
        }
    freshness = (
        check_evidence_freshness(contract, project)
        if check_freshness
        else {
            "pass": True,
            "status": "not_checked",
            "stale_decision_ids": [],
            "changes": [],
        }
    )
    if not freshness.get("pass"):
        reopen_plan = selective_reopen_plan(contract, freshness)
        invalid_scope = freshness.get("status") == "invalid_evidence_scope"
        return {
            "pass": False,
            "decision": "blocked",
            "reason_codes": [
                (
                    "DECISION_EVIDENCE_SCOPE_INVALID"
                    if invalid_scope
                    else "DECISION_EVIDENCE_STALE"
                )
            ],
            "required_actions": [
                f"reopen_decision:{item['decision_id']}" for item in reopen_plan
            ],
            "message": (
                "Repository evidence escaped the declared project root."
                if invalid_scope
                else "Repository evidence changed after the decision contract was frozen."
            ),
            "evidence_freshness": freshness,
            "selective_reopen": reopen_plan,
        }
    return {
        "pass": True,
        "decision": "pass",
        "reason_codes": [],
        "required_actions": [],
        "decision_completeness_done": True,
        "fingerprint": fingerprint,
        "evidence_freshness": freshness,
        "artifacts": deepcopy(contract.get("artifacts") or {}),
    }


def export_decision_contract(session_id: str, store: GrillSessionStore | None = None) -> dict:
    session = (store or GrillSessionStore()).load(session_id)
    if session["status"] != "frozen" or not session.get("contract"):
        raise GrillError("grill session must be frozen before it can be attached to a task plan")
    validation = validate_decision_contract({"decision_contract": session["contract"]})
    if not validation["pass"]:
        raise GrillError(validation["message"])
    return deepcopy(session["contract"])


def run_grill(
    operation: str = "start",
    *,
    description: str = "",
    project: str | None = None,
    decisions: Any = None,
    mode: str = "interactive",
    locale: str = "und",
    max_questions: int = 8,
    session_id: str | None = None,
    decision_id: str | None = None,
    answer: Any = None,
    selected_option: str | None = None,
    accept_recommendation: bool = False,
    request_id: str | None = None,
    fact_resolver: FactResolver | None = None,
    store: GrillSessionStore | None = None,
) -> dict:
    """Run one deterministic step of a decision-interrogation session."""
    try:
        if operation not in VALID_OPERATIONS:
            raise GrillError(f"invalid grill operation: {operation}")
        store = store or GrillSessionStore()
        if operation == "start":
            session = _new_session(
                description,
                project,
                decisions,
                mode,
                locale,
                max_questions,
                store.root,
            )
            _resolve_facts(session, fact_resolver)
            store.save(session)
            return _response(session)

        if not session_id:
            raise GrillError(f"session_id is required for grill operation {operation}")
        # Keep read-modify-write atomic across sibling MCP requests and CLI
        # processes so independent frontier answers cannot overwrite each other.
        with store.transaction(session_id):
            session = store.load(session_id)
            if operation == "answer":
                _answer(
                    session,
                    _bounded_text(decision_id, "decision_id", required=True, limit=80),
                    answer,
                    selected_option,
                    accept_recommendation,
                    request_id,
                )
                _resolve_facts(session, fact_resolver)
                store.save(session)
            elif operation == "status":
                if session["status"] == "active":
                    _resolve_facts(session, fact_resolver)
                    store.save(session)
            elif operation == "freeze":
                _resolve_facts(session, fact_resolver)
                _freeze(session)
                store.save(session)
            elif operation == "discard":
                if session["status"] == "frozen":
                    raise GrillError(
                        "frozen grill sessions are immutable and cannot be discarded"
                    )
                if session["status"] != "discarded":
                    session["status"] = "discarded"
                    for node in session["decisions"]:
                        if node["status"] == "open":
                            node["status"] = "discarded"
                    _append_history(session, {"event": "discarded"})
                    store.save(session)
            response = _response(session)
        if operation == "freeze" and session["status"] != "frozen":
            response.update(
                {
                    "pass": False,
                    "decision": "blocked",
                    "reason_codes": (
                        ["DECISION_CONTRADICTIONS"]
                        if response["readiness"]["contradictions"]
                        else ["DECISIONS_INCOMPLETE"]
                    ),
                    "required_actions": response["readiness"]["blocking_decision_ids"],
                    "message": "Resolve blocking decisions and contradictions before freezing.",
                }
            )
        elif operation == "freeze":
            response.update({"pass": True, "decision": "pass"})
        return response
    except GrillError as exc:
        return {
            "pass": False,
            "decision": "blocked",
            "error": str(exc),
            "reason_codes": ["INVALID_GRILL_REQUEST"],
            "required_actions": [],
        }
