"""Reference and impact analysis tools for flyto-indexer MCP server."""

import logging
import re
from pathlib import Path

try:
    from ..index_store import load_index, get_symbol_content_text
except ImportError:
    from index_store import load_index, get_symbol_content_text

try:
    from .resolver import resolve_symbol, get_dedup_key as _resolver_dedup_key
except ImportError:
    from resolver import resolve_symbol, get_dedup_key as _resolver_dedup_key

logger = logging.getLogger("flyto-indexer.references")


def _extract_path_from_source_id(source_id: str) -> str:
    """Extract file path from source_id like project:path:type:name"""
    parts = source_id.split(":")
    if len(parts) >= 2:
        return parts[1]
    return ""


def _get_dedup_key(source_id: str) -> str:
    """
    Get dedup key for cross-project deduplication.

    Uses project + basename + type + name to distinguish same-named
    symbols across different projects:
    - flyto-cloud:src/.../Cart.vue:component:Cart -> flyto-cloud:Cart.vue:component:Cart
    - flyto-landing:src/.../Cart.vue:component:Cart -> flyto-landing:Cart.vue:component:Cart
    """
    parts = source_id.split(":")
    if len(parts) >= 4:
        # project:path:type:name -> project:basename(path):type:name
        project = parts[0]
        path = parts[1]
        basename = path.rsplit("/", 1)[-1]  # Get filename only
        return f"{project}:{basename}:{parts[2]}:{parts[3]}"
    elif len(parts) >= 2:
        return parts[1]
    return source_id


