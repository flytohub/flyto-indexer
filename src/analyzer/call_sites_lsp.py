"""LSP-resolved call site enrichment.

Walks the existing flyto index for every function symbol, queries the
language server for `callHierarchy/outgoingCalls`, and adds the
resolved (type-aware) edges to the call graph map produced by the
regex pass. Edges land in the same `local_call_graph` dict shape, so
the engine consumes them transparently.

Design constraints:
  - **Soft fail**: any LSP error short-circuits to "no edges added".
    The regex result is the floor, never replaced.
  - **Bounded**: caps per-symbol outgoing depth at 1 and total edges
    at MAX_EDGES (default 5000) so a giant repo doesn't hang the CLI.
  - **Single-language probe**: queries one language server per file;
    the LSPManager picks pyright / tsserver / gopls / rust-analyzer
    based on extension. Servers that aren't installed are skipped.

Outputs (added to the supplied `existing` dict):
  - new edges in existing["local_call_graph"]
  - new package call sites in existing["function_calls"]
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("flyto-indexer.analyzer.call_sites_lsp")

_MAX_EDGES = 5000
_MAX_DEPTH = 1


def enrich_with_lsp(project_root: Path, existing: dict) -> dict:
    """Walk function symbols, resolve outgoing calls via LSP, merge
    into existing[local_call_graph]. Returns stats."""
    try:
        from ..lsp.manager import LSPManager
        from ..lsp.call_graph import outgoing_calls
    except ImportError:
        from lsp.manager import LSPManager  # type: ignore
        from lsp.call_graph import outgoing_calls  # type: ignore

    mgr = LSPManager.get_instance()
    if not mgr._enabled:
        return {"edges_added": 0, "skipped": "lsp_disabled"}

    # Use the existing index's symbol list so we know where every
    # function is defined (file + line) without reparsing source.
    index_path = project_root / ".flyto-index" / "index.json"
    if not index_path.exists():
        return {"edges_added": 0, "skipped": "no_index"}
    try:
        idx = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"edges_added": 0, "skipped": f"index_parse_failed: {e}"}

    symbols = idx.get("symbols", {})
    fn_symbols = [
        s for s in symbols.values()
        if s.get("type") in ("function", "method") and s.get("path")
    ]
    if not fn_symbols:
        return {"edges_added": 0, "skipped": "no_function_symbols"}

    graph: dict = existing.setdefault("local_call_graph", {})
    edges_added = 0
    symbols_visited = 0

    for sym in fn_symbols:
        if edges_added >= _MAX_EDGES:
            break
        path = sym.get("path", "")
        line = sym.get("start_line") or sym.get("line") or 1
        # convert to 0-based and column 0 (LSP wants positions, but
        # the symbol's start_line points at `def name`, position 0
        # is acceptable for prepareCallHierarchy on most servers).
        try:
            edges = outgoing_calls(
                project_root=project_root,
                source_file=project_root / path,
                line_0based=max(0, int(line) - 1),
                col_0based=0,
                max_depth=_MAX_DEPTH,
                max_edges=64,
            )
        except Exception as e:
            logger.debug("LSP outgoing_calls failed for %s: %s", path, e)
            continue
        if not edges:
            continue

        symbols_visited += 1
        fqn = f"{path}:{sym.get('name', '')}"
        bucket = graph.setdefault(fqn, [])
        existing_set = set(bucket) if isinstance(bucket, list) else set(bucket.keys())

        for e in edges:
            target = e.to_name or ""
            if not target or target in existing_set:
                continue
            existing_set.add(target)
            bucket.append(target)
            edges_added += 1
            if edges_added >= _MAX_EDGES:
                break

    return {
        "edges_added": edges_added,
        "symbols_visited": symbols_visited,
        "depth": _MAX_DEPTH,
    }
