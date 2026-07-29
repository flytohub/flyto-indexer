"""Decision-to-diff conformance for frozen Grill contracts."""

from __future__ import annotations

import fnmatch
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .grill_evidence import resolve_project_root


MAX_DIFF_BYTES = 4 * 1024 * 1024
MAX_UNTRACKED_BYTES = 256 * 1024
SHA_RE = re.compile(r"^[a-f0-9]{40}$")


def _run_git(root: Path, args: list[str], *, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        timeout=15,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(message or "git command failed")
    if binary:
        return result.stdout
    return result.stdout.decode("utf-8", errors="replace")


def _untracked_files(root: Path) -> list[str]:
    raw = _run_git(
        root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        binary=True,
    )
    return sorted(
        item.decode("utf-8", errors="replace")
        for item in raw.split(b"\0")
        if item
    )


def _added_text(diff_text: str) -> dict[str, str]:
    current_path = None
    collected: dict[str, list[str]] = {}
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            collected.setdefault(current_path, [])
        elif current_path and line.startswith("+") and not line.startswith("+++"):
            collected[current_path].append(line[1:])
    return {
        path: "\n".join(lines)
        for path, lines in collected.items()
    }


def collect_change_set(project: str | None, baseline_head: str | None) -> dict:
    """Collect tracked and untracked changes from the contract's git baseline."""
    root = resolve_project_root(project)
    if root is None:
        return {
            "status": "unavailable",
            "reason": "project_root_not_resolved",
            "changed_paths": [],
            "added_text": {},
        }
    baseline = baseline_head if baseline_head and SHA_RE.fullmatch(baseline_head) else "HEAD"
    try:
        raw_paths = _run_git(
            root,
            ["diff", "--relative", "--name-only", "-z", baseline, "--"],
            binary=True,
        )
        changed_paths = [
            item.decode("utf-8", errors="replace")
            for item in raw_paths.split(b"\0")
            if item
        ]
        untracked = _untracked_files(root)
        for path in untracked:
            if path not in changed_paths:
                changed_paths.append(path)
        diff_text = _run_git(
            root,
            [
                "diff",
                "--relative",
                "--no-ext-diff",
                "--unified=0",
                baseline,
                "--",
            ],
        )
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return {
            "status": "unavailable",
            "reason": str(exc)[:500],
            "project_root": str(root),
            "changed_paths": [],
            "added_text": {},
        }
    if len(diff_text.encode("utf-8")) > MAX_DIFF_BYTES:
        return {
            "status": "unavailable",
            "reason": f"diff exceeds {MAX_DIFF_BYTES} bytes",
            "project_root": str(root),
            "changed_paths": sorted(changed_paths),
            "added_text": {},
        }
    added = _added_text(diff_text)
    for relative in untracked:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
            if candidate.is_file() and candidate.stat().st_size <= MAX_UNTRACKED_BYTES:
                added[relative] = candidate.read_text(
                    encoding="utf-8", errors="replace"
                )
        except (OSError, ValueError):
            continue
    return {
        "status": "captured",
        "project_root": str(root),
        "baseline_head": baseline_head,
        "changed_paths": sorted(changed_paths),
        "added_text": added,
    }


def _matches(pattern: str, path: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/").lstrip("./")
    normalized_path = path.replace("\\", "/").lstrip("./")
    return (
        normalized_path == normalized_pattern
        or fnmatch.fnmatchcase(normalized_path, normalized_pattern)
    )


def _proof_status(command: str, validation: dict | None) -> dict:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return {"command": command, "status": "invalid"}
    if not tokens:
        return {"command": command, "status": "invalid"}
    executable = Path(tokens[0]).name
    is_python = bool(re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable))
    if is_python and len(tokens) >= 3 and tokens[1:3] == ["-m", "pytest"]:
        tool = "pytest"
    elif is_python and len(tokens) >= 3 and tokens[1:3] == ["-m", "ruff"]:
        tool = "ruff"
    elif executable in {"pytest", "ruff"}:
        tool = executable
    else:
        return {"command": command, "status": "unsupported"}
    result = (validation or {}).get(tool, {})
    return {
        "command": command,
        "status": "satisfied" if result.get("status") == "pass" else "failed",
        "satisfied_by": f"task.validate:{tool}",
    }


def validate_decision_conformance(
    task_contract: dict,
    *,
    project: str | None = None,
    validation: dict | None = None,
) -> dict:
    """Verify declared path/symbol/proof constraints against the current diff."""
    contract = (
        task_contract.get("decision_contract")
        if isinstance(task_contract, dict)
        else None
    )
    if not contract:
        return {
            "pass": True,
            "status": "not_required",
            "violations": [],
            "required_actions": [],
        }
    snapshot = contract.get("evidence_snapshot") or {}
    change_set = collect_change_set(
        project or contract.get("project"),
        snapshot.get("git_head"),
    )
    decisions = []
    violations = []
    proof_results = []
    has_machine_constraints = False
    for node in contract.get("decisions") or []:
        acceptance = node.get("acceptance") or {}
        expected_paths = acceptance.get("expected_paths") or []
        forbidden_paths = acceptance.get("forbidden_paths") or []
        expected_symbols = acceptance.get("expected_symbols") or []
        forbidden_symbols = acceptance.get("forbidden_symbols") or []
        proof_commands = acceptance.get("proof_commands") or []
        if any(
            (expected_paths, forbidden_paths, expected_symbols, forbidden_symbols)
        ):
            has_machine_constraints = True
        node_violations = []
        for pattern in expected_paths:
            if not any(_matches(pattern, path) for path in change_set["changed_paths"]):
                node_violations.append(
                    {"type": "expected_path_missing", "value": pattern}
                )
        for pattern in forbidden_paths:
            matched = [
                path
                for path in change_set["changed_paths"]
                if _matches(pattern, path)
            ]
            if matched:
                node_violations.append(
                    {
                        "type": "forbidden_path_changed",
                        "value": pattern,
                        "matched_paths": matched,
                    }
                )
        added_corpus = "\n".join(change_set.get("added_text", {}).values())
        for symbol in expected_symbols:
            if symbol not in added_corpus:
                node_violations.append(
                    {"type": "expected_symbol_missing", "value": symbol}
                )
        for symbol in forbidden_symbols:
            if symbol in added_corpus:
                node_violations.append(
                    {"type": "forbidden_symbol_added", "value": symbol}
                )
        node_proofs = [
            _proof_status(command, validation) for command in proof_commands
        ]
        proof_results.extend(
            {"decision_id": node.get("id"), **proof} for proof in node_proofs
        )
        for proof in node_proofs:
            if proof["status"] != "satisfied":
                node_violations.append(
                    {
                        "type": "proof_not_satisfied",
                        "value": proof["command"],
                        "status": proof["status"],
                    }
                )
        violations.extend(
            {"decision_id": node.get("id"), **violation}
            for violation in node_violations
        )
        decisions.append(
            {
                "decision_id": node.get("id"),
                "pass": not node_violations,
                "violations": node_violations,
                "assertions_for_audit": acceptance.get("assertions") or [],
            }
        )
    if change_set["status"] != "captured" and has_machine_constraints:
        violations.append(
            {
                "decision_id": None,
                "type": "change_set_unavailable",
                "value": change_set.get("reason"),
            }
        )
    required_actions = [
        (
            f"fix_conformance:{item.get('decision_id') or 'repository'}:"
            f"{item['type']}"
        )
        for item in violations
    ]
    return {
        "pass": not violations,
        "status": "pass" if not violations else "blocked",
        "violations": violations,
        "required_actions": required_actions,
        "decisions": decisions,
        "proof_results": proof_results,
        "change_set": {
            key: value
            for key, value in change_set.items()
            if key != "added_text"
        },
    }
