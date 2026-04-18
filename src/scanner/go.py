"""
Go scanner using token-aware regex parsing.

Extracts:
- functions (top-level) with params and return types
- methods (with receiver) + dependency edges to receiver struct
- structs (with field extraction via tokenizer)
- interfaces (with method extraction and embedding)
- interface implementation detection (struct method set satisfies interface)
- type aliases and named types
- const/var declarations
- imports
- HTTP routes (stdlib, chi, gorilla/mux, ServeHTTP)
"""

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


class GoScanner(BaseScanner):
    """
    Go code scanner using token-aware regex.

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
    - HTTP routes (stdlib, chi, gorilla/mux, ServeHTTP)
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

        # Pre-compute cleaned source for comment/string-aware matching
        cleaned = strip_comments_and_strings(content, "go")

        self._scan_imports(content, file_source_id, dependencies)
        self._scan_structs(content, cleaned, lines, rel_path, symbols, dependencies)
        self._scan_interfaces(content, cleaned, lines, rel_path, symbols, dependencies)
        method_positions = self._scan_methods(content, cleaned, lines, rel_path, symbols, dependencies)
        self._scan_functions(content, cleaned, lines, rel_path, symbols, method_positions)
        self._scan_type_aliases(content, cleaned, lines, rel_path, symbols)
        self._extract_const_var(content, lines, rel_path, symbols)
        self._detect_implementations(symbols, dependencies, rel_path)
        self._scan_http_routes(content, cleaned, lines, rel_path, symbols)

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

    def _scan_structs(self, content, cleaned, lines, rel_path, symbols, dependencies):
        """Extract struct definitions with field extraction via tokenizer."""
        for match in self.STRUCT_PATTERN.finditer(cleaned):
            name = match.group(1)
            start_line = cleaned[:match.start()].count('\n') + 1

            # Use tokenizer to find the opening brace and extract the block
            brace_pos = content.find('{', match.start())
            if brace_pos == -1:
                continue
            body_text, end_brace_pos = extract_block(content, brace_pos)
            end_line = content[:end_brace_pos + 1].count('\n') + 1

            symbols.append(Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.CLASS, name=name,
                start_line=start_line, end_line=end_line,
                content=self._extract_block(lines, start_line, end_line),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
            ))

            # Strip comments/strings from body for field extraction
            clean_body = strip_comments_and_strings(body_text, "go")

            # Extract struct fields from clean body
            struct_fields = self._extract_struct_fields(clean_body)
            if struct_fields:
                symbols[-1].metadata = {"fields": struct_fields}

            self._scan_struct_embeds(clean_body, name, start_line, rel_path, dependencies)

    def _scan_struct_embeds(self, body_text, struct_name, start_line, rel_path, dependencies):
        """Detect embedded types in struct body."""
        for embed_match in self.EMBED_PATTERN.finditer(body_text):
            raw_line = embed_match.group(0).strip()
            if not raw_line:
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

    def _scan_interfaces(self, content, cleaned, lines, rel_path, symbols, dependencies):
        """Extract interface definitions with method extraction and embedding."""
        for match in self.INTERFACE_PATTERN.finditer(cleaned):
            name = match.group(1)
            start_line = cleaned[:match.start()].count('\n') + 1

            # Use tokenizer for clean block extraction
            brace_pos = content.find('{', match.start())
            if brace_pos == -1:
                continue
            body_text, end_brace_pos = extract_block(content, brace_pos)
            end_line = content[:end_brace_pos + 1].count('\n') + 1

            # Strip comments/strings from body for method extraction
            clean_body = strip_comments_and_strings(body_text, "go")

            # Extract method names and full signatures
            iface_methods = []
            method_sigs = []
            for m in self.INTERFACE_METHOD_PATTERN.finditer(clean_body):
                method_name = m.group(1)
                iface_methods.append(method_name)
                # Extract full signature from original body
                line_start = body_text.rfind('\n', 0, m.start())
                line_end = body_text.find('\n', m.start())
                if line_end == -1:
                    line_end = len(body_text)
                sig_line = body_text[line_start + 1:line_end].strip()
                if sig_line:
                    method_sigs.append(sig_line)

            for embed_match in self.INTERFACE_EMBED_PATTERN.finditer(clean_body):
                embedded_name = embed_match.group(1)
                if embedded_name in iface_methods:
                    continue
                embed_line = start_line + clean_body[:embed_match.start()].count('\n') + 1
                dependencies.append(Dependency(
                    source_id=f"{self.project}:{rel_path}:interface:{name}",
                    target_id=f"{self.project}:{rel_path}:interface:{embedded_name}",
                    dep_type=DependencyType.EXTENDS,
                    source_line=embed_line,
                    metadata={"kind": "interface_embedding"},
                ))

            meta = {}
            if method_sigs:
                meta["method_signatures"] = method_sigs

            sym = Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.INTERFACE, name=name,
                start_line=start_line, end_line=end_line,
                content=self._extract_block(lines, start_line, end_line),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
                params=iface_methods,
            )
            if meta:
                sym.metadata = meta
            symbols.append(sym)

    def _scan_methods(self, content, cleaned, lines, rel_path, symbols, dependencies):
        """Extract methods with receiver. Returns set of method start line positions."""
        method_positions = set()
        for match in self.METHOD_PATTERN.finditer(cleaned):
            receiver_type = match.group(2)
            method_name = match.group(3)
            raw_params = match.group(4) or ""
            raw_returns = match.group(5) or match.group(6) or ""

            start_line = cleaned[:match.start()].count('\n') + 1
            method_positions.add(start_line)
            end_line = self._find_block_end(content, match.end(), start_line)

            # Parse params with full types
            parsed_params = self._parse_params_with_types(raw_params)

            symbols.append(Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.METHOD,
                name=f"{receiver_type}.{method_name}",
                start_line=start_line, end_line=end_line,
                content=self._extract_block(lines, start_line, end_line),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                params=[p["name"] for p in parsed_params] if parsed_params else self._parse_params(raw_params),
                returns=raw_returns.strip(),
                imports=[receiver_type],
                metadata={"param_types": parsed_params} if parsed_params else {},
            ))

            dependencies.append(Dependency(
                source_id=f"{self.project}:{rel_path}:method:{receiver_type}.{method_name}",
                target_id=f"{self.project}:{rel_path}:class:{receiver_type}",
                dep_type=DependencyType.EXTENDS,
                source_line=start_line,
            ))
        return method_positions

    def _scan_functions(self, content, cleaned, lines, rel_path, symbols, method_positions):
        """Extract top-level functions (excluding methods)."""
        for match in self.FUNC_PATTERN.finditer(cleaned):
            start_line = cleaned[:match.start()].count('\n') + 1
            if start_line in method_positions:
                continue

            name = match.group(1)
            raw_params = match.group(2) or ""
            raw_returns = match.group(3) or match.group(4) or ""
            end_line = self._find_block_end(content, match.end(), start_line)

            parsed_params = self._parse_params_with_types(raw_params)

            symbols.append(Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.FUNCTION, name=name,
                start_line=start_line, end_line=end_line,
                content=self._extract_block(lines, start_line, end_line),
                summary=self._extract_doc_comment(lines, start_line - 1),
                language="go",
                exports=[name] if name[0].isupper() else [],
                params=[p["name"] for p in parsed_params] if parsed_params else self._parse_params(raw_params),
                returns=raw_returns.strip(),
                metadata={"param_types": parsed_params} if parsed_params else {},
            ))

    def _scan_type_aliases(self, content, cleaned, lines, rel_path, symbols):
        """Extract type aliases and named types."""
        captured_lines = {
            s.start_line for s in symbols
            if s.symbol_type in (SymbolType.CLASS, SymbolType.INTERFACE)
        }
        for match in self.TYPE_ALIAS_PATTERN.finditer(cleaned):
            name = match.group(1)
            underlying = match.group(2).strip()
            start_line = cleaned[:match.start()].count('\n') + 1
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
        depth = 1
        pos = block_start_pos
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            if depth > 0:
                pos += 1
        return content[block_start_pos:pos]

    def _extract_const_var(self, content: str, lines: list[str],
                           rel_path: str, symbols: list[Symbol]) -> None:
        """Extract const and var declarations (single and block forms)."""
        block_ranges = set()

        for match in self.CONST_VAR_BLOCK_PATTERN.finditer(content):
            block_start_line = content[:match.start()].count('\n') + 1
            block_end_line = block_start_line + match.group(0).count('\n')
            for line_num in range(block_start_line, block_end_line + 1):
                block_ranges.add(line_num)

            block_body = match.group(1)
            for entry_match in self.CONST_VAR_ENTRY_PATTERN.finditer(block_body):
                name = entry_match.group(1)
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

        for match in self.CONST_VAR_SINGLE_PATTERN.finditer(content):
            start_line = content[:match.start()].count('\n') + 1
            if start_line in block_ranges:
                continue
            name = match.group(1)
            if name == '(':
                continue
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
        struct_methods = {}
        for s in symbols:
            if s.symbol_type == SymbolType.METHOD and "." in s.name:
                receiver, method = s.name.split(".", 1)
                struct_methods.setdefault(receiver, set()).add(method)

        interfaces = {}
        for s in symbols:
            if s.symbol_type == SymbolType.INTERFACE and s.params:
                interfaces[s.name] = set(s.params)

        for struct_name, methods in struct_methods.items():
            for iface_name, iface_methods in interfaces.items():
                if iface_methods and iface_methods.issubset(methods):
                    dependencies.append(Dependency(
                        source_id=f"{self.project}:{rel_path}:class:{struct_name}",
                        target_id=f"{self.project}:{rel_path}:interface:{iface_name}",
                        dep_type=DependencyType.IMPLEMENTS,
                        source_line=0,
                    ))

    # Struct field pattern: FieldName Type (optionally followed by tags)
    # Enhanced to handle pointer types, slices, maps, and complex types
    _STRUCT_FIELD_RE = re.compile(
        r'^\s*([A-Z][A-Za-z0-9_]*)\s+'
        r'(\*?(?:map\[[^\]]+\])?(?:\[\])*\*?[\w.]+(?:\[[^\]]*\])?)',
        re.MULTILINE
    )

    def _extract_struct_fields(self, body_text: str) -> list[dict]:
        """Extract fields from a cleaned struct body (comments/strings already stripped)."""
        fields = []
        for line in body_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped == '}':
                continue

            m = re.match(
                r'^([A-Z][A-Za-z0-9_]*)\s+'
                r'(\*?(?:map\[[^\]]+\])?(?:\[\])*\*?[\w.]+(?:\[[^\]]*\])?)',
                stripped
            )
            if m:
                field_name = m.group(1)
                field_type = m.group(2).rstrip(',')
                if field_type.startswith('`'):
                    continue
                fields.append({"name": field_name, "type": field_type})
        return fields

    # HTTP route registration patterns
    STDLIB_ROUTE_PATTERN = re.compile(
        r'\.HandleFunc\(\s*"((?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+)?(/[^"]*)"',
    )
    STDLIB_HANDLE_PATTERN = re.compile(
        r'\.Handle\(\s*"((?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+)?(/[^"]*)"',
    )
    FRAMEWORK_ROUTE_PATTERN = re.compile(
        r'\.(Get|Post|Put|Patch|Delete|Options|Head)\(\s*"(/[^"]*)"',
        re.IGNORECASE,
    )
    # chi router: r.Route("/prefix", func(r chi.Router) { ... })
    CHI_ROUTE_PATTERN = re.compile(
        r'\.Route\(\s*"(/[^"]*)"',
    )
    # gorilla/mux: router.HandleFunc("/path", handler).Methods("GET")
    GORILLA_METHODS_PATTERN = re.compile(
        r'\.HandleFunc\(\s*"(/[^"]*)"[^)]*\)\s*\.Methods\(\s*"([^"]+)"',
    )
    # ServeHTTP method pattern (struct implements http.Handler)
    SERVE_HTTP_PATTERN = re.compile(
        r'^func\s+\(\s*\w+\s+\*?(\w+)\s*\)\s+ServeHTTP\s*\(',
        re.MULTILINE,
    )

    def _scan_http_routes(self, content: str, cleaned: str, lines: list[str],
                          rel_path: str, symbols: list[Symbol]) -> None:
        """Detect HTTP route registrations (stdlib + frameworks).
        Uses cleaned source to avoid matching routes inside comments/strings."""
        # stdlib: mux.HandleFunc("GET /path", handler)
        for match in self.STDLIB_ROUTE_PATTERN.finditer(cleaned):
            method_prefix = match.group(1) or ""
            path = match.group(2)
            method = method_prefix.strip() if method_prefix else "GET"
            start_line = cleaned[:match.start()].count('\n') + 1

            after = content[match.end():]
            handler_match = re.match(r'\s*,\s*(\w+(?:\.\w+)*)', after)
            handler = handler_match.group(1) if handler_match else ""

            self._add_route_symbol(method, path, handler, start_line, lines, rel_path, symbols)

        # stdlib: http.Handle("/path", handler)
        for match in self.STDLIB_HANDLE_PATTERN.finditer(cleaned):
            method_prefix = match.group(1) or ""
            path = match.group(2)
            method = method_prefix.strip() if method_prefix else "GET"
            start_line = cleaned[:match.start()].count('\n') + 1

            after = content[match.end():]
            handler_match = re.match(r'\s*,\s*(\w+(?:\.\w+)*)', after)
            handler = handler_match.group(1) if handler_match else ""

            self._add_route_symbol(method, path, handler, start_line, lines, rel_path, symbols)

        # gorilla/mux: router.HandleFunc("/path", handler).Methods("GET")
        for match in self.GORILLA_METHODS_PATTERN.finditer(cleaned):
            path = match.group(1)
            method = match.group(2).upper()
            start_line = cleaned[:match.start()].count('\n') + 1
            self._add_route_symbol(method, path, "", start_line, lines, rel_path, symbols)

        # Frameworks: r.Get("/path", handler), e.Post("/path", handler), etc.
        for match in self.FRAMEWORK_ROUTE_PATTERN.finditer(cleaned):
            method = match.group(1).upper()
            path = match.group(2)
            start_line = cleaned[:match.start()].count('\n') + 1

            after = content[match.end():]
            handler_match = re.match(r'\s*,\s*(\w+(?:\.\w+)*)', after)
            handler = handler_match.group(1) if handler_match else ""

            self._add_route_symbol(method, path, handler, start_line, lines, rel_path, symbols)

        # chi: r.Route("/prefix", ...)
        for match in self.CHI_ROUTE_PATTERN.finditer(cleaned):
            path = match.group(1)
            start_line = cleaned[:match.start()].count('\n') + 1
            self._add_route_symbol("GROUP", path, "", start_line, lines, rel_path, symbols)

        # ServeHTTP: func (s *Server) ServeHTTP(w, r) — marks struct as HTTP handler
        for match in self.SERVE_HTTP_PATTERN.finditer(cleaned):
            receiver = match.group(1)
            start_line = cleaned[:match.start()].count('\n') + 1
            self._add_route_symbol("HANDLER", f"/{receiver}", receiver, start_line, lines, rel_path, symbols)

    def _add_route_symbol(self, method: str, path: str, handler: str,
                          start_line: int, lines: list[str], rel_path: str,
                          symbols: list[Symbol]) -> None:
        """Helper to create and add an API route symbol."""
        api_name = f"{method} {path}"
        # Deduplicate by name+line
        for existing in symbols:
            if existing.symbol_type == SymbolType.API and existing.name == api_name and existing.start_line == start_line:
                return
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

        for match in self.IMPORT_SINGLE_PATTERN.finditer(content):
            line = content[:match.start()].count('\n') + 1
            imports.append({
                "module": match.group(1),
                "names": [match.group(1).split("/")[-1]],
                "line": line,
            })

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
        """Find matching closing brace (token-aware)."""
        depth = 1
        pos = start_pos
        length = len(content)
        while pos < length and depth > 0:
            c = content[pos]

            # Skip line comments
            if c == '/' and pos + 1 < length and content[pos + 1] == '/':
                pos += 2
                while pos < length and content[pos] != '\n':
                    pos += 1
                continue

            # Skip block comments
            if c == '/' and pos + 1 < length and content[pos + 1] == '*':
                pos += 2
                while pos < length:
                    if content[pos] == '*' and pos + 1 < length and content[pos + 1] == '/':
                        pos += 2
                        break
                    pos += 1
                continue

            # Skip strings
            if c in ('"', "'", '`'):
                quote = c
                pos += 1
                if quote == '`':
                    while pos < length and content[pos] != '`':
                        pos += 1
                    if pos < length:
                        pos += 1
                else:
                    while pos < length:
                        if content[pos] == '\\' and pos + 1 < length:
                            pos += 2
                            continue
                        if content[pos] == quote:
                            pos += 1
                            break
                        pos += 1
                continue

            if c == '{':
                depth += 1
            elif c == '}':
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
        """Parse Go function parameters (names only)."""
        if not params_str.strip():
            return []

        params = []
        for param in params_str.split(","):
            param = param.strip()
            if param:
                parts = param.split()
                if parts:
                    params.append(parts[0])

        return params

    def _parse_params_with_types(self, params_str: str) -> list[dict]:
        """Parse Go function parameters with full type info.

        Go allows grouped types: (a, b int, c string) meaning a and b are both int.
        Returns list of {"name": ..., "type": ...} dicts.
        """
        if not params_str.strip():
            return []

        params = []
        # Split by comma, handling parentheses for func types
        parts = []
        depth = 0
        current = []
        for ch in params_str:
            if ch in ('(', '['):
                depth += 1
                current.append(ch)
            elif ch in (')', ']'):
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append(''.join(current).strip())

        # Process each part
        pending_names = []
        for part in parts:
            tokens = part.split(None, 1)
            if len(tokens) == 2:
                name, type_str = tokens[0], tokens[1]
                # Check if name looks like a type (no actual name given)
                if name.startswith('*') or name.startswith('[') or name.startswith('map['):
                    # This is a type-only param
                    params.append({"name": name, "type": ""})
                else:
                    # Assign type to any pending names
                    for pn in pending_names:
                        params.append({"name": pn, "type": type_str})
                    pending_names = []
                    params.append({"name": name, "type": type_str})
            elif len(tokens) == 1:
                # Could be just a name (type comes later) or a type-only param
                pending_names.append(tokens[0])

        # Any remaining pending names without types
        for pn in pending_names:
            params.append({"name": pn, "type": ""})

        return params
