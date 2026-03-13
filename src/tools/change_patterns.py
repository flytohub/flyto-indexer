"""Change pattern learning — discover co-change clusters from git history."""

import logging
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("flyto-indexer.change_patterns")

try:
    from .git_intel import _find_git_root, _get_cached_log, _get_project_root
except ImportError:
    from tools.git_intel import _find_git_root, _get_cached_log, _get_project_root


def discover_change_clusters(
    project: Optional[str] = None,
    min_support: int = 3,
    min_confidence: float = 0.3,
    max_clusters: int = 20,
) -> dict:
    """Discover groups of files that frequently change together.

    Uses association rule mining on git commit history:
    - support: how many commits include this file pair
    - confidence: P(B changes | A changes)
    - lift: confidence / P(B changes) — >1 means correlated

    Args:
        project: Filter to specific project
        min_support: Minimum co-change count (default 3)
        min_confidence: Minimum confidence ratio (default 0.3)
        max_clusters: Maximum clusters to return (default 20)

    Returns:
        {clusters: [{files, support, avg_confidence, category}], summary}
    """
    try:
        proj_name, proj_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    git_root = _find_git_root(proj_root)
    if not git_root:
        return {"error": f"No git repository found for {proj_name}"}

    try:
        entries = _get_cached_log(git_root, ("--since=6 months ago",))
    except RuntimeError as e:
        return {"error": str(e)}

    git_root_abs = os.path.abspath(git_root)
    proj_root_abs = os.path.abspath(proj_root)
    proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
    if proj_prefix == ".":
        proj_prefix = ""

    # Build per-file commit sets and co-occurrence matrix
    file_commits: Dict[str, set] = defaultdict(set)
    pair_commits: Dict[Tuple[str, str], set] = defaultdict(set)
    total_commits = len(entries)

    for entry in entries:
        commit_hash = entry.get("hash", "")
        rel_files = []
        for f in entry.get("files", []):
            if proj_prefix and not f.startswith(proj_prefix + "/"):
                continue
            rel = f[len(proj_prefix):].lstrip("/") if proj_prefix else f
            # Skip non-code files
            if _is_noise_file(rel):
                continue
            rel_files.append(rel)

        for f in rel_files:
            file_commits[f].add(commit_hash)

        # Record co-occurrences (only for manageable commit sizes)
        if 2 <= len(rel_files) <= 30:
            for i, a in enumerate(rel_files):
                for b in rel_files[i + 1:]:
                    pair = tuple(sorted([a, b]))
                    pair_commits[pair].add(commit_hash)

    if not pair_commits:
        return {
            "clusters": [],
            "summary": {"total_commits": total_commits, "total_files": len(file_commits)},
        }

    # Mine association rules
    rules = []
    for (a, b), commits in pair_commits.items():
        support = len(commits)
        if support < min_support:
            continue

        conf_a_to_b = support / len(file_commits[a]) if file_commits[a] else 0
        conf_b_to_a = support / len(file_commits[b]) if file_commits[b] else 0
        avg_confidence = (conf_a_to_b + conf_b_to_a) / 2

        if avg_confidence < min_confidence:
            continue

        # Lift: how much more likely than random
        p_b = len(file_commits[b]) / total_commits if total_commits else 0
        lift = avg_confidence / (p_b if p_b > 0 else 1)

        rules.append({
            "files": [a, b],
            "support": support,
            "confidence_a_to_b": round(conf_a_to_b, 3),
            "confidence_b_to_a": round(conf_b_to_a, 3),
            "avg_confidence": round(avg_confidence, 3),
            "lift": round(lift, 2),
        })

    # Merge pairwise rules into clusters using union-find
    clusters = _merge_into_clusters(rules, min_confidence)
    clusters.sort(key=lambda c: c["support"], reverse=True)
    clusters = clusters[:max_clusters]

    # Categorize clusters
    for cluster in clusters:
        cluster["category"] = _categorize_cluster(cluster["files"])

    return {
        "clusters": clusters,
        "summary": {
            "total_commits_analyzed": total_commits,
            "total_files": len(file_commits),
            "total_pairs": len(pair_commits),
            "total_clusters": len(clusters),
        },
    }


