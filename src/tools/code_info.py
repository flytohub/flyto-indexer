"""Code information tools for flyto-indexer MCP server."""

import json
from pathlib import Path

try:
    from ..index_store import (
        load_index,
        get_symbol_content_text,
        load_project_map,
        _get_test_mapper,
    )
except ImportError:
    from index_store import (
        load_index,
        get_symbol_content_text,
        load_project_map,
        _get_test_mapper,
    )


def get_file_info(path: str) -> dict:
    """
    Get file information.

    Includes purpose, category, keywords, APIs, dependencies, etc.
    """
    project_map = load_project_map()
    file_info = project_map.get("files", {}).get(path, {})

    if not file_info:
        return {"error": f"File not found: {path}"}

    return {
        "path": path,
        "purpose": file_info.get("purpose", ""),
        "category": file_info.get("category", ""),
        "keywords": file_info.get("keywords", []),
        "apis": file_info.get("apis", []),
        "dependencies": file_info.get("dependencies", []),
        "ui_elements": file_info.get("ui_elements", []),
    }


def get_file_symbols(path: str) -> dict:
    """
    Get symbols defined in a file.

    Lists all functions, classes, and components in the file.
    """
    index = load_index()
    symbols = []

    for symbol_id, symbol in index.get("symbols", {}).items():
        if symbol.get("path") == path:
            symbols.append({
                "id": symbol_id,
                "name": symbol.get("name", ""),
                "type": symbol.get("type", ""),
                "line": symbol.get("start_line", 0),
                "summary": symbol.get("summary", ""),
            })

    return {
        "path": path,
        "count": len(symbols),
        "symbols": symbols,
    }


def get_symbol_content(symbol_id: str) -> dict:
    """
    Get the full source code of a symbol.

    Loads content from content.jsonl if not in main index.
    """
    index = load_index()
    symbol = index.get("symbols", {}).get(symbol_id)
    resolved_id = symbol_id

    if not symbol:
        # Try fuzzy matching
        for sid, sym in index.get("symbols", {}).items():
            if symbol_id in sid or sid.endswith(symbol_id):
                symbol = sym
                resolved_id = sid
                break

    if not symbol:
        return {"error": f"Symbol not found: {symbol_id}"}

    # Get content (may be in content.jsonl)
    content = get_symbol_content_text(resolved_id, symbol)

    return {
        "symbol_id": resolved_id,
        "project": resolved_id.split(":")[0] if ":" in resolved_id else "",
        "path": symbol.get("path", ""),
        "name": symbol.get("name", ""),
        "type": symbol.get("type", ""),
        "line_start": symbol.get("start_line", 0),
        "line_end": symbol.get("end_line", 0),
        "summary": symbol.get("summary", ""),
        "content": content,
    }


def _collect_file_imports(path: str, dependencies: dict) -> list[dict]:
    """Collect imports for a file from dependencies where source is in this file."""
    imports = []
    seen_imports = set()
    for _dep_id, dep in dependencies.items():
        source_id = dep.get("source", "")
        if ":" in source_id:
            source_path = source_id.split(":")[1] if len(source_id.split(":")) >= 2 else ""
            if source_path == path and dep.get("type") == "imports":
                target = dep.get("target", "")
                if target not in seen_imports:
                    seen_imports.add(target)
                    names = dep.get("metadata", {}).get("names", [])
                    imports.append({"target": target, "names": names})
    return imports


def _collect_file_dependents(path: str, reverse_index: dict) -> list[dict]:
    """Collect dependents (who references symbols in this file)."""
    dependents = []
    seen_deps = set()
    for sym_id, callers in reverse_index.items():
        if ":" not in sym_id:
            continue
        sym_path = sym_id.split(":")[1] if len(sym_id.split(":")) >= 2 else ""
        if sym_path != path:
            continue
        sym_name = sym_id.split(":")[-1] if ":" in sym_id else sym_id
        for caller_id in callers:
            if ":" in caller_id:
                caller_path = caller_id.split(":")[1] if len(caller_id.split(":")) >= 2 else ""
                if caller_path != path and caller_id not in seen_deps:
                    seen_deps.add(caller_id)
                    dependents.append({
                        "from_path": caller_path,
                        "symbol_used": sym_name,
                        "confidence": "high",
                    })
    return dependents


def _collect_related_files(
    path: str, imports: list[dict], dependents: list[dict], symbols_map: dict,
) -> list[dict]:
    """Collect related files (1-hop import graph neighbors)."""
    related_files = []
    related_seen = set()
    # Import targets
    for imp in imports:
        target = imp["target"]
        # Try to resolve to a file path
        for sid, sym in symbols_map.items():
            if sym.get("path", "") and target in sid:
                rpath = sym["path"]
                if rpath != path and rpath not in related_seen:
                    related_seen.add(rpath)
                    related_files.append({"path": rpath, "relation": "imports"})
                break
    # Dependents as related
    for dep in dependents[:10]:
        rpath = dep["from_path"]
        if rpath not in related_seen:
            related_seen.add(rpath)
            related_files.append({"path": rpath, "relation": "imported_by"})
    return related_files


