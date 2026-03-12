"""Cross-repo Type Contract checking for flyto-indexer MCP server.

Extracts type schemas from Python (Pydantic, dataclass, TypedDict) and
TypeScript (interface, type alias) definitions, normalizes types across
languages, and compares field-level contracts to detect drift between
producer and consumer projects.

v2.0 improvements:
- Bracket-depth-aware generic splitting (no more broken Union[X, Dict[str, int]])
- Annotated[T, ...] unwrapping
- Literal type normalization
- Pydantic Field(alias=...) extraction
- Model inheritance chain walking
- TypeScript utility types (Pick, Omit, Partial, Required)
- Better return type capture for generics (Response[User])
- Cross-file consumer type lookup
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
    "tuple": "array",
    "set": "array",
    "Any": "any",
    "bytes": "string",
}

_TS_TO_NORMALIZED = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
    "undefined": "null",
    "void": "null",
    "any": "any",
    "unknown": "any",
    "never": "never",
    "object": "object",
    "Record": "object",
}


# =============================================================================
# Generic-aware type splitting
# =============================================================================

def _split_generic_args(type_str: str, delimiter: str = ',') -> list:
    """Split type arguments respecting bracket depth.

    Handles nested generics like Union[Dict[str, int], List[str]].
    """
    parts = []
    depth = 0
    current = []
    for ch in type_str:
        if ch in ('[', '<', '('):
            depth += 1
            current.append(ch)
        elif ch in (']', '>', ')'):
            depth -= 1
            current.append(ch)
        elif ch == delimiter and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    remainder = ''.join(current).strip()
    if remainder:
        parts.append(remainder)
    return parts


# =============================================================================
# Python type extraction
# =============================================================================

def _extract_field_alias(item: "ast.AnnAssign") -> Optional[str]:
    """Extract Pydantic Field(alias='...') or Field(serialization_alias='...')."""
    if item.value is None:
        return None
    if not isinstance(item.value, ast.Call):
        return None

    # Check if the call is Field(...)
    func = item.value.func
    func_name = ""
    if isinstance(func, ast.Name):
        func_name = func.id
    elif isinstance(func, ast.Attribute):
        func_name = func.attr
    if func_name != "Field":
        return None

    # Check keyword arguments for alias
    for kw in item.value.keywords:
        if kw.arg in ("alias", "serialization_alias"):
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
    return None


def _unwrap_annotated(annotation: ast.expr) -> ast.expr:
    """Unwrap Annotated[T, ...] to just T."""
    if not isinstance(annotation, ast.Subscript):
        return annotation
    if not isinstance(annotation.value, ast.Name):
        return annotation
    if annotation.value.id != "Annotated":
        return annotation
    # Annotated[T, ...] — extract T (first element of the tuple slice)
    if isinstance(annotation.slice, ast.Tuple) and annotation.slice.elts:
        return annotation.slice.elts[0]
    return annotation.slice


def _extract_python_fields(content: str, class_name: str, all_symbols: dict = None) -> dict:
    """Extract field schema from a Python class definition using AST.

    Supports Pydantic BaseModel, @dataclass, TypedDict, and plain classes.
    Walks inheritance chain if all_symbols is provided.
    Returns dict with name, model_type, fields mapping, and bases list.
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

        # Walk inheritance chain: merge parent fields first
        if all_symbols and base_names:
            for base_name in base_names:
                if base_name in ("BaseModel", "TypedDict", "object"):
                    continue
                parent_fields = _resolve_parent_fields(base_name, all_symbols)
                if parent_fields:
                    fields.update(parent_fields)

        for item in node.body:
            if not isinstance(item, ast.AnnAssign):
                continue
            if not isinstance(item.target, ast.Name):
                continue

            field_name = item.target.id

            # Unwrap Annotated[T, Field(...)] → T
            annotation = _unwrap_annotated(item.annotation)

            try:
                field_type = ast.unparse(annotation)
            except Exception:
                field_type = "complex"

            # Detect Optional
            optional = _is_optional_annotation(annotation)
            has_default = item.value is not None

            # Extract Pydantic Field alias
            alias = _extract_field_alias(item)

            field_info = {
                "type": field_type,
                "optional": optional,
                "has_default": has_default,
            }
            if alias:
                field_info["alias"] = alias

            fields[field_name] = field_info

        return {
            "name": class_name,
            "model_type": model_type,
            "fields": fields,
            "bases": base_names,
        }

    return {"name": class_name, "model_type": "unknown", "fields": {}, "error": "class not found"}


