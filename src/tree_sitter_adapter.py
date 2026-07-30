"""Optional Tree-sitter structural validation with deterministic fallback.

Tree-sitter and language grammars are deliberately optional.  The native
Flyto scanners remain authoritative; when explicitly enabled this adapter
cross-checks parse health and definition ranges without changing public tools
or making a missing grammar fatal.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from importlib import metadata
from pathlib import Path
from typing import Any


_TRUE_VALUES = {"1", "true", "yes", "on"}
_LANGUAGE_CONFIG = {
    ".c": ("c", "tree_sitter_c", "language"),
    ".go": ("go", "tree_sitter_go", "language"),
    ".java": ("java", "tree_sitter_java", "language"),
    ".js": ("javascript", "tree_sitter_javascript", "language"),
    ".jsx": ("javascript", "tree_sitter_javascript", "language"),
    ".py": ("python", "tree_sitter_python", "language"),
    ".rs": ("rust", "tree_sitter_rust", "language"),
    ".ts": ("typescript", "tree_sitter_typescript", "language_typescript"),
    ".tsx": ("typescript", "tree_sitter_typescript", "language_tsx"),
}
_DEFINITION_TYPES = {
    "class_declaration",
    "class_definition",
    "function_declaration",
    "function_definition",
    "function_item",
    "interface_declaration",
    "method_declaration",
    "method_definition",
    "struct_item",
    "trait_item",
    "type_alias_declaration",
    "type_declaration",
}
_NATIVE_DEFINITION_TYPES = {
    "class", "function", "interface", "method", "type",
}


def _package_version(distribution: str) -> str:
    """Return an optional parser version without importing its module."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unavailable"


class OptionalTreeSitterAdapter:
    """Load Tree-sitter lazily and expose bounded parse cross-check metrics."""

    def __init__(self, enabled: bool | None = None):
        """Create a lazy adapter controlled explicitly or by environment."""
        self.enabled = (
            str(os.environ.get("FLYTO_TREE_SITTER", "")).casefold()
            in _TRUE_VALUES
            if enabled is None
            else bool(enabled)
        )
        self._parsers: dict[str, Any] = {}
        self._unavailable: set[str] = set()
        self.reset()

    def reset(self) -> None:
        """Reset bounded per-scan parser metrics."""
        self._metrics = {
            "validated_files": 0,
            "fallback_files": 0,
            "parse_error_files": 0,
            "native_definitions": 0,
            "tree_sitter_definitions": 0,
            "definition_mismatches": 0,
        }

    def signature(self) -> str:
        """Hash parser mode and installed grammar versions for invalidation."""
        payload: dict[str, Any] = {
            "schema": "tree-sitter-adapter.v1",
            "enabled": self.enabled,
        }
        if self.enabled:
            payload["packages"] = {
                "tree-sitter": _package_version("tree-sitter"),
                **{
                    module_name: _package_version(module_name.replace("_", "-"))
                    for _, module_name, _ in sorted(set(_LANGUAGE_CONFIG.values()))
                },
            }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _load_parser(self, suffix: str):
        """Load and cache one optional language parser on first use."""
        config = _LANGUAGE_CONFIG.get(suffix.casefold())
        if config is None:
            return None
        language_name, module_name, factory_name = config
        if language_name in self._parsers:
            return self._parsers[language_name]
        if language_name in self._unavailable:
            return None
        try:
            tree_sitter = importlib.import_module("tree_sitter")
            grammar_module = importlib.import_module(module_name)
            grammar = getattr(grammar_module, factory_name)()
            try:
                language = tree_sitter.Language(grammar)
            except TypeError:
                language = grammar
            try:
                parser = tree_sitter.Parser(language)
            except TypeError:
                parser = tree_sitter.Parser()
                if hasattr(parser, "set_language"):
                    parser.set_language(language)
                else:
                    parser.language = language
            self._parsers[language_name] = parser
            return parser
        except (AttributeError, ImportError, TypeError, ValueError):
            self._unavailable.add(language_name)
            return None

    @staticmethod
    def _definition_nodes(root_node: Any) -> list[Any]:
        """Collect definition nodes with an iterative bounded traversal."""
        definitions = []
        stack = [root_node]
        while stack:
            node = stack.pop()
            if getattr(node, "type", "") in _DEFINITION_TYPES:
                definitions.append(node)
            stack.extend(reversed(list(getattr(node, "children", ()) or ())))
        return definitions

    @staticmethod
    def _node_name(node: Any, source: bytes) -> str:
        """Extract a definition name through the grammar's named field."""
        try:
            name_node = node.child_by_field_name("name")
        except (AttributeError, TypeError):
            return ""
        if name_node is None:
            return ""
        return source[name_node.start_byte:name_node.end_byte].decode(
            "utf-8",
            errors="replace",
        )

    def inspect(self, file_path: Path, content: str, native_symbols: list[Any]) -> dict:
        """Cross-check one native scan, falling back without raising."""
        if not self.enabled:
            return {"status": "disabled"}
        parser = self._load_parser(file_path.suffix)
        if parser is None:
            self._metrics["fallback_files"] += 1
            return {"status": "fallback", "reason": "grammar_unavailable"}

        source = content.encode("utf-8")
        try:
            tree = parser.parse(source)
            root = tree.root_node
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._metrics["fallback_files"] += 1
            return {"status": "fallback", "reason": "parse_failed"}

        if bool(getattr(root, "has_error", False)):
            self._metrics["parse_error_files"] += 1
        definition_nodes = self._definition_nodes(root)
        tree_names = {
            name
            for node in definition_nodes
            if (name := self._node_name(node, source))
        }
        native_names = {
            str(getattr(symbol, "name", ""))
            for symbol in native_symbols
            if str(
                getattr(getattr(symbol, "symbol_type", ""), "value", "")
            ) in _NATIVE_DEFINITION_TYPES
        }
        mismatches = len(native_names - tree_names)
        self._metrics["validated_files"] += 1
        self._metrics["native_definitions"] += len(native_names)
        self._metrics["tree_sitter_definitions"] += len(tree_names)
        self._metrics["definition_mismatches"] += mismatches
        return {
            "status": "validated",
            "has_error": bool(getattr(root, "has_error", False)),
            "native_definitions": len(native_names),
            "tree_sitter_definitions": len(tree_names),
            "definition_mismatches": mismatches,
        }

    def summary(self) -> dict:
        """Return parser-validation metrics and configuration identity."""
        return {
            "schema": "tree-sitter-adapter.v1",
            "mode": "validate" if self.enabled else "disabled",
            "signature": self.signature(),
            **self._metrics,
        }