def get_file_context(path: str, include_content: bool = False) -> dict:
    """
    One-call context package: returns everything an agent needs about a file.

    Aggregates: file info, symbols, imports, dependents, test file, related files.
    All from cached data, zero I/O.
    """
    index = load_index()
    symbols_map = index.get("symbols", {})

    # 1. File info
    info = get_file_info(path)
    if "error" in info:
        info = {"purpose": "", "category": "", "keywords": []}

    # 2. Symbols in this file
    file_symbols = get_file_symbols(path)
    symbols_list = file_symbols.get("symbols", [])

    # 3. Imports (dependencies where source is in this file)
    imports = _collect_file_imports(path, index.get("dependencies", {}))

    # 4. Dependents (who references symbols in this file)
    dependents = _collect_file_dependents(path, index.get("reverse_index", {}))

    # 5. Test file
    mapper = _get_test_mapper()
    test_file = mapper.find_test(path)

    # 6. Related files (1-hop import graph neighbors)
    related_files = _collect_related_files(path, imports, dependents, symbols_map)

    # 7. Optional: include content
    if include_content:
        for sym_entry in symbols_list:
            sid = sym_entry.get("id", "")
            sym_data = symbols_map.get(sid, {})
            content = get_symbol_content_text(sid, sym_data)
            sym_entry["content"] = content[:500] if content else ""

    return {
        "path": path,
        "info": info,
        "symbols": symbols_list,
        "imports": imports,
        "dependents": dependents[:30],
        "test_file": test_file,
        "related_files": related_files[:20],
        "summary": {
            "total_symbols": len(symbols_list),
            "total_imports": len(imports),
            "total_dependents": len(dependents),
        },
    }


def list_categories() -> dict:
    """
    List all categories.
    """
    project_map = load_project_map()
    categories = project_map.get("categories", {})

    return {
        "total": len(categories),
        "categories": [
            {"name": cat, "file_count": len(paths)}
            for cat, paths in sorted(categories.items(), key=lambda x: -len(x[1]))
        ],
    }


def list_apis() -> dict:
    """
    List all API endpoints with cross-language references.

    Combines:
    1. API endpoints from PROJECT_MAP (legacy)
    2. API symbols from index (detected by Python scanner decorators)
    3. API_CALLS from index (detected by TS/Vue scanner fetch/axios patterns)
    """
    project_map = load_project_map()
    api_map = project_map.get("api_map", {})
    index = load_index()
    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})

    # Collect API endpoints from index symbols (SymbolType.API)
    api_endpoints = {}  # path -> {method, defined_in, handler, symbol_id}
    for sid, sym in symbols.items():
        if sym.get("type") == "api":
            url = sym.get("name", "")
            meta = sym.get("metadata", {})
            if not meta:
                # Compact format: metadata might be in summary
                summary = sym.get("summary", "")
                method = "GET"
                if summary:
                    parts = summary.split(" ", 1)
                    if parts[0] in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                        method = parts[0]
            else:
                method = meta.get("method", "GET")
            api_endpoints[url] = {
                "method": method,
                "defined_in": sym.get("path", ""),
                "handler": meta.get("handler", "") if meta else "",
                "symbol_id": sid,
            }

    # Collect API calls from dependencies (DependencyType.API_CALLS)
    api_callers = {}  # url -> [{from_path, method}]
    for _dep_id, dep in dependencies.items():
        if dep.get("type") != "api_calls":
            continue
        url = dep.get("target", "")
        source_id = dep.get("source", "")
        source_path = ""
        if ":" in source_id:
            parts = source_id.split(":")
            if len(parts) >= 2:
                source_path = parts[1]
        meta = dep.get("metadata", {})
        method = meta.get("method", "GET")

        if url not in api_callers:
            api_callers[url] = []
        api_callers[url].append({"from_path": source_path, "method": method})

    # Merge everything
    all_urls = set(api_map.keys()) | set(api_endpoints.keys()) | set(api_callers.keys())

    apis = []
    for url in sorted(all_urls):
        entry = {
            "path": url,
            "method": api_endpoints.get(url, {}).get("method", ""),
            "defined_in": api_endpoints.get(url, {}).get("defined_in", ""),
            "handler": api_endpoints.get(url, {}).get("handler", ""),
            "called_by": sorted({
                c["from_path"] for c in api_callers.get(url, []) if c["from_path"]
            }),
            "call_count": len(api_callers.get(url, [])),
            "legacy_used_by": api_map.get(url, []),
        }
        apis.append(entry)

    # Sort by call count descending
    apis.sort(key=lambda x: -(x["call_count"] + len(x["legacy_used_by"])))

    return {
        "total": len(apis),
        "endpoints_with_definitions": len(api_endpoints),
        "endpoints_with_callers": len(api_callers),
        "apis": apis,
    }


