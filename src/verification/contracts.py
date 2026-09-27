"""Verification checks for API contracts, product loops, and dynamic validation.

These checks reason about static product boundaries and evidence. They do not own
the verification runner, baseline policy, or report formatting.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..engine import IndexEngine
from .support import _SKIP_WORKSPACE_DIRS, _load_index_json, _load_json_file, _read_ci_files


_CONTRACT_SOURCE_EXTENSIONS = {
    ".astro",
    ".go",
    ".js",
    ".jsx",
    ".py",
    ".svelte",
    ".ts",
    ".tsx",
    ".vue",
}

_FRONTEND_CONTRACT_SOURCE_EXTENSIONS = {
    ".astro",
    ".dart",
    ".js",
    ".jsx",
    ".svelte",
    ".ts",
    ".tsx",
    ".vue",
}

_FRONTEND_INDEX_SYMBOL_TYPES = {"component", "route", "store", "composable"}

_CONTRACT_SKIP_PARTS = {"__tests__", "__mocks__", "tests", "test", "fixtures"}

_CONTRACT_SKIP_SUFFIXES = (".test", ".spec")

_CONTRACT_SURFACE_TERMS = (
    "asset-map", "asset", "compliance", "ctem", "darkweb", "domain", "domains",
    "footprint", "pentest", "report", "reports", "scan", "scoring",
)

_SINGLE_PROJECT_ISLAND_TYPES = {
    "api", "route", "component", "composable", "store",
}

_SINGLE_PROJECT_PRODUCT_PARTS = {
    "app", "apps", "components", "compounds", "domains", "features", "hooks",
    "lib", "pages", "routes", "router", "routers", "services", "stores",
    "views",
}

_SINGLE_PROJECT_FEATURE_PARTS = {
    "app", "apps", "compounds", "domains", "features", "pages", "routes",
    "router", "routers", "stores", "views",
}

_SINGLE_PROJECT_ENTRY_PARTS = {
    "api", "app", "apps", "bin", "cli", "cmd", "commands", "pages", "routes",
    "router", "routers", "scripts", "server", "views",
}

_SINGLE_PROJECT_ENTRY_NAMES = {
    "__init__", "__main__", "app", "cli", "configure", "handler", "index",
    "main", "page", "route", "server", "setup",
}

_PRODUCT_LOOP_SURFACES: dict[str, tuple[str, ...]] = {
    "overview": (
        "dashboard", "overview", "posture", "workspace",
    ),
    "assets": (
        "asset", "asset-map", "inventory", "domain", "domains", "repo", "repos",
        "repository",
    ),
    "code_redteam": (
        "attack", "attack-path", "autofix", "pentest", "redteam", "red-team",
        "runner", "scan",
    ),
    "exposure": (
        "brand", "exposure", "external-report", "footprint", "issue", "issues",
        "sla",
    ),
    "runtime_cloud_identity": (
        "cloud", "container", "identity", "iam", "kubernetes", "runtime",
        "workload", "workloads",
    ),
    "darkweb": (
        "botshield", "breach", "credential", "darkweb", "dark-web",
        "data-leaks", "data_leaks", "ioc", "ioc-lookup", "ioc_lookup",
        "leak", "leaks", "malware", "malware-families",
        "malware_families", "ransomware", "ransomware-incidents",
        "ransomware_incidents", "sensor-map", "sensor_map", "threat",
        "threat-actors", "threat_actors", "threat-intel", "threat_intel",
    ),
    "scoring_compliance": (
        "audit", "compliance", "control", "evidence", "policy", "score",
        "scoring",
    ),
    "operations_admin": (
        "admin", "approval", "business-unit", "business-units", "fusion",
        "integration", "integrations", "organization", "settings",
    ),
}

_PRODUCT_LOOP_EVIDENCE_PARTS = {
    "docs", "evidence", "platform-loops", "recipes", "workflows",
}

_PRODUCT_LOOP_EVIDENCE_SUFFIXES = {
    ".json", ".md", ".yaml", ".yml",
}

_DYNAMIC_VALIDATION_GUARDS = {
    "audit_loops": "audit:loops",
    "audit_navbar_smoke": "audit:navbar-smoke",
    "branch_guard": "guard:branch",
    "compliance_evidence": "compliance:ci",
}

_RECIPE_ASSERTION_KINDS = {
    "event_invalidates_query",
    "query_key_present",
    "api_path_present",
    "event_routed",
    "route_renders_without_error",
    "dom_contains",
    "http_status",
    "command_succeeds",
}

_OPENAPI_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"}

_OPENAPI_SKIP_DIRS = {".git", ".flyto-index", "node_modules", "dist", "dist-next", "build", "coverage"}

def _check_single_project_islands(engine: IndexEngine, add_check) -> None:
    index = engine.index
    symbols = index.symbols or {}
    dependencies = index.dependencies or {}
    reverse_index = index.reverse_index or {}
    inbound: dict[str, set[str]] = {}
    outbound: dict[str, set[str]] = {}
    path_outbound: dict[str, set[str]] = {}
    project_root = getattr(engine, "project_root", None)
    source_refs = _collect_source_name_references(project_root) if isinstance(project_root, Path) else {}

    for target, callers in reverse_index.items():
        for caller in callers:
            inbound.setdefault(str(target), set()).add(str(caller))

    for dep in dependencies.values():
        source = _dep_value(dep, "source_id", "source")
        target = _dep_value(dep, "target_id", "target")
        metadata = _dep_metadata(dep)
        resolved = str(metadata.get("resolved_target") or "")
        targets = [value for value in (target, resolved) if value]
        for dep_target in targets:
            outbound.setdefault(source, set()).add(dep_target)
            inbound.setdefault(dep_target, set()).add(source)
        source_path = _symbol_path(symbols.get(source))
        if source_path:
            for dep_target in targets:
                path_outbound.setdefault(source_path, set()).add(dep_target)

    islands: list[dict[str, Any]] = []
    for sid, symbol in symbols.items():
        path = _symbol_path(symbol)
        sym_type = _symbol_type(symbol)
        name = _symbol_name(symbol)
        if not _is_single_project_candidate(path, sym_type, name):
            continue
        is_entry = _is_single_project_entry(path, sym_type, name)
        inbound_count = len(inbound.get(sid, set()))
        outbound_count = len(outbound.get(sid, set()) | path_outbound.get(path, set()))
        ref_count = _symbol_ref_count(symbol)
        if ref_count:
            inbound_count = max(inbound_count, ref_count)
        source_ref_count = len(source_refs.get(name, set()) - {path})
        if source_ref_count:
            inbound_count = max(inbound_count, source_ref_count)

        reason = ""
        if not is_entry and inbound_count == 0 and outbound_count == 0:
            reason = "no_inbound_or_outbound_edges"
        elif not is_entry and inbound_count == 0:
            reason = "no_inbound_edges"
        elif is_entry and sym_type in {"component", "route"} and outbound_count == 0 and _has_product_surface_signal(path, name):
            reason = "entry_without_data_or_call_edges"

        if reason:
            islands.append({
                "symbol": sid,
                "type": sym_type,
                "name": name,
                "path": path,
                "reason": reason,
                "inbound": inbound_count,
                "outbound": outbound_count,
            })

    api_defs, api_calls = _extract_single_project_api_contract(symbols, dependencies)
    if isinstance(project_root, Path):
        api_defs.extend(_extract_openapi_api_contracts(project_root))
        api_defs = _dedupe_contract_items(api_defs)
    unmatched_api_calls = _match_single_project_api_calls(api_defs, api_calls)
    status = "pass"
    summary = "No high-confidence single-project islands found"
    if islands or unmatched_api_calls:
        status = "warn"
        summary = "Single-project island signals found"

    add_check(
        "single_project_islands",
        status,
        summary,
        metrics={
            "candidate_symbols": sum(
                1 for symbol in symbols.values()
                if _is_single_project_candidate(
                    _symbol_path(symbol), _symbol_type(symbol), _symbol_name(symbol)
                )
            ),
            "island_count": len(islands),
            "island_samples": islands[:10],
            "api_definitions": len(api_defs),
            "api_calls": len(api_calls),
            "unmatched_api_calls": len(unmatched_api_calls),
            "unmatched_api_call_samples": _contract_samples(unmatched_api_calls),
        },
    )

def _collect_source_name_references(root: Path) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in _iter_contract_source_files(root):
        if path.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Track PascalCase components and conventional useX composables.
        for name in set(
            re.findall(r"\b(?:[A-Z][A-Za-z0-9_]{2,}|use[A-Z][A-Za-z0-9_]{2,})\b", text)
        ):
            refs.setdefault(name, set()).add(rel)
    return refs

def _extract_single_project_api_contract(symbols: dict[str, Any], dependencies: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    api_defs: list[dict[str, Any]] = []
    for sid, symbol in symbols.items():
        if _symbol_type(symbol) != "api":
            continue
        metadata = _symbol_metadata(symbol)
        raw_name = _symbol_name(symbol)
        method = str(metadata.get("method") or "").upper()
        route_path = str(metadata.get("path") or metadata.get("url") or "")
        if not route_path and " " in raw_name:
            method_part, route_path = raw_name.split(" ", 1)
            method = method or method_part.upper()
        if not _is_product_api_path(route_path):
            continue
        api_defs.append({
            "method": method,
            "path": route_path,
            "raw": raw_name,
            "normalized": _normalize_api_path(route_path),
            "source": _symbol_path(symbol),
            "symbol": sid,
        })

    api_calls: list[dict[str, Any]] = []
    for dep in dependencies.values():
        if _dep_type(dep) != "api_calls":
            continue
        metadata = _dep_metadata(dep)
        raw = str(metadata.get("url") or _dep_value(dep, "target_id", "target") or "")
        if not _is_product_api_path(raw):
            continue
        normalized = _normalize_api_path(raw)
        if normalized == "/api":
            continue
        source = _dep_value(dep, "source_id", "source")
        api_calls.append({
            "method": str(metadata.get("method") or "").upper(),
            "path": _strip_url_to_api_path(raw),
            "raw": raw,
            "normalized": normalized,
            "source": _symbol_id_path(source),
        })

    return _dedupe_contract_items(api_defs), _dedupe_contract_items(api_calls)

def _extract_openapi_api_contracts(root: Path) -> list[dict[str, Any]]:
    api_defs: list[dict[str, Any]] = []
    for path in _iter_openapi_contract_files(root):
        if path.suffix.lower() == ".json":
            api_defs.extend(_extract_openapi_json_api_contracts(path))
        else:
            api_defs.extend(_extract_openapi_yaml_api_contracts(path))
    return _dedupe_contract_items(api_defs)

def _iter_openapi_contract_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = set(path.relative_to(root).parts)
        if rel_parts & _OPENAPI_SKIP_DIRS:
            continue
        suffix = path.suffix.lower()
        if suffix not in {".yaml", ".yml", ".json"}:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/").lower()
        if "openapi" in rel:
            files.append(path)
    return files

def _extract_openapi_json_api_contracts(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    paths = data.get("paths") if isinstance(data, dict) else None
    if not isinstance(paths, dict):
        return []

    api_defs: list[dict[str, Any]] = []
    for route_path, route in paths.items():
        if not isinstance(route_path, str) or not _is_product_api_path(route_path):
            continue
        if not isinstance(route, dict):
            continue
        for method in route:
            method_name = str(method).upper()
            if method_name not in _OPENAPI_METHODS:
                continue
            api_defs.append(_openapi_contract_item(method_name, route_path, path))
    return api_defs

def _extract_openapi_yaml_api_contracts(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    api_defs: list[dict[str, Any]] = []
    in_paths = False
    paths_indent = 0
    current_path = ""
    current_path_indent = 0

    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))

        if not in_paths:
            if stripped == "paths:":
                in_paths = True
                paths_indent = indent
            continue
        if indent <= paths_indent:
            break

        path_match = re.match(r'''^[`"']?(/[^`"']*?)[`"']?:\s*(?:#.*)?$''', stripped)
        if path_match:
            current_path = path_match.group(1)
            current_path_indent = indent
            continue
        if not current_path or not _is_product_api_path(current_path) or indent <= current_path_indent:
            continue

        method = stripped.split(":", 1)[0].upper()
        if method in _OPENAPI_METHODS:
            api_defs.append(_openapi_contract_item(method, current_path, path))

    return api_defs

def _openapi_contract_item(method: str, route_path: str, path: Path) -> dict[str, Any]:
    return {
        "method": method,
        "path": route_path,
        "raw": f"{method} {route_path}",
        "normalized": _normalize_api_path(route_path),
        "source": str(path),
        "symbol": f"openapi:{path}:{method} {route_path}",
    }

def _match_single_project_api_calls(api_defs: list[dict[str, Any]], api_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not api_defs or not api_calls:
        return []
    api_keys = {
        (route.get("method", ""), route.get("normalized", ""))
        for route in api_defs
    }
    api_paths = {route.get("normalized", "") for route in api_defs}
    unmatched = []
    for call in api_calls:
        method = call.get("method", "")
        normalized = call.get("normalized", "")
        if not _is_product_api_path(str(call.get("path") or normalized)):
            continue
        if (method and (method, normalized) in api_keys) or normalized in api_paths:
            continue
        unmatched.append(call)
    return unmatched

def _check_cross_project_contract(projects: list[Path], checks: list[dict[str, Any]]) -> None:
    frontends = [project for project in projects if _looks_like_frontend(project)]
    backends = [project for project in projects if _looks_like_backend(project)]
    if not frontends or not backends:
        checks.append({
            "name": "cross_project_contract",
            "status": "pass",
            "summary": "No frontend/backend project pair in this workspace verification",
            "metrics": {
                "frontends": [project.name for project in frontends],
                "backends": [project.name for project in backends],
            },
        })
        return

    frontend_calls: list[dict[str, Any]] = []
    frontend_terms: dict[str, int] = {}
    for project in frontends:
        calls, terms = _extract_frontend_contract_signals(project)
        frontend_calls.extend(calls)
        _merge_term_counts(frontend_terms, terms)

    backend_routes: list[dict[str, Any]] = []
    backend_terms: dict[str, int] = {}
    for project in backends:
        routes, terms = _extract_backend_contract_signals(project)
        backend_routes.extend(routes)
        _merge_term_counts(backend_terms, terms)

    backend_keys = {
        (route.get("method", ""), route.get("normalized", ""))
        for route in backend_routes
    }
    backend_path_keys = {route.get("normalized", "") for route in backend_routes}
    unmatched = []
    matched = 0
    for call in frontend_calls:
        method = call.get("method", "")
        normalized = call.get("normalized", "")
        if (method and (method, normalized) in backend_keys) or (not method and normalized in backend_path_keys):
            matched += 1
            continue
        if method and normalized in backend_path_keys:
            matched += 1
            continue
        unmatched.append(call)

    shared_terms = sorted(set(frontend_terms) & set(backend_terms))
    frontend_only_terms = sorted(set(frontend_terms) - set(backend_terms))
    backend_only_terms = sorted(set(backend_terms) - set(frontend_terms))
    status = "pass"
    summary = "Frontend API calls are backed by backend routes"
    if not frontend_calls or not backend_routes:
        status = "warn"
        summary = "Frontend/backend contract has no extractable endpoints"
    elif unmatched:
        status = "warn"
        summary = "Some frontend API calls do not match indexed backend routes"
    elif not shared_terms:
        status = "warn"
        summary = "Frontend/backend endpoints match, but no shared product surface terms were found"

    checks.append({
        "name": "cross_project_contract",
        "status": status,
        "summary": summary,
        "metrics": {
            "frontends": [project.name for project in frontends],
            "backends": [project.name for project in backends],
            "frontend_calls": len(frontend_calls),
            "backend_routes": len(backend_routes),
            "matched_calls": matched,
            "unmatched_calls": len(unmatched),
            "unmatched_samples": _contract_samples(unmatched),
            "shared_terms": shared_terms,
            "frontend_only_terms": frontend_only_terms,
            "backend_only_terms": backend_only_terms,
        },
    })

def _check_product_loop_closure(projects: list[Path], checks: list[dict[str, Any]]) -> None:
    loops = _empty_product_loops()
    project_roles: dict[str, list[str]] = {}
    for project in projects:
        roles = []
        if _looks_like_frontend(project):
            roles.append("frontend")
        if _looks_like_backend(project):
            roles.append("backend")
        project_roles[project.name] = roles or ["project"]
        if roles:
            _merge_product_loops(loops, _collect_project_product_loop_signals(project, roles))

    active = {
        surface: metrics for surface, metrics in loops.items()
        if _product_loop_is_active(metrics)
    }
    unmatched_by_surface = _workspace_unmatched_api_calls_by_surface(projects)
    gaps: list[dict[str, Any]] = []
    for surface, metrics in active.items():
        reasons = []
        if metrics["ui"] and not metrics["api_calls"] and not metrics["backend_routes"]:
            reasons.append("ui_without_data_contract")
        if metrics["api_calls"] and not metrics["backend_routes"]:
            reasons.append("frontend_calls_without_backend_route")
        if unmatched_by_surface.get(surface):
            reasons.append("unmatched_frontend_api_calls")
        if metrics["backend_routes"] and not metrics["ui"]:
            reasons.append("backend_route_without_ui_surface")
        if metrics["ui"] and not metrics["tests"]:
            reasons.append("ui_without_surface_tests")
        if (metrics["ui"] or metrics["backend_routes"] or metrics["api_calls"]) and not (metrics["evidence"] or metrics["workflows"]):
            reasons.append("missing_evidence_or_recipe")
        if reasons:
            gaps.append({
                "surface": surface,
                "reasons": reasons,
                "counts": _product_loop_counts(metrics),
                "unmatched_api_call_samples": _contract_samples(unmatched_by_surface.get(surface, []), limit=6),
                "samples": metrics["samples"][:6],
            })

    status = "pass"
    summary = "Product surfaces have closed-loop signals"
    if gaps:
        status = "warn"
        # Naming the surface and its reason is the whole value of this check.
        # A fixed sentence left the reader to open the metrics JSON, and the
        # report truncates that before `gaps` -- so the finding was reachable
        # only through --json.
        shown = ", ".join(
            f"{gap['surface']} ({'/'.join(gap['reasons'])})" for gap in gaps[:3]
        )
        remainder = len(gaps) - 3
        if remainder > 0:
            shown += f", and {remainder} more"
        summary = f"Product surfaces have missing loop signals: {shown}"

    checks.append({
        "name": "product_loop_closure",
        "status": status,
        "summary": summary,
        "metrics": {
            "projects": project_roles,
            "surfaces": {surface: _product_loop_counts(metrics) for surface, metrics in active.items()},
            "active_surfaces": sorted(active),
            "unmatched_api_calls_by_surface": {
                surface: len(calls)
                for surface, calls in sorted(unmatched_by_surface.items())
            },
            "gap_count": len(gaps),
            "gaps": gaps[:12],
        },
    })

def _collect_project_product_loop_signals(project: Path, roles: list[str]) -> dict[str, dict[str, Any]]:
    loops = _empty_product_loops()
    index = _load_index_json(project)
    symbols = index.get("symbols") or {}
    dependencies = index.get("dependencies") or {}
    deps_iter = dependencies.values() if isinstance(dependencies, dict) else dependencies

    for sid, symbol in symbols.items():
        path = _symbol_path(symbol)
        name = _symbol_name(symbol)
        sym_type = _symbol_type(symbol)
        text = f"{path}\n{name}\n{json.dumps(_symbol_metadata(symbol), ensure_ascii=False, sort_keys=True)}"
        for surface in _classify_product_surfaces(text):
            if sym_type in {"component", "route", "store", "composable"} and _looks_like_frontend(project):
                _add_product_loop_signal(loops, surface, "ui", project.name, path, name)
            elif sym_type == "api":
                _add_product_loop_signal(loops, surface, "backend_routes", project.name, path, name)

    for dep in deps_iter:
        if _dep_type(dep) != "api_calls":
            continue
        metadata = _dep_metadata(dep)
        raw = str(metadata.get("url") or _dep_value(dep, "target_id", "target") or "")
        source = _dep_value(dep, "source_id", "source")
        text = f"{raw}\n{source}"
        for surface in _classify_product_surfaces(text):
            _add_product_loop_signal(
                loops,
                surface,
                "api_calls",
                project.name,
                _symbol_id_path(source),
                raw,
            )

    for path in _iter_product_loop_files(project):
        rel = str(path.relative_to(project))
        text = rel
        try:
            if path.suffix in _PRODUCT_LOOP_EVIDENCE_SUFFIXES:
                text += "\n" + path.read_text(encoding="utf-8", errors="ignore")[:20000]
        except OSError:
            pass
        surfaces = _classify_product_surfaces(text)
        if not surfaces:
            continue
        kind = _product_loop_file_kind(rel, path)
        for surface in surfaces:
            _add_product_loop_signal(loops, surface, kind, project.name, rel, Path(rel).name)

    _ci_files, ci_text = _read_ci_files(project)
    for surface in _classify_product_surfaces(ci_text):
        _add_product_loop_signal(loops, surface, "ci", project.name, "ci", surface)

    return loops

def _workspace_unmatched_api_calls_by_surface(projects: list[Path]) -> dict[str, list[dict[str, Any]]]:
    frontends = [project for project in projects if _looks_like_frontend(project)]
    backends = [project for project in projects if _looks_like_backend(project)]
    if not frontends or not backends:
        return {}

    frontend_calls: list[dict[str, Any]] = []
    for project in frontends:
        calls, _terms = _extract_frontend_contract_signals(project)
        frontend_calls.extend(calls)
    backend_routes: list[dict[str, Any]] = []
    for project in backends:
        routes, _terms = _extract_backend_contract_signals(project)
        backend_routes.extend(routes)

    backend_keys = {
        (route.get("method", ""), route.get("normalized", ""))
        for route in backend_routes
    }
    backend_path_keys = {route.get("normalized", "") for route in backend_routes}
    unmatched_by_surface: dict[str, list[dict[str, Any]]] = {}
    for call in frontend_calls:
        method = call.get("method", "")
        normalized = call.get("normalized", "")
        if (method and (method, normalized) in backend_keys) or normalized in backend_path_keys:
            continue
        surfaces = _classify_product_surfaces(
            f"{call.get('path', '')}\n{call.get('raw', '')}\n{call.get('source', '')}"
        )
        for surface in surfaces:
            unmatched_by_surface.setdefault(surface, []).append(call)
    return unmatched_by_surface

def _iter_product_loop_files(project: Path):
    for path in project.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(project))
        parts = set(Path(rel).parts)
        if parts & _SKIP_WORKSPACE_DIRS:
            continue
        suffix = path.suffix.lower()
        if parts & {"docs", "evidence", "recipes", "workflows", ".github"}:
            yield path
            continue
        if any(Path(rel).stem.endswith(test_suffix) for test_suffix in _CONTRACT_SKIP_SUFFIXES):
            yield path
            continue
        if suffix in _PRODUCT_LOOP_EVIDENCE_SUFFIXES and parts & _PRODUCT_LOOP_EVIDENCE_PARTS:
            yield path

def _product_loop_file_kind(rel: str, path: Path) -> str:
    parts = set(Path(rel).parts)
    stem = Path(rel).stem
    if ".github" in parts:
        return "ci"
    if "recipes" in parts or "workflows" in parts or path.suffix.lower() in {".yaml", ".yml"}:
        return "workflows"
    if any(stem.endswith(test_suffix) for test_suffix in _CONTRACT_SKIP_SUFFIXES) or parts & {"__tests__", "tests", "test"}:
        return "tests"
    if parts & _PRODUCT_LOOP_EVIDENCE_PARTS:
        return "evidence"
    return "evidence"

def _empty_product_loops() -> dict[str, dict[str, Any]]:
    return {
        surface: {
            "ui": set(),
            "api_calls": set(),
            "backend_routes": set(),
            "tests": set(),
            "evidence": set(),
            "workflows": set(),
            "ci": set(),
            "projects": set(),
            "samples": [],
        }
        for surface in _PRODUCT_LOOP_SURFACES
    }

def _merge_product_loops(target: dict[str, dict[str, Any]], incoming: dict[str, dict[str, Any]]) -> None:
    for surface, metrics in incoming.items():
        if surface not in target:
            target[surface] = metrics
            continue
        for key, value in metrics.items():
            if key == "samples":
                existing = target[surface]["samples"]
                for sample in value:
                    if sample not in existing:
                        existing.append(sample)
                continue
            target[surface].setdefault(key, set()).update(value)

def _add_product_loop_signal(
    loops: dict[str, dict[str, Any]],
    surface: str,
    kind: str,
    project: str,
    source: str,
    name: str,
) -> None:
    if surface not in loops:
        return
    loops[surface].setdefault(kind, set()).add(f"{project}:{source}:{name}")
    loops[surface]["projects"].add(project)
    sample = {"project": project, "kind": kind, "source": source, "name": name}
    if len(loops[surface]["samples"]) < 20 and sample not in loops[surface]["samples"]:
        loops[surface]["samples"].append(sample)

def _product_loop_is_active(metrics: dict[str, Any]) -> bool:
    return any(metrics[key] for key in ("ui", "api_calls", "backend_routes", "tests", "evidence", "workflows"))

def _product_loop_counts(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "ui": len(metrics["ui"]),
        "api_calls": len(metrics["api_calls"]),
        "backend_routes": len(metrics["backend_routes"]),
        "tests": len(metrics["tests"]),
        "evidence": len(metrics["evidence"]),
        "workflows": len(metrics["workflows"]),
        "ci": len(metrics["ci"]),
        "projects": sorted(metrics["projects"]),
    }

def _classify_product_surfaces(text: str) -> list[str]:
    lowered = text.lower()
    tokens = set(re.split(r"[^a-z0-9]+", lowered))
    matches = []
    for surface, terms in _PRODUCT_LOOP_SURFACES.items():
        if any(
            (term in lowered if "-" in term or "_" in term else term in tokens)
            for term in terms
        ):
            matches.append(surface)
    return matches

def _check_dynamic_validation_plan(projects: list[Path], checks: list[dict[str, Any]]) -> None:
    frontend_projects = [project for project in projects if _has_dynamic_validation_contract(project)]
    if not frontend_projects:
        checks.append({
            "name": "dynamic_validation_plan",
            "status": "pass",
            "summary": "No frontend project requiring browser/YAML validation plan",
            "metrics": {"frontend_projects": []},
        })
        return

    project_reports = []
    gaps: list[dict[str, Any]] = []
    for project in frontend_projects:
        report = _collect_dynamic_validation_project(project)
        project_reports.append(report)
        missing_registries = [
            name for name, present in report["registries"].items()
            if not present
        ]
        if missing_registries:
            gaps.append({
                "project": project.name,
                "surface": "workspace",
                "reasons": ["missing_dynamic_validation_registries"],
                "missing_registries": missing_registries,
            })
        for surface, surface_report in report["surfaces"].items():
            reasons = []
            if surface_report["registry_modules"] and not surface_report["browser_routes"]:
                reasons.append("missing_navbar_browser_smoke_route")
            if surface_report["registry_recipes"] and surface_report["missing_recipe_files"]:
                reasons.append("missing_recipe_files")
            if surface_report["registry_recipes"] and not surface_report["browser_recipe_files"]:
                reasons.append("recipes_without_browser_or_flyto_core_steps")
            if surface_report["prose_assertion_recipes"]:
                reasons.append("recipes_without_machine_checkable_assertions")
            if surface_report["invalid_routes"]:
                reasons.append("invalid_smoke_route_contract")
            if reasons:
                gaps.append({
                    "project": project.name,
                    "surface": surface,
                    "reasons": reasons,
                    "counts": {
                        "registry_modules": len(surface_report["registry_modules"]),
                        "browser_routes": len(surface_report["browser_routes"]),
                        "registry_recipes": len(surface_report["registry_recipes"]),
                        "existing_recipe_files": len(surface_report["existing_recipe_files"]),
                        "browser_recipe_files": len(surface_report["browser_recipe_files"]),
                    },
                    "missing_recipe_files": surface_report["missing_recipe_files"][:8],
                    "prose_assertion_recipes": surface_report["prose_assertion_recipes"][:8],
                    "invalid_routes": surface_report["invalid_routes"][:8],
                })
        missing_guards = [
            name for name, present in report["guards"].items()
            if not present
        ]
        if missing_guards:
            gaps.append({
                "project": project.name,
                "surface": "workspace",
                "reasons": ["missing_dynamic_validation_ci_guards"],
                "missing_guards": missing_guards,
            })

    status = "pass"
    summary = "Browser smoke and YAML recipe validation plans are closed"
    if gaps:
        status = "warn"
        summary = "Dynamic validation plan has missing smoke/recipe/CI coverage"

    checks.append({
        "name": "dynamic_validation_plan",
        "status": status,
        "summary": summary,
        "metrics": {
            "frontend_projects": [project.name for project in frontend_projects],
            "projects": project_reports,
            "gap_count": len(gaps),
            "gaps": gaps[:12],
        },
    })

def _collect_dynamic_validation_project(project: Path) -> dict[str, Any]:
    navbar_registry = _load_json_file(project / "docs" / "platform-loops" / "navbar-smoke-registry.json")
    loop_registry = _load_json_file(project / "docs" / "platform-loops" / "platform-loop-registry.json")
    surfaces = _empty_dynamic_surface_report()

    routes = navbar_registry.get("routes", [])
    if not isinstance(routes, list):
        routes = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        surface = str(route.get("surface") or "")
        if surface not in surfaces:
            continue
        route_id = str(route.get("id") or route.get("moduleId") or route.get("pathTemplate") or "")
        surfaces[surface]["browser_routes"].append(route_id)
        invalid = _invalid_navbar_smoke_route(route)
        if invalid:
            surfaces[surface]["invalid_routes"].append({"route": route_id, "missing": invalid})

    surface_entries = loop_registry.get("surfaces", [])
    if not isinstance(surface_entries, list):
        surface_entries = []
    for surface_entry in surface_entries:
        if not isinstance(surface_entry, dict):
            continue
        surface = str(surface_entry.get("id") or "")
        if surface not in surfaces:
            continue
        modules = _string_list(surface_entry.get("modules"))
        recipes = _string_list(surface_entry.get("recipes"))
        surfaces[surface]["registry_modules"].extend(modules)
        surfaces[surface]["registry_recipes"].extend(recipes)
        for recipe in recipes:
            recipe_path = project / "docs" / "platform-loops" / "recipes" / recipe
            if not recipe_path.is_file():
                surfaces[surface]["missing_recipe_files"].append(recipe)
                continue
            surfaces[surface]["existing_recipe_files"].append(recipe)
            text = recipe_path.read_text(encoding="utf-8", errors="ignore")
            if _recipe_has_dynamic_steps(text):
                surfaces[surface]["browser_recipe_files"].append(recipe)
            if not _recipe_assertions_machine_checkable(text):
                surfaces[surface]["prose_assertion_recipes"].append(recipe)

    _ci_files, ci_text = _read_ci_files(project)
    package_text = ""
    package_json = project / "package.json"
    if package_json.is_file():
        package_text = package_json.read_text(encoding="utf-8", errors="ignore")
    guard_text = f"{ci_text}\n{package_text}"
    guards = {
        name: token in guard_text
        for name, token in _DYNAMIC_VALIDATION_GUARDS.items()
    }

    return {
        "project": project.name,
        "registries": {
            "navbar_smoke": bool(navbar_registry),
            "platform_loops": bool(loop_registry),
        },
        "guards": guards,
        "surfaces": surfaces,
    }

def _empty_dynamic_surface_report() -> dict[str, dict[str, Any]]:
    return {
        surface: {
            "registry_modules": [],
            "browser_routes": [],
            "registry_recipes": [],
            "existing_recipe_files": [],
            "browser_recipe_files": [],
            "missing_recipe_files": [],
            "prose_assertion_recipes": [],
            "invalid_routes": [],
        }
        for surface in _PRODUCT_LOOP_SURFACES
    }

def _invalid_navbar_smoke_route(route: dict[str, Any]) -> list[str]:
    missing = []
    if not route.get("pathTemplate"):
        missing.append("pathTemplate")
    if route.get("mode") not in {"both", "engineer", "exec"}:
        missing.append("mode")
    if route.get("scrollPolicy") not in {"host", "self", "page", "document"}:
        missing.append("scrollPolicy")
    expected = route.get("expectedText")
    if not isinstance(expected, list) or not [item for item in expected if str(item).strip()]:
        missing.append("expectedText")
    return missing

def _recipe_has_dynamic_steps(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in (
        "flyto-core",
        "module: browser.",
        "browser.goto",
        "browser.click",
        "browser.extract",
        "browser.evaluate",
        "browser.wait",
    ))

def _recipe_assertions_machine_checkable(text: str) -> bool:
    """True only when a recipe carries a non-empty, structured assertions block.

    A machine-checkable assertion is a block-sequence item that opens a mapping
    keyed on a known ``assert:`` kind (e.g.
    ``- assert: event_invalidates_query``). Prose assertions
    (``- pipeline.progress invalidates ...``), unknown kinds, or a missing block
    fail this check, so a recipe of "browser.goto + browser.extract + paragraph"
    can no longer count as a closed validation plan.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("#") or not line.strip():
            continue
        if line.lstrip() == "assertions:" and not line.startswith(" "):
            start = i
            break
    if start is None:
        return False

    items: list[str] = []
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break  # dedent to the next top-level key ends the block
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    if not items:
        return False
    for item in items:
        if not item.startswith("assert:"):
            return False
        kind = item.split(":", 1)[1].strip()
        if kind not in _RECIPE_ASSERTION_KINDS:
            return False
    return True