def _is_optional_annotation(annotation: ast.expr) -> bool:
    """Check if an AST annotation represents an optional type."""
    if isinstance(annotation, ast.Subscript):
        if isinstance(annotation.value, ast.Name):
            if annotation.value.id == "Optional":
                return True
            if annotation.value.id == "Union":
                if isinstance(annotation.slice, ast.Tuple):
                    for elt in annotation.slice.elts:
                        if isinstance(elt, ast.Constant) and elt.value is None:
                            return True
                        elif isinstance(elt, ast.Name) and elt.id == "None":
                            return True
    elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        # X | None
        if isinstance(annotation.right, ast.Constant) and annotation.right.value is None:
            return True
        elif isinstance(annotation.right, ast.Name) and annotation.right.id == "None":
            return True
        # None | X
        if isinstance(annotation.left, ast.Constant) and annotation.left.value is None:
            return True
        elif isinstance(annotation.left, ast.Name) and annotation.left.id == "None":
            return True
    return False


def _resolve_parent_fields(base_name: str, all_symbols: dict) -> dict:
    """Resolve parent class fields by looking up base_name in the index."""
    for sid, sym in all_symbols.items():
        if sym.get("name") != base_name:
            continue
        if sym.get("type") not in ("class", "interface", "type"):
            continue
        content = get_symbol_content_text(sid, sym)
        if not content:
            continue
        path = sym.get("path", "")
        lang = _detect_language(path)
        if lang == "python":
            parent = _extract_python_fields(content, base_name)
        elif lang == "typescript":
            parent = _extract_ts_fields(content, base_name)
        else:
            continue
        return parent.get("fields", {})
    return {}


# =============================================================================
# TypeScript type extraction
# =============================================================================

def _extract_ts_fields(content: str, type_name: str, all_symbols: dict = None) -> dict:
    """Extract field schema from a TypeScript interface or type alias.

    Handles:
    - interface X extends Y { ... }
    - type X = { ... }
    - Utility types: Pick<T, K>, Omit<T, K>, Partial<T>, Required<T>
    """
    # Check for utility type aliases: type X = Pick<Base, 'a' | 'b'>
    util_match = re.search(
        rf'type\s+{re.escape(type_name)}\s*=\s*(Pick|Omit|Partial|Required|Readonly)\s*<',
        content
    )
    if util_match:
        return _extract_ts_utility_type(content, type_name, util_match, all_symbols)

    # Check for intersection type: type X = A & B & { ... }
    intersect_match = re.search(
        rf'type\s+{re.escape(type_name)}\s*=\s*(.+)',
        content
    )
    if intersect_match and '&' in intersect_match.group(1) and '{' not in intersect_match.group(1).split('&')[0]:
        return _extract_ts_intersection(content, type_name, intersect_match, all_symbols)

    # Try interface first, then type alias
    extends_bases = []
    for pattern_kind, pattern in [
        ("interface", rf'interface\s+{re.escape(type_name)}\s*(?:extends\s+([^{{]+))?\{{'),
        ("type", rf'type\s+{re.escape(type_name)}\s*(?:<[^>]*>)?\s*=\s*\{{'),
    ]:
        match = re.search(pattern, content)
        if not match:
            continue

        # Extract extends clause for interfaces
        if pattern_kind == "interface" and match.group(1):
            extends_bases = [b.strip() for b in match.group(1).split(',')]

        # Find the matching closing brace
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

        # Merge parent fields from extends
        if all_symbols and extends_bases:
            for base in extends_bases:
                base_clean = base.strip().split('<')[0]  # Strip generics
                parent_fields = _resolve_parent_fields(base_clean, all_symbols)
                if parent_fields:
                    fields.update(parent_fields)

        # Parse each field line — handle multi-line fields with bracket depth
        field_pattern = re.compile(
            r'(?:readonly\s+)?(\w+)(\?)?:\s*(.+?)(?:;|,)\s*$',
            re.MULTILINE
        )
        for field_match in field_pattern.finditer(body):
            field_name = field_match.group(1)
            is_optional = field_match.group(2) == '?'
            field_type = field_match.group(3).strip()

            fields[field_name] = {
                "type": field_type,
                "optional": is_optional,
                "has_default": False,
            }

        return {
            "name": type_name,
            "model_type": model_type,
            "fields": fields,
            "bases": extends_bases,
        }

    return {"name": type_name, "model_type": "unknown", "fields": {}, "error": "type not found"}


