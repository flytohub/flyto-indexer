"""
Generate per-project .flyto/ folder (Schema v1).

Philosophy:
  - Indexer builds the SKELETON (categories, files, hashes, symbols, refs)
  - AI fills in DESCRIPTIONS incrementally during normal work
  - Hash tracking marks descriptions as stale when files change

Outputs:
  .flyto/flyto.json              - Root manifest
  .flyto/nav/map.json            - Code map (categories → files → symbols)
  .flyto/index/summary.json      - Statistics
  .flyto/descriptions.jsonl      - AI-written file descriptions (append-only)
  .flyto/tags/symbol_tags.jsonl  - Dead code + TDD mapping tags

See FLYTO_SCHEMA_V1.md for the contract specification.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

from .models import ProjectIndex, SymbolType, DependencyType
from .flyto_tags import generate_tags, compute_tag_stats, write_tags

# Version
GENERATOR_NAME = "flyto-indexer"
GENERATOR_VERSION = "0.2.0"
SCHEMA_VERSION = 1

# Max files per category in the map.
MAX_FILES_PER_CATEGORY = 30

# Max symbols per file.
MAX_SYMBOLS_PER_FILE = 10

# Top-level symbol types.
TOP_LEVEL_KINDS = {
    SymbolType.CLASS,
    SymbolType.FUNCTION,
    SymbolType.COMPONENT,
    SymbolType.COMPOSABLE,
    SymbolType.STORE,
    SymbolType.INTERFACE,
    SymbolType.TYPE,
    SymbolType.VARIABLE,
    SymbolType.API,
    SymbolType.ROUTE,
}

# Category labels for display.
CATEGORY_LABELS = {
    "api": "API Endpoints",
    "component": "Components",
    "composable": "Composables",
    "service": "Services",
    "store": "Stores",
    "view": "Views / Pages",
    "util": "Utilities",
    "model": "Models",
    "config": "Configuration",
    "test": "Tests",
    "middleware": "Middleware",
    "route": "Routes",
    "gateway": "Gateway",
    "repository": "Repositories",
    "module": "Modules",
}


def generate_flyto_folder(
    project_index: ProjectIndex,
    project_root: Path,
) -> Path:
    """Generate .flyto/ inside *project_root*."""
    flyto_dir = project_root / ".flyto"
    nav_dir = flyto_dir / "nav"
    index_dir = flyto_dir / "index"

    nav_dir.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    languages = _count_languages(project_index)

    # --- flyto.json ---
    flyto_json = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now,
        "project": {
            "name": project_index.project,
            "root": ".",
            "languageHint": sorted(languages.keys()),
        },
        "generator": {
            "name": GENERATOR_NAME,
            "version": GENERATOR_VERSION,
        },
        "paths": {
            "map": "nav/map.json",
            "brief": "brief.md",
            "descriptions": "descriptions.jsonl",
            "indexSummary": "index/summary.json",
        },
    }
    _write_json(flyto_dir / "flyto.json", flyto_json)

    # --- nav/map.json ---
    code_map = _build_code_map(project_index)
    code_map["schemaVersion"] = SCHEMA_VERSION
    code_map["generatedAt"] = now
    _write_json(nav_dir / "map.json", code_map, compact=True)

    # --- tags/symbol_tags.jsonl ---
    tags = generate_tags(project_index)
    tags_dir = flyto_dir / "tags"
    write_tags(tags, tags_dir)
    tag_stats = compute_tag_stats(tags, project_index)

    # --- descriptions.jsonl (append skeleton descriptions for new/stale files) ---
    desc_path = flyto_dir / "descriptions.jsonl"
    file_imports, file_imported_by = _build_file_deps(project_index)
    desc_stats = _update_skeleton_descriptions(
        desc_path, code_map, project_index, now, file_imports, file_imported_by,
    )

    # --- brief.md ---
    _generate_brief(
        flyto_dir, code_map, project_index, tag_stats, desc_stats, languages,
    )

    # --- index/summary.json ---
    summary_json = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now,
        "counts": {
            "files": len(project_index.files),
            "folders": _count_folders(project_index),
            "symbols": len(project_index.symbols),
            "languages": languages,
        },
        "tags": tag_stats,
        "descriptions": desc_stats,
        "stalenessHint": {
            "recommendedReindexAfterHours": 24,
        },
    }
    _write_json(index_dir / "summary.json", summary_json)

    return flyto_dir


# ---------------------------------------------------------------------------
# Code map building
# ---------------------------------------------------------------------------

def _build_code_map(index: ProjectIndex) -> dict:
    """
    Build category-based code map.

    Structure:
      categories: [ { id, label, count, files: [ { path, hash, refs, symbols } ] } ]
      hotspots: [ { path, refs, hash } ]  (top 10 most-referenced files)
    """
    # Group symbols by file
    symbols_by_file: dict[str, list] = defaultdict(list)
    for sym in index.symbols.values():
        symbols_by_file[sym.path].append(sym)

    # Compute per-file importance and hash
    file_info: dict[str, dict] = {}
    for fpath, syms in symbols_by_file.items():
        total_refs = sum(s.reference_count for s in syms)
        # Use first symbol's content_hash or file manifest hash
        file_hash = ""
        if fpath in index.files:
            file_hash = index.files[fpath].content_hash
        elif syms:
            file_hash = syms[0].content_hash or ""

        file_info[fpath] = {
            "refs": total_refs,
            "hash": file_hash,
            "symbols": syms,
            "lines": index.files[fpath].line_count if fpath in index.files else 0,
        }

    # Categorize files
    categorized: dict[str, list[str]] = defaultdict(list)
    for fpath in symbols_by_file:
        cat = _categorize_file(fpath)
        categorized[cat].append(fpath)

    # Build categories
    categories = []
    for cat_id in sorted(categorized.keys()):
        files_in_cat = categorized[cat_id]
        total_count = len(files_in_cat)

        # Sort by refs (most important first), cap
        files_in_cat.sort(key=lambda f: file_info[f]["refs"], reverse=True)
        top_files = files_in_cat[:MAX_FILES_PER_CATEGORY]

        file_entries = []
        for fpath in sorted(top_files):  # alphabetical within cap
            info = file_info[fpath]
            syms = _build_symbols(info["symbols"])
            file_entries.append({
                "path": fpath,
                "hash": info["hash"],
                "refs": info["refs"],
                "lines": info["lines"],
                "symbols": syms,
            })

        categories.append({
            "id": cat_id,
            "label": CATEGORY_LABELS.get(cat_id, cat_id.title()),
            "count": total_count,
            "files": file_entries,
        })

    # Hotspots: top 10 most-referenced files across all categories
    all_files = sorted(file_info.items(), key=lambda x: x[1]["refs"], reverse=True)
    hotspots = []
    for fpath, info in all_files[:10]:
        if info["refs"] > 0:
            hotspots.append({
                "path": fpath,
                "refs": info["refs"],
                "hash": info["hash"],
            })

    return {
        "categories": categories,
        "hotspots": hotspots,
    }


def _build_symbols(symbols: list) -> list:
    """Build compact symbol list for a file."""
    top_level = [s for s in symbols if s.symbol_type in TOP_LEVEL_KINDS]
    if not top_level:
        top_level = [s for s in symbols if s.symbol_type == SymbolType.METHOD]

    top_level.sort(key=lambda s: s.start_line)
    top_level = top_level[:MAX_SYMBOLS_PER_FILE]

    result = []
    for sym in top_level:
        entry = {
            "name": sym.name,
            "kind": sym.symbol_type.value,
            "line": sym.start_line,
            "endLine": sym.end_line,
        }
        if sym.reference_count > 0:
            entry["refs"] = sym.reference_count
        result.append(entry)
    return result


def _categorize_file(path: str) -> str:
    """Categorize a file by its path patterns."""
    p = path.lower()

    if "/api/" in p or p.endswith("_api.py") or p.endswith("_api.js"):
        return "api"
    if "/composable" in p:
        return "composable"
    if "/component" in p:
        return "component"
    if "/service" in p:
        return "service"
    if "/store" in p:
        return "store"
    if "/view" in p or "/views/" in p or "/pages/" in p:
        return "view"
    if "/util" in p or "/helpers/" in p:
        return "util"
    if "/model" in p:
        return "model"
    if "/test" in p or p.startswith("test"):
        return "test"
    if "/config" in p:
        return "config"
    if "/middleware" in p:
        return "middleware"
    if "/route" in p:
        return "route"
    if "/gateway" in p:
        return "gateway"
    if "/repositor" in p:
        return "repository"
    return "module"


# ---------------------------------------------------------------------------
# Skeleton Descriptions (zero-LLM, from map data)
# ---------------------------------------------------------------------------

def _update_skeleton_descriptions(
    desc_path: Path,
    code_map: dict,
    index: ProjectIndex,
    now: str,
    file_imports: dict[str, list[str]] | None = None,
    file_imported_by: dict[str, list[str]] | None = None,
) -> dict:
    """Generate skeleton descriptions for files in the code map.

    Append-only: reads existing descriptions, adds new entries for
    files that are missing or stale (hash mismatch). Never removes
    existing entries (AI-written descriptions are preserved).

    Returns description stats dict.
    """
    # Read existing descriptions, build {path: hash} of latest entries
    existing: dict[str, str] = {}  # path -> latest hash
    existing_lines: list[str] = []
    if desc_path.exists():
        content = desc_path.read_text(encoding="utf-8").strip()
        if content:
            existing_lines = content.split("\n")
            # Bottom-up: latest entry wins
            for line in existing_lines:
                try:
                    entry = json.loads(line)
                    existing[entry["path"]] = entry.get("hash", "")
                except (json.JSONDecodeError, KeyError):
                    pass

    # Collect all files from code map (categories + hotspots)
    map_files: dict[str, dict] = {}  # path -> {hash, refs, category, symbols}
    for cat in code_map.get("categories", []):
        cat_id = cat["id"]
        for f in cat.get("files", []):
            path = f["path"]
            if path not in map_files or f["refs"] > map_files[path].get("refs", 0):
                map_files[path] = {
                    "hash": f["hash"],
                    "refs": f["refs"],
                    "category": cat_id,
                    "symbols": f.get("symbols", []),
                }

    # Mark hotspot files
    hotspot_paths = {h["path"] for h in code_map.get("hotspots", [])}

    # Generate new entries for missing/stale files
    new_lines: list[str] = []
    fresh = 0
    stale = 0
    missing = 0

    for fpath, info in map_files.items():
        current_hash = info["hash"]

        if fpath in existing and existing[fpath] == current_hash:
            fresh += 1
            continue

        if fpath in existing:
            stale += 1
        else:
            missing += 1

        # Build one_liner from data
        one_liner = _build_one_liner(fpath, info, hotspot_paths)

        # Build symbol names list
        sym_names = [s["name"] for s in info["symbols"][:5]]

        # Build symbol kinds summary
        kind_counts: dict[str, int] = defaultdict(int)
        for s in info["symbols"]:
            kind_counts[s["kind"]] += 1
        kinds_str = ", ".join(f"{count} {kind}" for kind, count in sorted(kind_counts.items()))

        entry = {
            "path": fpath,
            "hash": current_hash,
            "one_liner": one_liner,
            "category": info["category"],
            "refs": info["refs"],
            "lines": index.files[fpath].line_count if fpath in index.files else 0,
            "symbolCount": len(info["symbols"]),
            "symbolKinds": kinds_str,
            "topSymbols": sym_names,
            "imports": (file_imports.get(fpath, []))[:8] if file_imports else [],
            "importedBy": (file_imported_by.get(fpath, []))[:8] if file_imported_by else [],
            "hotspot": fpath in hotspot_paths,
            "source": "indexer",
            "updatedAt": now,
        }
        new_lines.append(json.dumps(entry, ensure_ascii=False))

    # Append new entries (never remove existing)
    if new_lines:
        with open(desc_path, "a", encoding="utf-8") as f:
            if existing_lines:
                # Ensure we start on a new line
                f.write("\n")
            f.write("\n".join(new_lines) + "\n")

    total_in_map = len(map_files)
    return {
        "total": total_in_map,
        "fresh": fresh + len(new_lines),  # newly written count as fresh
        "stale": 0,  # we just refreshed all stale ones
        "missing": 0,  # we just filled all missing ones
        "sources": {
            "indexer": fresh + stale + missing,
            "ai": 0,
        },
        "hotspot_total": len(hotspot_paths),
        "hotspot_covered": sum(
            1 for p in hotspot_paths
            if p in map_files
        ),
    }


def _build_one_liner(path: str, info: dict, hotspot_paths: set) -> str:
    """Build a mechanical one-liner description from map data."""
    category = info["category"]
    refs = info["refs"]
    symbols = info["symbols"]
    cat_label = CATEGORY_LABELS.get(category, category.title())

    # File basename for context
    basename = path.rsplit("/", 1)[-1]

    # Top symbol names (max 3)
    sym_names = [s["name"] for s in symbols[:3]]
    sym_str = ", ".join(sym_names)
    extra = len(symbols) - 3
    if extra > 0:
        sym_str += f" +{extra} more"

    # Symbol kind breakdown
    kind_counts: dict[str, int] = defaultdict(int)
    for s in symbols:
        kind_counts[s["kind"]] += 1

    # Build the one-liner
    parts = []
    parts.append(f"{cat_label}")

    if refs > 0:
        parts.append(f"({refs} refs)")

    if hotspot_paths and path in hotspot_paths:
        parts.append("[hotspot]")

    if sym_str:
        parts.append(f"— {sym_str}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# File-level dependency maps
# ---------------------------------------------------------------------------

def _build_file_deps(
    index: ProjectIndex,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build file-level import/importedBy maps from dependency graph.

    Returns (imports_map, imported_by_map) where keys are file paths
    and values are sorted lists of file basenames.
    """
    imports_raw: dict[str, set[str]] = defaultdict(set)
    imported_by_raw: dict[str, set[str]] = defaultdict(set)

    # Build lookup: basename_no_ext -> set of full paths
    known_files = set(index.files.keys())
    base_lookup: dict[str, set[str]] = defaultdict(set)
    for fpath in known_files:
        basename = fpath.rsplit("/", 1)[-1]
        base_no_ext = basename.rsplit(".", 1)[0] if "." in basename else basename
        base_lookup[base_no_ext].add(fpath)
        base_lookup[basename].add(fpath)

    for dep in index.dependencies.values():
        if dep.dep_type != DependencyType.IMPORTS:
            continue

        # Extract source file path from symbol ID (project:path:type:name)
        parts = dep.source_id.split(":", 3)
        if len(parts) < 2:
            continue
        source_file = parts[1]

        target = dep.target_id or ""
        if not target:
            continue

        # Resolve target to known file paths
        matched: set[str] = set()
        if target in known_files:
            matched.add(target)
        else:
            target_base = target.rsplit("/", 1)[-1]
            target_stem = target_base.rsplit(".", 1)[0] if "." in target_base else target_base
            matched = base_lookup.get(target_stem, set()) | base_lookup.get(target_base, set())

        for m in matched:
            if m == source_file:
                continue
            source_basename = source_file.rsplit("/", 1)[-1]
            matched_basename = m.rsplit("/", 1)[-1]
            imports_raw[source_file].add(matched_basename)
            imported_by_raw[m].add(source_basename)

    return (
        {k: sorted(v) for k, v in imports_raw.items()},
        {k: sorted(v) for k, v in imported_by_raw.items()},
    )


