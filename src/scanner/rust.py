"""
Rust scanner using regex-based parsing.

Extracts:
- functions (fn)
- structs
- traits
- impl blocks (methods)
- use statements
"""

import re
from pathlib import Path

try:
    from ..models import Dependency, DependencyType, Symbol, SymbolType
    from .base import BaseScanner
except ImportError:
    from models import Dependency, DependencyType, Symbol, SymbolType
    from scanner.base import BaseScanner


class RustScanner(BaseScanner):
    """
    Rust code scanner using regex.

    Handles:
    - fn name() {}
    - pub fn name<T>() -> Result<T, E> {}
    - struct Name {}
    - trait Name {}
    - impl Name { fn method() {} }
    - impl Trait for Name {}
    - use crate::module::item;
    """

    supported_extensions = [".rs"]

    # Regex patterns
    # Function: pub/async/const/unsafe fn name<generics>(params) -> return
    # Note: ^[ \t]* allows matching indented methods in impl blocks
    FN_PATTERN = re.compile(
        r'^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:const\s+)?(?:unsafe\s+)?fn\s+([a-z_]\w*)'
        r'(?:<[^>]+>)?\s*\(([^)]*)\)(?:\s*->\s*([^{]+))?\s*\{',
        re.MULTILINE
    )

    STRUCT_PATTERN = re.compile(
        r'^(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Z]\w*)(?:<[^>]+>)?(?:\s*\([^)]*\)\s*;|\s*\{|\s*;)',
        re.MULTILINE
    )

    TRAIT_PATTERN = re.compile(
        r'^(?:pub(?:\([^)]*\))?\s+)?(?:unsafe\s+)?trait\s+([A-Z]\w*)(?:<[^>]+>)?(?:\s*:\s*[^{]+)?\s*\{',
        re.MULTILINE
    )

    # impl Type { ... } or impl Trait for Type { ... }
    IMPL_PATTERN = re.compile(
        r'^impl(?:<[^>]+>)?\s+(?:([A-Z]\w*(?:<[^>]+>)?)\s+for\s+)?([A-Z]\w*)(?:<[^>]+>)?\s*\{',
        re.MULTILINE
    )

    # Method inside impl block
    IMPL_FN_PATTERN = re.compile(
        r'^\s+(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:const\s+)?fn\s+([a-z_]\w*)'
        r'(?:<[^>]+>)?\s*\(([^)]*)\)(?:\s*->\s*([^{]+))?\s*\{',
        re.MULTILINE
    )

    USE_PATTERN = re.compile(
        r'^(?:pub\s+)?use\s+([^;]+);',
        re.MULTILINE
    )

    ENUM_PATTERN = re.compile(
        r'^(?:pub(?:\([^)]*\))?\s+)?enum\s+([A-Z]\w*)(?:<[^>]+>)?\s*\{',
        re.MULTILINE
    )

    TYPE_ALIAS_PATTERN = re.compile(
        r'^(?:pub(?:\([^)]*\))?\s+)?type\s+([A-Z]\w*)(?:<[^>]+>)?\s*=',
        re.MULTILINE
    )

    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """Scan Rust file."""
        symbols = []
        dependencies = []
        lines = content.splitlines()
        rel_path = str(file_path)
        file_source_id = f"{self.project}:{rel_path}:file:{file_path.stem}"

        # Extract use statements
        for match in self.USE_PATTERN.finditer(content):
            use_path = match.group(1).strip()
            line = content[:match.start()].count('\n') + 1

            # Parse use path to extract module and names
            module, names = self._parse_use_path(use_path)

            dep = Dependency(
                source_id=file_source_id,
                target_id=module,
                dep_type=DependencyType.IMPORTS,
                source_line=line,
                metadata={"names": names},
            )
            dependencies.append(dep)

        # Track impl block positions to find methods
        impl_blocks = []
        for match in self.IMPL_PATTERN.finditer(content):
            trait_name = match.group(1)  # None if not "impl Trait for Type"
            type_name = match.group(2)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            impl_blocks.append({
                "trait": trait_name,
                "type": type_name,
                "start": match.start(),
                "end": match.end() + self._find_block_length(content, match.end()),
                "start_line": start_line,
                "end_line": end_line,
            })

        # Track trait block positions to skip trait method signatures
        trait_blocks = []
        for match in self.TRAIT_PATTERN.finditer(content):
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            trait_blocks.append({
                "start": match.start(),
                "end": match.end() + self._find_block_length(content, match.end()),
            })

        # Extract structs
        for match in self.STRUCT_PATTERN.finditer(content):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1

            # Determine end line based on whether it's a struct with body or tuple/unit struct
            match_text = match.group(0)
            if match_text.rstrip().endswith('{'):
                # Find the opening brace position
                brace_pos = match.end() - 1
                while brace_pos >= 0 and content[brace_pos] != '{':
                    brace_pos -= 1
                end_line = self._find_block_end(content, brace_pos + 1, start_line)
            elif ';' in match_text:
                end_line = start_line
            else:
                end_line = self._find_block_end(content, match.end(), start_line)

            block_content = self._extract_block(lines, start_line, end_line)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.CLASS,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="rust",
                exports=[name] if not match.group(0).strip().startswith("pub(") else [],
            )
            symbols.append(symbol)

        # Extract traits
        for match in self.TRAIT_PATTERN.finditer(content):
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
                language="rust",
                exports=[name],
            )
            symbols.append(symbol)

        # Extract enums
        for match in self.ENUM_PATTERN.finditer(content):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.TYPE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="rust",
                exports=[name],
            )
            symbols.append(symbol)

        # Extract type aliases
        for match in self.TYPE_ALIAS_PATTERN.finditer(content):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            # Type alias ends at semicolon
            end_pos = content.find(';', match.end())
            end_line = content[:end_pos + 1].count('\n') + 1 if end_pos > 0 else start_line

            block_content = self._extract_block(lines, start_line, end_line)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.TYPE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="rust",
                exports=[name],
            )
            symbols.append(symbol)

        # Extract top-level functions (not in impl blocks or trait blocks)
        for match in self.FN_PATTERN.finditer(content):
            pos = match.start()

            # Skip if inside a trait block (these are method signatures, not implementations)
            in_trait = False
            for trait_block in trait_blocks:
                if trait_block["start"] < pos < trait_block["end"]:
                    in_trait = True
                    break
            if in_trait:
                continue

            # Check if inside an impl block
            in_impl = False
            impl_type = None
            for impl_block in impl_blocks:
                if impl_block["start"] < pos < impl_block["end"]:
                    in_impl = True
                    impl_type = impl_block["type"]
                    break

            name = match.group(1)
            params = match.group(2) or ""
            returns = match.group(3) or ""
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            if in_impl:
                # Method
                symbol = Symbol(
                    project=self.project,
                    path=rel_path,
                    symbol_type=SymbolType.METHOD,
                    name=f"{impl_type}.{name}",
                    start_line=start_line,
                    end_line=end_line,
                    content=block_content,
                    summary=self._extract_doc_comment(lines, start_line - 1),
                    language="rust",
                    params=self._parse_params(params),
                    returns=returns.strip(),
                    imports=[impl_type],
                )
            else:
                # Top-level function
                symbol = Symbol(
                    project=self.project,
                    path=rel_path,
                    symbol_type=SymbolType.FUNCTION,
                    name=name,
                    start_line=start_line,
                    end_line=end_line,
                    content=block_content,
                    summary=self._extract_doc_comment(lines, start_line - 1),
                    language="rust",
                    exports=[name] if "pub" in match.group(0) else [],
                    params=self._parse_params(params),
                    returns=returns.strip(),
                )

            symbols.append(symbol)

        # Compute hashes
        for symbol in symbols:
            symbol.compute_hash()

        return symbols, dependencies

    def _parse_use_path(self, use_path: str) -> tuple[str, list[str]]:
        """Parse Rust use path to module and names."""
        # Handle: crate::module::item
        # Handle: std::collections::{HashMap, HashSet}
        # Handle: super::parent
        # Handle: self::sibling

        use_path = use_path.strip()

        # Check for multi-import: path::{a, b}
        if '{' in use_path:
            base_match = re.match(r'([^{]+)::\{([^}]+)\}', use_path)
            if base_match:
                base = base_match.group(1).strip()
                items = [i.strip().split(' as ')[0] for i in base_match.group(2).split(',')]
                return base, items

        # Single import
        parts = use_path.split("::")
        if parts:
            # Handle "as alias"
            last = parts[-1].split(' as ')[0].strip()
            module = "::".join(parts[:-1]) if len(parts) > 1 else parts[0]
            return module, [last]

        return use_path, [use_path.split("::")[-1]]

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

    def _find_block_length(self, content: str, start_pos: int) -> int:
        """Find length of block from start_pos."""
        depth = 1
        pos = start_pos
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            pos += 1
        return pos - start_pos

    def _extract_block(self, lines: list[str], start: int, end: int) -> str:
        """Extract block content from lines."""
        return "\n".join(lines[start - 1:end])

    def _extract_doc_comment(self, lines: list[str], line_before: int) -> str:
        """Extract doc comment (/// or //!) above a declaration."""
        if line_before < 0:
            return ""

        comments = []
        i = line_before - 1  # 0-indexed
        while i >= 0:
            line = lines[i].strip()
            if line.startswith("///") or line.startswith("//!"):
                comment = line[3:].strip()
                comments.insert(0, comment)
                i -= 1
            elif line.startswith("//"):
                # Regular comment, skip
                i -= 1
            else:
                break

        summary = " ".join(comments)
        if len(summary) > 200:
            summary = summary[:200] + "..."
        return summary

    def _parse_params(self, params_str: str) -> list[str]:
        """Parse Rust function parameters."""
        if not params_str.strip():
            return []

        params = []
        # Handle &self, &mut self, self
        depth = 0
        current = ""

        for char in params_str:
            if char in '<(':
                depth += 1
                current += char
            elif char in '>)':
                depth -= 1
                current += char
            elif char == ',' and depth == 0:
                param = current.strip()
                if param:
                    # Extract param name (before colon)
                    if ':' in param:
                        name = param.split(':')[0].strip()
                        # Handle &mut, &, mut
                        name = name.replace('&mut ', '').replace('&', '').replace('mut ', '').strip()
                        if name and name not in ('self',):
                            params.append(name)
                    elif param in ('self', '&self', '&mut self'):
                        pass  # Skip self
                    else:
                        params.append(param)
                current = ""
            else:
                current += char

        # Last param
        param = current.strip()
        if param:
            if ':' in param:
                name = param.split(':')[0].strip()
                name = name.replace('&mut ', '').replace('&', '').replace('mut ', '').strip()
                if name and name not in ('self',):
                    params.append(name)
            elif param not in ('self', '&self', '&mut self'):
                params.append(param)

        return params
