"""
Index-based data extraction — API, models, graph, complexity, reachability.
"""

import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from .constants import (
    BACKEND_EXTS, FRONTEND_EXTS, HTTP_METHODS,
    ENTRY_FILE_PATTERN, ENTRY_NAMES, API_CATEGORY_KEYS,
)

logger = logging.getLogger("flyto-indexer.profile")


def _classify_api_symbol(sym: dict) -> str:
    """Classify an API symbol into: api_definition, api_call_internal, api_call_external."""
    file_path = sym.get("path", "")
    ext = os.path.splitext(file_path)[1].lower()
    name = sym.get("name", "")
    meta = sym.get("metadata", {}) or {}

    url_text = name + " " + meta.get("path", "") + " " + meta.get("url", "")
    if "http://" in url_text or "https://" in url_text:
        return "api_call_external"

    if ext in BACKEND_EXTS:
        return "api_definition"
    if ext in FRONTEND_EXTS:
        return "api_call_internal"
    if meta.get("handler"):
        return "api_definition"

    return "api_definition"


def empty_extract_result() -> dict:
    return {
        "api_definitions": [],
        "api_calls_internal": [],
        "api_calls_external": [],
        "api_routes": [],
        "models": [],
        "symbol_counts": {},
        "entry_points": [],
        "module_graph": [],
        "module_graph_full": [],
        "module_graph_summary": {},
        "complexity_summary": {},
    }


def load_index_file(index_dir: Path) -> dict:
    """Load index.json (or .gz). Returns {} on missing or corrupt."""
    try:
        import gzip
        gz_path = index_dir / "index.json.gz"
        if gz_path.exists():
            with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                return json.load(f)
        json_path = index_dir / "index.json"
        if json_path.exists():
            return json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load index: %s", e)
    return {}


def _parse_api_entry(sym_or_route: dict, *, is_route: bool = False) -> dict:
    """Build a normalized API entry dict from a symbol or route record."""
    if is_route:
        return {
            "method": sym_or_route.get("method", "GET"),
            "path": sym_or_route.get("path", sym_or_route.get("url", "")),
            "handler": sym_or_route.get("handler", ""),
            "file": sym_or_route.get("file", sym_or_route.get("defined_in", "")),
        }
    meta = sym_or_route.get("metadata", {}) or {}
    method = meta.get("method", "GET") if meta else "GET"
    if not meta:
        summary = sym_or_route.get("summary", "")
        first = summary.split(" ", 1)[0]
        if first in HTTP_METHODS:
            method = first
    route_path = sym_or_route.get("name", "")
    for m_prefix in HTTP_METHODS:
        if route_path.startswith(m_prefix + " "):
            route_path = route_path[len(m_prefix) + 1:]
            break
    return {
        "method": method,
        "path": route_path,
        "handler": meta.get("handler", "") if meta else "",
        "file": sym_or_route.get("path", ""),
    }


def _collect_api_from_symbols(symbols: dict, result: dict) -> None:
    for _sid, sym in symbols.items():
        if sym.get("type") != "api":
            continue
        entry = _parse_api_entry(sym)
        category = _classify_api_symbol(sym)
        result[API_CATEGORY_KEYS[category]].append(entry)
        result["api_routes"].append(entry)


def _collect_api_from_dep_edges(index: dict, result: dict) -> None:
    raw_deps = index.get("dependencies", {})
    dep_values = raw_deps.values() if isinstance(raw_deps, dict) else raw_deps
    for dep_edge in dep_values:
        if not isinstance(dep_edge, dict):
            continue
        dep_type = dep_edge.get("type", dep_edge.get("dep_type", ""))
        if dep_type not in ("api_calls", "API_CALLS"):
            continue
        meta = dep_edge.get("metadata", {}) or {}
        url = meta.get("url", dep_edge.get("target", ""))
        method = meta.get("method", "GET")
        source = dep_edge.get("source", "")
        parts = source.split(":")
        source_file = parts[1] if len(parts) >= 2 else ""
        entry = {"method": method, "path": url, "handler": "", "file": source_file}
        if url.startswith("http://") or url.startswith("https://") or url.startswith("*/"):
            if entry not in result["api_calls_external"]:
                result["api_calls_external"].append(entry)
        elif url:
            if entry not in result["api_calls_internal"]:
                result["api_calls_internal"].append(entry)


