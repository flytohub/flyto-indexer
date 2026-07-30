"""Optional SCIP ingestion for compiler-accurate code intelligence.

The adapter reads standard ``index.scip`` protobuf payloads directly with a
small streaming wire decoder, or the equivalent protobuf-JSON projection.  It
does not generate SCIP, invoke a compiler, access the network, or add a runtime
dependency.  Projects opt in by placing an artifact at ``index.scip`` or
``.flyto-index/scip.json``, or by declaring ``scip_index`` in index metadata.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

DEFINITION_ROLE = 0x1


class SCIPDecodeError(ValueError):
    """Raised when a SCIP artifact is malformed or unsupported."""


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode one protobuf varint and return its value and next offset."""
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise SCIPDecodeError("Invalid protobuf varint")


def _wire_fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield bounded protobuf wire fields without a generated runtime."""
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number, wire_type = key >> 3, key & 0x07
        if not field_number:
            raise SCIPDecodeError("Invalid protobuf field number")
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
            yield field_number, wire_type, value
        elif wire_type == 1:
            end = offset + 8
            if end > len(data):
                raise SCIPDecodeError("Truncated fixed64 field")
            yield field_number, wire_type, data[offset:end]
            offset = end
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise SCIPDecodeError("Truncated length-delimited field")
            yield field_number, wire_type, data[offset:end]
            offset = end
        elif wire_type == 5:
            end = offset + 4
            if end > len(data):
                raise SCIPDecodeError("Truncated fixed32 field")
            yield field_number, wire_type, data[offset:end]
            offset = end
        else:
            raise SCIPDecodeError(f"Unsupported protobuf wire type: {wire_type}")


def _text(value: int | bytes) -> str:
    """Decode a protobuf length-delimited value as replacement-safe UTF-8."""
    if not isinstance(value, bytes):
        return ""
    return value.decode("utf-8", errors="replace")


def _packed_ints(value: int | bytes) -> list[int]:
    """Decode a packed protobuf integer field."""
    if isinstance(value, int):
        return [value]
    result = []
    offset = 0
    while offset < len(value):
        item, offset = _read_varint(value, offset)
        result.append(item)
    return result


def _typed_range(value: int | bytes, fields: int) -> list[int]:
    """Normalize a typed SCIP range into ordered coordinates."""
    if not isinstance(value, bytes):
        return []
    values = {}
    for field, wire_type, item in _wire_fields(value):
        if wire_type == 0 and 1 <= field <= fields:
            values[field] = int(item)
    return [values.get(field, 0) for field in range(1, fields + 1)]


def _parse_occurrence(data: bytes) -> dict[str, Any]:
    """Parse one binary SCIP occurrence message."""
    occurrence: dict[str, Any] = {"range": [], "symbol": "", "symbol_roles": 0}
    legacy_range = []
    for field, wire_type, value in _wire_fields(data):
        if field == 1:
            legacy_range.extend(_packed_ints(value))
        elif field == 2 and wire_type == 2:
            occurrence["symbol"] = _text(value)
        elif field == 3 and wire_type == 0:
            occurrence["symbol_roles"] = int(value)
        elif field == 8 and wire_type == 2:
            occurrence["range"] = _typed_range(value, 3)
        elif field == 9 and wire_type == 2:
            occurrence["range"] = _typed_range(value, 4)
    if not occurrence["range"]:
        occurrence["range"] = legacy_range
    return occurrence


def _parse_relationship(data: bytes) -> dict[str, Any]:
    """Parse one binary SCIP relationship message."""
    relationship = {
        "symbol": "",
        "is_reference": False,
        "is_implementation": False,
        "is_type_definition": False,
        "is_definition": False,
    }
    bool_fields = {
        2: "is_reference",
        3: "is_implementation",
        4: "is_type_definition",
        5: "is_definition",
    }
    for field, wire_type, value in _wire_fields(data):
        if field == 1 and wire_type == 2:
            relationship["symbol"] = _text(value)
        elif field in bool_fields and wire_type == 0:
            relationship[bool_fields[field]] = bool(value)
    return relationship


def _parse_symbol_information(data: bytes) -> dict[str, Any]:
    """Parse one binary SCIP symbol-information message."""
    information: dict[str, Any] = {
        "symbol": "",
        "display_name": "",
        "relationships": [],
    }
    for field, wire_type, value in _wire_fields(data):
        if field == 1 and wire_type == 2:
            information["symbol"] = _text(value)
        elif field == 4 and wire_type == 2 and isinstance(value, bytes):
            information["relationships"].append(_parse_relationship(value))
        elif field == 6 and wire_type == 2:
            information["display_name"] = _text(value)
    return information


def _parse_document(data: bytes) -> dict[str, Any]:
    """Parse one binary SCIP document message."""
    document: dict[str, Any] = {
        "relative_path": "",
        "language": "",
        "occurrences": [],
        "symbols": [],
    }
    for field, wire_type, value in _wire_fields(data):
        if field == 1 and wire_type == 2:
            document["relative_path"] = _text(value)
        elif field == 2 and wire_type == 2 and isinstance(value, bytes):
            document["occurrences"].append(_parse_occurrence(value))
        elif field == 3 and wire_type == 2 and isinstance(value, bytes):
            document["symbols"].append(_parse_symbol_information(value))
        elif field == 4 and wire_type == 2:
            document["language"] = _text(value)
    return document


def _parse_metadata(data: bytes) -> dict[str, Any]:
    """Parse the bounded metadata fields used for provenance."""
    metadata = {"project_root": ""}
    for field, wire_type, value in _wire_fields(data):
        if field == 3 and wire_type == 2:
            metadata["project_root"] = _text(value)
    return metadata


def _parse_binary(data: bytes) -> dict[str, Any]:
    """Parse a binary SCIP index into the adapter's normalized shape."""
    result = {"metadata": {}, "documents": []}
    for field, wire_type, value in _wire_fields(data):
        if field == 1 and wire_type == 2 and isinstance(value, bytes):
            result["metadata"] = _parse_metadata(value)
        elif field == 2 and wire_type == 2 and isinstance(value, bytes):
            result["documents"].append(_parse_document(value))
    return result


