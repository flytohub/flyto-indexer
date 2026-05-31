"""
Type-aware filter for taint analysis.

When the LSP is available, we can ask the language server what type a source
expression evaluates to. If the type is numeric, boolean, or a well-typed
structured value (not string-ish), the value cannot carry the kind of taint
our sinks care about (SQL injection / XSS / command injection are all
string-based), and we can suppress the finding as a type-gate false positive.

Defaults to "taint it" (permissive) when the LSP is unavailable or the type
can't be parsed — this filter only ever *removes* findings, never adds them.
"""

import logging
import re
from pathlib import Path
from typing import Optional

try:
    from ..lsp.cache import CacheKey, get_cache
    from ..lsp.manager import LSPManager
    from ..lsp.protocol import path_to_uri, utf16_offset
except ImportError:
    from lsp.cache import CacheKey, get_cache
    from lsp.manager import LSPManager
    from lsp.protocol import path_to_uri, utf16_offset

logger = logging.getLogger("flyto-indexer.type_filter")


# Types that cannot carry string-injection taint. If hover tells us the
# expression is one of these, the source is a false positive.
_NON_TAINTABLE_TYPES = {
    # Python
    "int", "float", "bool", "complex", "bytes_literal",
    "Decimal", "datetime", "date", "time", "timedelta",
    "UUID", "uuid.UUID",
    # TypeScript / JavaScript
    "number", "boolean", "bigint", "Date", "symbol",
    # Go — best-effort literal types
    "int", "int32", "int64", "uint", "uint32", "uint64",
    "float32", "float64", "bool", "time.Time", "time.Duration",
}


# Types that definitely CAN carry taint (belt-and-braces allowlist). When
# hover returns one of these we keep the finding even if parsing is fuzzy.
_TAINTABLE_TYPES = {
    "str", "string", "bytes", "bytearray",
    "String", "Buffer",
    "dict", "list", "tuple", "set", "frozenset",
    "object", "any", "Any", "unknown",
    "Dict", "List", "Tuple", "Set",
    "Record", "Map",
    "interface{}",
}


def _parse_type_from_hover(hover: str) -> Optional[str]:
    """Extract the primary type name from an LSP hover payload.

    Hover returns markdown that varies per server:
      pyright : "(variable) x: int"
      tsserver: "const x: number"
      gopls   : "var x int"

    We peel off the ``` fences, pick the first line that reads like a type
    declaration, and return the rightmost type token on that line.
    """
    if not hover:
        return None

    # Strip markdown code fences and language tags
    text = hover.replace("\r\n", "\n")
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = text.replace("```", "").strip()

    # Look at the first non-empty line
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue

        # Prefer the last ": Type" suffix (common in Python/TS)
        m = re.search(r":\s*([A-Za-z_][\w\.\[\]<>,\s|]*)\s*(?:=.*)?$", line)
        if m:
            return _normalize_type(m.group(1))

        # Fallback: "var name Type" (Go)
        m = re.search(r"\b(?:var|const|func)\s+\S+\s+([A-Za-z_][\w\.\[\]]*)", line)
        if m:
            return _normalize_type(m.group(1))
    return None


def _normalize_type(raw: str) -> str:
    """Trim generics, optional markers, unions — keep the root type name."""
    if not raw:
        return ""
    t = raw.strip()
    # Drop optional/union: "int | None" -> "int"
    if "|" in t:
        t = t.split("|", 1)[0].strip()
    # Drop generics: "List[str]" -> "List"
    for opener in ("[", "<"):
        if opener in t:
            t = t.split(opener, 1)[0].strip()
    # Drop ellipsis / trailing punctuation
    t = t.rstrip(".,;")
    return t


def is_type_taintable(type_name: str) -> Optional[bool]:
    """Return True if the type can carry string-injection taint, False if it can't,
    or None if we can't decide (be permissive: treat unknown as taintable)."""
    if not type_name:
        return None
    root = _normalize_type(type_name)
    if not root:
        return None
    if root in _TAINTABLE_TYPES:
        return True
    if root in _NON_TAINTABLE_TYPES:
        return False
    # Type not in either list — unknown, be permissive
    return None


def query_type_at(
    project_root: Path,
    source_file: Path,
    line_num_0based: int,
    col_0based: int,
) -> Optional[str]:
    """Ask LSP for the type at a position. Returns the normalized type or None."""
    manager = LSPManager.get_instance()
    if not manager._enabled:
        return None

    language = manager.language_for_path(str(source_file))
    if not language:
        return None

    client = manager.get_client(language, str(project_root))
    if client is None:
        return None

    uri = path_to_uri(str(source_file))

    # Read file content to compute UTF-16 col
    try:
        with open(source_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None
    lines = content.split("\n")
    if not (0 <= line_num_0based < len(lines)):
        return None
    utf16_col = utf16_offset(lines[line_num_0based], col_0based)

    # Cache
    cache = get_cache()
    key = CacheKey(method="hover", uri=uri, line=line_num_0based, character=utf16_col)
    hit = cache.get(key)
    if hit is not None:
        return hit if isinstance(hit, str) else None

    # Ensure file is opened for the server
    try:
        try:
            from ..lsp.resolver import _ensure_open  # reuse open memo
        except ImportError:
            from lsp.resolver import _ensure_open
    except ImportError:
        _ensure_open = None

    if _ensure_open:
        _LANG_ID = {"python": "python", "typescript": "typescript", "go": "go", "rust": "rust"}
        _ensure_open(client, uri, _LANG_ID.get(language, language), str(source_file))

    try:
        hover = client.text_document_hover(uri, line_num_0based, utf16_col)
    except Exception as e:
        logger.debug("hover failed: %s", e)
        cache.set(key, False)
        return None

    parsed = _parse_type_from_hover(hover or "")
    cache.set(key, parsed if parsed else False)
    return parsed


def source_is_taintable(
    project_root: Path,
    source_file: Path,
    line_num_0based: int,
    col_0based: int,
) -> bool:
    """Decide whether a taint source at this position produces string-ish data.

    Permissive default: when LSP is off / type unknown, return True.
    Only returns False when we're confident the type is non-taintable.
    """
    type_name = query_type_at(project_root, source_file, line_num_0based, col_0based)
    if type_name is None:
        return True
    verdict = is_type_taintable(type_name)
    return verdict is not False  # True or None both permissive