def _collect_api_from_routes(index: dict, result: dict) -> None:
    for route in index.get("routes", []):
        if not isinstance(route, dict):
            continue
        entry = _parse_api_entry(route, is_route=True)
        if any(r["path"] == entry["path"] and r["method"] == entry["method"]
               for r in result["api_routes"]):
            continue
        result["api_definitions"].append(entry)
        result["api_routes"].append(entry)


def _is_model_symbol(sym: dict, sym_type: str, field_count: int) -> bool:
    name = sym.get("name", "")
    summary = sym.get("summary", "").lower()
    return (
        field_count > 0
        or "model" in summary or "schema" in summary or "entity" in summary
        or "dataclass" in summary or "struct" in name.lower()
        or sym_type in ("interface", "struct")
    )


def _collect_models(symbols: dict) -> list[dict]:
    models = []
    for _sid, sym in symbols.items():
        sym_type = sym.get("type", "")
        if sym_type not in ("class", "interface", "type", "struct"):
            continue
        meta = sym.get("metadata", {}) or {}
        field_count = len(meta.get("fields", []))
        if not _is_model_symbol(sym, sym_type, field_count):
            continue
        models.append({
            "name": sym.get("name", ""),
            "type": sym_type,
            "fields": field_count,
            "file": sym.get("path", ""),
            "line": sym.get("start_line", 0),
        })
    models.sort(key=lambda m: m["name"])
    return models


def _collect_entry_points(symbols: dict) -> list[str]:
    entry_files = set()
    for sym in symbols.values():
        path = sym.get("path", "")
        if path and ENTRY_FILE_PATTERN.search(path):
            entry_files.add(path)
        if sym.get("name", "").lower() in ENTRY_NAMES:
            if path:
                entry_files.add(path)
    return sorted(entry_files)


def _file_pair_from_dep(dep_info: dict, symbols: dict) -> Optional[tuple]:
    source_file = dep_info.get("source_path", "")
    target = dep_info.get("target", "")
    if not (source_file and target):
        return None
    target_file = ""
    for sid, sym in symbols.items():
        if target in sid and sym.get("path"):
            target_file = sym["path"]
            break
    if not target_file or source_file == target_file:
        return None
    return (source_file, target_file)


def _build_file_connections(symbols: dict, dependencies: dict, reverse_index: dict) -> Counter:
    connections: Counter = Counter()

    for _key, dep_info in dependencies.items():
        if not isinstance(dep_info, dict):
            continue
        pair = _file_pair_from_dep(dep_info, symbols)
        if pair is not None:
            connections[pair] += 1

    for sym_id, callers in reverse_index.items():
        if ":" not in sym_id:
            continue
        parts = sym_id.split(":")
        target_file = parts[1] if len(parts) >= 2 else ""
        if not target_file:
            continue
        for caller_id in callers:
            if ":" not in caller_id:
                continue
            caller_parts = caller_id.split(":")
            source_file = caller_parts[1] if len(caller_parts) >= 2 else ""
            if source_file and source_file != target_file:
                connections[(source_file, target_file)] += 1
    return connections


def _empty_graph_summary() -> dict:
    return {
        "total_connections": 0,
        "avg_refs_per_module": 0,
        "most_connected_file": "",
        "orphan_files": [],
        "orphan_count": 0,
    }


def _compute_graph_summary(symbols: dict, file_connections: Counter) -> dict:
    if not file_connections:
        return _empty_graph_summary()

    file_ref_counts: Counter = Counter()
    for (src, tgt), count in file_connections.items():
        file_ref_counts[src] += count
        file_ref_counts[tgt] += count

    all_indexed_files = {sym.get("path", "") for sym in symbols.values() if sym.get("path")}
    connected_files = set()
    for src, tgt in file_connections:
        connected_files.add(src)
        connected_files.add(tgt)
    orphan_files = sorted(all_indexed_files - connected_files)

    most_connected = file_ref_counts.most_common(1)[0][0] if file_ref_counts else ""
    avg_refs = sum(file_ref_counts.values()) / max(len(file_ref_counts), 1)

    return {
        "total_connections": len(file_connections),
        "avg_refs_per_module": round(avg_refs, 1),
        "most_connected_file": most_connected,
        "orphan_files": orphan_files,
        "orphan_count": len(orphan_files),
    }


