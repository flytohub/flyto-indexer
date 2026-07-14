"""Search-document builders shared by BM25 and semantic indexes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def build_symbol_document(symbol: Any) -> str:
    """Build searchable text for a symbol object or serialized symbol dict."""
    parts: list[str] = []
    for value in (
        _field(symbol, "name"),
        _field(symbol, "path"),
        _field(symbol, "language"),
        _symbol_type(symbol),
        _field(symbol, "summary"),
    ):
        _append(parts, value)

    for field in ("exports", "imports", "params"):
        _extend(parts, _field(symbol, field))

    _append(parts, _field(symbol, "returns"))
    _extend(parts, _metadata_terms(_field(symbol, "metadata")))

    content = _field(symbol, "content")
    if content:
        _append(parts, str(content)[:300])

    return " ".join(parts)


def _field(symbol: Any, name: str, default: Any = "") -> Any:
    if isinstance(symbol, Mapping):
        return symbol.get(name, default)
    return getattr(symbol, name, default)


def _symbol_type(symbol: Any) -> str:
    value = _field(symbol, "symbol_type", None)
    if value is None:
        value = _field(symbol, "type", "")
    return getattr(value, "value", str(value)) if value is not None else ""


def _append(parts: list[str], value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if text:
        parts.append(text)


def _extend(parts: list[str], values: Any) -> None:
    if not values:
        return
    if isinstance(values, (str, bytes)):
        _append(parts, values)
        return
    if isinstance(values, Iterable):
        for value in values:
            _append(parts, value)
        return
    _append(parts, values)


def _metadata_terms(value: Any, limit: int = 50) -> list[str]:
    terms: list[str] = []
    _collect_metadata_terms(value, terms, limit)
    return terms


def _collect_metadata_terms(value: Any, terms: list[str], limit: int) -> None:
    if len(terms) >= limit or value is None:
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _append(terms, key)
            if len(terms) >= limit:
                return
            _collect_metadata_terms(item, terms, limit)
        return
    if isinstance(value, (str, bytes)):
        _append(terms, value)
        return
    if isinstance(value, Iterable):
        for item in value:
            _collect_metadata_terms(item, terms, limit)
            if len(terms) >= limit:
                return
        return
    _append(terms, value)