def _extract_ts_utility_type(content: str, type_name: str, match, all_symbols: dict = None) -> dict:
    """Extract fields from TypeScript utility type (Pick, Omit, Partial, Required)."""
    util_name = match.group(1)
    # Extract the generic args: Pick<Base, 'a' | 'b'>
    start = match.end()  # After the '<'
    depth = 1
    pos = start
    while pos < len(content) and depth > 0:
        if content[pos] == '<':
            depth += 1
        elif content[pos] == '>':
            depth -= 1
        pos += 1

    if depth != 0:
        return {"name": type_name, "model_type": "type", "fields": {}, "error": "unclosed generic"}

    args_str = content[start:pos - 1]
    args = _split_generic_args(args_str)

    if not args:
        return {"name": type_name, "model_type": "type", "fields": {}, "error": "no args"}

    base_type_name = args[0].strip()

    # Resolve base type fields
    base_fields = {}
    if all_symbols:
        base_fields = _resolve_parent_fields(base_type_name, all_symbols)

    if not base_fields:
        return {
            "name": type_name,
            "model_type": "type",
            "fields": {},
            "utility": util_name,
            "base": base_type_name,
        }

    if util_name == "Partial":
        # All fields become optional
        fields = {}
        for k, v in base_fields.items():
            fields[k] = {**v, "optional": True}
        return {"name": type_name, "model_type": "type", "fields": fields}

    if util_name == "Required":
        fields = {}
        for k, v in base_fields.items():
            fields[k] = {**v, "optional": False}
        return {"name": type_name, "model_type": "type", "fields": fields}

    if util_name in ("Pick", "Omit") and len(args) >= 2:
        # Extract key names from the second arg: 'a' | 'b' or "a" | "b"
        keys_str = args[1].strip()
        keys = set()
        for k in re.findall(r"['\"](\w+)['\"]", keys_str):
            keys.add(k)

        if util_name == "Pick":
            fields = {k: v for k, v in base_fields.items() if k in keys}
        else:  # Omit
            fields = {k: v for k, v in base_fields.items() if k not in keys}
        return {"name": type_name, "model_type": "type", "fields": fields}

    return {"name": type_name, "model_type": "type", "fields": base_fields}


def _extract_ts_intersection(content: str, type_name: str, match, all_symbols: dict = None) -> dict:
    """Extract fields from TypeScript intersection type: type X = A & B."""
    rhs = match.group(1).strip().rstrip(';')
    parts = [p.strip() for p in rhs.split('&')]

    fields = {}
    for part in parts:
        part_name = part.split('<')[0].strip()
        if part_name.startswith('{'):
            # Inline object type — try to parse fields from it
            inline_fields = _parse_inline_ts_fields(part)
            fields.update(inline_fields)
        elif all_symbols:
            parent_fields = _resolve_parent_fields(part_name, all_symbols)
            if parent_fields:
                fields.update(parent_fields)

    return {"name": type_name, "model_type": "type", "fields": fields}


def _parse_inline_ts_fields(body: str) -> dict:
    """Parse fields from an inline TypeScript object type like { a: string; b: number }."""
    # Strip outer braces
    body = body.strip()
    if body.startswith('{'):
        body = body[1:]
    if body.endswith('}'):
        body = body[:-1]

    fields = {}
    pattern = re.compile(r'(?:readonly\s+)?(\w+)(\?)?:\s*(.+?)(?:;|,|$)', re.MULTILINE)
    for m in pattern.finditer(body):
        fields[m.group(1)] = {
            "type": m.group(3).strip(),
            "optional": m.group(2) == '?',
            "has_default": False,
        }
    return fields


# =============================================================================
# Type normalization
# =============================================================================