def load_content_file(index_dir: Path) -> dict:
    """Load content.jsonl from an index directory."""
    content_map = {}
    content_file = index_dir / "content.jsonl"
    if content_file.exists():
        try:
            with open(content_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        content_map[record["id"]] = record["content"]
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.debug("Failed to load content from %s: %s", content_file, e)
    return content_map


def compute_complexity_summary(symbols: dict, index_dir: Path) -> dict:
    """Compute complexity summary from indexed symbols."""
    try:
        try:
            from ..analyzer.complexity import _line_threshold_for_file, _is_test_file
        except ImportError:
            from analyzer.complexity import _line_threshold_for_file, _is_test_file
    except ImportError:
        def _line_threshold_for_file(p):
            return 100 if any(p.endswith(e) for e in (".vue", ".tsx", ".jsx")) else 80
        def _is_test_file(p):
            lower = p.lower()
            return any(pat in lower for pat in ("test_", "_test.", ".test.", ".spec.", "/test/", "/tests/"))

    content_map = load_content_file(index_dir) if index_dir.exists() else {}

    total_functions = 0
    complex_functions = 0
    all_scores = []
    most_complex = []

    for sym_id, sym in symbols.items():
        sym_type = sym.get("type", "")
        if sym_type not in ("function", "method"):
            continue

        path = sym.get("path", "")
        if _is_test_file(path):
            continue

        total_functions += 1

        content = ""
        if isinstance(sym.get("content"), str) and sym["content"]:
            content = sym["content"]
        else:
            content = content_map.get(sym_id, "")

        if not content:
            all_scores.append(0)
            continue

        lines_list = content.split("\n")
        line_count = len(lines_list)
        params_list = sym.get("params", [])
        param_count = len(params_list) if isinstance(params_list, list) else 0

        is_python = path.endswith(".py")
        indent_unit = 4 if is_python else 2

        max_depth = 0
        branches = 0
        base_indent = 0
        for ln in lines_list:
            stripped = ln.strip()
            if stripped:
                base_indent = len(ln) - len(ln.lstrip())
                break

        for ln in lines_list:
            stripped = ln.strip()
            if not stripped:
                continue
            indent = len(ln) - len(ln.lstrip())
            depth = max(0, (indent - base_indent) // indent_unit)
            max_depth = max(max_depth, depth)
            if is_python:
                branch_kws = ("if ", "elif ", "for ", "while ", "try:", "except ", "with ")
            else:
                branch_kws = ("if ", "if(", "else if ", "for ", "for(", "while ", "while(", "switch ", "switch(", "try ", "try{", "catch ", "catch(")
            for kw in branch_kws:
                if stripped.startswith(kw):
                    branches += 1
                    break

        score = 0
        line_threshold = _line_threshold_for_file(path)
        if line_count > line_threshold:
            score += (line_count - line_threshold) // 10
        if max_depth > 3:
            score += (max_depth - 3) * 5
        if param_count > 5:
            score += (param_count - 5) * 2
        if branches > 10:
            score += (branches - 10)

        all_scores.append(score)

        if score >= 5:
            complex_functions += 1
            most_complex.append({
                "name": sym.get("name", ""),
                "path": path,
                "score": score,
                "line": sym.get("start_line", sym.get("line", 0)),
            })

    most_complex.sort(key=lambda x: x["score"], reverse=True)
    avg_complexity = round(sum(all_scores) / max(len(all_scores), 1), 2)

    return {
        "total_functions": total_functions,
        "complex_functions": complex_functions,
        "avg_complexity": avg_complexity,
        "most_complex": most_complex[:50],
    }


def compute_reachability(deps: dict, idx: dict) -> dict:
    """Compute basic reachability: which dependencies are actually imported."""
    dep_list = deps.get("dependencies", [])
    if isinstance(dep_list, dict):
        dep_list = dep_list.get("dependencies", [])
    if not dep_list:
        return {"total_deps": 0, "reachable": 0, "unreachable": 0, "unreachable_pct": 0, "details": []}

    all_imports = set()
    raw_deps = idx.get("_raw_dependencies", [])
    if isinstance(raw_deps, dict):
        raw_deps = raw_deps.get("dependencies", [])
    for dep_edge in raw_deps:
        if isinstance(dep_edge, dict):
            dep_type = dep_edge.get("dep_type", dep_edge.get("type", ""))
            if dep_type == "imports":
                target = dep_edge.get("target_id", dep_edge.get("target", ""))
                if target:
                    all_imports.add(target.lower())

    for conn in idx.get("module_graph_full", idx.get("module_graph", [])):
        if isinstance(conn, dict):
            target = conn.get("target_file", "")
            if target:
                all_imports.add(target.lower())

    health_inputs = idx.get("_health_inputs", {})
    index_dir = health_inputs.get("index_dir")
    if index_dir:
        project_root = index_dir.parent if hasattr(index_dir, 'parent') else None
        if project_root and project_root.exists():
            import_re = re.compile(r'"([^"]+)"')
            for go_file in project_root.rglob("*.go"):
                rel = str(go_file.relative_to(project_root))
                if any(skip in rel for skip in ("vendor/", "testdata/", "pkg/mod/")):
                    continue
                try:
                    src = go_file.read_text(encoding="utf-8", errors="replace")
                    in_import = False
                    for line in src.splitlines()[:200]:
                        stripped = line.strip()
                        if stripped.startswith("import ("):
                            in_import = True
                            continue
                        if in_import and stripped == ")":
                            in_import = False
                            continue
                        if in_import or stripped.startswith("import "):
                            for m in import_re.finditer(stripped):
                                all_imports.add(m.group(1).lower())
                except Exception:
                    pass

    import_packages = set()
    for imp in all_imports:
        imp_norm = imp.replace("\\", "/")
        parts = imp_norm.split("/")
        if parts:
            if parts[0].startswith("@") and len(parts) > 1:
                import_packages.add(f"{parts[0]}/{parts[1]}")
            else:
                import_packages.add(parts[0])
            import_packages.add(imp_norm)

    total = 0
    reachable = 0
    unreachable = 0
    details = []
    for dep in dep_list:
        if isinstance(dep, dict):
            name = dep.get("name", "")
        else:
            name = str(dep)
        if not name:
            continue
        total += 1
        name_lower = name.lower()
        is_reachable = any(name_lower in imp for imp in import_packages) or any(name_lower in imp for imp in all_imports)
        if is_reachable:
            reachable += 1
        else:
            unreachable += 1
            details.append({"package": name, "reachable": False})

    unreachable_pct = round(unreachable / max(total, 1) * 100) if total > 0 else 0

    return {
        "total_deps": total,
        "reachable": reachable,
        "unreachable": unreachable,
        "unreachable_pct": unreachable_pct,
        "unreachable_packages": [d["package"] for d in details],
    }


def extract_from_index(project_path: Path) -> dict:
    """Extract data from the flyto-indexer index if available."""
    result = empty_extract_result()

    index_dir = project_path / ".flyto-index"
    if not index_dir.exists():
        return result

    index = load_index_file(index_dir)
    if not index:
        return result

    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})
    reverse_index = index.get("reverse_index", {})

    result["symbol_counts"] = dict(Counter(
        sym.get("type", "unknown") for sym in symbols.values()
    ).most_common())

    _collect_api_from_symbols(symbols, result)
    _collect_api_from_dep_edges(index, result)
    _collect_api_from_routes(index, result)
    for key in ("api_definitions", "api_calls_internal", "api_calls_external", "api_routes"):
        result[key].sort(key=lambda r: (r["method"], r["path"]))

    result["models"] = _collect_models(symbols)
    result["entry_points"] = _collect_entry_points(symbols)

    file_connections = _build_file_connections(symbols, dependencies, reverse_index)
    all_connections = [
        {"source_file": pair[0], "target_file": pair[1], "import_count": count}
        for pair, count in file_connections.most_common()
    ]
    result["module_graph_full"] = all_connections
    result["module_graph"] = all_connections[:10]
    result["module_graph_summary"] = _compute_graph_summary(symbols, file_connections)

    result["complexity_summary"] = compute_complexity_summary(symbols, index_dir)
    result["_health_inputs"] = {
        "symbols": symbols,
        "reverse_index": reverse_index,
        "index_dir": index_dir,
        "complexity_summary": result["complexity_summary"],
    }
    result["_raw_dependencies"] = index.get("dependencies", [])
    result["_raw_symbols"] = symbols

    return result
