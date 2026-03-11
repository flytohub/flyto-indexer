"""Cross-repo Type Contract checking for flyto-indexer MCP server.

Extracts type schemas from Python (Pydantic, dataclass, TypedDict) and
TypeScript (interface, type alias) definitions, normalizes types across
languages, and compares field-level contracts to detect drift between
producer and consumer projects.
"""

import ast
import re
from typing import Optional

try:
    from ..index_store import load_index, get_symbol_content_text
except ImportError:
    from index_store import load_index, get_symbol_content_text


# =============================================================================
# Type normalization maps
# =============================================================================

_PY_TO_NORMALIZED = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "None": "null",
    "NoneType": "null",
    "dict": "object",
    "list": "array",
    "Any": "any",
}

_TS_TO_NORMALIZED = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
    "undefined": "null",
    "void": "null",
    "any": "any",
    "object": "object",
    "Record": "object",
}


# =============================================================================
# Python type extraction
# =============================================================================

def _extract_python_fields(content: str, class_name: str) -> dict:
    """Extract field schema from a Python class definition using AST.

    Supports Pydantic BaseModel, @dataclass, TypedDict, and plain classes.
    Returns dict with name, model_type, and fields mapping.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {"name": class_name, "model_type": "unknown", "fields": {}, "error": "SyntaxError"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != class_name:
            continue

        # Detect model type from base classes
        model_type = "class"
        base_names = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_names.append(base.id)
            elif isinstance(base, ast.Attribute):
                base_names.append(base.attr)

        if "BaseModel" in base_names:
            model_type = "pydantic"
        elif "TypedDict" in base_names:
            model_type = "typeddict"
        else:
            # Check for @dataclass decorator
            for dec in node.decorator_list:
                dec_name = ""
                if isinstance(dec, ast.Name):
                    dec_name = dec.id
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                    dec_name = dec.func.id
                elif isinstance(dec, ast.Attribute):
                    dec_name = dec.attr
                if dec_name == "dataclass":
                    model_type = "dataclass"
                    break

        fields = {}
        for item in node.body:
            if not isinstance(item, ast.AnnAssign):
                continue
            if not isinstance(item.target, ast.Name):
                continue

            field_name = item.target.id
            try:
                field_type = ast.unparse(item.annotation)
            except Exception:
                field_type = "complex"

            # Detect Optional
            optional = False
            if isinstance(item.annotation, ast.Subscript):
                # Optional[X] case
                if isinstance(item.annotation.value, ast.Name) and item.annotation.value.id == "Optional":
                    optional = True
                # Union[..., None] case
                elif isinstance(item.annotation.value, ast.Name) and item.annotation.value.id == "Union":
                    if isinstance(item.annotation.slice, ast.Tuple):
                        for elt in item.annotation.slice.elts:
                            if isinstance(elt, ast.Constant) and elt.value is None:
                                optional = True
                            elif isinstance(elt, ast.Name) and elt.id == "None":
                                optional = True
            # X | None syntax (Python 3.10+)
            elif isinstance(item.annotation, ast.BinOp) and isinstance(item.annotation.op, ast.BitOr):
                if isinstance(item.annotation.right, ast.Constant) and item.annotation.right.value is None:
                    optional = True
                elif isinstance(item.annotation.right, ast.Name) and item.annotation.right.id == "None":
                    optional = True

            has_default = item.value is not None

            fields[field_name] = {
                "type": field_type,
                "optional": optional,
                "has_default": has_default,
            }

        return {
            "name": class_name,
            "model_type": model_type,
            "fields": fields,
        }

    return {"name": class_name, "model_type": "unknown", "fields": {}, "error": "class not found"}


# =============================================================================
# TypeScript type extraction
# =============================================================================

def _extract_ts_fields(content: str, type_name: str) -> dict:
    """Extract field schema from a TypeScript interface or type alias.

    Uses regex-based parsing. Handles nested braces by counting depth.
    """
    # Try interface first, then type alias
    for pattern_kind, pattern in [
        ("interface", rf'interface\s+{re.escape(type_name)}\s*(?:extends\s+[^{{]+)?\{{'),
        ("type", rf'type\s+{re.escape(type_name)}\s*=\s*\{{'),
    ]:
        match = re.search(pattern, content)
        if not match:
            continue

        # Find the matching closing brace by counting depth
        start = match.end()
        depth = 1
        pos = start
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            pos += 1

        if depth != 0:
            continue

        body = content[start:pos - 1]
        model_type = pattern_kind

        fields = {}
        # Parse each field line
        field_pattern = re.compile(r'(?:readonly\s+)?(\w+)(\?)?:\s*(.+?)(?:;|,)\s*$', re.MULTILINE)
        for field_match in field_pattern.finditer(body):
            field_name = field_match.group(1)
            is_optional = field_match.group(2) == '?'
            field_type = field_match.group(3).strip()

            fields[field_name] = {
                "type": field_type,
                "optional": is_optional,
                "has_default": False,  # TS interfaces don't have defaults
            }

        return {
            "name": type_name,
            "model_type": model_type,
            "fields": fields,
        }

    return {"name": type_name, "model_type": "unknown", "fields": {}, "error": "type not found"}


# =============================================================================
# Type normalization
# =============================================================================

def _normalize_type(type_str: str, lang: str) -> str:
    """Normalize a type string for cross-language comparison.

    Maps Python and TypeScript types to a common representation.
    """
    type_str = type_str.strip()

    if lang == "python":
        # Optional[X] -> X | null
        opt_match = re.match(r'^Optional\[(.+)\]$', type_str)
        if opt_match:
            inner = _normalize_type(opt_match.group(1), lang)
            return f"{inner} | null"

        # Union[X, None] -> X | null
        union_match = re.match(r'^Union\[(.+)\]$', type_str)
        if union_match:
            parts = [p.strip() for p in union_match.group(1).split(',')]
            normalized = []
            for p in parts:
                n = _normalize_type(p, lang)
                if n != "null":
                    normalized.append(n)
            if any(p.strip() in ("None", "NoneType") for p in parts):
                return " | ".join(normalized) + " | null" if normalized else "null"
            return " | ".join(normalized) if normalized else "any"

        # list[X] -> X[]
        list_match = re.match(r'^(?:list|List)\[(.+)\]$', type_str)
        if list_match:
            inner = _normalize_type(list_match.group(1), lang)
            return f"{inner}[]"

        # dict[K, V] -> Record<K, V>
        dict_match = re.match(r'^(?:dict|Dict)\[(.+),\s*(.+)\]$', type_str)
        if dict_match:
            k = _normalize_type(dict_match.group(1), lang)
            v = _normalize_type(dict_match.group(2), lang)
            return f"Record<{k}, {v}>"

        # X | None -> X | null (Python 3.10+ syntax)
        if ' | ' in type_str:
            parts = [p.strip() for p in type_str.split(' | ')]
            normalized = [_normalize_type(p, lang) for p in parts]
            return " | ".join(normalized)

        return _PY_TO_NORMALIZED.get(type_str, type_str)

    elif lang == "typescript":
        # X[] -> X[]
        arr_match = re.match(r'^(.+)\[\]$', type_str)
        if arr_match:
            inner = _normalize_type(arr_match.group(1), lang)
            return f"{inner}[]"

        # Array<X> -> X[]
        arr_generic_match = re.match(r'^Array<(.+)>$', type_str)
        if arr_generic_match:
            inner = _normalize_type(arr_generic_match.group(1), lang)
            return f"{inner}[]"

        # Record<K, V> -> Record<K, V> (already normalized)
        record_match = re.match(r'^Record<(.+),\s*(.+)>$', type_str)
        if record_match:
            k = _normalize_type(record_match.group(1), lang)
            v = _normalize_type(record_match.group(2), lang)
            return f"Record<{k}, {v}>"

        # X | null / X | undefined
        if ' | ' in type_str:
            parts = [p.strip() for p in type_str.split(' | ')]
            normalized = [_normalize_type(p, lang) for p in parts]
            return " | ".join(normalized)

        return _TS_TO_NORMALIZED.get(type_str, type_str)

    return type_str


# =============================================================================
# Schema comparison
# =============================================================================

def _compare_schemas(producer: dict, consumer: dict) -> list:
    """Compare two type schemas field by field.

    Returns list of mismatch dicts with field, issue, producer_value,
    consumer_value, and severity.
    """
    mismatches = []
    producer_fields = producer.get("fields", {})
    consumer_fields = consumer.get("fields", {})

    # Detect language from model_type
    def _lang_for(schema):
        mt = schema.get("model_type", "")
        if mt in ("pydantic", "dataclass", "typeddict", "class"):
            return "python"
        if mt in ("interface", "type"):
            return "typescript"
        return "python"  # default

    producer_lang = _lang_for(producer)
    consumer_lang = _lang_for(consumer)

    all_fields = set(producer_fields.keys()) | set(consumer_fields.keys())

    for field in sorted(all_fields):
        in_producer = field in producer_fields
        in_consumer = field in consumer_fields

        if in_producer and not in_consumer:
            mismatches.append({
                "field": field,
                "issue": "missing_in_consumer",
                "producer_value": producer_fields[field]["type"],
                "consumer_value": None,
                "severity": "info",
            })
        elif not in_producer and in_consumer:
            mismatches.append({
                "field": field,
                "issue": "missing_in_producer",
                "producer_value": None,
                "consumer_value": consumer_fields[field]["type"],
                "severity": "error",
            })
        else:
            # Both have the field — compare types
            p_type = _normalize_type(producer_fields[field]["type"], producer_lang)
            c_type = _normalize_type(consumer_fields[field]["type"], consumer_lang)

            if p_type != c_type:
                mismatches.append({
                    "field": field,
                    "issue": "type_mismatch",
                    "producer_value": producer_fields[field]["type"],
                    "consumer_value": consumer_fields[field]["type"],
                    "severity": "error",
                })

            # Optionality mismatch: producer says optional but consumer says required
            p_optional = producer_fields[field].get("optional", False)
            c_optional = consumer_fields[field].get("optional", False)
            if p_optional and not c_optional:
                mismatches.append({
                    "field": field,
                    "issue": "optionality_mismatch",
                    "producer_value": "optional",
                    "consumer_value": "required",
                    "severity": "warning",
                })

    return mismatches


# =============================================================================
# Helper: resolve symbol_id (same pattern as references.py)
# =============================================================================

def _resolve_symbol(symbols: dict, symbol_id: str) -> tuple:
    """Resolve a partial symbol_id to (resolved_id, symbol_data).

    Returns (None, None) if not found.
    """
    if symbol_id in symbols:
        return symbol_id, symbols[symbol_id]

    # Try exact name match
    name_matches = []
    partial_matches = []
    for sid, sym in symbols.items():
        sym_name = sym.get("name", "")
        if sym_name == symbol_id:
            name_matches.append(sid)
        elif symbol_id in sid or sid.endswith(symbol_id):
            partial_matches.append(sid)

    if name_matches:
        # Prefer classes/interfaces
        for sid in name_matches:
            sym = symbols[sid]
            if sym.get("type") in ("class", "interface", "type"):
                return sid, sym
        return name_matches[0], symbols[name_matches[0]]
    elif partial_matches:
        return partial_matches[0], symbols[partial_matches[0]]

    return None, None


def _detect_language(path: str) -> str:
    """Detect language from file extension."""
    if path.endswith((".py",)):
        return "python"
    if path.endswith((".ts", ".tsx")):
        return "typescript"
    if path.endswith((".js", ".jsx")):
        return "typescript"  # treat JS same as TS for type extraction
    return "unknown"


# =============================================================================
# Tool 1: extract_type_schema
# =============================================================================

def extract_type_schema(symbol_id: str) -> dict:
    """Extract the field-level type schema from a class, interface, or type alias.

    Resolves the symbol from the index, detects language, and parses fields.
    Works with Pydantic BaseModel, @dataclass, TypedDict, and TS interfaces.
    """
    index = load_index()
    symbols = index.get("symbols", {})

    resolved_id, sym = _resolve_symbol(symbols, symbol_id)
    if not sym:
        return {"error": f"Symbol not found: {symbol_id}"}

    path = sym.get("path", "")
    lang = _detect_language(path)
    content = get_symbol_content_text(resolved_id, sym)

    if not content:
        return {"error": f"No content for symbol: {resolved_id}"}

    name = sym.get("name", "")

    if lang == "python":
        schema = _extract_python_fields(content, name)
    elif lang == "typescript":
        schema = _extract_ts_fields(content, name)
    else:
        return {"error": f"Unsupported language for type extraction: {lang}", "path": path}

    return {
        "symbol_id": resolved_id,
        "name": name,
        "path": path,
        "language": lang,
        **schema,
    }


# =============================================================================
# Tool 2: check_api_contracts
# =============================================================================

def check_api_contracts(source_project: str = None, consumer_project: str = None) -> dict:
    """Check type contracts between API producers and consumers.

    For each API endpoint in source_project:
    1. Find the handler and extract its return type schema
    2. Find consumers in consumer_project that reference the endpoint
    3. Compare schemas to detect mismatches
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    contracts = []

    # Find API symbols
    api_symbols = []
    for sid, sym in symbols.items():
        if sym.get("type") != "api":
            continue
        sym_project = sid.split(":")[0] if ":" in sid else ""
        if source_project and source_project.lower() not in sym_project.lower():
            continue
        api_symbols.append((sid, sym, sym_project))

    for api_sid, api_sym, api_project in api_symbols:
        api_name = api_sym.get("name", "")
        api_path = api_sym.get("path", "")
        api_line = api_sym.get("start_line", 0)

        # Find handler function near the API endpoint (same file, close line range)
        handler_sym = None
        handler_sid = None
        for sid, sym in symbols.items():
            if sym.get("path") != api_path:
                continue
            if sym.get("type") not in ("function", "method"):
                continue
            sym_start = sym.get("start_line", 0)
            # Handler is usually right after the route decorator
            if 0 < sym_start - api_line <= 5:
                handler_sym = sym
                handler_sid = sid
                break

        if not handler_sym:
            continue

        # Extract return type from handler content
        handler_content = get_symbol_content_text(handler_sid, handler_sym)
        if not handler_content:
            continue

        # Look for return type annotation or response_model
        return_type_name = None

        # Check -> ReturnType annotation
        ret_match = re.search(r'->\s*(\w+)', handler_content)
        if ret_match:
            return_type_name = ret_match.group(1)

        # Check response_model=TypeName in decorator
        if not return_type_name:
            resp_match = re.search(r'response_model\s*=\s*(\w+)', handler_content)
            if resp_match:
                return_type_name = resp_match.group(1)

        if not return_type_name:
            continue

        # Find the return type in the index
        producer_schema = None
        for sid, sym in symbols.items():
            if sym.get("name") == return_type_name:
                sym_project_check = sid.split(":")[0] if ":" in sid else ""
                if sym_project_check == api_project:
                    content = get_symbol_content_text(sid, sym)
                    if content:
                        lang = _detect_language(sym.get("path", ""))
                        if lang == "python":
                            producer_schema = _extract_python_fields(content, return_type_name)
                        elif lang == "typescript":
                            producer_schema = _extract_ts_fields(content, return_type_name)
                        if producer_schema and producer_schema.get("fields"):
                            break

        if not producer_schema or not producer_schema.get("fields"):
            continue

        # Find consumers: look for references to this API endpoint from other projects
        consumers = []
        # Search reverse_index for the handler or API symbol
        consumer_refs = set()
        for ref_id in reverse_index.get(handler_sid, []):
            ref_project = ref_id.split(":")[0] if ":" in ref_id else ""
            if ref_project == api_project:
                continue
            if consumer_project and consumer_project.lower() not in ref_project.lower():
                continue
            consumer_refs.add((ref_id, ref_project))

        for ref_id in reverse_index.get(api_sid, []):
            ref_project = ref_id.split(":")[0] if ":" in ref_id else ""
            if ref_project == api_project:
                continue
            if consumer_project and consumer_project.lower() not in ref_project.lower():
                continue
            consumer_refs.add((ref_id, ref_project))

        for ref_id, ref_project in consumer_refs:
            ref_sym = symbols.get(ref_id, {})
            ref_content = get_symbol_content_text(ref_id, ref_sym)
            if not ref_content:
                continue

            # Try to find a type annotation at the consumer site
            # Look for TypeScript interfaces/types that match the response pattern
            ref_path = ref_sym.get("path", "")
            ref_lang = _detect_language(ref_path)

            # Search for interface/type in the same file that could be the consumer type
            consumer_type = None
            for csid, csym in symbols.items():
                if csym.get("path") != ref_path:
                    continue
                if csym.get("type") not in ("interface", "type", "class"):
                    continue
                c_content = get_symbol_content_text(csid, csym)
                if not c_content:
                    continue
                c_name = csym.get("name", "")
                if ref_lang == "typescript":
                    consumer_type = _extract_ts_fields(c_content, c_name)
                elif ref_lang == "python":
                    consumer_type = _extract_python_fields(c_content, c_name)
                if consumer_type and consumer_type.get("fields"):
                    mismatches = _compare_schemas(producer_schema, consumer_type)
                    if mismatches:
                        consumers.append({
                            "project": ref_project,
                            "type": consumer_type,
                            "symbol_id": csid,
                            "mismatches": mismatches,
                        })

        if consumers:
            contracts.append({
                "endpoint": api_name,
                "producer_project": api_project,
                "producer_type": producer_schema,
                "handler": handler_sid,
                "consumers": consumers,
            })

    mismatches_found = sum(
        len(c.get("mismatches", []))
        for contract in contracts
        for c in contract.get("consumers", [])
    )

    return {
        "contracts_checked": len(api_symbols),
        "mismatches_found": mismatches_found,
        "contracts": contracts,
    }


