"""
Go scanner using regex-based parsing.

Extracts:
- functions (top-level)
- methods (with receiver) + dependency edges to receiver struct
- structs (with embedded type detection)
- interfaces (with method extraction and embedding)
- interface implementation detection (struct method set satisfies interface)
- type aliases and named types
- const/var declarations
- imports
"""

import re
from pathlib import Path

try:
    from ..models import Dependency, DependencyType, Symbol, SymbolType
    from .base import BaseScanner
except ImportError:
    from models import Dependency, DependencyType, Symbol, SymbolType
    from scanner.base import BaseScanner


class GoScanner(BaseScanner):
    """
    Go code scanner using regex.

    Handles:
    - func Name() {}
    - func (r *Receiver) Method() {}
    - type Name struct {}
    - type Name interface {}
    - type Name underlying_type (type aliases)
    - const/var declarations (single and block)
    - struct embedding
    - interface method extraction and embedding
    - interface implementation detection
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

    # Interface method signatures (inside interface body)
    INTERFACE_METHOD_PATTERN = re.compile(
        r'^\s+([A-Z_a-z]\w*)\s*\(', re.MULTILINE
    )

    # Embedded types in struct bodies (line with just a type name, no field name)
    EMBED_PATTERN = re.compile(
        r'^\s+(\*?(?:[\w.]+\.)?[A-Z]\w*)\s*$', re.MULTILINE
    )

    # Go builtin types that should never be treated as embedded types
    _EMBED_BLOCKLIST = frozenset({
        "error", "string", "bool", "int", "int8", "int16", "int32", "int64",
        "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
        "float32", "float64", "complex64", "complex128", "byte", "rune",
        "any", "comparable",
    })

    # Embedded interfaces inside interface bodies (just a type name, no parens)
    INTERFACE_EMBED_PATTERN = re.compile(
        r'^\s+([A-Z_a-z]\w*)\s*$', re.MULTILINE
    )

    # Type aliases and named types: type Name underlying (not struct/interface)
    TYPE_ALIAS_PATTERN = re.compile(
        r'^type\s+([A-Z_a-z]\w*)\s+(?!struct\b|interface\b)(\S+.*?)$', re.MULTILINE
    )

    # Single const/var: const Name type = ... or var Name type
    CONST_VAR_SINGLE_PATTERN = re.compile(
        r'^(?:const|var)\s+(\w+)\s+', re.MULTILINE
    )

    # Block const/var: const ( ... ) or var ( ... )
    CONST_VAR_BLOCK_PATTERN = re.compile(
        r'^(?:const|var)\s*\(([\s\S]*?)\)', re.MULTILINE
    )

    # Individual entries inside a const/var block
    CONST_VAR_ENTRY_PATTERN = re.compile(
        r'^\s+(\w+)', re.MULTILINE
    )

    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """Scan Go file."""
        symbols = []
        dependencies = []
        lines = content.splitlines()
        rel_path = str(file_path)
        file_source_id = f"{self.project}:{rel_path}:file:{file_path.stem}"

        self._scan_imports(content, file_source_id, dependencies)
        self._scan_structs(content, lines, rel_path, symbols, dependencies)
        self._scan_interfaces(content, lines, rel_path, symbols, dependencies)
        method_positions = self._scan_methods(content, lines, rel_path, symbols, dependencies)
        self._scan_functions(content, lines, rel_path, symbols, method_positions)
        self._scan_type_aliases(content, lines, rel_path, symbols)
        self._extract_const_var(content, lines, rel_path, symbols)
        self._detect_implementations(symbols, dependencies, rel_path)
        self._scan_http_routes(content, lines, rel_path, symbols)

        for symbol in symbols:
            symbol.compute_hash()

        return symbols, dependencies

    def _scan_imports(self, content, file_source_id, dependencies):
        """Extract import statements and add dependency edges."""
        for imp in self._extract_imports(content):
            dependencies.append(Dependency(
                source_id=file_source_id,
                target_id=imp["module"],
                dep_type=DependencyType.IMPORTS,
                source_line=imp["line"],
                metadata={"alias": imp.get("alias", "")},
            ))

    def _scan_structs(self, content, lines, rel_path, symbols, dependencies):
        """Extract struct definitions with embedding detection."""
        for match in self.STRUCT_PATTERN.finditer(content):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)

            symbols.append(Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.CLASS, name=name,
                start_line=start_line, end_line=end_line,
                content=self._extract_block(lines, start_line, end_line),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
            ))

            body_text = self._extract_body_text(content, match.end(), start_line, end_line)

            # Extract struct fields
            struct_fields = self._extract_struct_fields(body_text)
            if struct_fields:
                symbols[-1].metadata = {"fields": struct_fields}

            self._scan_struct_embeds(body_text, name, start_line, rel_path, dependencies)

    def _scan_struct_embeds(self, body_text, struct_name, start_line, rel_path, dependencies):
        """Detect embedded types in struct body."""
        for embed_match in self.EMBED_PATTERN.finditer(body_text):
            raw_line = embed_match.group(0).strip()
            if raw_line.startswith("//"):
                continue
            embedded_type = embed_match.group(1).lstrip('*')
            base_type = embedded_type.split('.')[-1] if '.' in embedded_type else embedded_type
            if base_type.lower() in self._EMBED_BLOCKLIST:
                continue
            embed_line = start_line + body_text[:embed_match.start()].count('\n') + 1
            dependencies.append(Dependency(
                source_id=f"{self.project}:{rel_path}:class:{struct_name}",
                target_id=f"{self.project}:{rel_path}:class:{base_type}",
                dep_type=DependencyType.EXTENDS,
                source_line=embed_line,
                metadata={"kind": "embedding", "embedded_type": embedded_type},
            ))

    def _scan_interfaces(self, content, lines, rel_path, symbols, dependencies):
        """Extract interface definitions with method extraction and embedding."""
        for match in self.INTERFACE_PATTERN.finditer(content):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            end_line = self._find_block_end(content, match.end(), start_line)

            body_text = self._extract_body_text(content, match.end(), start_line, end_line)
            iface_methods = [m.group(1) for m in self.INTERFACE_METHOD_PATTERN.finditer(body_text)]

            for embed_match in self.INTERFACE_EMBED_PATTERN.finditer(body_text):
                embedded_name = embed_match.group(1)
                if embedded_name in iface_methods:
                    continue
                embed_line = start_line + body_text[:embed_match.start()].count('\n') + 1
                dependencies.append(Dependency(
                    source_id=f"{self.project}:{rel_path}:interface:{name}",
                    target_id=f"{self.project}:{rel_path}:interface:{embedded_name}",
                    dep_type=DependencyType.EXTENDS,
                    source_line=embed_line,
                    metadata={"kind": "interface_embedding"},
                ))

            symbols.append(Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.INTERFACE, name=name,
                start_line=start_line, end_line=end_line,
                content=self._extract_block(lines, start_line, end_line),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
                params=iface_methods,
            ))

    def _scan_methods(self, content, lines, rel_path, symbols, dependencies):
        """Extract methods with receiver. Returns set of method start line positions."""
        method_positions = set()
        for match in self.METHOD_PATTERN.finditer(content):
            receiver_type = match.group(2)
            method_name = match.group(3)
            params = match.group(4) or ""
            returns = match.group(5) or match.group(6) or ""

            start_line = content[:match.start()].count('\n') + 1
            method_positions.add(start_line)
            end_line = self._find_block_end(content, match.end(), start_line)

            symbols.append(Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.METHOD,
                name=f"{receiver_type}.{method_name}",
                start_line=start_line, end_line=end_line,
                content=self._extract_block(lines, start_line, end_line),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                params=self._parse_params(params),
                returns=returns.strip(),
                imports=[receiver_type],
            ))

            dependencies.append(Dependency(
                source_id=f"{self.project}:{rel_path}:method:{receiver_type}.{method_name}",
                target_id=f"{self.project}:{rel_path}:class:{receiver_type}",
                dep_type=DependencyType.EXTENDS,
                source_line=start_line,
            ))
        return method_positions

    def _scan_functions(self, content, lines, rel_path, symbols, method_positions):
        """Extract top-level functions (excluding methods)."""
        for match in self.FUNC_PATTERN.finditer(content):
            start_line = content[:match.start()].count('\n') + 1
            if start_line in method_positions:
                continue

            name = match.group(1)
            params = match.group(2) or ""
            returns = match.group(3) or match.group(4) or ""
            end_line = self._find_block_end(content, match.end(), start_line)

            symbols.append(Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.FUNCTION, name=name,
                start_line=start_line, end_line=end_line,
                content=self._extract_block(lines, start_line, end_line),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
                params=self._parse_params(params),
                returns=returns.strip(),
            ))

    def _scan_type_aliases(self, content, lines, rel_path, symbols):
        """Extract type aliases and named types."""
        captured_lines = {
            s.start_line for s in symbols
            if s.symbol_type in (SymbolType.CLASS, SymbolType.INTERFACE)
        }
        for match in self.TYPE_ALIAS_PATTERN.finditer(content):
            name = match.group(1)
            underlying = match.group(2).strip()
            start_line = content[:match.start()].count('\n') + 1
            if start_line in captured_lines:
                continue

            symbols.append(Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.TYPE, name=name,
                start_line=start_line, end_line=start_line,
                content=self._extract_block(lines, start_line, start_line),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
                returns=underlying,
            ))

    def _extract_body_text(self, content: str, block_start_pos: int,
                           start_line: int, end_line: int) -> str:
        """Extract the body text between opening { and closing } of a block."""
        # Find the closing brace position
        depth = 1
        pos = block_start_pos
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            if depth > 0:
                pos += 1
        # content[block_start_pos:pos] is the body (excluding braces)
        return content[block_start_pos:pos]

    def _extract_const_var(self, content: str, lines: list[str],
                           rel_path: str, symbols: list[Symbol]) -> None:
        """Extract const and var declarations (single and block forms)."""
        # Track positions of block const/var to avoid double-matching
        block_ranges = set()

        # Block form: const ( ... ) or var ( ... )
        for match in self.CONST_VAR_BLOCK_PATTERN.finditer(content):
            block_start_line = content[:match.start()].count('\n') + 1
            block_end_line = block_start_line + match.group(0).count('\n')
            for line_num in range(block_start_line, block_end_line + 1):
                block_ranges.add(line_num)

            block_body = match.group(1)
            for entry_match in self.CONST_VAR_ENTRY_PATTERN.finditer(block_body):
                name = entry_match.group(1)
                # Skip blank/comment-only entries
                if name in ('_', ''):
                    continue
                entry_line = block_start_line + block_body[:entry_match.start()].count('\n') + 1
                symbol = Symbol(
                    project=self.project,
                    path=rel_path,
                    symbol_type=SymbolType.VARIABLE,
                    name=name,
                    start_line=entry_line,
                    end_line=entry_line,
                    content=self._extract_block(lines, entry_line, entry_line),
                    language="go",
                    exports=[name] if name[0:1].isupper() else [],
                )
                symbols.append(symbol)

        # Single form: const Name ... or var Name ...
        for match in self.CONST_VAR_SINGLE_PATTERN.finditer(content):
            start_line = content[:match.start()].count('\n') + 1
            if start_line in block_ranges:
                continue
            name = match.group(1)
            if name == '(':
                continue  # This is a block opener, skip
            symbol = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.VARIABLE,
                name=name,
                start_line=start_line,
                end_line=start_line,
                content=self._extract_block(lines, start_line, start_line),
                language="go",
                exports=[name] if name[0:1].isupper() else [],
            )
            symbols.append(symbol)

    def _detect_implementations(self, symbols: list[Symbol],
                                dependencies: list[Dependency],
                                rel_path: str) -> None:
        """Detect when a struct's method set satisfies an interface's method set."""
        # Build method sets per receiver type
        struct_methods = {}
        for s in symbols:
            if s.symbol_type == SymbolType.METHOD and "." in s.name:
                receiver, method = s.name.split(".", 1)
                struct_methods.setdefault(receiver, set()).add(method)

        # Build interface method sets (from params field)
        interfaces = {}
        for s in symbols:
            if s.symbol_type == SymbolType.INTERFACE and s.params:
                interfaces[s.name] = set(s.params)

        # Match: if struct has all interface methods -> IMPLEMENTS
        for struct_name, methods in struct_methods.items():
            for iface_name, iface_methods in interfaces.items():
                if iface_methods and iface_methods.issubset(methods):
                    dependencies.append(Dependency(
                        source_id=f"{self.project}:{rel_path}:class:{struct_name}",
                        target_id=f"{self.project}:{rel_path}:interface:{iface_name}",
                        dep_type=DependencyType.IMPLEMENTS,
                        source_line=0,
                    ))

    # Struct field pattern: FieldName type (optionally followed by tags)
    STRUCT_FIELD_PATTERN = re.compile(
        r'^\s+([A-Z][A-Za-z0-9_]*)\s+(\S+)', re.MULTILINE
    )

    def _extract_struct_fields(self, body_text: str) -> list[dict]:
        """Extract fields from a struct body."""
        fields = []
        for line in body_text.splitlines():
            stripped = line.strip()
            # Skip empty, comments, closing brace, embedded types (no uppercase field name pattern)
            if not stripped or stripped.startswith("//") or stripped == "}":
                continue
            m = re.match(r'^([A-Z][A-Za-z0-9_]*)\s+(\S+)', stripped)
            if m:
                field_name = m.group(1)
                field_type = m.group(2).rstrip(',')
                # Strip json tags if captured
                if field_type.startswith('`'):
                    continue
                fields.append({"name": field_name, "type": field_type})
        return fields

    # HTTP route registration patterns
    STDLIB_ROUTE_PATTERN = re.compile(
        r'\.HandleFunc\(\s*"((?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+)?(/[^"]*)"',
    )
    FRAMEWORK_ROUTE_PATTERN = re.compile(
        r'\.(Get|Post|Put|Patch|Delete|Options|Head)\(\s*"(/[^"]*)"',
        re.IGNORECASE,
    )

    def _scan_http_routes(self, content: str, lines: list[str],
                          rel_path: str, symbols: list[Symbol]) -> None:
        """Detect HTTP route registrations (stdlib + frameworks)."""
        # stdlib: mux.HandleFunc("GET /path", handler) or http.HandleFunc("/path", handler)
        for match in self.STDLIB_ROUTE_PATTERN.finditer(content):
            method_prefix = match.group(1) or ""
            path = match.group(2)
            method = method_prefix.strip() if method_prefix else "GET"
            start_line = content[:match.start()].count('\n') + 1

            # Try to extract handler name from after the path
            after = content[match.end():]
            handler_match = re.match(r'\s*,\s*(\w+(?:\.\w+)*)', after)
            handler = handler_match.group(1) if handler_match else ""

            api_name = f"{method} {path}"
            sym = Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.API, name=api_name,
                start_line=start_line, end_line=start_line,
                content=self._extract_block(lines, start_line, start_line),
                summary=f"{method} {path} -> {handler}",
                language="go",
            )
            sym.metadata = {"method": method, "path": path, "handler": handler}
            sym.compute_hash()
            symbols.append(sym)

        # Frameworks: r.GET("/path", handler), e.POST("/path", handler), etc.
        for match in self.FRAMEWORK_ROUTE_PATTERN.finditer(content):
            method = match.group(1).upper()
            path = match.group(2)
            start_line = content[:match.start()].count('\n') + 1

            after = content[match.end():]
            handler_match = re.match(r'\s*,\s*(\w+(?:\.\w+)*)', after)
            handler = handler_match.group(1) if handler_match else ""

            api_name = f"{method} {path}"
            sym = Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.API, name=api_name,
                start_line=start_line, end_line=start_line,
                content=self._extract_block(lines, start_line, start_line),
                summary=f"{method} {path} -> {handler}",
                language="go",
            )
            sym.metadata = {"method": method, "path": path, "handler": handler}
            sym.compute_hash()
            symbols.append(sym)

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
