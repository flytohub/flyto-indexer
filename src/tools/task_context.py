"""Lean task context: scoped instructions and requirement traceability.

The module adds no public tool or workflow action. ``task(plan)`` attaches the
small, target-relevant contract; ``task(gate|validate)`` checks that it still
matches the repository.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .grill_evidence import resolve_project_root


CONTEXT_VERSION = "task-context.v1"
MAX_INSTRUCTION_BYTES = 256 * 1024
MAX_SPEC_BYTES = 512 * 1024
MAX_INSTRUCTION_FILES = 24
MAX_SPEC_FILES = 12
MAX_CLAUSES = 16
MAX_CLAUSES_PER_FILE = 96
MAX_REQUIREMENTS = 64

INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "QWEN.md")
ROOT_INSTRUCTION_PATHS = (".github/copilot-instructions.md",)
SPEC_NAMES = (
    "SPEC.md",
    "REQUIREMENTS.md",
    "PRD.md",
    "spec.md",
    "requirements.md",
    "prd.md",
)
SPEC_DIRS = (
    "specs",
    "docs/specs",
    "docs/adr",
    "docs/adrs",
    "adr",
    "adrs",
    "openspec/changes",
)

_HARD_RULE_RE = re.compile(
    r"\b(?:must|always|never|do not|don't|must not|required|avoid|"
    r"必須|務必|永遠|不要|不可|禁止)\b",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"^(?:never|do not|don't|must not|avoid|不要|不可|禁止)\s+(.+)$",
    re.IGNORECASE,
)
_POSITIVE_RE = re.compile(
    r"^(?:must|always|required(?:\s+to)?|use|prefer|必須|務必|應使用)\s+(.+)$",
    re.IGNORECASE,
)
_REQUIREMENT_HEADING_RE = re.compile(
    r"^#{2,6}\s+(?:(?P<id>(?:REQ|FR|NFR|US)-[\w.-]+)\s*[:—-]\s*)?"
    r"(?P<kind>Requirement|Scenario|Acceptance(?:\s+Criterion)?)\s*"
    r"(?::|—|-)?\s*(?P<text>.*)$",
    re.IGNORECASE,
)
_EXPLICIT_REQUIREMENT_RE = re.compile(
    r"^(?:[-*]\s+)?(?P<id>(?:REQ|FR|NFR|SCN|ACC|US)-[\w.-]+)"
    r"\s*[:—-]\s*(?P<text>.+)$",
    re.IGNORECASE,
)
_CHECKBOX_RE = re.compile(r"^[-*]\s+\[[ xX]\]\s+(?P<text>.+)$")
_RFC2119_RE = re.compile(r"\b(?:SHALL|MUST|SHOULD)\b")
_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_PROOF_RE = re.compile(
    r"^(?:python(?:\d+(?:\.\d+)*)?\s+-m\s+)?(?:pytest|ruff)\b"
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_relative(root: Path, candidate: Path) -> str | None:
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _symbol_path(target: str) -> str | None:
    parts = target.split(":")
    if len(parts) >= 4 and "/" in parts[1]:
        return parts[1]
    return None


def _target_path(root: Path, target: str) -> tuple[Path, str]:
    symbol_path = _symbol_path(target)
    raw = symbol_path or target
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    relative = _safe_relative(root, candidate)
    if relative is None:
        return root, "."
    if candidate.exists() and candidate.is_dir():
        scope = candidate.resolve()
    elif candidate.suffix or candidate.exists():
        scope = candidate.resolve().parent
    else:
        scope = root
    return scope, relative or "."


def _scopes(root: Path, scope: Path) -> list[Path]:
    result = []
    current = scope.resolve()
    root = root.resolve()
    while True:
        result.append(current)
        if current == root:
            break
        if root not in current.parents:
            return [root]
        current = current.parent
    return list(reversed(result))


def _instruction_candidates(root: Path, targets: list[str]) -> list[tuple[Path, set[str]]]:
    candidates: dict[Path, set[str]] = {}
    for target in targets or ["."]:
        scope, relative = _target_path(root, target)
        for directory in _scopes(root, scope):
            for name in INSTRUCTION_NAMES:
                path = directory / name
                if path.is_file():
                    candidates.setdefault(path, set()).add(relative)
            cursor_rules = directory / ".cursor" / "rules"
            if cursor_rules.is_dir():
                for path in sorted(cursor_rules.glob("*.mdc"))[:8]:
                    candidates.setdefault(path, set()).add(relative)
        for relative_path in ROOT_INSTRUCTION_PATHS:
            path = root / relative_path
            if path.is_file():
                candidates.setdefault(path, set()).add(relative)
    ordered = sorted(
        candidates.items(),
        key=lambda item: (
            len(item[0].relative_to(root).parts),
            item[0].relative_to(root).as_posix(),
        ),
    )
    return ordered[:MAX_INSTRUCTION_FILES]


def _provider(path: Path) -> str:
    name = path.name.lower()
    if name == "agents.md":
        return "agents"
    if name == "claude.md":
        return "claude"
    if name == "gemini.md":
        return "gemini"
    if name == "qwen.md":
        return "qwen"
    if name == "copilot-instructions.md":
        return "copilot"
    if path.suffix == ".mdc":
        return "cursor"
    return "generic"


def _clean_clause(line: str) -> str:
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
    return re.sub(r"\s+", " ", text)[:240]


def _clauses(text: str) -> list[dict[str, Any]]:
    clauses = []
    fallback = []
    heading = ""
    current: dict[str, Any] | None = None
    in_fence = False

    def flush() -> None:
        nonlocal current
        if not current:
            return
        clause = _clean_clause(current["text"])
        if clause:
            item = {
                "line": current["line"],
                "text": clause,
                "section": current["section"],
            }
            if current["listed"] or _HARD_RULE_RE.search(clause):
                clauses.append(item)
            elif len(fallback) < 2:
                fallback.append(item)
        current = None

    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            flush()
            continue
        if stripped.startswith("#"):
            flush()
            heading = stripped.lstrip("#").strip()[:120]
            continue
        listed = bool(re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", raw))
        if listed:
            flush()
            current = {
                "line": line_number,
                "text": stripped,
                "section": heading,
                "listed": True,
            }
        elif current:
            current["text"] = f"{current['text']} {stripped}"
        else:
            current = {
                "line": line_number,
                "text": stripped,
                "section": heading,
                "listed": False,
            }
    flush()
    return (clauses or fallback)[:MAX_CLAUSES_PER_FILE]


def _target_terms(targets: list[str]) -> set[str]:
    ignored = {
        "src",
        "lib",
        "app",
        "test",
        "tests",
        "file",
        "tools",
        "index",
    }
    return {
        token
        for target in targets
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", target.lower())
        if token not in ignored
    }


def _clause_score(clause: dict[str, Any], targets: list[str]) -> int:
    section = clause.get("section", "").lower()
    text = clause.get("text", "").lower()
    terms = _target_terms(targets)
    target_text = " ".join(targets).lower()
    domain_sections = {
        "frontend": ("frontend", "ui", "tsx", "vue", "css"),
        "accessibility": ("frontend", "ui", "tsx", "vue", "html"),
        "responsive": ("frontend", "ui", "tsx", "vue", "css"),
        "project memory": (".md", "docs/", "readme", "changelog", "state"),
        "deployment": ("deploy", "release", "ops", "infra"),
    }
    for domain, target_markers in domain_sections.items():
        if domain in section and not any(
            marker in target_text for marker in target_markers
        ):
            return -100
    generic_sections = (
        "agent",
        "gate",
        "workflow",
        "testing",
        "verification",
        "security",
        "shell",
        "code",
    )
    score = 4 if (_HARD_RULE_RE.search(text) or _directive(text)) else 0
    score += 3 if any(marker in section for marker in generic_sections) else 0
    score += 2 * sum(term in f"{section} {text}" for term in terms)
    return score


def _select_clauses(
    clauses: list[dict[str, Any]],
    targets: list[str],
) -> list[dict[str, Any]]:
    scored = [
        (_clause_score(clause, targets), clause)
        for clause in clauses
    ]
    relevant = [item for item in scored if item[0] > 0]
    if not relevant:
        first_by_source: dict[str, tuple[int, dict[str, Any]]] = {}
        for score, clause in scored:
            first_by_source.setdefault(clause["source"], (score, clause))
        relevant = list(first_by_source.values())
    relevant.sort(
        key=lambda item: (
            -item[0],
            -item[1]["depth"],
            item[1]["source"],
            item[1]["line"],
        )
    )
    selected = [clause for _score, clause in relevant[:MAX_CLAUSES]]
    selected.sort(
        key=lambda item: (-item["depth"], item["source"], item["line"])
    )
    return selected


def _directive(text: str) -> tuple[str, str] | None:
    cleaned = re.sub(r"[`*_#]", "", text).strip().rstrip(".。")
    match = _NEGATIVE_RE.match(cleaned)
    polarity = "deny"
    if not match:
        match = _POSITIVE_RE.match(cleaned)
        polarity = "allow"
    if not match:
        return None
    body = match.group(1).lower()
    tokens = re.findall(r"[\w./-]+", body, flags=re.UNICODE)
    ignored = {"a", "an", "the", "to", "for", "please", "should", "use", "using"}
    key = " ".join(token for token in tokens if token not in ignored)[:160]
    return (polarity, key) if key else None


def _similar_directives(left: str, right: str) -> bool:
    if left == right:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    union = left_tokens | right_tokens
    if not union:
        return False
    return len(left_tokens & right_tokens) / len(union) >= 0.75


def _instruction_conflicts(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    directives = []
    for clause in clauses:
        directive = _directive(clause["text"])
        if directive:
            directives.append((clause, *directive))
    conflicts = []
    for index, (left, left_polarity, left_key) in enumerate(directives):
        for right, right_polarity, right_key in directives[index + 1:]:
            if left_polarity == right_polarity:
                continue
            if not _similar_directives(left_key, right_key):
                continue
            left_depth = left["depth"]
            right_depth = right["depth"]
            if left_depth == right_depth:
                status = "unresolved"
                winner = None
            else:
                status = "resolved_by_scope"
                winner = (
                    right["source"] if right_depth > left_depth else left["source"]
                )
            conflicts.append(
                {
                    "key": left_key if len(left_key) <= len(right_key) else right_key,
                    "status": status,
                    "winner": winner,
                    "clauses": [
                        {
                            "source": left["source"],
                            "line": left["line"],
                            "text": left["text"],
                        },
                        {
                            "source": right["source"],
                            "line": right["line"],
                            "text": right["text"],
                        },
                    ],
                }
            )
    return conflicts[:16]


def resolve_instruction_context(
    project: str | None,
    targets: list[str] | None,
) -> dict[str, Any]:
    """Resolve only the coding-agent instructions that apply to target paths."""
    root = resolve_project_root(project)
    target_inputs = list(dict.fromkeys(targets or ["."]))
    if root is None:
        return {
            "version": CONTEXT_VERSION,
            "status": "unavailable",
            "targets": target_inputs,
            "files": [],
            "clauses": [],
            "conflicts": [],
            "fingerprint": None,
        }
    files = []
    selected_clauses = []
    for path, applies_to in _instruction_candidates(root, target_inputs):
        try:
            if path.stat().st_size > MAX_INSTRUCTION_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        scope_path = path.parent
        if path.parent.name == "rules" and path.parent.parent.name == ".cursor":
            scope_path = path.parent.parent.parent
        scope = scope_path.relative_to(root).as_posix() or "."
        depth = len(scope_path.relative_to(root).parts)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        files.append(
            {
                "path": relative,
                "provider": _provider(path),
                "scope": scope,
                "depth": depth,
                "sha256": digest,
                "applies_to": sorted(applies_to),
            }
        )
        for clause in _clauses(text):
            selected_clauses.append(
                {
                    **clause,
                    "source": relative,
                    "scope": scope,
                    "depth": depth,
                }
            )
    selected_clauses = _select_clauses(selected_clauses, target_inputs)
    conflicts = _instruction_conflicts(selected_clauses)
    unresolved = [item for item in conflicts if item["status"] == "unresolved"]
    payload = {
        "targets": target_inputs,
        "files": files,
        "clauses": selected_clauses,
    }
    return {
        "version": CONTEXT_VERSION,
        "status": "blocked" if unresolved else "ready",
        **payload,
        "conflicts": conflicts,
        "summary": {
            "file_count": len(files),
            "clause_count": len(selected_clauses),
            "unresolved_conflicts": len(unresolved),
        },
        "fingerprint": _fingerprint(payload),
    }


def _spec_candidates(root: Path, targets: list[str], description: str) -> list[Path]:
    candidates: set[Path] = set()
    for target in targets:
        _scope, relative = _target_path(root, target)
        path = root / relative
        parts = {part.lower() for part in path.relative_to(root).parts}
        looks_like_spec = (
            path.name in SPEC_NAMES
            or bool(parts & {"spec", "specs", "adr", "adrs", "openspec"})
        )
        if (
            looks_like_spec
            and path.is_file()
            and path.suffix.lower() in {".md", ".mdx"}
        ):
            candidates.add(path)
    for name in SPEC_NAMES:
        path = root / name
        if path.is_file():
            candidates.add(path)
    description_tokens = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", description.lower())
        if token not in {"the", "and", "with", "from", "this", "that"}
    }
    discovered = []
    for relative_dir in SPEC_DIRS:
        directory = root / relative_dir
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.md"):
            relative = path.relative_to(root).as_posix().lower()
            score = sum(token in relative for token in description_tokens)
            discovered.append((-score, relative, path))
    for _score, _relative, path in sorted(discovered)[:MAX_SPEC_FILES]:
        candidates.add(path)
    return sorted(
        candidates,
        key=lambda path: path.relative_to(root).as_posix(),
    )[:MAX_SPEC_FILES]


def _kind_prefix(kind: str) -> str:
    return {
        "requirement": "REQ",
        "scenario": "SCN",
        "acceptance": "ACC",
        "task": "TASK",
    }.get(kind, "REQ")


def _requirement_id(kind: str, source: str, line: int, text: str) -> str:
    digest = hashlib.sha256(
        f"{source}:{line}:{text}".encode("utf-8")
    ).hexdigest()[:8].upper()
    return f"{_kind_prefix(kind)}-{digest}"


def _inline_contract(text: str) -> tuple[list[str], list[str], list[str]]:
    paths = []
    symbols = []
    proofs = []
    for value in _CODE_SPAN_RE.findall(text):
        clean = value.strip()
        if _PROOF_RE.match(clean):
            proofs.append(clean)
        elif "/" in clean or re.search(r"\.[A-Za-z0-9]{1,8}$", clean):
            paths.append(clean.lstrip("./"))
        elif re.fullmatch(r"[A-Za-z_][\w.:-]*", clean):
            symbols.append(clean)
    return (
        list(dict.fromkeys(paths))[:12],
        list(dict.fromkeys(symbols))[:12],
        list(dict.fromkeys(proofs))[:8],
    )


def _parse_requirements(path: Path, root: Path) -> tuple[list[dict[str, Any]], dict]:
    relative = path.relative_to(root).as_posix()
    try:
        if path.stat().st_size > MAX_SPEC_BYTES:
            return [], {"path": relative, "status": "too_large"}
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], {"path": relative, "status": "unavailable"}
    requirements = []
    acceptance_section = False
    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        heading = stripped.lstrip("#").strip().lower() if stripped.startswith("#") else ""
        if heading:
            acceptance_section = any(
                token in heading
                for token in ("acceptance", "requirements", "scenarios")
            )
        kind = None
        explicit_id = None
        body = ""
        match = _REQUIREMENT_HEADING_RE.match(stripped)
        if match:
            raw_kind = match.group("kind").lower()
            kind = "scenario" if raw_kind.startswith("scenario") else (
                "acceptance" if raw_kind.startswith("acceptance") else "requirement"
            )
            explicit_id = match.group("id")
            body = match.group("text").strip() or stripped.lstrip("#").strip()
        else:
            match = _EXPLICIT_REQUIREMENT_RE.match(stripped)
            if match:
                explicit_id = match.group("id")
                prefix = explicit_id.split("-", 1)[0].upper()
                kind = "scenario" if prefix == "SCN" else (
                    "acceptance" if prefix == "ACC" else "requirement"
                )
                body = match.group("text").strip()
            else:
                match = _CHECKBOX_RE.match(stripped)
                if match and acceptance_section:
                    kind = "acceptance"
                    body = match.group("text").strip()
                elif _RFC2119_RE.search(stripped) and not stripped.startswith("#"):
                    kind = "requirement"
                    body = _clean_clause(stripped)
        if not kind or not body:
            continue
        expected_paths, expected_symbols, proofs = _inline_contract(body)
        requirements.append(
            {
                "id": (explicit_id or _requirement_id(
                    kind, relative, line_number, body
                )).upper(),
                "kind": kind,
                "text": body[:360],
                "source": relative,
                "line": line_number,
                "expected_paths": expected_paths,
                "expected_symbols": expected_symbols,
                "proof_commands": proofs,
            }
        )
        if len(requirements) >= MAX_REQUIREMENTS:
            break
    return requirements, {
        "path": relative,
        "status": "parsed",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "requirement_count": len(requirements),
    }


def _normalize_allowed_paths(targets: Iterable[str]) -> list[str]:
    allowed = []
    for target in targets:
        value = _symbol_path(target) or target
        if not value or value == "." or Path(value).is_absolute():
            if value == ".":
                return ["**"]
            continue
        normalized = value.replace("\\", "/").lstrip("./")
        if ":" in normalized and "/" not in normalized:
            continue
        allowed.append(normalized.rstrip("/"))
    return list(dict.fromkeys(allowed))


def _planned_steps(requirement: dict, execution_plan: list[dict]) -> list[str]:
    if not execution_plan:
        return []
    tokens = set(re.findall(r"[a-z0-9_]+", requirement["text"].lower()))
    scored = []
    for step in execution_plan:
        corpus = " ".join(
            str(step.get(key, "")) for key in ("id", "purpose", "tool")
        ).lower()
        score = sum(token in corpus for token in tokens if len(token) > 3)
        scored.append((score, step.get("id", "")))
    best_score = max(score for score, _step_id in scored)
    if best_score:
        return [step_id for score, step_id in scored if score == best_score][:3]
    apply_steps = [
        step.get("id", "")
        for step in execution_plan
        if "apply" in f"{step.get('id', '')} {step.get('purpose', '')}"
    ]
    return (apply_steps or [execution_plan[-1].get("id", "")])[:3]


def build_intent_ledger(
    project: str | None,
    description: str,
    targets: list[str] | None,
    execution_plan: list[dict] | None,
) -> dict[str, Any]:
    """Map task intent and bounded Markdown requirements to plan steps."""
    root = resolve_project_root(project)
    target_inputs = list(dict.fromkeys(targets or ["."]))
    plan = execution_plan or []
    if root is None:
        return {
            "version": CONTEXT_VERSION,
            "status": "unavailable",
            "description": description,
            "targets": target_inputs,
            "sources": [],
            "requirements": [],
            "fingerprint": None,
        }
    requirements = []
    sources = []
    for path in _spec_candidates(root, target_inputs, description):
        parsed, source = _parse_requirements(path, root)
        requirements.extend(parsed)
        sources.append(source)
        if len(requirements) >= MAX_REQUIREMENTS:
            requirements = requirements[:MAX_REQUIREMENTS]
            break
    task_requirement = {
        "id": _requirement_id("task", "task.description", 1, description),
        "kind": "task",
        "text": description[:500],
        "source": "task.description",
        "line": 1,
        "expected_paths": _normalize_allowed_paths(target_inputs),
        "expected_symbols": [],
        "proof_commands": [],
    }
    requirements.insert(0, task_requirement)
    for requirement in requirements:
        requirement["planned_steps"] = _planned_steps(requirement, plan)
    orphan_ids = [
        requirement["id"]
        for requirement in requirements
        if not requirement["planned_steps"]
    ]
    payload = {
        "description": description,
        "targets": target_inputs,
        "execution_plan": [
            {
                "id": step.get("id"),
                "purpose": step.get("purpose"),
                "tool": step.get("tool"),
            }
            for step in plan
        ],
        "sources": sources,
        "requirements": requirements,
        "allowed_paths": _normalize_allowed_paths(target_inputs),
    }
    return {
        "version": CONTEXT_VERSION,
        "status": "blocked" if orphan_ids else "ready",
        **payload,
        "orphan_requirements": orphan_ids,
        "summary": {
            "source_count": len(sources),
            "requirement_count": len(requirements),
            "orphan_count": len(orphan_ids),
        },
        "fingerprint": _fingerprint(payload),
    }


def attach_task_context(
    task_contract: dict,
    *,
    project: str | None,
    description: str,
    targets: list[str] | None,
) -> dict:
    """Attach both lean context contracts to an existing task plan."""
    if not isinstance(task_contract, dict) or task_contract.get("error"):
        return task_contract
    execution_plan = list(task_contract.get("execution_plan") or [])
    if not execution_plan:
        for index, sub_task in enumerate(task_contract.get("sub_tasks") or [], 1):
            prefix = f"subtask_{index:02d}"
            for step in sub_task.get("execution_plan") or []:
                nested = dict(step)
                nested["id"] = f"{prefix}:{step.get('id', 'step')}"
                nested["depends_on"] = [
                    f"{prefix}:{dependency}"
                    for dependency in step.get("depends_on") or []
                ]
                nested["subtask_index"] = index
                nested["subtask_intent"] = sub_task.get("intent")
                execution_plan.append(nested)
    instruction_context = resolve_instruction_context(project, targets)
    ledger = build_intent_ledger(
        project,
        description,
        targets,
        execution_plan,
    )
    task_contract["instruction_context"] = instruction_context
    task_contract["intent_ledger"] = ledger
    profile = task_contract.setdefault("task_profile", {})
    profile["instruction_fingerprint"] = instruction_context.get("fingerprint")
    profile["intent_fingerprint"] = ledger.get("fingerprint")
    return task_contract


def _match_allowed(path: str, allowed: list[str]) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    for pattern in allowed:
        clean = pattern.replace("\\", "/").lstrip("./")
        if clean == "**":
            return True
        if normalized == clean or normalized.startswith(f"{clean.rstrip('/')}/"):
            return True
        if fnmatch.fnmatchcase(normalized, clean):
            return True
    return False


def _change_set(
    project: str | None,
    supplied: dict | None,
) -> dict:
    if supplied is not None:
        return supplied
    from .grill_conformance import collect_change_set

    return collect_change_set(project, None)


def validate_instruction_context(
    task_contract: dict,
    *,
    project: str | None = None,
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Fail closed when scoped instructions drift or a diff enters a new scope."""
    expected = task_contract.get("instruction_context") if task_contract else None
    if not expected:
        return {
            "pass": True,
            "status": "not_required",
            "violations": [],
            "required_actions": [],
        }
    contract_project = (
        task_contract.get("task_profile", {}).get("project") or project
    )
    current = resolve_instruction_context(
        contract_project,
        expected.get("targets") or ["."],
    )
    violations: list[dict[str, Any]] = []
    if current.get("fingerprint") != expected.get("fingerprint"):
        violations.append({"type": "instruction_context_stale"})
    for conflict in current.get("conflicts") or []:
        if conflict.get("status") == "unresolved":
            violations.append(
                {
                    "type": "instruction_conflict",
                    "key": conflict.get("key"),
                    "sources": [
                        clause.get("source") for clause in conflict.get("clauses", [])
                    ],
                }
            )
    if changed_paths:
        planned_files = {
            item.get("path") for item in expected.get("files") or []
        }
        changed_context = resolve_instruction_context(
            contract_project,
            changed_paths,
        )
        new_files = sorted(
            item.get("path")
            for item in changed_context.get("files") or []
            if item.get("path") not in planned_files
        )
        if new_files:
            violations.append(
                {
                    "type": "unplanned_instruction_scope",
                    "instruction_files": new_files,
                }
            )
    return {
        "pass": not violations,
        "status": "pass" if not violations else "blocked",
        "violations": violations,
        "required_actions": [
            f"refresh_task_context:{item['type']}" for item in violations
        ],
        "fingerprint": current.get("fingerprint"),
    }