# ---------------------------------------------------------------------------
# brief.md generation (< 500 tokens project overview)
# ---------------------------------------------------------------------------

def _generate_brief(
    flyto_dir: Path,
    code_map: dict,
    index: ProjectIndex,
    tag_stats: dict,
    desc_stats: dict,
    languages: dict,
) -> None:
    """Generate .flyto/brief.md — a < 500 token project overview.

    Built entirely from map.json + summary.json data, zero LLM.
    Designed to be read by AI at session start for instant orientation.
    """
    project_name = index.project
    file_count = len(index.files)

    # Language summary
    lang_parts = []
    for lang, count in sorted(languages.items(), key=lambda x: -x[1]):
        lang_parts.append(f"{lang.title()} {count}")
    lang_str = ", ".join(lang_parts) if lang_parts else "unknown"

    lines = [f"# {project_name}", f"{lang_str} | {file_count} files", ""]

    # Structure table — categories sorted by file count
    categories = code_map.get("categories", [])
    if categories:
        lines.append("## Structure")
        lines.append("| Category | Files | Top Files |")
        lines.append("|----------|-------|-----------|")
        for cat in sorted(categories, key=lambda c: -c["count"]):
            cat_label = CATEGORY_LABELS.get(cat["id"], cat["id"].title())
            # Top 2 files by refs
            top = sorted(cat["files"], key=lambda f: -f["refs"])[:2]
            top_names = ", ".join(f["path"].rsplit("/", 1)[-1] for f in top)
            lines.append(f"| {cat_label} | {cat['count']} | {top_names} |")
        lines.append("")

    # Hotspots
    hotspots = code_map.get("hotspots", [])
    if hotspots:
        lines.append("## Hotspots")
        for h in hotspots[:5]:
            basename = h["path"].rsplit("/", 1)[-1]
            lines.append(f"- {basename} ({h['refs']} refs)")
        lines.append("")

    # Health
    health_parts = []
    dead = tag_stats.get("dead_code", 0)
    dead_lines = tag_stats.get("dead_code_lines", 0)
    if dead > 0:
        health_parts.append(f"Dead code: {dead} symbols / {dead_lines} lines")
    tdd_covered = tag_stats.get("tdd_covered", 0)
    tdd_testable = tag_stats.get("tdd_testable", 0)
    if tdd_testable > 0:
        pct = round(tdd_covered / tdd_testable * 100, 1) if tdd_testable else 0
        health_parts.append(f"TDD: {tdd_covered}/{tdd_testable} ({pct}%)")
    if health_parts:
        lines.append("## Health")
        for p in health_parts:
            lines.append(f"- {p}")
        lines.append("")

    brief_path = flyto_dir / "brief.md"
    brief_path.write_text("\n".join(lines), encoding="utf-8")


