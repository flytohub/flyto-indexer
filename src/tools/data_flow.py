"""Data flow lite — lightweight parameter threading via name matching."""

import logging
import re
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("flyto-indexer.data_flow")

try:
    from ..index_store import get_symbol_content_text, load_index
except ImportError:
    from index_store import get_symbol_content_text, load_index


def trace_data_flow(
    param_name: str,
    start_symbol: Optional[str] = None,
    project: Optional[str] = None,
    max_depth: int = 5,
    max_results: int = 20,
) -> dict:
    """Trace how a named parameter/variable flows through the call graph.

    Uses name matching: if function A calls B(email=user.email), and B has
    param 'email', we record the flow A.user.email → B.email.

    Args:
        param_name: Parameter/variable name to trace (e.g., "email", "user_id")
        start_symbol: Symbol to start tracing from (optional, searches all if omitted)
        project: Filter to specific project
        max_depth: Maximum trace depth (default 5)
        max_results: Maximum flow chains to return (default 20)

    Returns:
        {flows: [{chain: [steps], depth}], sources: [symbols that introduce this name]}
    """
    index = load_index()
    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})

    # Step 1: Find all symbols that have this param name
    param_symbols = _find_symbols_with_param(param_name, symbols, project)

    # Step 2: Find all symbols whose content references this name
    content_symbols = _find_symbols_referencing(param_name, symbols, project)

    # Step 3: Build call edges with param matching
    call_edges = _build_param_edges(param_name, dependencies, symbols)

    # Step 4: Trace flows
    if start_symbol:
        start_ids = _resolve_symbol(start_symbol, symbols, project)
    else:
        # Start from symbols that define/introduce this param
        start_ids = [sid for sid in param_symbols if _is_source(sid, param_name, symbols)]

    flows = []
    visited_chains = set()

    for sid in start_ids[:10]:
        chains = _trace_forward(sid, param_name, call_edges, symbols, max_depth)
        for chain in chains:
            chain_key = "→".join(chain["chain"])
            if chain_key not in visited_chains:
                visited_chains.add(chain_key)
                flows.append(chain)
            if len(flows) >= max_results:
                break
        if len(flows) >= max_results:
            break

    # Sort by depth (longer chains = more interesting)
    flows.sort(key=lambda f: f.get("depth", 0), reverse=True)

    return {
        "param_name": param_name,
        "flows": flows[:max_results],
        "sources": [
            {"symbol_id": sid, "name": symbols.get(sid, {}).get("name", ""),
             "path": symbols.get(sid, {}).get("path", "")}
            for sid in param_symbols[:10]
        ],
        "references": [
            {"symbol_id": sid, "name": symbols.get(sid, {}).get("name", ""),
             "path": symbols.get(sid, {}).get("path", "")}
            for sid in content_symbols[:10]
        ],
        "summary": {
            "total_flows": len(flows),
            "total_sources": len(param_symbols),
            "total_references": len(content_symbols),
            "total_call_edges": len(call_edges),
        },
    }


def _find_symbols_with_param(param_name: str, symbols: dict, project: Optional[str]) -> list:
    """Find symbols that have param_name as a parameter."""
    result = []
    param_lower = param_name.lower()
    for sid, sym in symbols.items():
        if project and not sid.lower().startswith(project.lower()):
            continue
        params = sym.get("params", [])
        if not params:
            continue
        for p in params:
            p_name = p.get("name", p) if isinstance(p, dict) else str(p)
            if p_name.lower() == param_lower or param_lower in p_name.lower():
                result.append(sid)
                break
    return result


def _find_symbols_referencing(param_name: str, symbols: dict, project: Optional[str],
                              max_check: int = 2000) -> list:
    """Find symbols whose content references param_name."""
    result = []
    pattern = re.compile(r'\b' + re.escape(param_name) + r'\b')
    checked = 0
    for sid, sym in symbols.items():
        if project and not sid.lower().startswith(project.lower()):
            continue
        if sym.get("type", "") not in ("function", "method"):
            continue
        checked += 1
        if checked > max_check:
            break
        content = get_symbol_content_text(sid, sym)
        if content and pattern.search(content):
            result.append(sid)
    return result


def _resolve_symbol(target: str, symbols: dict, project: Optional[str]) -> list:
    """Resolve a symbol name/id to symbol IDs."""
    if target in symbols:
        return [target]
    matches = []
    target_lower = target.lower()
    for sid, sym in symbols.items():
        if project and not sid.lower().startswith(project.lower()):
            continue
        name = sym.get("name", "")
        if name == target or name.lower() == target_lower:
            matches.append(sid)
    return matches[:5]


def _build_param_edges(param_name: str, dependencies: dict, symbols: dict) -> list:
    """Build call edges where param_name is potentially threaded through."""
    edges = []
    param_lower = param_name.lower()

    for _dep_id, dep in dependencies.items():
        if dep.get("type", "") != "calls":
            continue

        source = dep.get("source", "")
        resolved = dep.get("metadata", {}).get("resolved_target", "")
        target_raw = dep.get("target", "")

        if not source or not (resolved or target_raw):
            continue

        target_id = resolved if resolved and resolved in symbols else None
        if not target_id:
            continue

        # Check if the callee has a param matching param_name
        callee_sym = symbols.get(target_id, {})
        callee_params = callee_sym.get("params", [])
        has_matching_param = False
        for p in callee_params:
            p_name = p.get("name", p) if isinstance(p, dict) else str(p)
            if param_lower in p_name.lower():
                has_matching_param = True
                break

        if has_matching_param:
            edges.append({
                "from": source,
                "to": target_id,
                "call_target": target_raw,
            })

    return edges


def _is_source(sym_id: str, param_name: str, symbols: dict) -> bool:
    """Check if a symbol is a likely source/origin for this parameter."""
    sym = symbols.get(sym_id, {})
    # Functions that have this as a parameter are sources
    params = sym.get("params", [])
    for p in params:
        p_name = p.get("name", p) if isinstance(p, dict) else str(p)
        if param_name.lower() == p_name.lower():
            return True
    return False


def _trace_forward(start_id: str, param_name: str, call_edges: list,
                   symbols: dict, max_depth: int) -> list:
    """Trace param_name forward through call edges from start_id."""
    chains = []

    # Build adjacency from edges
    adj = defaultdict(list)
    for edge in call_edges:
        adj[edge["from"]].append(edge["to"])

    # DFS
    stack = [([start_id], {start_id})]
    while stack:
        path, visited = stack.pop()
        current = path[-1]

        nexts = adj.get(current, [])
        if not nexts or len(path) >= max_depth:
            if len(path) > 1:
                chains.append({
                    "chain": path,
                    "names": [symbols.get(s, {}).get("name", "?") for s in path],
                    "depth": len(path) - 1,
                })
            continue

        expanded = False
        for nxt in nexts[:5]:  # limit branching
            if nxt not in visited:
                expanded = True
                stack.append((path + [nxt], visited | {nxt}))

        if not expanded and len(path) > 1:
            chains.append({
                "chain": path,
                "names": [symbols.get(s, {}).get("name", "?") for s in path],
                "depth": len(path) - 1,
            })

    return chains