def _find_refs_from_reverse_index(resolved_id, reverse_index, symbols, target_path, seen_keys, seen_paths, dependencies):
    """Method 0: Use pre-computed reverse index (fastest & most accurate), plus name-based reverse index lookup."""
    references = []
    target_name = symbols.get(resolved_id, {}).get("name", "")

    # Exact resolved_id lookup
    if resolved_id in reverse_index:
        for caller_id in reverse_index[resolved_id]:
            caller_symbol = symbols.get(caller_id, {})
            from_path = caller_symbol.get("path", "") or _extract_path_from_source_id(caller_id)

            # Skip self-references
            if from_path == target_path:
                continue

            # Dedup across projects (flyto-cloud vs flyto-cloud-dev)
            dedup_key = _get_dedup_key(caller_id)
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)

            # Find the line from dependencies
            line = 0
            for dep in dependencies.values():
                resolved_target = dep.get("metadata", {}).get("resolved_target", "")
                if resolved_target == resolved_id and dep.get("source", "") == caller_id:
                    line = dep.get("line", 0)
                    break

            key = (from_path, caller_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            references.append({
                "type": "call",
                "from_symbol": caller_id,
                "from_path": from_path,
                "from_name": caller_symbol.get("name", ""),
                "line": line,
                "confidence": "high",  # From reverse index
            })

    # Also check reverse index by name (some deps might not be fully resolved)
    if target_name in reverse_index:
        for caller_id in reverse_index[target_name]:
            caller_symbol = symbols.get(caller_id, {})
            from_path = caller_symbol.get("path", "") or _extract_path_from_source_id(caller_id)

            if from_path == target_path:
                continue

            # Dedup across projects
            dedup_key = _get_dedup_key(caller_id)
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)

            key = (from_path, caller_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            references.append({
                "type": "call",
                "from_symbol": caller_id,
                "from_path": from_path,
                "from_name": caller_symbol.get("name", ""),
                "line": 0,
                "confidence": "medium",
            })

    return references


def _find_refs_from_dependencies(resolved_id, target_name, dependencies, symbols, target_path, seen_keys, seen_paths):
    """Method 1: Search dependencies (calls, extends, implements, uses)."""
    references = []

    for _dep_id, dep in dependencies.items():
        dep_type = dep.get("type", "")
        target = dep.get("target", "")
        resolved_target = dep.get("metadata", {}).get("resolved_target", "")

        # Check if this dependency targets our symbol
        if dep_type in ("calls", "extends", "implements", "uses") and (target in (resolved_id, target_name) or resolved_target == resolved_id):
                source_id = dep.get("source", "")
                source_symbol = symbols.get(source_id, {})

                # Get path from symbol or extract from source_id
                from_path = source_symbol.get("path", "") or _extract_path_from_source_id(source_id)

                # Skip self-references (same file)
                if from_path == target_path:
                    continue

                # Dedup across projects
                dedup_key = _get_dedup_key(source_id)
                if dedup_key in seen_paths:
                    continue
                seen_paths.add(dedup_key)

                line = dep.get("line", 0)
                key = (from_path, line)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                references.append({
                    "type": dep_type,
                    "from_symbol": source_id,
                    "from_path": from_path,
                    "from_name": source_symbol.get("name", ""),
                    "line": line,
                    "confidence": "high" if resolved_target else "medium",
                })

    return references


def _find_refs_from_content(resolved_id, target_name, target_path, symbols, seen_keys, seen_paths):
    """Method 2: Search content for symbol name usage (capped to avoid O(N*C) at scale)."""
    references = []
    _CONTENT_SEARCH_MAX = 5000  # Skip content fallback for very large indexes

    if not target_name or len(target_name) < 2 or len(symbols) > _CONTENT_SEARCH_MAX:
        return references

    pattern = rf'\b{re.escape(target_name)}\s*\('

    for sym_id, sym in symbols.items():
        if sym_id == resolved_id:
            continue

        sym_path = sym.get("path", "")
        # Skip same file (self-references)
        if sym_path == target_path:
            continue

        # Dedup across projects
        dedup_key = _get_dedup_key(sym_id)
        if dedup_key in seen_paths:
            continue

        content = get_symbol_content_text(sym_id, sym)
        matches = list(re.finditer(pattern, content))

        if matches:
            seen_paths.add(dedup_key)  # Only add if matches found

            # Find line number of first match
            first_match = matches[0]
            line_offset = content[:first_match.start()].count('\n')
            line = sym.get("start_line", 0) + line_offset

            key = (sym_path, line)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            references.append({
                "type": "usage",
                "from_symbol": sym_id,
                "from_path": sym_path,
                "from_name": sym.get("name", ""),
                "line": line,
                "occurrences": len(matches),
                "confidence": "low",  # Content regex match
            })

    return references


def find_references(symbol_id: str) -> dict:
    """
    Find all places that reference this symbol.

    Uses:
    1. Reverse index (pre-computed during indexing)
    2. Resolved dependencies
    3. Content search as fallback
    """
    index = load_index()
    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})
    reverse_index = index.get("reverse_index", {})

    resolved_id = resolve_symbol(symbol_id, symbols)
    target_symbol = symbols.get(resolved_id)
    if not target_symbol:
        return {"error": f"Symbol not found: {symbol_id}"}

    target_name = target_symbol.get("name", "")
    target_path = target_symbol.get("path", "")
    references = []
    seen_keys = set()  # Use (path, line) as key to avoid duplicates
    seen_paths = set()  # Track unique paths for dedup across projects

    # Method 0: Use pre-computed reverse index (fastest & most accurate) + name-based lookup
    references.extend(_find_refs_from_reverse_index(
        resolved_id, reverse_index, symbols, target_path, seen_keys, seen_paths, dependencies
    ))

    # Method 1: Search dependencies (calls, extends, implements, uses)
    references.extend(_find_refs_from_dependencies(
        resolved_id, target_name, dependencies, symbols, target_path, seen_keys, seen_paths
    ))

    # Method 2: Search content for symbol name usage (capped to avoid O(N*C) at scale)
    references.extend(_find_refs_from_content(
        resolved_id, target_name, target_path, symbols, seen_keys, seen_paths
    ))

    # Method 3: LSP enrichment (type-aware references from language servers)
    lsp_refs = _enrich_with_lsp(resolved_id, target_symbol, index)
    if lsp_refs:
        for ref in lsp_refs:
            key = (ref["from_path"], ref.get("line", 0))
            if key not in seen_keys:
                seen_keys.add(key)
                references.append(ref)

    # Sort by confidence (high first), then by path
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    references.sort(key=lambda x: (
        confidence_order.get(x.get("confidence", "low"), 2),
        x.get("from_path", ""),
        x.get("line", 0)
    ))

    # Group by project
    by_project = {}
    for ref in references:
        project = ref["from_symbol"].split(":")[0] if ":" in ref["from_symbol"] else "unknown"
        if project not in by_project:
            by_project[project] = []
        by_project[project].append(ref)

    # Count by confidence
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    for ref in references:
        conf = ref.get("confidence", "low")
        confidence_counts[conf] = confidence_counts.get(conf, 0) + 1

    # next_action hint for AI
    if len(references) == 0:
        next_action = "No references found — may be dead code. Use find_dead_code to verify."
    elif len(references) <= 5:
        top = next((r for r in references if r.get("confidence") == "high"), references[0])
        next_action = f"{len(references)} reference(s). Highest-confidence: {top.get('from_path', '')}:{top.get('from_name', '')}"
    else:
        next_action = f"{len(references)} references across {len(by_project)} project(s). Use impact_analysis for risk assessment."

    return {
        "symbol": resolved_id,
        "name": target_name,
        "total": len(references),  # Unique callers (deduped across projects)
        "confidence_breakdown": confidence_counts,
        "by_project": by_project,
        "references": references,
        "next_action": next_action,
    }


