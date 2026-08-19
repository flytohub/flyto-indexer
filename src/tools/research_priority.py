"""Research-priority tool adapter.

Wraps `analyzer.research_priority` in the same index-resolution contract the
other project-scoped tools use: resolve projects from the merged index, run
per project, merge and re-rank, and report per-project coverage so a partial
scan is never presented as a whole-project answer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    from ..analyzer.research_priority import (
        DEFAULT_TOP_N,
        DEFAULT_WEIGHTS,
        MAX_TOP_N,
        rank_research_priority,
    )
    from ..index_store import load_index
except ImportError:  # pragma: no cover - flat-layout fallback
    from analyzer.research_priority import (  # type: ignore
        DEFAULT_TOP_N,
        DEFAULT_WEIGHTS,
        MAX_TOP_N,
        rank_research_priority,
    )
    from index_store import load_index  # type: ignore


def research_priority(
    project: Optional[str] = None,
    top_n: int = DEFAULT_TOP_N,
    since_days: int = 180,
    include_sanitized: bool = True,
    include_unproven: bool = True,
    sarif_path: Optional[str] = None,
) -> dict:
    """Rank the code paths most worth a security researcher's next hour.

    Fuses taint reachability, sink severity, entry-point exposure, function
    complexity, git churn, test gaps, and error-handling weakness into one
    ordered list. Each candidate carries the signals and the plain-language
    reasons behind its position, so the ranking can be argued with.

    Args:
        project: Restrict to one indexed project (substring match).
        top_n: Candidates to return (capped at MAX_TOP_N).
        since_days: Churn window in days.
        include_sanitized: Keep flows a sanitizer claims to neutralize.
        include_unproven: Keep the weaker evidence tiers. False returns only
            candidates with a proven source-to-sink flow.

    Returns:
        dict with `candidates`, `coverage`, `weights`, and per-project stats.
    """
    index = load_index()
    project_roots = index.get("project_roots", {})
    projects = index.get("projects", [])

    if project:
        projects = [p for p in projects if project.lower() in p.lower()]

    if not projects:
        return {
            "error": "No indexed project matched",
            "requested_project": project,
            "available_projects": index.get("projects", []),
        }

    all_candidates = []
    per_project = []
    weights = dict(DEFAULT_WEIGHTS)
    total_candidates = 0
    total_flows = 0

    for proj in projects:
        root = project_roots.get(proj)
        if not root or not Path(root).exists():
            per_project.append({
                "project": proj,
                "scanned": False,
                "reason": "project root not found on disk",
            })
            continue

        try:
            report = rank_research_priority(
                Path(root),
                index=index,
                project=proj,
                top_n=min(int(top_n), MAX_TOP_N),
                since_days=since_days,
                include_sanitized=include_sanitized,
                include_unproven=include_unproven,
                sarif_path=sarif_path,
            )
        except Exception as exc:  # pragma: no cover - defensive
            per_project.append({
                "project": proj,
                "scanned": False,
                "reason": f"analysis failed: {exc}",
            })
            continue

        weights = report.weights
        total_candidates += report.total_candidates
        total_flows += report.total_flows
        for candidate in report.candidates:
            item = candidate.to_dict()
            item["project"] = proj
            all_candidates.append(item)
        per_project.append({
            "project": proj,
            "scanned": True,
            "candidates": report.total_candidates,
            "coverage": report.coverage,
            "elapsed_seconds": round(report.elapsed_seconds, 2),
        })

    all_candidates.sort(
        key=lambda c: (-c["score"], c["file"], c["line"]),
    )
    limit = max(1, min(int(top_n), MAX_TOP_N))

    return {
        "candidates": all_candidates[:limit],
        "returned": min(len(all_candidates), limit),
        "total_candidates": total_candidates,
        "total_flows": total_flows,
        "weights": weights,
        "projects": per_project,
        "how_to_read": (
            "Score orders leads, it does not confirm bugs. Read `reasons` and "
            "`signals` before spending time; a signal listed in "
            "coverage.signals_unavailable was not measured, not measured as zero."
        ),
    }
