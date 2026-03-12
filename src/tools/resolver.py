"""Shared symbol resolution — single implementation used by all reference/impact tools."""


# Preferred types for resolution (higher-signal symbols first)
_PREFERRED_TYPES = {"composable", "function", "class", "component"}


def resolve_symbol(symbol_id: str, symbols: dict) -> str:
    """Resolve a symbol_id (exact, name, or partial match) to a canonical symbol_id.

    Resolution order:
    1. Exact match in symbols dict
    2. Exact name match → prefer composable/function/class/component types
    3. Partial match (symbol_id is substring of a symbol key, or key ends with it)

    Returns the resolved symbol_id, or the original if no match found.
    """
    if symbol_id in symbols:
        return symbol_id

    name_matches = []
    partial_matches = []

    for sid, sym in symbols.items():
        sym_name = sym.get("name", "")
        if sym_name == symbol_id:
            name_matches.append(sid)
        elif symbol_id in sid or sid.endswith(symbol_id):
            partial_matches.append(sid)

    if name_matches:
        # Prefer composable/function/class/component over methods
        for sid in name_matches:
            if symbols[sid].get("type") in _PREFERRED_TYPES:
                return sid
        return name_matches[0]

    if partial_matches:
        return partial_matches[0]

    return symbol_id


def get_dedup_key(source_id: str) -> str:
    """Build a cross-project dedup key: basename:type:name."""
    parts = source_id.split(":")
    if len(parts) >= 4:
        basename = parts[1].rsplit("/", 1)[-1]
        return f"{basename}:{parts[2]}:{parts[3]}"
    return source_id
