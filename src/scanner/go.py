"""
Go scanner using regex-based parsing.

Extracts:
- functions (top-level)
- methods (with receiver)
- structs
- interfaces
- imports
"""

import re
from pathlib import Path

try:
    from .base import BaseScanner
    from ..models import Symbol, Dependency, SymbolType, DependencyType
except ImportError:
    from scanner.base import BaseScanner
    from models import Symbol, Dependency, SymbolType, DependencyType


class GoScanner(BaseScanner):
    """
    Go code scanner using regex.

    Handles:
    - func Name() {}
    - func (r *Receiver) Method() {}
    - type Name struct {}
    - type Name interface {}
    - import "pkg" / import ("pkg1" "pkg2")
    """

    supported_extensions = [".go"]

    # Regex patterns
    FUNC_PATTERN = re.compile(
        r'^func\s+([A-Z_a-z]\w*)\s*\(([^)]*)\)\s*(?:\(([^)]*)\)|([^{\s]+))?\s*\{',
        re.MULTILINE
    )

    METHOD_PATTERN = re.compile(
        r'^func\s+\(\s*(\w+)\s+\*?(\w+)\s*\)\s+([A-Z_a-z]\w*)\s*\(([^)]*)\)\s*(?:\(([^)]*)\)|([^{\s]+))?\s*\{',
        re.MULTILINE
    )

    STRUCT_PATTERN = re.compile(
        r'^type\s+([A-Z_a-z]\w*)\s+struct\s*\{',
        re.MULTILINE
    )

    INTERFACE_PATTERN = re.compile(
        r'^type\s+([A-Z_a-z]\w*)\s+interface\s*\{',
        re.MULTILINE
    )

    IMPORT_SINGLE_PATTERN = re.compile(
        r'^import\s+"([^"]+)"',
        re.MULTILINE
    )

    IMPORT_BLOCK_PATTERN = re.compile(
        r'^import\s*\(([\s\S]*?)\)',
        re.MULTILINE
    )

    IMPORT_LINE_PATTERN = re.compile(
        r'(?:(\w+)\s+)?"([^"]+)"'
    )

    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """Scan Go file."""
        symbols = []
        dependencies = []
        lines = content.splitlines()
        rel_path = str(file_path)
        file_source_id = f"{self.project}:{rel_path}:file:{file_path.stem}"

        # Extract imports
        imports = self._extract_imports(content)
        for imp in imports:
            dep = Dependency(
                source_id=file_source_id,
                target_id=imp["module"],
                dep_type=DependencyType.IMPORTS,
                source_line=imp["line"],
                metadata={"alias": imp.get("alias", "")},
            )
            dependencies.append(dep)

        # Extract structs
        for match in self.STRUCT_PATTERN.finditer(content):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.CLASS,  # Use CLASS for struct
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
            )
            symbols.append(symbol)

        # Extract interfaces
        for match in self.INTERFACE_PATTERN.finditer(content):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.INTERFACE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
            )
            symbols.append(symbol)

        # Extract methods (with receiver) - do this before functions
        method_positions = set()
        for match in self.METHOD_PATTERN.finditer(content):
            receiver_name = match.group(1)
            receiver_type = match.group(2)
            method_name = match.group(3)
            params = match.group(4) or ""
            returns = match.group(5) or match.group(6) or ""

            start_line = content[:match.start()].count('\n') + 1
            method_positions.add(start_line)
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.METHOD,
                name=f"{receiver_type}.{method_name}",
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                params=self._parse_params(params),
                returns=returns.strip(),
                imports=[receiver_type],  # Link to receiver type
            )
            symbols.append(symbol)

        # Extract top-level functions (excluding methods)
        for match in self.FUNC_PATTERN.finditer(content):
            start_line = content[:match.start()].count('\n') + 1
            # Skip if this position was already captured as a method
            if start_line in method_positions:
                continue

            name = match.group(1)
            params = match.group(2) or ""
            returns = match.group(3) or match.group(4) or ""

            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.FUNCTION,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
                params=self._parse_params(params),
                returns=returns.strip(),
            )
            symbols.append(symbol)

        # Compute hashes
        for symbol in symbols:
            symbol.compute_hash()

        return symbols, dependencies

    def _extract_imports(self, content: str) -> list[dict]:
        """Extract import statements."""
        imports = []

        # Single imports: import "pkg"
        for match in self.IMPORT_SINGLE_PATTERN.finditer(content):
            line = content[:match.start()].count('\n') + 1
            imports.append({
                "module": match.group(1),
                "names": [match.group(1).split("/")[-1]],
                "line": line,
            })

        # Import blocks: import ( "pkg1" "pkg2" )
        for match in self.IMPORT_BLOCK_PATTERN.finditer(content):
            block_start = content[:match.start()].count('\n') + 1
            block_content = match.group(1)

            for line_match in self.IMPORT_LINE_PATTERN.finditer(block_content):
                alias = line_match.group(1) or ""
                module = line_match.group(2)
                line_offset = block_content[:line_match.start()].count('\n')

                pkg_name = alias if alias else module.split("/")[-1]
                imports.append({
                    "module": module,
                    "names": [pkg_name],
                    "alias": alias,
                    "line": block_start + line_offset + 1,
                })

        return imports

    def _find_block_end(self, content: str, start_pos: int, start_line: int) -> int:
        """Find matching closing brace."""
        depth = 1
        pos = start_pos
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            pos += 1

        return start_line + content[start_pos:pos].count('\n')

    def _extract_block(self, lines: list[str], start: int, end: int) -> str:
        """Extract block content from lines."""
        return "\n".join(lines[start - 1:end])

    def _extract_doc_comment(self, lines: list[str], line_before: int) -> str:
        """Extract doc comment above a declaration."""
        if line_before < 0:
            return ""

        comments = []
        i = line_before - 1  # 0-indexed
        while i >= 0:
            line = lines[i].strip()
            if line.startswith("//"):
                comments.insert(0, line[2:].strip())
                i -= 1
            else:
                break

        summary = " ".join(comments)
        if len(summary) > 200:
            summary = summary[:200] + "..."
        return summary

    def _parse_params(self, params_str: str) -> list[str]:
        """Parse Go function parameters."""
        if not params_str.strip():
            return []

        params = []
        # Simple split by comma, handling types
        for param in params_str.split(","):
            param = param.strip()
            if param:
                # Take the param name (first word before type)
                parts = param.split()
                if parts:
                    params.append(parts[0])

        return params