def _normalize_type(type_str: str, lang: str) -> str:
    """Normalize a type string for cross-language comparison.

    Maps Python and TypeScript types to a common representation.
    Uses bracket-depth-aware splitting for generics.
    """
    type_str = type_str.strip()
    if not type_str:
        return "any"

    if lang == "python":
        # Annotated[X, ...] -> normalize X
        ann_match = re.match(r'^Annotated\[(.+)\]$', type_str)
        if ann_match:
            inner_args = _split_generic_args(ann_match.group(1))
            if inner_args:
                return _normalize_type(inner_args[0], lang)

        # Literal["a", "b"] -> "a" | "b"
        lit_match = re.match(r'^Literal\[(.+)\]$', type_str)
        if lit_match:
            parts = _split_generic_args(lit_match.group(1))
            return " | ".join(p.strip() for p in parts)

        # Optional[X] -> X | null
        opt_match = re.match(r'^Optional\[(.+)\]$', type_str)
        if opt_match:
            inner = _normalize_type(opt_match.group(1), lang)
            return f"{inner} | null"

        # Union[X, Y, None] -> X | Y | null
        union_match = re.match(r'^Union\[(.+)\]$', type_str)
        if union_match:
            parts = _split_generic_args(union_match.group(1))
            normalized = []
            has_none = False
            for p in parts:
                p = p.strip()
                if p in ("None", "NoneType"):
                    has_none = True
                else:
                    normalized.append(_normalize_type(p, lang))
            result = " | ".join(normalized) if normalized else "any"
            if has_none:
                result += " | null"
            return result

        # list[X] / List[X] -> X[]
        list_match = re.match(r'^(?:list|List)\[(.+)\]$', type_str)
        if list_match:
            inner = _normalize_type(list_match.group(1), lang)
            return f"{inner}[]"

        # tuple[X, Y, Z] -> [X, Y, Z] (fixed tuple) or X[] (homogeneous)
        tuple_match = re.match(r'^(?:tuple|Tuple)\[(.+)\]$', type_str)
        if tuple_match:
            parts = _split_generic_args(tuple_match.group(1))
            if len(parts) == 2 and parts[1].strip() == '...':
                return f"{_normalize_type(parts[0], lang)}[]"
            return "array"

        # set[X] / Set[X] -> X[]
        set_match = re.match(r'^(?:set|Set|frozenset|FrozenSet)\[(.+)\]$', type_str)
        if set_match:
            inner = _normalize_type(set_match.group(1), lang)
            return f"{inner}[]"

        # dict[K, V] / Dict[K, V] -> Record<K, V>
        dict_match = re.match(r'^(?:dict|Dict)\[(.+)\]$', type_str)
        if dict_match:
            parts = _split_generic_args(dict_match.group(1))
            if len(parts) == 2:
                k = _normalize_type(parts[0], lang)
                v = _normalize_type(parts[1], lang)
                return f"Record<{k}, {v}>"
            return "object"

        # Callable[[X, Y], Z] -> (X, Y) => Z
        callable_match = re.match(r'^(?:Callable|callable)\[(.+)\]$', type_str)
        if callable_match:
            return "function"

        # X | None -> X | null (Python 3.10+ syntax)
        if ' | ' in type_str:
            parts = _split_generic_args(type_str, '|')
            normalized = []
            for p in parts:
                p = p.strip()
                normalized.append(_normalize_type(p, lang))
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

        # Record<K, V> -> Record<K, V>
        record_match = re.match(r'^Record<(.+)>$', type_str)
        if record_match:
            parts = _split_generic_args(record_match.group(1))
            if len(parts) == 2:
                k = _normalize_type(parts[0], lang)
                v = _normalize_type(parts[1], lang)
                return f"Record<{k}, {v}>"
            return "object"

        # Partial<X>, Required<X> -> keep as-is (structural)
        # Pick<X, K>, Omit<X, K> -> keep as-is

        # X | Y | null
        if ' | ' in type_str:
            parts = _split_generic_args(type_str, '|')
            normalized = [_normalize_type(p.strip(), lang) for p in parts]
            return " | ".join(normalized)

        # X & Y (intersection) -> keep as compound
        if ' & ' in type_str:
            return type_str  # intersection types are structural

        return _TS_TO_NORMALIZED.get(type_str, type_str)

    return type_str


# =============================================================================
# Schema comparison
# =============================================================================

