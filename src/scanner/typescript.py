"""
TypeScript/JavaScript scanner using regex-based parsing.

Extracts:
- Functions (function declarations, arrow functions, exports)
- Classes
- Interfaces/Types
- Composables (useXxx functions)
- Imports
"""

import re
from pathlib import Path
from typing import Optional

try:
    from .base import BaseScanner
    from ..models import Symbol, Dependency, SymbolType, DependencyType
except ImportError:
    from scanner.base import BaseScanner
    from models import Symbol, Dependency, SymbolType, DependencyType


class TypeScriptScanner(BaseScanner):
    """
    TypeScript/JavaScript scanner

    Extracts:
    - functions
    - classes
    - interfaces/types
    - composables (useXxx)
    - imports
    """

    supported_extensions = [".ts", ".tsx", ".js", ".jsx"]

    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """Scan a TypeScript/JavaScript file"""
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
                metadata={"names": imp["names"]},
            )
            dependencies.append(dep)

        # Extract calls (function calls)
        calls = self._extract_calls(content)
        for call in calls:
            dep = Dependency(
                source_id=file_source_id,
                target_id=call["name"],  # Raw call name, resolved later
                dep_type=DependencyType.CALLS,
                source_line=call["line"],
                metadata={"raw_call": True},
            )
            dependencies.append(dep)

        # Extract functions
        for match in re.finditer(
            r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)',
            content, re.MULTILINE
        ):
            name = match.group(1)
            params = match.group(2)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            func_content = '\n'.join(lines[start_line-1:end_line])

            # Determine if it's a composable
            symbol_type = SymbolType.COMPOSABLE if name.startswith('use') else SymbolType.FUNCTION

            symbols.append(Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=symbol_type,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=func_content,
                summary=self._extract_jsdoc(content, match.start()),
                language="typescript",
                exports=[name] if 'export' in content[max(0, match.start()-20):match.start()] else [],
                params=[p.strip().split(':')[0].strip() for p in params.split(',') if p.strip()],
            ))

        # Extract arrow function exports: export const xxx = () => {}
        for match in re.finditer(
            r'^export\s+const\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>',
            content, re.MULTILINE
        ):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            func_content = '\n'.join(lines[start_line-1:end_line])

            symbol_type = SymbolType.COMPOSABLE if name.startswith('use') else SymbolType.FUNCTION

            symbols.append(Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=symbol_type,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=func_content,
                summary=self._extract_jsdoc(content, match.start()),
                language="typescript",
                exports=[name],
            ))

        # Extract classes
        for match in re.finditer(
            r'^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?',
            content, re.MULTILINE
        ):
            name = match.group(1)
            extends = match.group(2)
            implements = match.group(3)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            class_content = '\n'.join(lines[start_line-1:end_line])

            symbols.append(Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.CLASS,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=class_content,
                summary=self._extract_jsdoc(content, match.start()),
                language="typescript",
                exports=[name] if 'export' in content[max(0, match.start()-20):match.start()] else [],
                imports=[extends] if extends else [],
            ))

        # Extract interfaces
        for match in re.finditer(
            r'^(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+([^{]+))?',
            content, re.MULTILINE
        ):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            interface_content = '\n'.join(lines[start_line-1:end_line])

            symbols.append(Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.INTERFACE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=interface_content,
                language="typescript",
                exports=[name] if 'export' in content[max(0, match.start()-20):match.start()] else [],
            ))

        # Extract type aliases
        for match in re.finditer(
            r'^(?:export\s+)?type\s+(\w+)(?:<[^>]*>)?\s*=',
            content, re.MULTILINE
        ):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            # Type aliases usually end at semicolon or newline
            end_match = re.search(r';|\n\n', content[match.end():])
            if end_match:
                end_pos = match.end() + end_match.end()
            else:
                end_pos = match.end() + 100
            end_line = content[:end_pos].count('\n') + 1
            type_content = '\n'.join(lines[start_line-1:end_line])

            symbols.append(Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.TYPE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=type_content,
                language="typescript",
                exports=[name] if 'export' in content[max(0, match.start()-20):match.start()] else [],
            ))

        # Compute hash
        for symbol in symbols:
            symbol.compute_hash()

        return symbols, dependencies

    def _extract_imports(self, content: str) -> list[dict]:
        """Extract import statements"""
        imports = []

        # import { x, y } from 'module'
        for match in re.finditer(
            r"import\s+(?:\{([^}]+)\}|(\w+))\s+from\s+['\"]([^'\"]+)['\"]",
            content
        ):
            names_str = match.group(1) or match.group(2)
            module = match.group(3)
            names = [n.strip().split(' as ')[0].strip() for n in names_str.split(',')] if names_str else []
            line = content[:match.start()].count('\n') + 1
            imports.append({
                "module": module,
                "names": names,
                "line": line,
            })

        # import * as x from 'module'
        for match in re.finditer(
            r"import\s+\*\s+as\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]",
            content
        ):
            imports.append({
                "module": match.group(2),
                "names": [match.group(1)],
                "line": content[:match.start()].count('\n') + 1,
            })

        return imports

    def _find_block_end(self, content: str, start_pos: int, start_line: int) -> int:
        """Find end position of code block"""
        # Simple bracket matching
        depth = 0
        in_string = False
        string_char = None
        pos = start_pos

        while pos < len(content):
            char = content[pos]

            # Handle strings
            if char in '"\'`' and (pos == 0 or content[pos-1] != '\\'):
                if not in_string:
                    in_string = True
                    string_char = char
                elif char == string_char:
                    in_string = False
                pos += 1
                continue

            if in_string:
                pos += 1
                continue

            # Handle brackets
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return content[:pos+1].count('\n') + 1

            pos += 1

        # If not found, return a reasonable end line
        return min(start_line + 50, content.count('\n') + 1)

    def _extract_jsdoc(self, content: str, pos: int) -> str:
        """Extract JSDoc comments"""
        # Look backward for JSDoc
        search_start = max(0, pos - 500)
        search_content = content[search_start:pos]

        match = re.search(r'/\*\*\s*(.*?)\s*\*/', search_content, re.DOTALL)
        if match:
            # Clean up comment
            doc = match.group(1)
            doc = re.sub(r'\n\s*\*\s*', ' ', doc)
            doc = re.sub(r'@\w+.*', '', doc)  # Remove @param etc.
            return doc.strip()[:200]

        return ""

    def _extract_calls(self, content: str) -> list[dict]:
        """
        Extract function calls

        Matches patterns like:
        - functionName(
        - object.method(
        - useComposable(

        Returns:
            List of dicts with 'name' and 'line' keys
        """
        calls = []
        seen = set()  # Avoid duplicates

        # Keywords and control structures to skip
        skip_keywords = {
            'if', 'for', 'while', 'switch', 'catch', 'function', 'return',
            'new', 'typeof', 'instanceof', 'delete', 'void', 'throw',
            'async', 'await', 'import', 'export', 'from', 'class',
            'const', 'let', 'var', 'else', 'try', 'finally',
        }

        # Match function calls: identifier( or identifier.identifier(
        # Also handle chains like a.b.c(
        pattern = r'(\b[a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)*)\s*\('

        for match in re.finditer(pattern, content):
            name = match.group(1)
            line = content[:match.start()].count('\n') + 1

            # Skip keywords
            first_part = name.split('.')[0]
            if first_part in skip_keywords:
                continue

            # Skip common built-ins that are not useful to track
            if first_part in {'console', 'Math', 'JSON', 'Object', 'Array', 'String', 'Number', 'Boolean', 'Date', 'Promise', 'Error'}:
                continue

            # Skip if inside a string or comment (simple heuristic)
            before = content[max(0, match.start()-50):match.start()]
            if before.count('"') % 2 == 1 or before.count("'") % 2 == 1:
                continue

            key = (name, line)
            if key not in seen:
                seen.add(key)
                calls.append({
                    "name": name,
                    "line": line,
                })

        return calls
