"""LSP integration for flyto-indexer — optional type-aware code intelligence.

Responsibilities of this package:
  - `manager`:           pool & lifecycle of language-server subprocesses
  - `client`:            JSON-RPC transport + textDocument/* wrappers
  - `protocol`:          URI / Position / Range dataclasses + UTF-16 helpers
  - `mapper`:            flyto symbol dict → LSP position
  - `cache`:             mtime-keyed memoization of LSP responses
  - `resolver`:          import → target file precision layer (Phase 1)
  - `call_graph`:        call-hierarchy traversal (Phase 3)
  - `workspace_symbols`: workspace/symbol candidate search (Phase 3)

Every public helper here checks LSPManager.enabled and degrades cleanly —
callers can always pretend LSP is available and fall back on empty results.
"""

from .manager import LSPManager
from .cache import clear_cache, get_cache
from .resolver import lsp_available_for, resolve_import_via_lsp
from .call_graph import CallEdge, incoming_calls, outgoing_calls
from .workspace_symbols import WorkspaceSymbol, query_symbol

__all__ = [
    "LSPManager",
    "clear_cache", "get_cache",
    "lsp_available_for", "resolve_import_via_lsp",
    "CallEdge", "incoming_calls", "outgoing_calls",
    "WorkspaceSymbol", "query_symbol",
]