def _enrich_impact_with_call_hierarchy(
    resolved_id: str,
    symbols: dict,
    seen_paths: set,
    project_root: str | None = None,
    max_depth: int = 2,
) -> list:
    """Add indirect callers via LSP call-hierarchy traversal.

    Gives real blast radius: when A calls B calls C, modifying C surfaces both
    A and B as affected. The regex reverse_index only reports direct callers.

    Returns a list of affected-dicts (matches impact_analysis output shape).
    Empty list when LSP is unavailable or fails — never raises.
    """
    try:
        from ..lsp.call_graph import incoming_calls
        from ..lsp.manager import LSPManager
        from ..lsp.mapper import symbol_to_lsp_position
    except ImportError:
        try:
            from lsp.call_graph import incoming_calls
            from lsp.manager import LSPManager
            from lsp.mapper import symbol_to_lsp_position
        except ImportError:
            return []

    manager = LSPManager.get_instance()
    if not manager._enabled:
        return []

    target_symbol = symbols.get(resolved_id, {})
    target_path = target_symbol.get("path", "")
    if not target_path:
        return []

    if project_root is None:
        import os
        from pathlib import Path as _P
        project_root = os.environ.get("FLYTO_PROJECT_ROOT") or os.getcwd()
    project_root_path = Path(project_root)

    pos = symbol_to_lsp_position(target_symbol, str(project_root_path))
    if pos is None:
        return []
    _uri, line, col = pos

    abs_target = project_root_path / target_path
    if not abs_target.is_file():
        abs_target = Path(target_path)
        if not abs_target.is_file():
            return []

    try:
        edges = incoming_calls(
            project_root_path, abs_target,
            line_0based=line, col_0based=col,
            max_depth=max_depth,
        )
    except Exception as e:
        logger.debug("call hierarchy enrichment failed: %s", e)
        return []

    out: list = []
    for edge in edges:
        key = (edge.from_file, edge.from_line)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        reason = f"Indirect call (depth {edge.depth})" if edge.depth > 1 else "Direct call (lsp)"
        out.append({
            "id": f"{edge.from_file}:{edge.from_name}",
            "path": edge.from_file,
            "name": edge.from_name,
            "type": "function",
            "line": edge.from_line,
            "reason": reason,
            "source": "lsp-call-hierarchy",
        })
    return out