def suggest_cochanges(
    target_files: List[str],
    project: Optional[str] = None,
    min_confidence: float = 0.3,
    max_suggestions: int = 10,
) -> dict:
    """Given file(s) being modified, suggest other files that likely need changes.

    This is the main integration point for task(action='plan').

    Args:
        target_files: Files being modified
        project: Project name
        min_confidence: Minimum confidence to suggest
        max_suggestions: Max suggestions to return

    Returns:
        {suggestions: [{path, confidence, support, reason}]}
    """
    try:
        proj_name, proj_root = _get_project_root(project)
    except ValueError as e:
        return {"error": str(e)}

    git_root = _find_git_root(proj_root)
    if not git_root:
        return {"error": f"No git repository found for {proj_name}"}

    try:
        entries = _get_cached_log(git_root, ("--since=6 months ago",))
    except RuntimeError as e:
        return {"error": str(e)}

    git_root_abs = os.path.abspath(git_root)
    proj_root_abs = os.path.abspath(proj_root)
    proj_prefix = os.path.relpath(proj_root_abs, git_root_abs)
    if proj_prefix == ".":
        proj_prefix = ""

    # Normalize target files
    targets = set()
    for f in target_files:
        norm = f.replace("\\", "/").strip("/")
        targets.add(norm)

    # Find commits touching any target file and count co-occurrences
    target_commit_count = 0
    cochange_count: Dict[str, int] = defaultdict(int)

    for entry in entries:
        rel_files = []
        for f in entry.get("files", []):
            if proj_prefix and not f.startswith(proj_prefix + "/"):
                continue
            rel = f[len(proj_prefix):].lstrip("/") if proj_prefix else f
            rel_files.append(rel)

        if not targets.intersection(rel_files):
            continue

        target_commit_count += 1
        for f in rel_files:
            if f not in targets and not _is_noise_file(f):
                cochange_count[f] += 1

    if not cochange_count:
        return {"suggestions": [], "target_commits": target_commit_count}

    suggestions = []
    for f, count in cochange_count.items():
        confidence = count / target_commit_count if target_commit_count else 0
        if confidence < min_confidence:
            continue
        suggestions.append({
            "path": f,
            "confidence": round(confidence, 3),
            "support": count,
            "reason": f"Changed together in {count}/{target_commit_count} commits ({confidence:.0%})",
        })

    suggestions.sort(key=lambda s: s["confidence"], reverse=True)
    suggestions = suggestions[:max_suggestions]

    return {
        "target_files": list(targets),
        "target_commits": target_commit_count,
        "suggestions": suggestions,
    }


def _is_noise_file(path: str) -> bool:
    """Skip non-code files that add noise to co-change analysis."""
    noise_patterns = {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "poetry.lock", "Pipfile.lock", "Cargo.lock",
        ".gitignore", ".eslintrc", ".prettierrc",
        "CHANGELOG.md", "LICENSE",
    }
    basename = os.path.basename(path)
    if basename in noise_patterns:
        return True
    if basename.startswith("."):
        return True
    # Skip generated/build files
    noise_dirs = {"node_modules/", "dist/", "build/", "__pycache__/", ".git/"}
    return any(d in path for d in noise_dirs)


def _merge_into_clusters(rules: list, min_confidence: float) -> list:
    """Merge pairwise rules into multi-file clusters using union-find."""
    parent = {}

    def find(x):
        if x not in parent:
            parent[x] = x
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Only merge high-confidence pairs
    for rule in rules:
        if rule["avg_confidence"] >= min_confidence:
            union(rule["files"][0], rule["files"][1])

    # Group files by cluster root
    cluster_files = defaultdict(set)
    for rule in rules:
        for f in rule["files"]:
            root = find(f)
            cluster_files[root].add(f)

    # Build cluster summaries
    clusters = []
    for _root, files in cluster_files.items():
        # Aggregate support from rules within this cluster
        total_support = 0
        total_conf = 0
        count = 0
        for rule in rules:
            if set(rule["files"]).issubset(files):
                total_support = max(total_support, rule["support"])
                total_conf += rule["avg_confidence"]
                count += 1

        clusters.append({
            "files": sorted(files),
            "size": len(files),
            "support": total_support,
            "avg_confidence": round(total_conf / count, 3) if count else 0,
        })

    return clusters


def _categorize_cluster(files: list) -> str:
    """Categorize a cluster based on file patterns."""
    has_test = any("test" in f.lower() for f in files)
    has_route = any("route" in f.lower() or "api/" in f.lower() for f in files)
    has_schema = any("schema" in f.lower() or "model" in f.lower() or "type" in f.lower() for f in files)
    has_ui = any(f.endswith((".vue", ".tsx", ".jsx")) for f in files)
    has_i18n = any("i18n" in f.lower() or "locale" in f.lower() for f in files)
    has_config = any("config" in f.lower() or "setting" in f.lower() for f in files)

    if has_route and has_schema:
        return "api_endpoint"
    if has_ui and has_test:
        return "component_with_test"
    if has_route and has_ui:
        return "feature_slice"
    if has_i18n:
        return "i18n_bundle"
    if has_config:
        return "config_change"
    if has_test:
        return "code_with_test"
    return "coupled_files"
