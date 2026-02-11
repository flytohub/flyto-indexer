"""
Java scanner using regex-based parsing.

Extracts:
- classes
- interfaces
- methods
- imports
- extends/implements relationships
"""

import re
from pathlib import Path

try:
    from ..models import Dependency, DependencyType, Symbol, SymbolType
    from .base import BaseScanner
except ImportError:
    from models import Dependency, DependencyType, Symbol, SymbolType
    from scanner.base import BaseScanner


class JavaScanner(BaseScanner):
    """
    Java code scanner using regex.

    Handles:
    - public class Name extends Base implements Interface {}
    - public interface Name extends OtherInterface {}
    - public void method(params) {}
    - import package.Class;
    - import static package.Class.method;
    """

    supported_extensions = [".java"]

    # Regex patterns
    IMPORT_PATTERN = re.compile(
        r'^import\s+(?:static\s+)?([^;]+);',
        re.MULTILINE
    )

    PACKAGE_PATTERN = re.compile(
        r'^package\s+([^;]+);',
        re.MULTILINE
    )

    # Class: public/private/protected abstract/final class Name<T> extends Base implements A, B
    CLASS_PATTERN = re.compile(
        r'^(?:(?:public|private|protected)\s+)?(?:(?:abstract|final|static)\s+)*class\s+([A-Z]\w*)'
        r'(?:<[^>]+>)?'
        r'(?:\s+extends\s+([A-Z]\w*(?:<[^>]+>)?))?'
        r'(?:\s+implements\s+([A-Z]\w*(?:<[^>]+>)?(?:\s*,\s*[A-Z]\w*(?:<[^>]+>)?)*))?'
        r'\s*\{',
        re.MULTILINE
    )

    # Interface: public interface Name<T> extends A, B
    INTERFACE_PATTERN = re.compile(
        r'^(?:(?:public|private|protected)\s+)?interface\s+([A-Z]\w*)'
        r'(?:<[^>]+>)?'
        r'(?:\s+extends\s+([A-Z]\w*(?:<[^>]+>)?(?:\s*,\s*[A-Z]\w*(?:<[^>]+>)?)*))?'
        r'\s*\{',
        re.MULTILINE
    )

    # Enum: public enum Name implements Interface
    ENUM_PATTERN = re.compile(
        r'^(?:(?:public|private|protected)\s+)?enum\s+([A-Z]\w*)'
        r'(?:\s+implements\s+([A-Z]\w*(?:<[^>]+>)?(?:\s*,\s*[A-Z]\w*(?:<[^>]+>)?)*))?'
        r'\s*\{',
        re.MULTILINE
    )

    # Method: modifiers returnType name(params) throws Exceptions
    METHOD_PATTERN = re.compile(
        r'^[ \t]*(?:(?:public|private|protected)\s+)?(?:(?:static|final|abstract|synchronized|native)\s+)*'
        r'(?:<[^>]+>\s+)?'  # Generic return type
        r'([A-Za-z_]\w*(?:<[^>]+>)?(?:\[\])*)\s+'  # Return type
        r'([a-z_]\w*)\s*'  # Method name
        r'\(([^)]*)\)'  # Parameters
        r'(?:\s+throws\s+[A-Z]\w*(?:\s*,\s*[A-Z]\w*)*)?'  # Throws clause
        r'\s*\{',
        re.MULTILINE
    )

    # Constructor: modifiers ClassName(params)
    CONSTRUCTOR_PATTERN = re.compile(
        r'^[ \t]*(?:(?:public|private|protected)\s+)?([A-Z]\w*)\s*\(([^)]*)\)\s*(?:throws\s+[A-Z]\w*(?:\s*,\s*[A-Z]\w*)*)?\s*\{',
        re.MULTILINE
    )

    # Annotation type
    ANNOTATION_PATTERN = re.compile(
        r'^(?:(?:public|private|protected)\s+)?@interface\s+([A-Z]\w*)\s*\{',
        re.MULTILINE
    )

    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """Scan Java file."""
        symbols = []
        dependencies = []
        lines = content.splitlines()
        rel_path = str(file_path)
        file_source_id = f"{self.project}:{rel_path}:file:{file_path.stem}"

        # Extract package
        package_match = self.PACKAGE_PATTERN.search(content)
        if package_match:
            package_match.group(1).strip()

        # Extract imports
        for match in self.IMPORT_PATTERN.finditer(content):
            import_path = match.group(1).strip()
            line = content[:match.start()].count('\n') + 1

            # Parse import path
            parts = import_path.split(".")
            class_name = parts[-1] if parts else import_path

            dep = Dependency(
                source_id=file_source_id,
                target_id=import_path,
                dep_type=DependencyType.IMPORTS,
                source_line=line,
                metadata={"names": [class_name]},
            )
            dependencies.append(dep)

        # Track class positions for method attribution
        class_blocks = []

        # Extract classes
        for match in self.CLASS_PATTERN.finditer(content):
            name = match.group(1)
            extends = match.group(2)
            implements = match.group(3)

            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            class_blocks.append({
                "name": name,
                "start": match.start(),
                "end": match.end() + self._find_block_length(content, match.end()),
                "start_line": start_line,
                "end_line": end_line,
            })

            # Create extends dependency
            if extends:
                extends_name = extends.split('<')[0].strip()
                dep = Dependency(
                    source_id=f"{self.project}:{rel_path}:class:{name}",
                    target_id=extends_name,
                    dep_type=DependencyType.EXTENDS,
                    source_line=start_line,
                )
                dependencies.append(dep)

            # Create implements dependencies
            if implements:
                for iface in implements.split(","):
                    iface_name = iface.strip().split('<')[0]
                    if iface_name:
                        dep = Dependency(
                            source_id=f"{self.project}:{rel_path}:class:{name}",
                            target_id=iface_name,
                            dep_type=DependencyType.IMPLEMENTS,
                            source_line=start_line,
                        )
                        dependencies.append(dep)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.CLASS,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_javadoc(lines, start_line - 1),
                language="java",
                exports=[name],
                imports=[extends] if extends else [],
            )
            symbols.append(symbol)

        # Extract interfaces
        for match in self.INTERFACE_PATTERN.finditer(content):
            name = match.group(1)
            extends = match.group(2)

            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            class_blocks.append({
                "name": name,
                "start": match.start(),
                "end": match.end() + self._find_block_length(content, match.end()),
                "start_line": start_line,
                "end_line": end_line,
            })

            # Create extends dependencies for interfaces
            if extends:
                for ext in extends.split(","):
                    ext_name = ext.strip().split('<')[0]
                    if ext_name:
                        dep = Dependency(
                            source_id=f"{self.project}:{rel_path}:interface:{name}",
                            target_id=ext_name,
                            dep_type=DependencyType.EXTENDS,
                            source_line=start_line,
                        )
                        dependencies.append(dep)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.INTERFACE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_javadoc(lines, start_line - 1),
                language="java",
                exports=[name],
            )
            symbols.append(symbol)

        # Extract enums
        for match in self.ENUM_PATTERN.finditer(content):
            name = match.group(1)
            implements = match.group(2)

            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            class_blocks.append({
                "name": name,
                "start": match.start(),
                "end": match.end() + self._find_block_length(content, match.end()),
                "start_line": start_line,
                "end_line": end_line,
            })

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.TYPE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_javadoc(lines, start_line - 1),
                language="java",
                exports=[name],
            )
            symbols.append(symbol)

        # Extract annotation types
        for match in self.ANNOTATION_PATTERN.finditer(content):
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
                summary=self._extract_javadoc(lines, start_line - 1),
                language="java",
                exports=[name],
            )
            symbols.append(symbol)

        # Extract methods
        for match in self.METHOD_PATTERN.finditer(content):
            pos = match.start()
            return_type = match.group(1)
            method_name = match.group(2)
            params = match.group(3) or ""

            # Skip if this is actually a constructor (return type matches class name)
            is_constructor = False
            for class_block in class_blocks:
                if class_block["start"] < pos < class_block["end"]:
                    if return_type == class_block["name"]:
                        is_constructor = True
                    break

            if is_constructor:
                continue

            # Find enclosing class
            enclosing_class = None
            for class_block in class_blocks:
                if class_block["start"] < pos < class_block["end"]:
                    enclosing_class = class_block["name"]
                    break

            if not enclosing_class:
                continue  # Skip methods outside classes

            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.METHOD,
                name=f"{enclosing_class}.{method_name}",
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_javadoc(lines, start_line - 1),
                language="java",
                params=self._parse_params(params),
                returns=return_type,
                imports=[enclosing_class],
            )
            symbols.append(symbol)

        # Extract constructors
        for match in self.CONSTRUCTOR_PATTERN.finditer(content):
            pos = match.start()
            constructor_name = match.group(1)
            params = match.group(2) or ""

            # Verify it's inside the matching class
            enclosing_class = None
            for class_block in class_blocks:
                if class_block["start"] < pos < class_block["end"]:
                    if class_block["name"] == constructor_name:
                        enclosing_class = constructor_name
                    break

            if not enclosing_class:
                continue

            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)
            block_content = self._extract_block(lines, start_line, end_line)

            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.METHOD,
                name=f"{enclosing_class}.<init>",
                start_line=start_line,
                end_line=end_line,
                content=block_content,
                summary=self._extract_javadoc(lines, start_line - 1),
                language="java",
                params=self._parse_params(params),
                returns=enclosing_class,
                imports=[enclosing_class],
            )
            symbols.append(symbol)

        # Compute hashes
        for symbol in symbols:
            symbol.compute_hash()

        return symbols, dependencies

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

    def _extract_javadoc(self, lines: list[str], line_before: int) -> str:
        """Extract Javadoc comment above a declaration."""
        if line_before < 0:
            return ""

        # Look for /** ... */ block
        i = line_before - 1  # 0-indexed

        # Skip annotations
        while i >= 0:
            line = lines[i].strip()
            if line.startswith("@"):
                i -= 1
            else:
                break

        # Check if we found end of javadoc
        if i < 0:
            return ""

        line = lines[i].strip()
        if not line.endswith("*/"):
            return ""

        # Collect javadoc lines
        javadoc_lines = []
        while i >= 0:
            line = lines[i].strip()
            javadoc_lines.insert(0, line)
            if line.startswith("/**"):
                break
            i -= 1

        # Parse javadoc
        text = " ".join(javadoc_lines)
        # Remove markers
        text = text.replace("/**", "").replace("*/", "")
        text = re.sub(r'\s*\*\s*', ' ', text)
        # Remove @tags
        text = re.sub(r'@\w+[^@]*', '', text)
        text = text.strip()

        if len(text) > 200:
            text = text[:200] + "..."
        return text

    def _parse_params(self, params_str: str) -> list[str]:
        """Parse Java method parameters."""
        if not params_str.strip():
            return []

        params = []
        depth = 0
        current = ""

        for char in params_str:
            if char in '<':
                depth += 1
                current += char
            elif char in '>':
                depth -= 1
                current += char
            elif char == ',' and depth == 0:
                param = current.strip()
                if param:
                    # Format: Type name or Type... name
                    parts = param.split()
                    if len(parts) >= 2:
                        params.append(parts[-1])  # Last part is the name
                current = ""
            else:
                current += char

        # Last param
        param = current.strip()
        if param:
            parts = param.split()
            if len(parts) >= 2:
                params.append(parts[-1])

        return params