def _enrich_with_lsp(resolved_id: str, target_symbol: dict, index: dict) -> list:
    """Attempt to get type-aware references via LSP.

    Returns a list of reference dicts (same format as find_references entries),
    or an empty list if LSP is unavailable or fails.
    """
    try:
        try:
            from ..index_store import _get_lsp_manager
        except ImportError:
            from index_store import _get_lsp_manager

        manager = _get_lsp_manager()
        if manager is None:
            return []

        target_path = target_symbol.get("path", "")
        if not target_path:
            return []

        language = manager.language_for_path(target_path)
        if not language:
            return []

        # Determine project root from index metadata
        project_root = index.get("project_root", "")
        if not project_root:
            # Fallback: try to infer from index dir
            import os
            project_root = os.environ.get("FLYTO_INDEX_DIR", "")
            if project_root:
                project_root = str(os.path.dirname(project_root))
            else:
                project_root = os.getcwd()

        client = manager.get_client(language, project_root)
        if client is None:
            return []

        try:
            from ..lsp.mapper import symbol_to_lsp_position, lsp_locations_to_references
        except ImportError:
            from lsp.mapper import symbol_to_lsp_position, lsp_locations_to_references

        pos = symbol_to_lsp_position(target_symbol, project_root)
        if pos is None:
            return []

        uri, line, col = pos

        # Open the document so the server knows about it
        import os
        abs_path = os.path.join(project_root, target_path)
        if not os.path.isfile(abs_path):
            abs_path = target_path
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            lsp_cfg = {"python": "python", "typescript": "typescript",
                       "go": "go", "rust": "rust"}
            lang_id = lsp_cfg.get(language, language)
            client.did_open(uri, lang_id, text)
        except (OSError, IOError):
            pass

        locations = client.text_document_references(uri, line, col)
        if not locations:
            return []

        return lsp_locations_to_references(locations, index)

    except Exception as e:
        logger.debug("LSP enrichment failed: %s", e, exc_info=True)
        return []


