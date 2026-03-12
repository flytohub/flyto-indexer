"""Shared symbol resolution — single implementation used by all reference/impact tools."""


# Preferred types for resolution (higher-signal symbols first)
_PREFERRED_TYPES = {"composable", "function", "class", "component"}


def resolve_symbol(symbol_id: str, symbols: dict) -> str:
    """Resolve a symbol_id (exact, path:name, name, or partial match) to a canonical symbol_id.

    Resolution order:
    1. Exact match in symbols dict
    2. Path:name hint (e.g., "src/tools/maintenance.py:_is_potentially_dead")
       - Splits on ":" when exactly 2 parts and not already an exact key
       - Supports "ClassName.method_name" in the name portion
       - Prefers composable/function/class/component types
    3. Exact name match → prefer composable/function/class/component types
    4. Partial match (symbol_id is substring of a symbol key, or key ends with it)

    Returns the resolved symbol_id, or the original if no match found.
    """
    if symbol_id in symbols:
        return symbol_id

    # 2. Path:name hint (e.g., "src/tools/maintenance.py:_is_potentially_dead")
    if ":" in symbol_id:
        parts = symbol_id.split(":")
        if len(parts) == 2:
            path_hint, name_hint = parts
            path_name_matches = []
            for sid, sym in symbols.items():
                sym_name = sym.get("name", "")
                sym_path = sym.get("path", "")
                # Handle "ClassName.method_name" in name_hint
                if "." in name_hint:
                    dotted = sym.get("parent", "") + "." + sym_name if sym.get("parent") else sym_name
                    name_match = dotted == name_hint or sym_name == name_hint
                else:
                    name_match = sym_name == name_hint
                if name_match and path_hint in sym_path:
                    path_name_matches.append(sid)
            if path_name_matches:
                # Prefer composable/function/class/component
                for sid in path_name_matches:
                    if symbols[sid].get("type") in _PREFERRED_TYPES:
                        return sid
                return path_name_matches[0]

    # 3. Name match / partial match (existing logic)
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
