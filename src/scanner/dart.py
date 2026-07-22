"""Dart scanner for Flutter applications and standalone Dart packages.

The scanner is deliberately token-aware and dependency-free. It indexes
top-level declarations and direct type members while ignoring invocation
expressions inside method bodies.
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    from ..models import Dependency, DependencyType, Symbol, SymbolType
    from .base import BaseScanner
    from .tokenizer import extract_block, strip_comments_and_strings
except ImportError:
    from models import Dependency, DependencyType, Symbol, SymbolType
    from scanner.base import BaseScanner
    from scanner.tokenizer import extract_block, strip_comments_and_strings


class DartScanner(BaseScanner):
    """Index Dart types, functions, constructors, methods, and imports."""

    supported_extensions = [".dart"]

    _DIRECTIVE_PATTERN = re.compile(
        r"^(?P<kind>import|export|part)\s+['\"](?P<target>[^'\"]+)['\"]",
        re.MULTILINE,
    )
    _TYPE_PATTERN = re.compile(
        r"^[ \t]*(?:(?:abstract|base|final|sealed|interface)\s+)*"
        r"(?P<kind>mixin\s+class|class|mixin|enum|extension(?:\s+type)?)"
        r"(?:\s+(?P<name>[A-Za-z_$][\w$]*))?[^;{}]*\{",
        re.MULTILINE,
    )
    _GETTER_PATTERN = re.compile(
        r"^[ \t]*(?:(?:static|abstract|external|covariant)\s+)*"
        r"(?:[A-Za-z_$][\w$<>,?. ]*\s+)?get\s+(?P<name>[A-Za-z_$][\w$]*)"
        r"\s*(?:=>|\{)",
        re.MULTILINE,
    )
    _CALLABLE_MODIFIERS = {
        "abstract", "const", "covariant", "external", "factory", "final",
        "late", "static",
    }
    _CONTROL_PREFIXES = {
        "assert", "await", "case", "catch", "do", "else", "for", "if",
        "return", "switch", "throw", "while", "yield",
    }
    _WIDGET_BASES = (
        "Widget", "State<", "StatelessWidget", "StatefulWidget",
        "ConsumerWidget", "ConsumerStatefulWidget", "HookWidget",
    )

    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """Scan one Dart file without requiring the Dart SDK."""
        rel_path = str(file_path)
        lines = content.splitlines()
        cleaned = strip_comments_and_strings(content, "dart")
        depths = self._brace_depths(cleaned)
        symbols: list[Symbol] = []
        dependencies = self._scan_directives(rel_path, content)
        type_blocks = self._scan_types(rel_path, content, cleaned, lines, depths, symbols, dependencies)
        symbols.extend(self._scan_callables(rel_path, content, cleaned, lines, depths, type_blocks))
        symbols.extend(self._scan_getters(rel_path, content, lines, depths, type_blocks, symbols))

        for symbol in symbols:
            symbol.compute_hash()
        return symbols, dependencies

    def _scan_directives(self, rel_path: str, content: str) -> list[Dependency]:
        source_id = f"{self.project}:{rel_path}:file:{Path(rel_path).stem}"
        dependencies = []
        for match in self._DIRECTIVE_PATTERN.finditer(content):
            target = match.group("target")
            dependencies.append(Dependency(
                source_id=source_id,
                target_id=target,
                dep_type=DependencyType.RE_EXPORTS if match.group("kind") == "export" else DependencyType.IMPORTS,
                source_line=content[:match.start()].count("\n") + 1,
                metadata={"directive": match.group("kind")},
            ))
        return dependencies

    def _scan_types(
        self,
        rel_path: str,
        content: str,
        cleaned: str,
        lines: list[str],
        depths: list[int],
        symbols: list[Symbol],
        dependencies: list[Dependency],
    ) -> list[dict]:
        blocks = []
        for match in self._TYPE_PATTERN.finditer(cleaned):
            open_pos = cleaned.find("{", match.start(), match.end())
            if open_pos < 0:
                continue
            _body, end_pos = extract_block(content, open_pos)
            kind = " ".join(match.group("kind").split())
            name = match.group("name") or f"extension@{content[:match.start()].count(chr(10)) + 1}"
            start_line = content[:match.start()].count("\n") + 1
            end_line = content[:min(end_pos + 1, len(content))].count("\n") + 1
            header = content[match.start():open_pos]
            bases = self._type_bases(header)
            symbol_type = self._type_symbol_type(kind, header)
            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=symbol_type,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content="\n".join(lines[start_line - 1:end_line]),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="dart",
                exports=[name] if not name.startswith("_") else [],
                imports=bases,
                metadata={"kind": kind, "bases": bases},
            )
            symbols.append(symbol)
            source_id = symbol.id
            for base in bases:
                dep_type = DependencyType.IMPLEMENTS if "implements" in header and base in header.split("implements", 1)[1] else DependencyType.EXTENDS
                dependencies.append(Dependency(
                    source_id=source_id,
                    target_id=base,
                    dep_type=dep_type,
                    source_line=start_line,
                ))
            blocks.append({
                "name": name,
                "start": open_pos,
                "end": end_pos,
                "body_depth": depths[open_pos] + 1,
            })
        return blocks

    def _scan_callables(
        self,
        rel_path: str,
        content: str,
        cleaned: str,
        lines: list[str],
        depths: list[int],
        type_blocks: list[dict],
    ) -> list[Symbol]:
        symbols = []
        offsets = self._line_offsets(cleaned)
        line_index = 0
        while line_index < len(lines):
            start = offsets[line_index]
            raw_line = cleaned[start:offsets[line_index + 1] if line_index + 1 < len(offsets) else len(cleaned)]
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("@") or "(" not in stripped:
                line_index += 1
                continue
            header, end_line_index = self._collect_callable_header(cleaned, offsets, line_index)
            parsed = self._parse_callable_header(header)
            if parsed is None:
                line_index += 1
                continue
            name, params, suffix = parsed
            owner = self._owner_for_position(start, depths[start], type_blocks)
            if depths[start] != 0 and owner is None:
                line_index += 1
                continue
            if owner and depths[start] != owner["body_depth"]:
                line_index += 1
                continue

            start_line = line_index + 1
            end_line = end_line_index + 1
            open_brace = self._callable_open_brace(cleaned, offsets, line_index, end_line_index, suffix)
            if open_brace >= 0:
                _body, end_pos = extract_block(content, open_brace)
                end_line = content[:min(end_pos + 1, len(content))].count("\n") + 1
            elif "=>" in suffix:
                semicolon = cleaned.find(";", offsets[end_line_index])
                if semicolon >= 0:
                    end_line = cleaned[:semicolon].count("\n") + 1

            qualified = f"{owner['name']}.{name}" if owner else name
            symbols.append(Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.METHOD if owner else SymbolType.FUNCTION,
                name=qualified,
                start_line=start_line,
                end_line=end_line,
                content="\n".join(lines[start_line - 1:end_line]),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="dart",
                exports=[name] if not owner and not name.startswith("_") else [],
                params=self._parameter_names(params),
            ))
            line_index = max(line_index + 1, end_line_index + 1)
        return symbols

    def _scan_getters(
        self,
        rel_path: str,
        content: str,
        lines: list[str],
        depths: list[int],
        type_blocks: list[dict],
        existing: list[Symbol],
    ) -> list[Symbol]:
        symbols = []
        known = {(symbol.start_line, symbol.name) for symbol in existing}
        for match in self._GETTER_PATTERN.finditer(content):
            owner = self._owner_for_position(match.start(), depths[match.start()], type_blocks)
            if depths[match.start()] != 0 and owner is None:
                continue
            if owner and depths[match.start()] != owner["body_depth"]:
                continue
            name = match.group("name")
            qualified = f"{owner['name']}.{name}" if owner else name
            start_line = content[:match.start()].count("\n") + 1
            if (start_line, qualified) in known:
                continue
            end_line = start_line
            brace = content.find("{", match.start(), match.end())
            if brace >= 0:
                _body, end_pos = extract_block(content, brace)
                end_line = content[:min(end_pos + 1, len(content))].count("\n") + 1
            symbols.append(Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.METHOD if owner else SymbolType.FUNCTION,
                name=qualified,
                start_line=start_line,
                end_line=end_line,
                content="\n".join(lines[start_line - 1:end_line]),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="dart",
                exports=[name] if not owner and not name.startswith("_") else [],
                metadata={"getter": True},
            ))
        return symbols

    def _collect_callable_header(self, cleaned: str, offsets: list[int], start_line: int) -> tuple[str, int]:
        text = ""
        balance = 0
        seen_open = False
        end_line = start_line
        for index in range(start_line, min(start_line + 24, len(offsets))):
            end = offsets[index + 1] if index + 1 < len(offsets) else len(cleaned)
            piece = cleaned[offsets[index]:end]
            text += piece
            for char in piece:
                if char == "(":
                    balance += 1
                    seen_open = True
                elif char == ")" and balance:
                    balance -= 1
            end_line = index
            if seen_open and balance == 0:
                break
        return text.strip(), end_line

    def _parse_callable_header(self, header: str) -> tuple[str, str, str] | None:
        open_paren = header.find("(")
        if open_paren < 0:
            return None
        close_paren = self._matching_paren(header, open_paren)
        if close_paren < 0:
            return None
        prefix = " ".join(header[:open_paren].split())
        suffix = header[close_paren + 1:].strip()
        if not prefix or "=" in prefix or ":" in prefix:
            return None
        first = prefix.split()[0]
        if first in self._CONTROL_PREFIXES:
            return None
        while prefix.split() and prefix.split()[0] in self._CALLABLE_MODIFIERS:
            prefix = " ".join(prefix.split()[1:])
        if not prefix:
            return None
        name = prefix.split()[-1]
        if name == "operator":
            return None
        if not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?", name):
            return None
        allowed_suffix = ("", ";", "=>", "{", "async", "async*", "sync*", ":")
        if suffix and not suffix.startswith(allowed_suffix[1:]):
            return None
        return name, header[open_paren + 1:close_paren], suffix

    @staticmethod
    def _matching_paren(text: str, open_pos: int) -> int:
        depth = 0
        for index in range(open_pos, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    return index
        return -1

    @staticmethod
    def _callable_open_brace(cleaned: str, offsets: list[int], start_line: int, end_line: int, suffix: str) -> int:
        if "=>" in suffix or ";" in suffix and "{" not in suffix:
            return -1
        start = offsets[start_line]
        end = offsets[end_line + 1] if end_line + 1 < len(offsets) else len(cleaned)
        return cleaned.find("{", start, end)

    @staticmethod
    def _owner_for_position(position: int, depth: int, blocks: list[dict]) -> dict | None:
        candidates = [block for block in blocks if block["start"] < position < block["end"] and depth >= block["body_depth"]]
        return min(candidates, key=lambda block: block["end"] - block["start"]) if candidates else None

    @staticmethod
    def _parameter_names(params: str) -> list[str]:
        names = []
        for part in re.split(r",(?![^<]*>)", params):
            cleaned = re.sub(r"[{}\[\]]", " ", part).strip()
            cleaned = cleaned.split("=", 1)[0].strip()
            tokens = re.findall(r"[A-Za-z_$][\w$]*", cleaned)
            if not tokens:
                continue
            candidate = tokens[-1]
            if candidate in {"required", "this", "super"} and len(tokens) > 1:
                candidate = tokens[-2]
            names.append(candidate)
        return names

    @staticmethod
    def _brace_depths(cleaned: str) -> list[int]:
        depths = [0] * (len(cleaned) + 1)
        depth = 0
        for index, char in enumerate(cleaned):
            depths[index] = depth
            if char == "{":
                depth += 1
            elif char == "}":
                depth = max(0, depth - 1)
        depths[len(cleaned)] = depth
        return depths

    @staticmethod
    def _line_offsets(content: str) -> list[int]:
        offsets = [0]
        offsets.extend(match.end() for match in re.finditer("\n", content))
        return offsets

    @staticmethod
    def _type_bases(header: str) -> list[str]:
        bases = []
        for keyword in ("extends", "with", "implements", "on"):
            match = re.search(rf"\b{keyword}\s+([^{{]+?)(?=\b(?:with|implements|on)\b|$)", header)
            if not match:
                continue
            bases.extend(
                item.strip().split("<", 1)[0]
                for item in match.group(1).split(",")
                if item.strip()
            )
        return list(dict.fromkeys(bases))

    def _type_symbol_type(self, kind: str, header: str) -> SymbolType:
        if kind == "class" or kind == "mixin class":
            if any(base in header for base in self._WIDGET_BASES):
                return SymbolType.COMPONENT
            return SymbolType.CLASS
        if kind == "mixin":
            return SymbolType.INTERFACE
        return SymbolType.TYPE

    @staticmethod
    def _extract_doc_comment(lines: list[str], zero_based_line: int) -> str:
        index = zero_based_line - 1
        parts = []
        while index >= 0:
            stripped = lines[index].strip()
            if stripped.startswith("///"):
                parts.append(stripped[3:].strip())
                index -= 1
                continue
            if stripped.startswith("@") and not parts:
                index -= 1
                continue
            if not stripped and parts:
                index -= 1
                continue
            break
        return " ".join(reversed(parts))