def impact_analysis(symbol_id: str) -> dict:
    """
    Impact analysis.

    Determine which locations would be affected by modifying a symbol.
    Uses the reverse_index for accurate lookups.
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    resolved_id = resolve_symbol(symbol_id, symbols)

    affected = []
    seen_paths = set()  # Dedup across projects

    def get_basename_key(source_id: str) -> str:
        parts = source_id.split(":")
        if len(parts) >= 4:
            basename = parts[1].rsplit("/", 1)[-1]
            return f"{basename}:{parts[2]}:{parts[3]}"
        return source_id

    # Method 1: Use reverse_index (most accurate)
    if resolved_id in reverse_index:
        for caller_id in reverse_index[resolved_id]:
            dedup_key = get_basename_key(caller_id)
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)

            caller_symbol = symbols.get(caller_id, {})
            affected.append({
                "id": caller_id,
                "path": caller_symbol.get("path", ""),
                "name": caller_symbol.get("name", ""),
                "type": caller_symbol.get("type", ""),
                "reason": "Direct call",
            })

    # Method 2: Check resolved_target in dependencies
    for _dep_id, dep in dependencies.items():
        resolved_target = dep.get("metadata", {}).get("resolved_target", "")
        if resolved_target == resolved_id:
            source_id = dep.get("source", "")
            dedup_key = get_basename_key(source_id)
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)

            source_symbol = symbols.get(source_id, {})
            affected.append({
                "id": source_id,
                "path": source_symbol.get("path", ""),
                "name": source_symbol.get("name", ""),
                "type": dep.get("type", ""),
                "reason": f"Via {dep.get('type', 'unknown')} dependency",
            })

    # Method 3: LSP call-hierarchy enrichment (depth-2 incoming calls)
    # Gives real blast radius — reverse_index is regex-built and can miss
    # cross-module callers that hit the same-named function through rebinding.
    lsp_depth_hits = _enrich_impact_with_call_hierarchy(
        resolved_id, symbols, seen_paths, project_root=None,
    )
    affected.extend(lsp_depth_hits)

    warning = ""
    if len(affected) == 0:
        suggestion = "This symbol is not referenced anywhere else and can be safely modified."
    elif len(affected) <= 3:
        warning = f"Modification affects {len(affected)} locations"
        suggestion = "Impact is small. Recommend checking each call site individually."
    elif len(affected) <= 10:
        warning = f"⚠️ Modification affects {len(affected)} locations"
        suggestion = "Moderate impact. Recommend careful evaluation."
    else:
        warning = f"⚠️ Modification affects {len(affected)} locations!"
        suggestion = "High impact. Recommend cautious modification and thorough testing."

    # next_action hint for AI
    if len(affected) == 0:
        next_action = "Safe to modify — no callers found."
    elif len(affected) <= 3:
        first = affected[0]
        next_action = f"Low impact ({len(affected)} callers). Check first caller: {first.get('path', '')}:{first.get('name', '')}"
    else:
        next_action = f"High impact ({len(affected)} callers). Use edit_impact_preview for detailed call sites with code lines."

    return {
        "symbol": resolved_id,
        "affected_count": len(affected),
        "affected": affected[:20],  # Limit to top 20
        "has_more": len(affected) > 20,
        "warning": warning,
        "suggestion": suggestion,
        "next_action": next_action,
    }


def _collect_call_sites(resolved_id, target_name, target_path, reverse_index, symbols):
    """Collect call sites from reverse_index by resolved_id and target_name.

    Returns (call_sites, seen_keys) where seen_keys tracks dedup state.
    """
    call_sites = []
    seen_keys = set()

    def get_dedup_key(source_id: str) -> str:
        parts = source_id.split(":")
        if len(parts) >= 4:
            basename = parts[1].rsplit("/", 1)[-1]
            return f"{basename}:{parts[2]}:{parts[3]}"
        return source_id

    def _process_callers(caller_ids, confidence):
        for caller_id in caller_ids:
            dedup_key = get_dedup_key(caller_id)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            caller_sym = symbols.get(caller_id, {})
            caller_path = caller_sym.get("path", "")
            if caller_path == target_path:
                continue

            # Get the actual code line
            content = get_symbol_content_text(caller_id, caller_sym)
            code_line = ""
            line_num = 0
            if content and target_name:
                for i, line in enumerate(content.split("\n")):
                    if target_name in line:
                        code_line = line.strip()[:120]
                        line_num = caller_sym.get("start_line", 0) + i
                        break

            call_sites.append({
                "file": caller_path,
                "line": line_num,
                "code": code_line,
                "caller_name": caller_sym.get("name", ""),
                "confidence": confidence,
            })

    # From reverse_index by resolved_id
    _process_callers(reverse_index.get(resolved_id, []), "high")

    # Also check by name in reverse_index
    _process_callers(reverse_index.get(target_name, []), "medium")

    return call_sites, seen_keys


def _assess_edit_risk(total, by_project, change_type):
    """Compute risk level, reason, change description, and suggestions.

    Returns a dict with keys: risk, risk_reason, change_description, suggestions.
    """
    change_risk_map = {
        "rename": ("All call sites must update the name.", ["Update all call sites in a single commit", "Use find-and-replace across all files"]),
        "delete": ("All call sites will break.", ["Ensure no code depends on this before deleting", "Consider deprecation first"]),
        "signature_change": ("Call sites may need parameter updates.", ["Review each call site for compatibility", "Consider adding default parameters for backward compatibility"]),
        "add_param": ("Call sites may need to pass the new argument.", ["Add default value to new parameter if possible", "Update call sites that need the new parameter"]),
        "modify": ("Internal logic change only.", ["Run tests to verify behavior", "Check if return type/shape changed"]),
    }
    risk_info = change_risk_map.get(change_type, ("Unknown change type.", []))

    if total == 0:
        risk = "safe"
        risk_reason = "No call sites found, safe to change."
    elif total <= 3 and len(by_project) <= 1:
        risk = "low"
        risk_reason = f"{total} call site(s) in {len(by_project)} project(s)"
    elif total <= 10:
        risk = "moderate"
        risk_reason = f"{total} call sites across {len(by_project)} project(s)"
    else:
        risk = "high"
        risk_reason = f"{total} call sites across {len(by_project)} project(s)"

    # For delete/rename, risk is always elevated
    if change_type in ("delete", "rename") and total > 0:
        risk = "high" if total > 3 else "moderate"

    return {
        "risk": risk,
        "risk_reason": risk_reason,
        "change_description": risk_info[0],
        "suggestions": risk_info[1],
    }


def edit_impact_preview(symbol_id: str, change_type: str = "modify") -> dict:
    """
    Preview the impact of editing a symbol before making changes.

    Shows all call sites with actual code lines, risk assessment, and suggestions.
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})

    resolved_id = resolve_symbol(symbol_id, symbols)
    target_symbol = symbols.get(resolved_id)
    if not target_symbol:
        return {"error": f"Symbol not found: {symbol_id}"}

    target_name = target_symbol.get("name", "")
    target_path = target_symbol.get("path", "")

    # Collect call sites (limit to 30)
    call_sites, _seen = _collect_call_sites(
        resolved_id, target_name, target_path, reverse_index, symbols
    )
    call_sites = call_sites[:30]

    # Build path->project lookup (O(N) once, not O(N*M))
    path_to_project: dict[str, str] = {}
    for sid in symbols:
        parts = sid.split(":")
        if len(parts) >= 2:
            sym_path = symbols[sid].get("path", "")
            if sym_path and sym_path not in path_to_project:
                path_to_project[sym_path] = parts[0]

    # Group by project
    by_project: dict[str, int] = {}
    for cs in call_sites:
        proj = path_to_project.get(cs["file"], "unknown")
        by_project[proj] = by_project.get(proj, 0) + 1

    total = len(call_sites)

    # Risk assessment
    risk_result = _assess_edit_risk(total, by_project, change_type)

    # next_action hint for AI
    if total == 0:
        next_action = "Safe to proceed — no call sites found."
    elif change_type in ("rename", "delete"):
        files_to_update = sorted({cs["file"] for cs in call_sites})
        next_action = f"Update {len(files_to_update)} file(s): {', '.join(files_to_update[:5])}"
    else:
        next_action = f"Run tests after change. Use find_test_file to locate test files for {target_path}."

    return {
        "symbol": resolved_id,
        "symbol_name": target_name,
        "change_type": change_type,
        "total_call_sites": total,
        "call_sites": call_sites,
        "by_project": by_project,
        "risk": risk_result["risk"],
        "risk_reason": risk_result["risk_reason"],
        "change_description": risk_result["change_description"],
        "suggestions": risk_result["suggestions"],
        "next_action": next_action,
    }


