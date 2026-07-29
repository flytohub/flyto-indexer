"""Lightweight token-aware scanner for C and common C++ source files.

This is intentionally dependency-free.  It extracts stable function/type
symbols and include/call edges without pretending to be a full compiler.
Preprocessor-heavy or generated code can still be indexed as file content and
upgraded later through a clang-based adapter.
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


class CScanner(BaseScanner):
    """Extract C/C++ functions, typedef structs, includes, and call edges."""

    supported_extensions = [".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"]

    INCLUDE_PATTERN = re.compile(
        r"^\s*#\s*include\s*([<\"])([^>\"]+)[>\"]",
        re.MULTILINE,
    )
    FUNCTION_PATTERN = re.compile(
        r"^[ \t]*"
        r"(?P<returns>(?:(?:static|inline|extern|const|volatile|unsigned|signed)\s+)*"
        r"[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*(?:\s*\*+)?)"
        r"\s+(?P<name>[A-Za-z_]\w*)\s*"
        r"\((?P<params>[^;{}]*)\)\s*"
        r"(?:__attribute__\s*\(\([^)]*\)\)\s*)?\{",
        re.MULTILINE,
    )
    TYPEDEF_STRUCT_PATTERN = re.compile(
        r"^[ \t]*typedef\s+struct(?:\s+(?P<tag>[A-Za-z_]\w*))?\s*\{",
        re.MULTILINE,
    )
    CALL_PATTERN = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
    CONTROL_NAMES = frozenset(
        {
            "if",
            "for",
            "while",
            "switch",
            "return",
            "sizeof",
            "_Alignof",
            "static_assert",
        }
    )

    def scan_file(
        self,
        file_path: Path,
        content: str,
    ) -> tuple[list[Symbol], list[Dependency]]:
        symbols: list[Symbol] = []
        dependencies: list[Dependency] = []
        rel_path = str(file_path)
        language = "cpp" if file_path.suffix in {".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"} else "c"
        cleaned = strip_comments_and_strings(content, "c")
        file_source_id = f"{self.project}:{rel_path}:file:{file_path.stem}"

        self._scan_includes(content, file_source_id, dependencies)
        self._scan_typedef_structs(content, cleaned, rel_path, language, symbols)
        self._scan_functions(content, cleaned, rel_path, language, symbols, dependencies)

        for symbol in symbols:
            symbol.compute_hash()
        return symbols, dependencies

    def _scan_includes(
        self,
        content: str,
        file_source_id: str,
        dependencies: list[Dependency],
    ) -> None:
        for match in self.INCLUDE_PATTERN.finditer(content):
            dependencies.append(
                Dependency(
                    source_id=file_source_id,
                    target_id=match.group(2),
                    dep_type=DependencyType.IMPORTS,
                    source_line=content[: match.start()].count("\n") + 1,
                    metadata={
                        "system": match.group(1) == "<",
                        "language": "c-family",
                    },
                )
            )

    def _scan_typedef_structs(
        self,
        content: str,
        cleaned: str,
        rel_path: str,
        language: str,
        symbols: list[Symbol],
    ) -> None:
        for match in self.TYPEDEF_STRUCT_PATTERN.finditer(cleaned):
            brace_pos = cleaned.find("{", match.start())
            if brace_pos < 0:
                continue
            body, end_pos = extract_block(content, brace_pos)
            tail = content[end_pos + 1 : end_pos + 200]
            alias_match = re.match(r"\s*([A-Za-z_]\w*)\s*;", tail)
            name = alias_match.group(1) if alias_match else match.group("tag")
            if not name:
                continue
            start_line = content[: match.start()].count("\n") + 1
            end_line = content[: end_pos + 1].count("\n") + 1
            fields = []
            for raw_line in body.splitlines():
                field_match = re.match(
                    r"\s*([A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*(?:\s*\*+)?)"
                    r"\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*;",
                    raw_line,
                )
                if field_match:
                    fields.append(
                        {
                            "name": field_match.group(2),
                            "type": " ".join(field_match.group(1).split()),
                        }
                    )
            symbols.append(
                Symbol(
                    project=self.project,
                    path=rel_path,
                    symbol_type=SymbolType.TYPE,
                    name=name,
                    start_line=start_line,
                    end_line=end_line,
                    content="\n".join(content.splitlines()[start_line - 1 : end_line]),
                    summary=f"C typedef struct {name}",
                    language=language,
                    exports=[name],
                    metadata={"fields": fields, "kind": "typedef_struct"},
                )
            )

    def _scan_functions(
        self,
        content: str,
        cleaned: str,
        rel_path: str,
        language: str,
        symbols: list[Symbol],
        dependencies: list[Dependency],
    ) -> None:
        for match in self.FUNCTION_PATTERN.finditer(cleaned):
            name = match.group("name")
            if name in self.CONTROL_NAMES:
                continue
            brace_pos = cleaned.find("{", match.start())
            if brace_pos < 0:
                continue
            body, end_pos = extract_block(content, brace_pos)
            start_line = content[: match.start()].count("\n") + 1
            end_line = content[: end_pos + 1].count("\n") + 1
            params = self._split_params(match.group("params"))
            returns = " ".join(match.group("returns").split())
            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.FUNCTION,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content="\n".join(content.splitlines()[start_line - 1 : end_line]),
                summary=f"{language.upper()} function {name}",
                language=language,
                exports=[name],
                params=params,
                returns=returns,
            )
            symbols.append(symbol)

            cleaned_body = strip_comments_and_strings(body, "c")
            for call_match in self.CALL_PATTERN.finditer(cleaned_body):
                target = call_match.group(1)
                if target in self.CONTROL_NAMES or target == name:
                    continue
                dependencies.append(
                    Dependency(
                        source_id=symbol.id,
                        target_id=target,
                        dep_type=DependencyType.CALLS,
                        source_line=start_line
                        + cleaned_body[: call_match.start()].count("\n"),
                        metadata={"language": language},
                    )
                )

    @staticmethod
    def _split_params(raw: str) -> list[str]:
        raw = raw.strip()
        if not raw or raw == "void":
            return []
        return [" ".join(item.strip().split()) for item in raw.split(",") if item.strip()]
