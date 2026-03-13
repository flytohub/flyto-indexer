"""Context budget optimizer — rank and trim results by relevance for LLM consumption."""

import logging
import os
from typing import List, Optional

logger = logging.getLogger("flyto-indexer.context_budget")

try:
    from .git_intel import _find_git_root, _get_cached_log, _get_project_root
except ImportError:
    from tools.git_intel import _find_git_root, _get_cached_log, _get_project_root


def score_references(
    references: list,
    target_symbol: str = "",
    project: Optional[str] = None,
) -> list:
    """Score and rank references by relevance.

    Scoring factors:
    - recency: recently modified files score higher
    - distance: closer in the call graph = higher score
    - confidence: high-confidence refs score more
    - test_penalty: test files score lower (unless investigating tests)
    - hotspot_bonus: frequently changed files score higher

    Args:
        references: List of reference dicts with path, confidence, etc.
        target_symbol: The symbol being analyzed (for distance calc)
        project: Project name

    Returns:
        Same list sorted by relevance_score (descending), with score added
    """
    if not references:
        return references

    # Build recent-files set from git log
    recent_files = _get_recent_files(project, days=30)
    hotspot_files = _get_recent_files(project, days=90)

    for ref in references:
        score = 0.0
        path = ref.get("path", ref.get("from_path", ""))

        # Confidence scoring
        confidence = ref.get("confidence", "medium")
        if confidence == "high":
            score += 10
        elif confidence == "medium":
            score += 5
        else:
            score += 1

        # Recency scoring
        if path in recent_files:
            score += 8
        elif path in hotspot_files:
            score += 4

        # Test file penalty
        path_lower = path.lower()
        if "test" in path_lower or "spec" in path_lower:
            score -= 5

        # Same-project bonus
        ref_project = ref.get("project", "")
        if target_symbol and ":" in target_symbol:
            target_project = target_symbol.split(":")[0]
            if ref_project == target_project:
                score += 3

        # Source file proximity (same directory = more relevant)
        if target_symbol and ":" in target_symbol:
            target_path = target_symbol.split(":")[1] if len(target_symbol.split(":")) > 1 else ""
            if target_path and path:
                target_dir = os.path.dirname(target_path)
                ref_dir = os.path.dirname(path)
                if target_dir == ref_dir:
                    score += 5
                elif target_dir and ref_dir and (
                    target_dir.startswith(ref_dir) or ref_dir.startswith(target_dir)
                ):
                    score += 2

        ref["relevance_score"] = round(score, 1)

    # Sort by relevance score descending
    references.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)
    return references


def trim_to_budget(
    result: dict,
    budget_tokens: int = 4000,
    list_keys: Optional[List[str]] = None,
) -> dict:
    """Trim result dict to fit within a token budget.

    Estimates ~4 tokens per JSON field value character.
    Trims list fields from the bottom (lowest relevance) until under budget.

    Args:
        result: Result dict to trim
        budget_tokens: Target token budget (default 4000)
        list_keys: Which list keys to trim (default: common ones)

    Returns:
        Trimmed result with budget_info metadata
    """
    if list_keys is None:
        list_keys = [
            "references", "affected_symbols", "affected_files",
            "callers", "impacts", "changes", "dependents",
        ]

    # Estimate current size
    current_tokens = _estimate_tokens(result)

    if current_tokens <= budget_tokens:
        result["budget_info"] = {
            "estimated_tokens": current_tokens,
            "budget": budget_tokens,
            "trimmed": False,
        }
        return result

    # Trim list fields from largest to smallest
    for key in list_keys:
        if current_tokens <= budget_tokens:
            break

        items = result.get(key)
        if not isinstance(items, list) or not items:
            continue

        original_len = len(items)
        # Binary search for right cutoff
        while len(items) > 1 and current_tokens > budget_tokens:
            items = items[:len(items) * 3 // 4]  # Remove bottom 25%
            result[key] = items
            current_tokens = _estimate_tokens(result)

        if len(items) < original_len:
            result[f"{key}_total"] = original_len
            result[f"{key}_trimmed"] = True

        # Also check nested dicts
        for item in list(result.values()):
            if isinstance(item, dict):
                for sub_key in list_keys:
                    sub_items = item.get(sub_key)
                    if isinstance(sub_items, list) and len(sub_items) > 5:
                        original_len = len(sub_items)
                        item[sub_key] = sub_items[:5]
                        item[f"{sub_key}_total"] = original_len
                        item[f"{sub_key}_trimmed"] = True

    result["budget_info"] = {
        "estimated_tokens": _estimate_tokens(result),
        "budget": budget_tokens,
        "trimmed": True,
    }
    return result


def _estimate_tokens(obj, depth: int = 0) -> int:
    """Rough token estimate for a JSON-serializable object.

    ~4 chars per token for English text, ~3 for code/paths.
    """
    if depth > 10:
        return 0
    if obj is None:
        return 1
    if isinstance(obj, bool):
        return 1
    if isinstance(obj, (int, float)):
        return 1
    if isinstance(obj, str):
        return max(1, len(obj) // 3)
    if isinstance(obj, list):
        return sum(_estimate_tokens(item, depth + 1) for item in obj) + len(obj)
    if isinstance(obj, dict):
        total = 0
        for k, v in obj.items():
            total += len(k) // 3 + 1  # key tokens
            total += _estimate_tokens(v, depth + 1)
        return total
    return 1


def _get_recent_files(project: Optional[str], days: int = 30) -> set:
    """Get files modified in the last N days from git log."""
    try:
        _proj_name, proj_root = _get_project_root(project)
    except (ValueError, TypeError):
        return set()

    git_root = _find_git_root(proj_root)
    if not git_root:
        return set()

    git_root_abs = os.path.abspath(git_root)
    proj_root_abs = os.path.abspath(proj_root)
    proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
    if proj_prefix == ".":
        proj_prefix = ""

    try:
        entries = _get_cached_log(git_root, (f"--since={days} days ago",))
    except RuntimeError:
        return set()

    recent = set()
    for entry in entries:
        for f in entry.get("files", []):
            if proj_prefix and not f.startswith(proj_prefix + "/"):
                continue
            rel = f[len(proj_prefix):].lstrip("/") if proj_prefix else f
            recent.add(rel)
    return recent