def _json_key(data: dict, snake: str, camel: str, default=None):
    """Read a field from either protobuf JSON naming convention."""
    if snake in data:
        return data[snake]
    return data.get(camel, default)


def _normalize_json(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize protobuf JSON and common SCIP JSON spellings."""
    documents = []
    for raw_document in data.get("documents") or []:
        occurrences = []
        for raw in raw_document.get("occurrences") or []:
            occurrences.append({
                "range": list(raw.get("range") or []),
                "symbol": str(raw.get("symbol") or ""),
                "symbol_roles": int(
                    _json_key(raw, "symbol_roles", "symbolRoles", 0) or 0
                ),
            })
        symbols = []
        for raw in raw_document.get("symbols") or []:
            relationships = []
            for relationship in raw.get("relationships") or []:
                relationships.append({
                    "symbol": str(relationship.get("symbol") or ""),
                    "is_reference": bool(_json_key(
                        relationship, "is_reference", "isReference", False
                    )),
                    "is_implementation": bool(_json_key(
                        relationship,
                        "is_implementation",
                        "isImplementation",
                        False,
                    )),
                    "is_type_definition": bool(_json_key(
                        relationship,
                        "is_type_definition",
                        "isTypeDefinition",
                        False,
                    )),
                    "is_definition": bool(_json_key(
                        relationship, "is_definition", "isDefinition", False
                    )),
                })
            symbols.append({
                "symbol": str(raw.get("symbol") or ""),
                "display_name": str(
                    _json_key(raw, "display_name", "displayName", "") or ""
                ),
                "relationships": relationships,
            })
        documents.append({
            "relative_path": str(_json_key(
                raw_document, "relative_path", "relativePath", ""
            ) or ""),
            "language": str(raw_document.get("language") or ""),
            "occurrences": occurrences,
            "symbols": symbols,
        })
    metadata = data.get("metadata") or {}
    return {
        "metadata": {
            "project_root": str(_json_key(
                metadata, "project_root", "projectRoot", ""
            ) or ""),
        },
        "documents": documents,
    }


@lru_cache(maxsize=8)
def _load_cached(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    """Load and cache an artifact by path, timestamp, and byte size."""
    del mtime_ns, size
    path = Path(path_text)
    raw = path.read_bytes()
    if path.suffix.lower() == ".json" or raw.lstrip().startswith(b"{"):
        payload = _normalize_json(json.loads(raw.decode("utf-8")))
        artifact_format = "scip-protobuf-json"
    else:
        payload = _parse_binary(raw)
        artifact_format = "scip-protobuf"
    return {
        "schema": "scip-evidence.v1",
        "status": "loaded",
        "artifact": str(path),
        "format": artifact_format,
        "fingerprint": hashlib.sha256(raw).hexdigest(),
        **payload,
    }


def load_scip_artifact(path: str | Path) -> dict[str, Any]:
    """Load a SCIP binary or protobuf-JSON artifact without external tooling."""
    artifact = Path(path).expanduser().resolve()
    if not artifact.is_file():
        return {
            "schema": "scip-evidence.v1",
            "status": "unavailable",
            "reason": "artifact_not_found",
            "artifact": str(artifact),
        }
    try:
        stat = artifact.stat()
        return _load_cached(str(artifact), stat.st_mtime_ns, stat.st_size)
    except (OSError, UnicodeError, json.JSONDecodeError, SCIPDecodeError) as exc:
        return {
            "schema": "scip-evidence.v1",
            "status": "invalid",
            "reason": type(exc).__name__,
            "message": str(exc),
            "artifact": str(artifact),
        }


def _artifact_candidates(index: dict, project: str) -> list[Path]:
    """Return explicit or conventional SCIP artifact candidates."""
    explicit = index.get("scip_index")
    candidates = []
    if isinstance(explicit, str):
        candidates.append(Path(explicit))
    elif isinstance(explicit, dict):
        value = explicit.get(project)
        if value:
            candidates.append(Path(value))
    root = (index.get("project_roots") or {}).get(project)
    root = root or index.get("root_path") or index.get("project_root")
    if root:
        root_path = Path(root)
        candidates.extend([
            root_path / "index.scip",
            root_path / ".flyto-index" / "index.scip",
            root_path / ".flyto-index" / "scip.json",
            root_path / "index.scip.json",
        ])
    return candidates


def _line(occurrence: dict) -> int:
    """Return the one-based start line for a normalized occurrence."""
    source_range = occurrence.get("range") or []
    return int(source_range[0]) + 1 if source_range else 0


def _containing_symbol(index: dict, path: str, line: int) -> tuple[str, str]:
    """Resolve the smallest indexed symbol containing a source line."""
    best_id = ""
    best_name = ""
    best_span = None
    for symbol_id, symbol in (index.get("symbols") or {}).items():
        if symbol.get("path") != path:
            continue
        start = int(symbol.get("start_line") or 0)
        end = int(symbol.get("end_line") or start)
        if start <= line <= max(start, end):
            span = max(0, end - start)
            if best_span is None or span < best_span:
                best_id = symbol_id
                best_name = str(symbol.get("name") or "")
                best_span = span
    return best_id, best_name


def find_scip_references(
    index: dict,
    resolved_id: str,
    target_symbol: dict,
) -> dict[str, Any]:
    """Return precise SCIP references and artifact provenance for one symbol."""
    project = resolved_id.split(":", 1)[0]
    artifact = next(
        (candidate for candidate in _artifact_candidates(index, project)
         if candidate.is_file()),
        None,
    )
    if artifact is None:
        return {
            "schema": "scip-reference-evidence.v1",
            "status": "unavailable",
            "reason": "artifact_not_found",
            "references": [],
        }
    evidence = load_scip_artifact(artifact)
    if evidence.get("status") != "loaded":
        return {
            "schema": "scip-reference-evidence.v1",
            "status": evidence.get("status"),
            "reason": evidence.get("reason"),
            "artifact": evidence.get("artifact"),
            "references": [],
        }

    target_path = str(target_symbol.get("path") or "")
    start = int(target_symbol.get("start_line") or 0)
    end = int(target_symbol.get("end_line") or start)
    target_scip = ""
    for document in evidence["documents"]:
        if document["relative_path"] != target_path:
            continue
        for occurrence in document["occurrences"]:
            line = _line(occurrence)
            if (
                occurrence["symbol_roles"] & DEFINITION_ROLE
                and start <= line <= max(start, end)
            ):
                target_scip = occurrence["symbol"]
                break
        if target_scip:
            break

    if not target_scip:
        return {
            "schema": "scip-reference-evidence.v1",
            "status": "unresolved",
            "reason": "target_definition_not_found",
            "artifact": evidence["artifact"],
            "fingerprint": evidence["fingerprint"],
            "references": [],
        }

    related = {target_scip}
    implementation_symbols = set()
    for document in evidence["documents"]:
        for information in document["symbols"]:
            source_symbol = information["symbol"]
            for relationship in information["relationships"]:
                related_symbol = relationship["symbol"]
                if relationship["is_reference"]:
                    if source_symbol == target_scip:
                        related.add(related_symbol)
                    if related_symbol == target_scip:
                        related.add(source_symbol)
                if relationship["is_implementation"] and related_symbol == target_scip:
                    related.add(source_symbol)
                    implementation_symbols.add(source_symbol)

    references = []
    for document in evidence["documents"]:
        path = document["relative_path"]
        for occurrence in document["occurrences"]:
            symbol = occurrence["symbol"]
            line = _line(occurrence)
            is_definition = bool(occurrence["symbol_roles"] & DEFINITION_ROLE)
            if symbol not in related:
                continue
            if symbol == target_scip and is_definition and path == target_path:
                continue
            from_symbol, from_name = _containing_symbol(index, path, line)
            references.append({
                "type": (
                    "implementation"
                    if symbol in implementation_symbols and is_definition
                    else "usage"
                ),
                "from_symbol": from_symbol,
                "from_path": path,
                "from_name": from_name,
                "line": line,
                "confidence": "high",
                "source": "scip",
                "scip_symbol": symbol,
                "artifact_fingerprint": evidence["fingerprint"],
            })
    references.sort(key=lambda item: (
        item["from_path"],
        item["line"],
        item["scip_symbol"],
    ))
    return {
        "schema": "scip-reference-evidence.v1",
        "status": "resolved",
        "artifact": evidence["artifact"],
        "format": evidence["format"],
        "fingerprint": evidence["fingerprint"],
        "target_symbol": target_scip,
        "related_symbols": sorted(related),
        "references": references,
    }