def cross_project_impact(
    symbol_name: str,
    source_project: str = None
) -> dict:
    """
    Cross-project API change tracking.

    When a function/class in one project changes, find which locations in other projects need updating.
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    index.get("dependencies", {})

    # Find source symbols
    source_symbols = []
    for sym_id, sym in symbols.items():
        if sym.get("name") == symbol_name:
            sym_project = sym_id.split(":")[0] if ":" in sym_id else ""
            if source_project and source_project.lower() not in sym_project.lower():
                continue
            source_symbols.append({
                "id": sym_id,
                "project": sym_project,
                "path": sym.get("path", ""),
                "type": sym.get("type", ""),
            })

    if not source_symbols:
        return {"error": f"Symbol '{symbol_name}' not found"}

    # Find cross-project references
    cross_project_refs = []

    for source in source_symbols:
        src_project = source["project"]
        source_id = source["id"]

        # Find references from reverse_index
        callers = reverse_index.get(source_id, [])

        for caller_id in callers:
            caller_project = caller_id.split(":")[0] if ":" in caller_id else ""

            # Only care about cross-project references
            if caller_project == src_project:
                continue

            # Skip forked projects (flyto-cloud-dev is a fork of flyto-cloud)
            if (src_project == "flyto-cloud" and caller_project == "flyto-cloud-dev") or \
               (src_project == "flyto-cloud-dev" and caller_project == "flyto-cloud"):
                continue

            caller_sym = symbols.get(caller_id, {})
            cross_project_refs.append({
                "caller_id": caller_id,
                "caller_project": caller_project,
                "caller_path": caller_sym.get("path", ""),
                "caller_name": caller_sym.get("name", ""),
                "caller_type": caller_sym.get("type", ""),
                "source_project": src_project,
                "source_id": source_id,
            })

    # Group by project
    by_affected_project = {}
    for ref in cross_project_refs:
        proj = ref["caller_project"]
        if proj not in by_affected_project:
            by_affected_project[proj] = []
        by_affected_project[proj].append(ref)

    # Generate suggestions
    if len(cross_project_refs) == 0:
        suggestion = f"'{symbol_name}' has no cross-project references and can be safely modified."
        risk = "low"
    elif len(by_affected_project) == 1:
        suggestion = f"Modifying '{symbol_name}' will affect {len(cross_project_refs)} call sites in 1 other project."
        risk = "medium"
    else:
        suggestion = f"⚠️ Modifying '{symbol_name}' will affect {len(by_affected_project)} other projects!"
        risk = "high"

    return {
        "symbol_name": symbol_name,
        "source_symbols": source_symbols,
        "cross_project_refs": cross_project_refs,
        "by_affected_project": {k: len(v) for k, v in by_affected_project.items()},
        "affected_projects": list(by_affected_project.keys()),
        "total_cross_refs": len(cross_project_refs),
        "risk": risk,
        "suggestion": suggestion,
    }


def _collect_imports(target_paths, imports_map):
    """Collect what the target paths depend on (imports)."""
    results = []
    seen_imports = set()
    for path in target_paths:
        for imp in imports_map.get(path, []):
            target = imp["target"]
            if target not in seen_imports:
                seen_imports.add(target)
                results.append({
                    "from": path,
                    "to": target,
                    "type": imp["type"],
                })
    return results


def _dependents_from_reverse_index(path, reverse_index, seen_dependents, target_paths):
    """Extract dependents for a single path from reverse_index (more accurate, includes 'uses' dependencies)."""
    results = []
    # First, use reverse_index (more accurate, includes 'uses' dependencies)
    for sid, callers in reverse_index.items():
        if ":" in sid:
            sym_path = sid.split(":")[1]
            if sym_path == path:
                for caller in callers:
                    if ":" in caller:
                        caller_path = caller.split(":")[1]
                        if caller_path not in seen_dependents and caller_path not in target_paths:
                            seen_dependents.add(caller_path)
                            results.append({
                                "from": caller_path,
                                "to": path,
                                "type": "calls",  # reverse_index doesn't track dep type
                            })
    return results


def _dependents_from_map(path, dependents_map, seen_dependents, target_paths):
    """Extract dependents for a single path from dependents_map (fallback for additional deps)."""
    results = []
    # Fallback: also check dependents_map for additional deps
    for dep in dependents_map.get(path, []):
        source = dep["source"]
        if source not in seen_dependents and source not in target_paths:
            seen_dependents.add(source)
            results.append({
                "from": source,
                "to": path,
                "type": dep["type"],
            })
    return results


def _collect_dependents(target_paths, reverse_index, dependents_map):
    """Collect what depends on the target paths (dependents), using reverse_index for accuracy."""
    results = []
    seen_dependents = set()
    for path in target_paths:
        results.extend(_dependents_from_reverse_index(path, reverse_index, seen_dependents, target_paths))
        results.extend(_dependents_from_map(path, dependents_map, seen_dependents, target_paths))
    return results


def _build_dependency_maps(dependencies):
    """Build imports_map and dependents_map from raw dependencies."""
    imports_map = {}
    dependents_map = {}

    for dep in dependencies.values():
        source = dep.get("source", "")
        parts = source.split(":")
        source_path = parts[1] if len(parts) > 1 else ""
        if not source_path:
            continue

        imports_map.setdefault(source_path, []).append({
            "target": dep.get("target", ""),
            "type": dep.get("type", ""),
            "line": dep.get("line", 0),
        })

        resolved_target = dep.get("metadata", {}).get("resolved_target", "")
        if resolved_target:
            target_path = resolved_target.split(":")[1] if ":" in resolved_target else ""
            if target_path:
                dependents_map.setdefault(target_path, []).append({
                    "source": source_path,
                    "source_id": source,
                    "type": dep.get("type", ""),
                    "line": dep.get("line", 0),
                })

    return imports_map, dependents_map


def _resolve_target_paths(file_path, symbol_id, project, symbols):
    """Determine target paths based on query parameters."""
    if file_path:
        return {file_path}
    if symbol_id:
        parts = symbol_id.split(":")
        if len(parts) > 1:
            return {parts[1]}
    if project:
        return {
            sym.get("path", "")
            for sid, sym in symbols.items()
            if sid.startswith(project + ":")
        }
    return set()


def _build_dep_summary(imports, dependents, target_paths):
    """Build summary statistics for a dependency graph."""
    import_types = {}
    for imp in imports:
        t = imp["type"]
        import_types[t] = import_types.get(t, 0) + 1

    dependent_types = {}
    for dep in dependents:
        t = dep["type"]
        dependent_types[t] = dependent_types.get(t, 0) + 1

    return {
        "target_files": len(target_paths),
        "imports_count": len(imports),
        "dependents_count": len(dependents),
        "import_types": import_types,
        "dependent_types": dependent_types,
    }


def dependency_graph(
    file_path: str = None,
    symbol_id: str = None,
    project: str = None,
    direction: str = "both",
    max_depth: int = 2
) -> dict:
    """Get dependency graph for a file, symbol, or project."""
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})

    imports_map, dependents_map = _build_dependency_maps(index.get("dependencies", {}))
    target_paths = _resolve_target_paths(file_path, symbol_id, project, symbols)

    if not target_paths:
        return {"error": "No valid target specified. Provide file_path, symbol_id, or project."}

    imports = _collect_imports(target_paths, imports_map) if direction in ("both", "imports") else []
    dependents = _collect_dependents(target_paths, reverse_index, dependents_map) if direction in ("both", "dependents") else []

    return {
        "query": {
            "file_path": file_path, "symbol_id": symbol_id,
            "project": project, "direction": direction, "max_depth": max_depth,
        },
        "imports": imports,
        "dependents": dependents,
        "summary": _build_dep_summary(imports, dependents, target_paths),
    }


def batch_impact_analysis(symbol_ids: list) -> dict:
    """
    Run impact analysis on multiple symbols at once.

    More efficient than calling impact_analysis() repeatedly because
    the index is loaded only once. Returns per-symbol breakdown and
    a deduplicated union of all affected symbols.
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    def get_basename_key(source_id: str) -> str:
        parts = source_id.split(":")
        if len(parts) >= 4:
            basename = parts[1].rsplit("/", 1)[-1]
            return f"{basename}:{parts[2]}:{parts[3]}"
        return source_id

    def _resolve(symbol_id: str) -> str:
        if symbol_id in symbols:
            return symbol_id
        for sid, sym in symbols.items():
            if sym.get("name") == symbol_id and sym.get("type") in ("composable", "function", "class"):
                return sid
        for sid in symbols:
            if symbol_id in sid:
                return sid
        return symbol_id

    per_symbol = []
    all_affected = {}  # keyed by dedup_key -> affected dict

    for sid in symbol_ids:
        resolved_id = _resolve(sid)
        affected = []
        seen_paths = set()

        # Method 1: reverse_index
        if resolved_id in reverse_index:
            for caller_id in reverse_index[resolved_id]:
                dedup_key = get_basename_key(caller_id)
                if dedup_key in seen_paths:
                    continue
                seen_paths.add(dedup_key)

                caller_symbol = symbols.get(caller_id, {})
                entry = {
                    "id": caller_id,
                    "path": caller_symbol.get("path", ""),
                    "name": caller_symbol.get("name", ""),
                    "type": caller_symbol.get("type", ""),
                    "reason": "Direct call",
                }
                affected.append(entry)
                all_affected[dedup_key] = entry

        # Method 2: resolved_target in dependencies
        for _dep_id, dep in dependencies.items():
            resolved_target = dep.get("metadata", {}).get("resolved_target", "")
            if resolved_target == resolved_id:
                source_id = dep.get("source", "")
                dedup_key = get_basename_key(source_id)
                if dedup_key in seen_paths:
                    continue
                seen_paths.add(dedup_key)

                source_symbol = symbols.get(source_id, {})
                entry = {
                    "id": source_id,
                    "path": source_symbol.get("path", ""),
                    "name": source_symbol.get("name", ""),
                    "type": dep.get("type", ""),
                    "reason": f"Via {dep.get('type', 'unknown')} dependency",
                }
                affected.append(entry)
                all_affected[dedup_key] = entry

        count = len(affected)
        if count == 0:
            risk = "safe"
        elif count <= 10:
            risk = "moderate"
        else:
            risk = "high"

        per_symbol.append({
            "symbol_id": resolved_id,
            "affected_count": count,
            "risk": risk,
        })

    deduplicated = list(all_affected.values())
    total = len(deduplicated)

    if total == 0:
        overall_risk = "safe"
    elif total <= 10:
        overall_risk = "moderate"
    else:
        overall_risk = "high"

    return {
        "symbols_analyzed": len(symbol_ids),
        "total_affected": total,
        "per_symbol": per_symbol,
        "deduplicated_affected": deduplicated,
        "overall_risk": overall_risk,
    }