def _compare_schemas(producer: dict, consumer: dict) -> list:
    """Compare two type schemas field by field.

    Returns list of mismatch dicts with field, issue, producer_value,
    consumer_value, and severity.

    Also checks field aliases for Pydantic models.
    """
    mismatches = []
    producer_fields = producer.get("fields", {})
    consumer_fields = consumer.get("fields", {})

    def _lang_for(schema):
        mt = schema.get("model_type", "")
        if mt in ("pydantic", "dataclass", "typeddict", "class"):
            return "python"
        if mt in ("interface", "type"):
            return "typescript"
        return "python"

    producer_lang = _lang_for(producer)
    consumer_lang = _lang_for(consumer)

    # Build alias mapping for producer (Pydantic field aliases)
    producer_alias_map = {}  # alias -> field_name
    for fname, finfo in producer_fields.items():
        alias = finfo.get("alias")
        if alias:
            producer_alias_map[alias] = fname

    all_fields = set(producer_fields.keys()) | set(consumer_fields.keys())

    for field in sorted(all_fields):
        in_producer = field in producer_fields
        in_consumer = field in consumer_fields

        # Check alias mapping: consumer might use the alias name
        if not in_producer and field in producer_alias_map:
            in_producer = True
            actual_field = producer_alias_map[field]
            producer_fields_entry = producer_fields[actual_field]
        elif in_producer:
            producer_fields_entry = producer_fields[field]
        else:
            producer_fields_entry = None

        if in_producer and not in_consumer:
            # Check if consumer uses the Python field name for an aliased field
            alias = producer_fields.get(field, {}).get("alias")
            if alias and alias in consumer_fields:
                continue  # Consumer uses alias, no mismatch
            mismatches.append({
                "field": field,
                "issue": "missing_in_consumer",
                "producer_value": producer_fields_entry["type"] if producer_fields_entry else None,
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
            p_type = _normalize_type(producer_fields_entry["type"], producer_lang)
            c_type = _normalize_type(consumer_fields[field]["type"], consumer_lang)

            if p_type != c_type:
                mismatches.append({
                    "field": field,
                    "issue": "type_mismatch",
                    "producer_value": producer_fields_entry["type"],
                    "consumer_value": consumer_fields[field]["type"],
                    "severity": "error",
                })

            p_optional = producer_fields_entry.get("optional", False)
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
# Helper: resolve symbol_id
# =============================================================================

def _resolve_symbol(symbols: dict, symbol_id: str) -> tuple:
    """Resolve a partial symbol_id to (resolved_id, symbol_data)."""
    if symbol_id in symbols:
        return symbol_id, symbols[symbol_id]

    name_matches = []
    partial_matches = []
    for sid, sym in symbols.items():
        sym_name = sym.get("name", "")
        if sym_name == symbol_id:
            name_matches.append(sid)
        elif symbol_id in sid or sid.endswith(symbol_id):
            partial_matches.append(sid)

    if name_matches:
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
    if path.endswith(".py"):
        return "python"
    if path.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "typescript"
    return "unknown"


# =============================================================================
# Tool 1: extract_type_schema
# =============================================================================

def extract_type_schema(symbol_id: str) -> dict:
    """Extract the field-level type schema from a class, interface, or type alias.

    Resolves the symbol from the index, detects language, and parses fields.
    Walks inheritance chains to include parent fields.
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
        schema = _extract_python_fields(content, name, all_symbols=symbols)
    elif lang == "typescript":
        schema = _extract_ts_fields(content, name, all_symbols=symbols)
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

def _find_handler_for_api(api_sym: dict, api_path: str, api_line: int, symbols: dict):
    """Find the handler function near an API endpoint.

    Returns (handler_sid, handler_sym) or None if no handler found.
    """
    for sid, sym in symbols.items():
        if sym.get("path") != api_path:
            continue
        if sym.get("type") not in ("function", "method"):
            continue
        sym_start = sym.get("start_line", 0)
        if 0 < sym_start - api_line <= 5:
            return sid, sym
    return None


def _extract_return_type_name(handler_content: str):
    """Extract return type name from handler content.

    Checks -> ReturnType annotation and response_model= parameter.
    Returns the type name string or None.
    """
    # Check -> ReturnType or -> ReturnType[Inner]
    ret_match = re.search(r'->\s*([A-Za-z_]\w*(?:\[[\w\[\], ]*\])?)', handler_content)
    if ret_match:
        return ret_match.group(1)

    # Check response_model=TypeName
    resp_match = re.search(r'response_model\s*=\s*([A-Za-z_]\w*(?:\[[\w\[\], ]*\])?)', handler_content)
    if resp_match:
        return resp_match.group(1)

    return None


def _find_producer_schema(actual_type: str, api_project: str, symbols: dict):
    """Find and extract the producer's type schema.

    Searches the index for actual_type within api_project and extracts fields.
    Returns the schema dict or None.
    """
    for sid, sym in symbols.items():
        if sym.get("name") != actual_type:
            continue
        sym_project_check = sid.split(":")[0] if ":" in sid else ""
        if sym_project_check == api_project:
            content = get_symbol_content_text(sid, sym)
            if content:
                lang = _detect_language(sym.get("path", ""))
                if lang == "python":
                    schema = _extract_python_fields(content, actual_type, all_symbols=symbols)
                elif lang == "typescript":
                    schema = _extract_ts_fields(content, actual_type, all_symbols=symbols)
                else:
                    continue
                if schema and schema.get("fields"):
                    return schema
    return None


def _find_consumer_mismatches(handler_sid: str, api_sid: str, api_project: str,
                              producer_schema: dict, consumer_project: str,
                              symbols: dict, reverse_index: dict) -> list:
    """Find consumer type mismatches for an API endpoint.

    Finds references from other projects and compares their type schemas
    against the producer schema.
    Returns list of consumer mismatch dicts.
    """
    consumer_refs = set()
    for ref_target in (handler_sid, api_sid):
        for ref_id in reverse_index.get(ref_target, []):
            ref_project = ref_id.split(":")[0] if ":" in ref_id else ""
            if ref_project == api_project:
                continue
            if consumer_project and consumer_project.lower() not in ref_project.lower():
                continue
            consumer_refs.add((ref_id, ref_project))

    consumers = []
    for ref_id, ref_project in consumer_refs:
        ref_sym = symbols.get(ref_id, {})
        ref_path = ref_sym.get("path", "")
        ref_lang = _detect_language(ref_path)

        # Search ALL type symbols in the consumer project (not just same file)
        for csid, csym in symbols.items():
            csym_project = csid.split(":")[0] if ":" in csid else ""
            if csym_project != ref_project:
                continue
            if csym.get("type") not in ("interface", "type", "class"):
                continue
            c_content = get_symbol_content_text(csid, csym)
            if not c_content:
                continue
            c_name = csym.get("name", "")
            c_path = csym.get("path", "")
            c_lang = _detect_language(c_path)

            if c_lang == "typescript":
                consumer_type = _extract_ts_fields(c_content, c_name, all_symbols=symbols)
            elif c_lang == "python":
                consumer_type = _extract_python_fields(c_content, c_name, all_symbols=symbols)
            else:
                continue
            if consumer_type and consumer_type.get("fields"):
                mismatches = _compare_schemas(producer_schema, consumer_type)
                if mismatches:
                    consumers.append({
                        "project": ref_project,
                        "type": consumer_type,
                        "symbol_id": csid,
                        "mismatches": mismatches,
                    })

    return consumers


def check_api_contracts(source_project: str = None, consumer_project: str = None) -> dict:
    """Check type contracts between API producers and consumers.

    For each API endpoint in source_project:
    1. Find the handler and extract its return type schema
    2. Find consumers that reference the endpoint
    3. Compare schemas to detect mismatches

    Improved: captures generic return types (Response[User]) and searches
    across files for consumer types.
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})

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

        # Find handler function near the API endpoint
        handler_result = _find_handler_for_api(api_sym, api_path, api_line, symbols)
        if not handler_result:
            continue
        handler_sid, handler_sym = handler_result

        handler_content = get_symbol_content_text(handler_sid, handler_sym)
        if not handler_content:
            continue

        # Extract return type
        return_type_name = _extract_return_type_name(handler_content)
        if not return_type_name:
            continue

        # Strip generic wrapper to get the actual type name
        actual_type = return_type_name.split('[')[0]

        # Find the return type in the index
        producer_schema = _find_producer_schema(actual_type, api_project, symbols)
        if not producer_schema:
            continue

        # Find consumers: references from other projects
        consumers = _find_consumer_mismatches(
            handler_sid, api_sid, api_project,
            producer_schema, consumer_project,
            symbols, reverse_index,
        )

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

    Finds types with the same name across projects and compares schemas.
    Walks inheritance chains for accurate field comparison.
    """
    index = load_index()
    symbols = index.get("symbols", {})

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
        projects_seen = set(e[2] for e in entries)
        if len(projects_seen) < 2:
            continue

        schemas = []
        for sid, sym, sym_project in entries:
            content = get_symbol_content_text(sid, sym)
            if not content:
                continue
            path = sym.get("path", "")
            lang = _detect_language(path)
            if lang == "python":
                schema = _extract_python_fields(content, name, all_symbols=symbols)
            elif lang == "typescript":
                schema = _extract_ts_fields(content, name, all_symbols=symbols)
            else:
                continue
            if schema.get("fields"):
                schemas.append((sid, sym_project, schema))

        if len(schemas) < 2:
            continue

        types_checked += 1

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