# =============================================================================
# Tool 3: contract_drift
# =============================================================================

def contract_drift(project: str = None) -> dict:
    """Detect type schema drift between projects.

    Finds Pydantic models, dataclasses, and TypeScript interfaces that have
    mirror types (same name) in other projects, and compares their schemas.
    """
    index = load_index()
    symbols = index.get("symbols", {})

    # Collect type-like symbols grouped by name
    type_symbols = {}  # name -> [(sid, sym, project)]
    for sid, sym in symbols.items():
        sym_type = sym.get("type", "")
        if sym_type not in ("class", "interface", "type"):
            continue
        sym_project = sid.split(":")[0] if ":" in sid else ""
        if project and project.lower() not in sym_project.lower():
            continue
        name = sym.get("name", "")
        if not name:
            continue
        if name not in type_symbols:
            type_symbols[name] = []
        type_symbols[name].append((sid, sym, sym_project))

    drifts = []
    types_checked = 0

    for name, entries in type_symbols.items():
        # Only check types that exist in multiple projects
        projects_seen = set(e[2] for e in entries)
        if len(projects_seen) < 2:
            continue

        # Extract schemas for each entry
        schemas = []
        for sid, sym, sym_project in entries:
            content = get_symbol_content_text(sid, sym)
            if not content:
                continue
            path = sym.get("path", "")
            lang = _detect_language(path)
            if lang == "python":
                schema = _extract_python_fields(content, name)
            elif lang == "typescript":
                schema = _extract_ts_fields(content, name)
            else:
                continue
            if schema.get("fields"):
                schemas.append((sid, sym_project, schema))

        if len(schemas) < 2:
            continue

        types_checked += 1

        # Compare each pair (first entry is "source", rest are "consumers")
        source_sid, source_project, source_schema = schemas[0]
        for consumer_sid, consumer_proj, consumer_schema in schemas[1:]:
            if consumer_proj == source_project:
                continue
            mismatches = _compare_schemas(source_schema, consumer_schema)
            if mismatches:
                drifts.append({
                    "source": {
                        "project": source_project,
                        "type": name,
                        "symbol_id": source_sid,
                    },
                    "consumer": {
                        "project": consumer_proj,
                        "type": name,
                        "symbol_id": consumer_sid,
                    },
                    "mismatches": mismatches,
                })

    return {
        "types_checked": types_checked,
        "drifts_found": len(drifts),
        "drifts": drifts,
    }