def _proof_satisfied(command: str, validation: dict | None) -> bool:
    executable = command.split()
    if not executable:
        return False
    if "pytest" in executable[:3]:
        return (validation or {}).get("pytest", {}).get("status") == "pass"
    if "ruff" in executable[:3]:
        return (validation or {}).get("ruff", {}).get("status") == "pass"
    return False


def validate_intent_ledger(
    task_contract: dict,
    *,
    project: str | None = None,
    validation: dict | None = None,
    change_set: dict | None = None,
    check_diff: bool = True,
) -> dict[str, Any]:
    """Check requirement coverage, spec freshness, proofs, and diff scope."""
    expected = task_contract.get("intent_ledger") if task_contract else None
    if not expected:
        return {
            "pass": True,
            "status": "not_required",
            "violations": [],
            "required_actions": [],
        }
    task_project = (
        task_contract.get("task_profile", {}).get("project") or project
    )
    current = build_intent_ledger(
        task_project,
        expected.get("description", ""),
        expected.get("targets") or ["."],
        expected.get("execution_plan") or [],
    )
    violations: list[dict[str, Any]] = []
    if current.get("fingerprint") != expected.get("fingerprint"):
        violations.append({"type": "intent_ledger_stale"})
    for requirement_id in expected.get("orphan_requirements") or []:
        violations.append(
            {"type": "orphan_requirement", "requirement_id": requirement_id}
        )
    captured: dict[str, Any] | None = None
    changed_paths: list[str] = []
    if check_diff:
        captured = _change_set(task_project, change_set)
        changed_paths = captured.get("changed_paths") or []
        if captured.get("status") not in {None, "captured"}:
            violations.append(
                {
                    "type": "change_set_unavailable",
                    "reason": captured.get("reason"),
                }
            )
        allowed = expected.get("allowed_paths") or []
        unplanned = [
            path for path in changed_paths if not _match_allowed(path, allowed)
        ]
        if unplanned:
            violations.append(
                {"type": "unplanned_diff", "changed_paths": unplanned[:40]}
            )
        for requirement in expected.get("requirements") or []:
            if requirement.get("kind") == "task":
                continue
            paths = requirement.get("expected_paths") or []
            if paths and not any(
                _match_allowed(path, paths) for path in changed_paths
            ):
                violations.append(
                    {
                        "type": "requirement_path_uncovered",
                        "requirement_id": requirement.get("id"),
                        "expected_paths": paths,
                    }
                )
            for command in requirement.get("proof_commands") or []:
                if not _proof_satisfied(command, validation):
                    violations.append(
                        {
                            "type": "requirement_proof_unsatisfied",
                            "requirement_id": requirement.get("id"),
                            "command": command,
                        }
                    )
    return {
        "pass": not violations,
        "status": "pass" if not violations else "blocked",
        "violations": violations,
        "required_actions": [
            (
                f"fix_intent_ledger:{item.get('requirement_id', 'task')}:"
                f"{item['type']}"
            )
            for item in violations
        ],
        "summary": {
            "requirements": len(expected.get("requirements") or []),
            "changed_paths": len(changed_paths),
            "violations": len(violations),
        },
        "change_set": (
            {
                key: value
                for key, value in captured.items()
                if key != "added_text"
            }
            if isinstance(captured, dict)
            else captured
        ),
    }
