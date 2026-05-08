"""
Human-readable profile formatter.
"""


def _format_header(profile: dict) -> list[str]:
    project_type = profile.get("project_type", "")
    project_sub_type = profile.get("project_sub_type", "")
    type_label = project_type
    if project_sub_type:
        type_label = f"{project_type} ({project_sub_type})"
    header = f"Project Profile: {profile['name']}"
    if type_label:
        header += f" [{type_label}]"
    return [header, f"Generated: {profile['generated_at']}", ""]


def _format_structure(profile: dict) -> list[str]:
    langs = profile.get("languages", {})
    lang_str = ", ".join(f"{k} ({v})" for k, v in
                         sorted(langs.items(), key=lambda x: -x[1])[:8])
    parts = [f"Files: {profile['file_count']}"]
    folder_structure = profile.get("folder_structure")
    if folder_structure:
        parts.append(f"Folders: {len(folder_structure)}")
    parts.append(f"Languages: {lang_str}")
    return ["Structure", f"  {' | '.join(parts)}", ""]


def _format_apis(profile: dict) -> list[str]:
    api_defs = profile.get("api_definitions", [])
    api_internal = profile.get("api_calls_internal", [])
    api_external = profile.get("api_calls_external", [])
    services = profile.get("services", [])
    if not (api_defs or api_internal or api_external or services):
        return []

    out = ["Services & APIs"]
    if api_defs:
        out.append(f"  Backend routes: {len(api_defs)} defined")
        for route in api_defs[:15]:
            method = route.get("method", "GET")
            path = route.get("path", "")
            out.append(f"    {method:6s} {path}")
        if len(api_defs) > 15:
            out.append(f"    ... and {len(api_defs) - 15} more")
        out.append("")
    if services:
        svc_names = ", ".join(s["name"] for s in services)
        out.append(f"  Services: {svc_names}")
        out.append("")
    if api_internal:
        out.append(f"  Frontend API calls: {len(api_internal)} internal")
    if api_external:
        out.append(f"  External API calls: {len(api_external)}")
    if api_internal or api_external:
        out.append("")
    return out


def _format_models(profile: dict) -> list[str]:
    models = profile.get("models", [])
    if not models:
        return []
    out = [f"Models ({len(models)})"]
    for m in models[:15]:
        field_str = f"{m['fields']} fields" if m.get("fields") else "no fields extracted"
        out.append(f"  {m['name']} ({field_str}) -- {m['file']}:{m['line']}")
    if len(models) > 15:
        out.append(f"  ... and {len(models) - 15} more")
    out.append("")
    return out


def _format_symbols(profile: dict) -> list[str]:
    sym_counts = profile.get("symbol_counts", {})
    if not sym_counts:
        return []

    def _plural(word: str, count: int) -> str:
        if count == 1:
            return word
        if word.endswith("s"):
            return word + "es"
        return word + "s"

    parts = [f"{v} {_plural(k, v)}" for k, v in
             sorted(sym_counts.items(), key=lambda x: -x[1])]
    return ["Symbols", f"  {', '.join(parts)}", ""]


def _format_dependencies(profile: dict) -> list[str]:
    deps = profile.get("dependencies", {})
    if not (deps and deps.get("total_count", 0) > 0):
        return []
    eco = deps.get("ecosystems", [])
    indirect = (f", {deps.get('indirect_count', 0)} indirect"
                if deps.get("indirect_count") else "")
    plural = "s" if len(eco) != 1 else ""
    return [
        "Dependencies",
        (f"  {deps['total_count']} packages "
         f"({deps.get('production_count', 0)} production, "
         f"{deps.get('dev_count', 0)} dev{indirect}) "
         f"across {len(eco)} ecosystem{plural} [{', '.join(eco)}]"),
        "",
    ]


def _format_connections(profile: dict) -> list[str]:
    module_graph = profile.get("module_graph", [])
    if not module_graph:
        return []
    out = [f"Connections (top {min(10, len(module_graph))} module pairs)"]
    for edge in module_graph[:10]:
        out.append(
            f"  {edge['source_file']} -> {edge['target_file']} ({edge['import_count']} refs)"
        )
    summary = profile.get("module_graph_summary", {})
    if summary:
        total_conn = summary.get("total_connections", 0)
        avg_refs = summary.get("avg_refs_per_module", 0)
        orphan_count = summary.get("orphan_count", 0)
        most_connected = summary.get("most_connected_file", "")
        out.append(f"  --- {total_conn} total connections, avg {avg_refs} refs/module")
        if most_connected:
            out.append(f"  Most connected: {most_connected}")
        if orphan_count > 0:
            out.append(f"  Orphan files (no imports/importers): {orphan_count}")
    out.append("")
    return out