def generate_brief_from_flyto(project_root: Path) -> str:
    """Generate brief.md from existing .flyto/ data (no re-index needed).

    Returns the brief content as a string, and writes to .flyto/brief.md.
    Raises FileNotFoundError if .flyto/ doesn't exist.
    """
    flyto_dir = project_root / ".flyto"
    if not flyto_dir.exists():
        raise FileNotFoundError(f"No .flyto/ found at {project_root}")

    map_path = flyto_dir / "nav" / "map.json"
    summary_path = flyto_dir / "index" / "summary.json"
    flyto_path = flyto_dir / "flyto.json"

    if not map_path.exists() or not summary_path.exists():
        raise FileNotFoundError("Missing map.json or summary.json — run 'flyto-index scan' first")

    code_map = json.loads(map_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # Extract project name
    project_name = project_root.name
    if flyto_path.exists():
        flyto_json = json.loads(flyto_path.read_text(encoding="utf-8"))
        project_name = flyto_json.get("project", {}).get("name", project_name)

    counts = summary.get("counts", {})
    languages = counts.get("languages", {})
    file_count = counts.get("files", 0)
    tag_stats = summary.get("tags", {})

    lang_parts = []
    for lang, count in sorted(languages.items(), key=lambda x: -x[1]):
        lang_parts.append(f"{lang.title()} {count}")
    lang_str = ", ".join(lang_parts) if lang_parts else "unknown"

    lines = [f"# {project_name}", f"{lang_str} | {file_count} files", ""]

    categories = code_map.get("categories", [])
    if categories:
        lines.append("## Structure")
        lines.append("| Category | Files | Top Files |")
        lines.append("|----------|-------|-----------|")
        for cat in sorted(categories, key=lambda c: -c["count"]):
            cat_label = CATEGORY_LABELS.get(cat["id"], cat["id"].title())
            top = sorted(cat["files"], key=lambda f: -f["refs"])[:2]
            top_names = ", ".join(f["path"].rsplit("/", 1)[-1] for f in top)
            lines.append(f"| {cat_label} | {cat['count']} | {top_names} |")
        lines.append("")

    hotspots = code_map.get("hotspots", [])
    if hotspots:
        lines.append("## Hotspots")
        for h in hotspots[:5]:
            basename = h["path"].rsplit("/", 1)[-1]
            lines.append(f"- {basename} ({h['refs']} refs)")
        lines.append("")

    health_parts = []
    dead = tag_stats.get("dead_code", 0)
    dead_lines = tag_stats.get("dead_code_lines", 0)
    if dead > 0:
        health_parts.append(f"Dead code: {dead} symbols / {dead_lines} lines")
    tdd_covered = tag_stats.get("tdd_covered", 0)
    tdd_testable = tag_stats.get("tdd_testable", 0)
    if tdd_testable > 0:
        pct = round(tdd_covered / tdd_testable * 100, 1) if tdd_testable else 0
        health_parts.append(f"TDD: {tdd_covered}/{tdd_testable} ({pct}%)")
    if health_parts:
        lines.append("## Health")
        for p in health_parts:
            lines.append(f"- {p}")
        lines.append("")

    content = "\n".join(lines)
    brief_path = flyto_dir / "brief.md"
    brief_path.write_text(content, encoding="utf-8")
    return content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_languages(index: ProjectIndex) -> dict:
    lang_count: dict[str, int] = defaultdict(int)
    seen: set[str] = set()
    for sym in index.symbols.values():
        if sym.path not in seen and sym.language:
            seen.add(sym.path)
            lang_count[sym.language] += 1
    return dict(lang_count)


def _count_folders(index: ProjectIndex) -> int:
    folders: set[str] = set()
    for fpath in index.files:
        parts = Path(fpath).parts
        for i in range(1, len(parts)):
            folders.add("/".join(parts[:i]))
    return len(folders)


def _write_json(path: Path, data: dict, compact: bool = False):
    if compact:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
