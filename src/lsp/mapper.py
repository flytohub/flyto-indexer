"""Bridge between flyto symbols and LSP positions."""

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from .protocol import Location, uri_to_path, path_to_uri

logger = logging.getLogger("flyto-indexer.lsp.mapper")


def find_symbol_at_line(
    content: str, name: str, target_line: int
) -> Optional[Tuple[int, int]]:
    """Find the (line, column) of a symbol name at or near target_line.

    Searches target_line first, then expands +/-5 lines.
    Returns zero-based (line, col) or None.
    """
    lines = content.split("\n")
    if not lines or not name:
        return None

    # Build a pattern that matches the symbol name as a word
    pattern = re.compile(r"\b" + re.escape(name) + r"\b")

    # Search target_line first, then expand outward
    search_order = [target_line]
    for offset in range(1, 6):
        search_order.append(target_line + offset)
        search_order.append(target_line - offset)

    for line_idx in search_order:
        if 0 <= line_idx < len(lines):
            m = pattern.search(lines[line_idx])
            if m:
                return (line_idx, m.start())

    return None


def symbol_to_lsp_position(
    symbol_data: dict, project_root: str
) -> Optional[Tuple[str, int, int]]:
    """Map a flyto symbol to an LSP position (uri, line, col).

    Reads the source file and finds the symbol name near start_line.
    Returns None if the file cannot be read or symbol not found.
    """
    path = symbol_data.get("path", "")
    name = symbol_data.get("name", "")
    start_line = symbol_data.get("start_line", 0)

    if not path or not name:
        return None

    # Build absolute path
    abs_path = os.path.join(project_root, path)
    if not os.path.isfile(abs_path):
        # Try path as-is (might already be absolute)
        if not os.path.isfile(path):
            return None
        abs_path = path

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (OSError, IOError):
        return None

    # start_line in flyto is 1-based, LSP is 0-based
    lsp_line = max(0, start_line - 1)

    pos = find_symbol_at_line(content, name, lsp_line)
    if pos is None:
        return None

    uri = path_to_uri(abs_path)
    return (uri, pos[0], pos[1])


def lsp_locations_to_references(
    locations: List[Location], index: dict
) -> List[Dict]:
    """Convert LSP Location objects to flyto reference dicts.

    Each returned dict matches the format used in find_references():
      - type, from_path, from_symbol, from_name, line, confidence, source
    """
    symbols = index.get("symbols", {})
    files = index.get("files", {})

    references = []
    for loc in locations:
        file_path = uri_to_path(loc.uri)
        line = loc.range.start.line + 1  # LSP is 0-based, flyto is 1-based

        # Try to find the containing symbol in the index
        from_symbol = ""
        from_name = ""
        best_match = None
        best_distance = float("inf")

        # Look through symbols to find one containing this location
        for sym_id, sym in symbols.items():
            sym_path = sym.get("path", "")
            if not sym_path:
                continue
            # Match by filename (handles relative vs absolute)
            if not (file_path.endswith(sym_path) or sym_path.endswith(file_path)
                    or os.path.basename(file_path) == os.path.basename(sym_path)):
                continue

            sym_start = sym.get("start_line", 0)
            sym_end = sym.get("end_line", sym_start + 100)
            if sym_start <= line <= sym_end:
                distance = line - sym_start
                if distance < best_distance:
                    best_distance = distance
                    best_match = sym_id

        if best_match:
            from_symbol = best_match
            from_name = symbols[best_match].get("name", "")

        # Normalize path: try to get relative path from index
        rel_path = file_path
        for fpath in files:
            if file_path.endswith(fpath):
                rel_path = fpath
                break

        references.append({
            "type": "usage",
            "from_symbol": from_symbol,
            "from_path": rel_path,
            "from_name": from_name,
            "line": line,
            "confidence": "high",
            "source": "lsp",
        })

    return references