def _format_complexity(profile: dict) -> list[str]:
    complexity = profile.get("complexity_summary", {})
    if not (complexity and complexity.get("total_functions", 0) > 0):
        return []
    out = [
        "Complexity",
        (f"  {complexity['total_functions']} functions analyzed, "
         f"{complexity['complex_functions']} complex (score >= 5), "
         f"avg score {complexity['avg_complexity']}"),
    ]
    most_complex = complexity.get("most_complex", [])
    if most_complex:
        out.append("  Top complex functions:")
        for fn in most_complex[:5]:
            out.append(f"    {fn['name']} (score={fn['score']}) -- {fn['path']}:{fn.get('line', 0)}")
    out.append("")
    return out


def _format_health_dim_detail(dim_name: str, dim: dict) -> str:
    if dim_name == "security" and dim.get("finding_count", 0) > 0:
        return f"  ({dim['finding_count']} findings)"
    if dim_name == "complexity" and dim.get("complex_count", 0) > 0:
        return f"  ({dim['complex_count']} complex functions)"
    if dim_name == "dead_code" and dim.get("dead_count", 0) > 0:
        return f"  ({dim['dead_count']} unreferenced symbols)"
    if dim_name == "coverage":
        if dim.get("coverage_pct", 0) > 0:
            return f"  ({dim['coverage_pct']}% covered)"
        return "  (no coverage data)"
    return ""


def _format_health(profile: dict) -> list[str]:
    health = profile.get("health_dimensions", {})
    if not (health and health.get("overall")):
        return []
    overall = health["overall"]
    out = [f"Health Score: {overall['grade']} ({overall['score']}/{overall['max']})"]
    for dim_name in ("security", "complexity", "dead_code", "coverage"):
        dim = health.get(dim_name, {})
        if not dim:
            continue
        label = dim_name.replace("_", " ").title()
        detail = _format_health_dim_detail(dim_name, dim)
        out.append(f"  {label:12s} {dim['score']:2d}/{dim['max']} {dim['status']}{detail}")
    out.append("")
    return out


def _format_entry_points(profile: dict) -> list[str]:
    entry_points = profile.get("entry_points", [])
    if not entry_points:
        return []
    out = [f"Entry Points ({len(entry_points)})"]
    for ep in entry_points[:10]:
        out.append(f"  {ep}")
    if len(entry_points) > 10:
        out.append(f"  ... and {len(entry_points) - 10} more")
    out.append("")
    return out


def _format_frameworks(profile: dict) -> list[str]:
    frameworks = profile.get("frameworks", [])
    if not frameworks:
        return []
    out = [f"Frameworks ({len(frameworks)})"]
    for fw in frameworks:
        version_str = f" v{fw['version']}" if fw.get("version") else ""
        out.append(f"  {fw['name']}{version_str} [{fw['type']}]")
        if fw.get("conventions"):
            conv_parts = [f"{k}={v}" for k, v in fw["conventions"].items()]
            out.append(f"    Conventions: {', '.join(conv_parts)}")
        if fw.get("entry_points"):
            ep_list = fw["entry_points"][:3]
            out.append(f"    Entry points: {', '.join(ep_list)}")
    out.append("")
    return out


def _format_patterns(profile: dict) -> list[str]:
    patterns = profile.get("patterns", [])
    if not patterns:
        return []
    return ["Patterns Detected", f"  {', '.join(patterns)}", ""]


def _format_infrastructure(profile: dict) -> list[str]:
    parts = [
        f"{label}: {'yes' if profile.get(key) else 'no'}"
        for key, label in [("has_docker", "Docker"), ("has_ci", "CI"),
                           ("has_tests", "Tests"), ("has_docs", "Docs")]
    ]
    out = ["Infrastructure", f"  {' | '.join(parts)}"]
    config_files = profile.get("config_files", [])
    if config_files:
        out.append(f"  Config: {', '.join(config_files[:10])}")
        if len(config_files) > 10:
            out.append(f"    ... and {len(config_files) - 10} more")
    out.append("")
    return out


def _format_git(profile: dict) -> list[str]:
    authors = profile.get("recent_authors", [])
    last_commit = profile.get("last_commit_date", "")
    if not (authors or last_commit):
        return []
    out = ["Git"]
    if authors:
        out.append(f"  Authors: {', '.join(authors)}")
    if last_commit:
        date_only = last_commit[:10] if len(last_commit) >= 10 else last_commit
        out.append(f"  Last commit: {date_only}")
    return out


def format_profile(profile: dict) -> str:
    """Format a project profile as human-readable text."""
    sections = [
        _format_header(profile),
        _format_structure(profile),
        _format_apis(profile),
        _format_models(profile),
        _format_symbols(profile),
        _format_dependencies(profile),
        _format_connections(profile),
        _format_complexity(profile),
        _format_health(profile),
        _format_entry_points(profile),
        _format_frameworks(profile),
        _format_patterns(profile),
        _format_infrastructure(profile),
        _format_git(profile),
    ]
    lines = [line for section in sections for line in section]
    return "\n".join(lines)