def list_projects() -> dict:
    """
    List all indexed projects with statistics.
    """
    index = load_index()
    projects = index.get("projects", [])

    # Count symbols per project
    stats = {}
    for sid, sym in index.get("symbols", {}).items():
        project = sid.split(":")[0] if ":" in sid else "unknown"
        if project not in stats:
            stats[project] = {"files": set(), "symbols": 0, "by_type": {}}
        stats[project]["files"].add(sym.get("path", ""))
        stats[project]["symbols"] += 1
        sym_type = sym.get("type", "unknown")
        stats[project]["by_type"][sym_type] = stats[project]["by_type"].get(sym_type, 0) + 1

    result = []
    for proj in projects:
        s = stats.get(proj, {"files": set(), "symbols": 0, "by_type": {}})
        result.append({
            "project": proj,
            "files": len(s["files"]),
            "symbols": s["symbols"],
            "by_type": s["by_type"],
        })

    # Sort by symbol count
    result.sort(key=lambda x: -x["symbols"])

    return {
        "total_projects": len(projects),
        "total_symbols": len(index.get("symbols", {})),
        "projects": result,
    }


def get_description(path: str, project: str = None) -> dict:
    """
    Get the latest description for a file path.

    Searches all indexed projects' .flyto/descriptions.jsonl files.
    Returns the latest entry matching the path (bottom-up, last wins).
    """
    import hashlib

    # Determine which project roots to search
    index = load_index()
    project_roots = index.get("project_roots", {})

    if project and project in project_roots:
        roots_to_search = {project: project_roots[project]}
    else:
        roots_to_search = project_roots

    # Search each project's descriptions.jsonl
    for proj_name, root in roots_to_search.items():
        desc_path = Path(root) / ".flyto" / "descriptions.jsonl"
        if not desc_path.exists():
            continue

        latest = None
        for line in desc_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if entry.get("path") == path:
                    latest = entry
            except json.JSONDecodeError:
                pass

        if latest:
            # Check staleness
            full_path = Path(root) / path
            stale = False
            if full_path.exists() and latest.get("hash"):
                import hashlib
                current_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()[:16]
                if current_hash != latest["hash"]:
                    stale = True

            return {
                "project": proj_name,
                "path": path,
                "one_liner": latest.get("one_liner", ""),
                "source": latest.get("source", "unknown"),
                "updatedAt": latest.get("updatedAt", ""),
                "stale": stale,
                "category": latest.get("category", ""),
                "refs": latest.get("refs", 0),
                "hotspot": latest.get("hotspot", False),
            }

    return {"error": f"No description found for: {path}"}


def update_description(path: str, summary: str, project: str = None) -> dict:
    """
    Write or update a file description.

    Appends a new entry to the project's .flyto/descriptions.jsonl.
    Hash is computed from the current file content for staleness tracking.
    """
    import hashlib
    from datetime import datetime, timezone

    index = load_index()
    project_roots = index.get("project_roots", {})

    # Find the right project root
    target_root = None
    target_project = None

    if project and project in project_roots:
        target_root = project_roots[project]
        target_project = project
    else:
        # Try to find which project contains this path
        for proj_name, root in project_roots.items():
            if (Path(root) / path).exists():
                target_root = root
                target_project = proj_name
                break

    if not target_root:
        return {"error": f"Cannot find project containing: {path}"}

    desc_path = Path(target_root) / ".flyto" / "descriptions.jsonl"
    if not desc_path.parent.exists():
        return {"error": f"No .flyto/ found in {target_project}. Run 'flyto-index init' first."}

    # Compute file hash
    full_path = Path(target_root) / path
    file_hash = ""
    if full_path.exists():
        file_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()[:16]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "path": path,
        "hash": file_hash,
        "one_liner": summary,
        "source": "ai",
        "updatedAt": now,
    }
    line = json.dumps(entry, ensure_ascii=False)

    # Append
    with open(desc_path, "a", encoding="utf-8") as f:
        if desc_path.exists() and desc_path.stat().st_size > 0:
            with open(desc_path, "rb") as check:
                check.seek(-1, 2)
                if check.read(1) != b"\n":
                    f.write("\n")
        f.write(line + "\n")

    return {
        "ok": True,
        "project": target_project,
        "path": path,
        "one_liner": summary,
        "hash": file_hash,
        "updatedAt": now,
    }


def find_test_file(path: str) -> dict:
    """
    Find the corresponding test file for a source file, or vice versa.

    Uses naming convention (primary) and import analysis (fallback).
    """
    mapper = _get_test_mapper()

    try:
        from ..test_mapper import TestMapper
    except ImportError:
        from test_mapper import TestMapper
    if TestMapper._is_test_file(path):
        source = mapper.find_source(path)
        return {
            "query_path": path,
            "is_test_file": True,
            "source_file": source,
            "test_file": path,
        }
    else:
        test = mapper.find_test(path)
        return {
            "query_path": path,
            "is_test_file": False,
            "source_file": path,
            "test_file": test,
        }
