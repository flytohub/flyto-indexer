"""
LSP workspace-wide symbol search.

Uses workspace/symbol to query every connected language server for project-level
symbol candidates matching a name. Used as a precision layer on top of the
regex-built index when we need to disambiguate common names.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .manager import LSPManager, _LSP_SERVERS
from .protocol import uri_to_path

logger = logging.getLogger("flyto-indexer.lsp.workspace_symbols")


@dataclass
class WorkspaceSymbol:
    name: str
    kind: int        # LSP SymbolKind numeric code
    file: str
    line: int        # 1-based
    container: str   # enclosing class/module name, if any


def _parse_symbol(item: dict) -> WorkspaceSymbol | None:
    try:
        name = item.get("name") or ""
        if not name:
            return None
        kind = int(item.get("kind") or 0)
        location = item.get("location") or {}
        uri = location.get("uri") or ""
        rng = location.get("range") or {}
        line = (rng.get("start") or {}).get("line", 0) + 1
        container = item.get("containerName") or ""
        return WorkspaceSymbol(
            name=name, kind=kind,
            file=uri_to_path(uri), line=line,
            container=container,
        )
    except (TypeError, ValueError):
        return None


def query_symbol(
    project_root: Path,
    query: str,
    languages: list[str] | None = None,
) -> List[WorkspaceSymbol]:
    """Return workspace-wide symbol matches across all running language servers.

    `languages` limits which servers to hit (default: all available ones for
    this project). Dedupes by (file, line, name) so multi-server overlap
    doesn't produce duplicate hits.
    """
    manager = LSPManager.get_instance()
    if not manager._enabled or not query:
        return []

    langs = languages or list(_LSP_SERVERS.keys())
    results: List[WorkspaceSymbol] = []
    seen: set = set()

    for lang in langs:
        client = manager.get_client(lang, str(project_root))
        if client is None:
            continue
        try:
            items = client.workspace_symbol(query)
        except Exception as e:
            logger.debug("workspace_symbol %s failed: %s", lang, e)
            continue

        for item in items:
            sym = _parse_symbol(item)
            if sym is None:
                continue
            key = (sym.file, sym.line, sym.name)
            if key in seen:
                continue
            seen.add(key)
            results.append(sym)

    return results