def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]

def _looks_like_frontend(project: Path) -> bool:
    if _project_has_index_symbol_types(project, _FRONTEND_INDEX_SYMBOL_TYPES):
        return True
    calls, _terms = _extract_frontend_contract_signals(project)
    if calls and ((project / "package.json").is_file() or (project / "pubspec.yaml").is_file()):
        return True
    return (project / "pubspec.yaml").is_file() and (project / "lib").is_dir()

def _looks_like_backend(project: Path) -> bool:
    return _project_has_index_symbol_types(project, {"api"})

def _project_has_index_symbol_types(project: Path, symbol_types: set[str]) -> bool:
    index = _load_index_json(project)
    symbols = index.get("symbols") or {}
    values = symbols.values() if isinstance(symbols, dict) else symbols
    return any(
        _symbol_type(symbol) in symbol_types
        and not _is_contract_skipped_path(_symbol_path(symbol))
        for symbol in values
    )

def _has_dynamic_validation_contract(project: Path) -> bool:
    registry_root = project / "docs" / "platform-loops"
    return any(
        (registry_root / name).is_file()
        for name in ("navbar-smoke-registry.json", "platform-loop-registry.json")
    )

def _extract_frontend_contract_signals(project: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    calls: list[dict[str, Any]] = []
    terms: dict[str, int] = {}
    roots = [path for path in (project / "src-next", project / "src", project / "lib") if path.exists()]
    for root in roots:
        for path in _iter_contract_source_files(root, extensions=_FRONTEND_CONTRACT_SOURCE_EXTENSIONS):
            rel = str(path.relative_to(project))
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            _count_surface_terms(rel + "\n" + text, terms)
            for call in _extract_api_calls_from_text(text):
                call["project"] = project.name
                call["source"] = rel
                calls.append(call)
    return _dedupe_contract_items(calls), terms

def _extract_backend_contract_signals(project: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    routes = _extract_backend_routes_from_index(project)
    terms: dict[str, int] = {}
    for route in routes:
        _count_surface_terms(f"{route.get('path', '')}\n{route.get('raw', '')}", terms)
    for root_name in ("api", "internal"):
        root = project / root_name
        if not root.exists():
            continue
        for path in _iter_contract_source_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            _count_surface_terms(str(path.relative_to(project)) + "\n" + text, terms)
    return routes, terms

def _extract_backend_routes_from_index(project: Path) -> list[dict[str, Any]]:
    index = _load_index_json(project)
    routes: list[dict[str, Any]] = []
    for symbol in (index.get("symbols") or {}).values():
        if not isinstance(symbol, dict) or symbol.get("type") != "api":
            continue
        metadata = symbol.get("metadata") if isinstance(symbol.get("metadata"), dict) else {}
        method = str(metadata.get("method") or "").upper()
        route_path = str(metadata.get("path") or "")
        raw = str(symbol.get("name") or "")
        if not route_path and " " in raw:
            method_part, route_path = raw.split(" ", 1)
            method = method or method_part.upper()
        if not _is_product_api_path(route_path):
            continue
        routes.append({
            "project": project.name,
            "method": method,
            "path": route_path,
            "raw": raw,
            "normalized": _normalize_api_path(route_path),
            "source": symbol.get("path", ""),
        })
    return _dedupe_contract_items(routes)

def _extract_api_calls_from_text(text: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    string_pattern = re.compile(r"(?P<quote>[`'\"])(?P<value>[^`'\"\n\r]*/api/v1[^`'\"\n\r]*)(?P=quote)")
    comment_pattern = re.compile(r"\b(?P<method>GET|POST|PUT|PATCH|DELETE)\s+(?P<path>/api/v1/[^\s,;`'\")]+)")
    for match in string_pattern.finditer(text):
        raw = match.group("value")
        if _strip_url_to_api_path(raw).rstrip("/") == "/api/v1":
            continue
        method = _infer_http_method(text[max(0, match.start() - 100):match.start()])
        calls.append({
            "method": method,
            "path": _strip_url_to_api_path(raw),
            "raw": raw,
            "normalized": _normalize_api_path(raw),
        })
    for match in comment_pattern.finditer(text):
        raw = match.group("path")
        calls.append({
            "method": match.group("method").upper(),
            "path": _strip_url_to_api_path(raw),
            "raw": raw,
            "normalized": _normalize_api_path(raw),
        })
    return _dedupe_contract_items(calls)

def _iter_contract_source_files(root: Path, *, extensions: set[str] | None = None):
    allowed_extensions = extensions or _CONTRACT_SOURCE_EXTENSIONS
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in allowed_extensions:
            continue
        if _is_contract_skipped_path(str(path)):
            continue
        yield path

def _infer_http_method(prefix: str) -> str:
    matches = re.findall(r"\b(GET|POST|PUT|PATCH|DELETE)\b", prefix, flags=re.IGNORECASE)
    return matches[-1].upper() if matches else ""

def _strip_url_to_api_path(raw: str) -> str:
    raw = raw.strip()
    idx = raw.find("/api/v1")
    path = raw[idx:] if idx >= 0 else raw
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = re.sub(r"(?<!/)\$\{[^}/]*(?:\}|$)", "", path)
    return path.rstrip(".,:;")

def _is_product_api_path(raw: str) -> bool:
    path = _strip_url_to_api_path(raw)
    return path == "/api/v1" or path.startswith("/api/v1/")

def _normalize_api_path(raw: str) -> str:
    path = _strip_url_to_api_path(raw)
    path = re.sub(r"\$\{[^}/]*$", "{param}", path)
    path = re.sub(r"\$\{[^}]+\}", "{param}", path)
    path = re.sub(r"\{[^}/]+\}", "{param}", path)
    path = re.sub(r"\[[^]/]+\]", "{param}", path)
    path = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "{param}", path)
    path = re.sub(r"(?<=/)\*(?=/|$)", "{param}", path)
    path = re.sub(r"(?<=[^/])\*(?=/|$)", "{param}", path)
    path = re.sub(r"/+", "/", path).rstrip("/")
    return path or "/"

def _dedupe_contract_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen = set()
    for item in items:
        key = (item.get("method", ""), item.get("normalized", ""), item.get("path", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped

def _count_surface_terms(text: str, counts: dict[str, int]) -> None:
    lowered = text.lower()
    for term in _CONTRACT_SURFACE_TERMS:
        count = lowered.count(term)
        if count:
            counts[term] = counts.get(term, 0) + count

def _merge_term_counts(target: dict[str, int], incoming: dict[str, int]) -> None:
    for key, value in incoming.items():
        target[key] = target.get(key, 0) + value

def _contract_samples(items: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "method": item.get("method", ""),
            "path": item.get("path", ""),
            "normalized": item.get("normalized", ""),
            "source": item.get("source", ""),
        }
        for item in items[:limit]
    ]

def _symbol_value(symbol: Any, attr: str, key: str | None = None, default: Any = "") -> Any:
    if symbol is None:
        return default
    if isinstance(symbol, dict):
        return symbol.get(key or attr, default)
    return getattr(symbol, attr, default)

def _symbol_path(symbol: Any) -> str:
    return str(_symbol_value(symbol, "path", default=""))

def _symbol_type(symbol: Any) -> str:
    value = _symbol_value(symbol, "symbol_type", "type", "")
    return str(getattr(value, "value", value))

def _symbol_name(symbol: Any) -> str:
    return str(_symbol_value(symbol, "name", default=""))

def _symbol_metadata(symbol: Any) -> dict[str, Any]:
    metadata = _symbol_value(symbol, "metadata", default={})
    return metadata if isinstance(metadata, dict) else {}

def _symbol_ref_count(symbol: Any) -> int:
    value = _symbol_value(symbol, "reference_count", "ref_count", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

def _dep_value(dep: Any, attr: str, key: str | None = None) -> str:
    if isinstance(dep, dict):
        return str(dep.get(key or attr, "") or "")
    return str(getattr(dep, attr, "") or "")

def _dep_type(dep: Any) -> str:
    value = dep.get("type", "") if isinstance(dep, dict) else getattr(dep, "dep_type", "")
    return str(getattr(value, "value", value))

def _dep_metadata(dep: Any) -> dict[str, Any]:
    metadata = dep.get("metadata", {}) if isinstance(dep, dict) else getattr(dep, "metadata", {})
    return metadata if isinstance(metadata, dict) else {}

def _symbol_id_path(symbol_id: str) -> str:
    parts = symbol_id.split(":")
    return parts[1] if len(parts) >= 4 else ""

def _is_single_project_candidate(path: str, sym_type: str, name: str) -> bool:
    if not path or _is_contract_skipped_path(path):
        return False
    if sym_type in {"api", "route"}:
        return True
    if sym_type in {"component", "composable", "store"}:
        return _has_single_project_feature_signal(path, name)
    return False

def _has_single_project_feature_signal(path: str, name: str = "") -> bool:
    lowered = f"{path}\n{name}".lower()
    if any(term in lowered for term in _CONTRACT_SURFACE_TERMS):
        return True
    parts = set(Path(path).parts)
    return bool(parts & _SINGLE_PROJECT_FEATURE_PARTS)

def _has_product_surface_signal(path: str, name: str = "") -> bool:
    lowered = f"{path}\n{name}".lower()
    if any(term in lowered for term in _CONTRACT_SURFACE_TERMS):
        return True
    parts = set(Path(path).parts)
    return bool(parts & _SINGLE_PROJECT_PRODUCT_PARTS)

def _is_single_project_entry(path: str, sym_type: str, name: str) -> bool:
    parts = set(Path(path).parts)
    stem = Path(path).stem.lower()
    bare_name = name.split(".")[-1].lower()
    if sym_type in {"api", "route"}:
        return True
    if bare_name in _SINGLE_PROJECT_ENTRY_NAMES or stem in _SINGLE_PROJECT_ENTRY_NAMES:
        return True
    return bool(parts & _SINGLE_PROJECT_ENTRY_PARTS)

def _is_contract_skipped_path(path: str) -> bool:
    parts = set(Path(path).parts)
    if parts & _SKIP_WORKSPACE_DIRS or parts & _CONTRACT_SKIP_PARTS:
        return True
    stem = Path(path).stem
    return any(stem.endswith(suffix) for suffix in _CONTRACT_SKIP_SUFFIXES)

