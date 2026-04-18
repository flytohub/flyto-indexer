"""
TypeScript/JavaScript scanner using token-aware regex parsing.

Extracts:
- Functions (function declarations, arrow functions, exports)
- Classes (with extends, implements, methods, properties)
- Interfaces/Types (with field extraction via tokenizer)
- Composables (useXxx functions)
- React components (uppercase function in .tsx/.jsx)
- Decorators (NestJS, Angular)
- Imports and re-exports
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


class TypeScriptScanner(BaseScanner):
    """
    TypeScript/JavaScript scanner using token-aware regex.

    Extracts:
    - functions
    - classes (with extends, implements, methods, properties)
    - interfaces/types (with field extraction)
    - composables (useXxx)
    - React components (uppercase function in .tsx/.jsx)
    - decorators (@Controller, @Injectable, etc.)
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
        is_tsx_jsx = file_path.suffix in ('.tsx', '.jsx')

        # Pre-compute cleaned source
        cleaned = strip_comments_and_strings(content, "ts")

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

            if imp.get("re_export"):
                re_dep = Dependency(
                    source_id=file_source_id,
                    target_id=imp["module"],
                    dep_type=DependencyType.RE_EXPORTS,
                    source_line=imp["line"],
                    metadata={
                        "re_export": True,
                        "original_module": imp["module"],
                        "names": imp["names"],
                        "star": imp.get("star", False),
                    },
                )
                dependencies.append(re_dep)

        # Extract calls with function-level caller attribution
        calls = self._extract_calls(content, cleaned)
        # We'll build func_ranges after symbol extraction (below) but calls are
        # extracted from cleaned source which matches line numbers.  To avoid
        # a two-pass approach, collect calls now and attribute after symbols
        # are known.  Store for post-processing.
        _pending_calls = calls

        # Track what we've already processed to avoid duplicates
        processed_lines = set()

        # Extract functions (use cleaned source to avoid matching inside comments/strings)
        for match in re.finditer(
            r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)',
            cleaned, re.MULTILINE
        ):
            name = match.group(1)
            params = match.group(2)
            start_line = cleaned[:match.start()].count('\n') + 1
            if start_line in processed_lines:
                continue
            processed_lines.add(start_line)
            end_line = self._find_block_end(content, match.end(), start_line)
            func_content = '\n'.join(lines[start_line-1:end_line])

            # Determine symbol type
            if name.startswith('use'):
                symbol_type = SymbolType.COMPOSABLE
            elif is_tsx_jsx and name[0:1].isupper():
                symbol_type = SymbolType.COMPONENT
            else:
                symbol_type = SymbolType.FUNCTION

            is_exported = bool(re.search(r'export\s+', cleaned[max(0, match.start()-30):match.start()]))

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
                exports=[name] if is_exported or 'export' in content[max(0, match.start()-20):match.start()] else [],
                params=[p.strip().split(':')[0].strip() for p in params.split(',') if p.strip()],
            ))

        # Extract arrow function exports: export const xxx = () => {}
        for match in re.finditer(
            r'^export\s+const\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>',
            cleaned, re.MULTILINE
        ):
            name = match.group(1)
            start_line = cleaned[:match.start()].count('\n') + 1
            if start_line in processed_lines:
                continue
            processed_lines.add(start_line)
            end_line = self._find_block_end(content, match.end(), start_line)
            func_content = '\n'.join(lines[start_line-1:end_line])

            if name.startswith('use'):
                symbol_type = SymbolType.COMPOSABLE
            elif is_tsx_jsx and name[0:1].isupper():
                symbol_type = SymbolType.COMPONENT
            else:
                symbol_type = SymbolType.FUNCTION

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

        # Extract classes (with full support for extends, implements, abstract)
        for match in re.finditer(
            r'^(?:export\s+)?(?:export\s+default\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?',
            cleaned, re.MULTILINE
        ):
            name = match.group(1)
            extends = match.group(2)
            implements_str = match.group(3)
            start_line = cleaned[:match.start()].count('\n') + 1
            if start_line in processed_lines:
                continue
            processed_lines.add(start_line)

            # Find class body using tokenizer
            brace_pos = content.find('{', match.start())
            if brace_pos == -1:
                continue
            class_body, end_brace_pos = extract_block(content, brace_pos)
            end_line = content[:end_brace_pos + 1].count('\n') + 1
            class_content = '\n'.join(lines[start_line-1:end_line])

            is_exported = bool(re.search(r'export\s+', cleaned[max(0, match.start()-30):match.start()]))
            is_default = 'default' in cleaned[max(0, match.start()-30):match.start() + 50]
            is_abstract = 'abstract' in cleaned[max(0, match.start()-20):match.start() + len(match.group(0))]

            # Parse implements list
            implements_list = []
            if implements_str:
                implements_list = [i.strip() for i in implements_str.split(',') if i.strip()]

            # Extract class methods and properties from body
            clean_body = strip_comments_and_strings(class_body, "ts")
            class_methods = self._extract_class_members(clean_body, class_body)

            meta = {}
            if class_methods.get("methods"):
                meta["methods"] = class_methods["methods"]
            if class_methods.get("properties"):
                meta["properties"] = class_methods["properties"]
            if implements_list:
                meta["implements"] = implements_list
            if is_abstract:
                meta["abstract"] = True
            if is_default:
                meta["default_export"] = True

            exports = []
            if is_exported:
                exports = [name]

            sym = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.CLASS,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=class_content,
                summary=self._extract_jsdoc(content, match.start()),
                language="typescript",
                exports=exports,
                imports=[extends] if extends else [],
            )
            if meta:
                sym.metadata = meta
            symbols.append(sym)

        # Extract interfaces (use tokenizer for body)
        for match in re.finditer(
            r'^(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+([^{]+))?',
            cleaned, re.MULTILINE
        ):
            name = match.group(1)
            extends_str = match.group(2)
            start_line = cleaned[:match.start()].count('\n') + 1
            if start_line in processed_lines:
                continue
            processed_lines.add(start_line)

            # Use tokenizer for block extraction
            brace_pos = content.find('{', match.start())
            if brace_pos == -1:
                continue
            iface_body, end_brace_pos = extract_block(content, brace_pos)
            end_line = content[:end_brace_pos + 1].count('\n') + 1
            interface_content = '\n'.join(lines[start_line-1:end_line])

            is_exported = bool(re.search(r'export\s+', cleaned[max(0, match.start()-30):match.start()]))

            iface_sym = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.INTERFACE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=interface_content,
                language="typescript",
                exports=[name] if is_exported or 'export' in content[max(0, match.start()-20):match.start()] else [],
            )

            # Extract fields from cleaned body
            clean_body = strip_comments_and_strings(iface_body, "ts")
            iface_fields = self._extract_ts_fields_from_body(clean_body, iface_body)
            meta = {}
            if iface_fields:
                meta["fields"] = iface_fields
            if extends_str:
                extends_list = [e.strip() for e in extends_str.split(',') if e.strip()]
                if extends_list:
                    meta["extends"] = extends_list
                    iface_sym.imports = extends_list
            if meta:
                iface_sym.metadata = meta
            symbols.append(iface_sym)

        # Extract type aliases
        for match in re.finditer(
            r'^(?:export\s+)?type\s+(\w+)(?:<[^>]*>)?\s*=',
            cleaned, re.MULTILINE
        ):
            name = match.group(1)
            start_line = cleaned[:match.start()].count('\n') + 1
            if start_line in processed_lines:
                continue
            processed_lines.add(start_line)

            # Check if type body starts with { (object type)
            after_eq = cleaned[match.end():].lstrip()
            if after_eq.startswith('{'):
                brace_pos = content.find('{', match.end())
                if brace_pos != -1:
                    type_body, end_brace_pos = extract_block(content, brace_pos)
                    end_line = content[:end_brace_pos + 1].count('\n') + 1
                else:
                    end_match = re.search(r';|\n\n', content[match.end():])
                    end_pos = match.end() + end_match.end() if end_match else match.end() + 100
                    end_line = content[:end_pos].count('\n') + 1
            else:
                end_match = re.search(r';|\n\n', content[match.end():])
                end_pos = match.end() + end_match.end() if end_match else match.end() + 100
                end_line = content[:end_pos].count('\n') + 1

            type_content = '\n'.join(lines[start_line-1:end_line])
            is_exported = bool(re.search(r'export\s+', cleaned[max(0, match.start()-30):match.start()]))

            type_sym = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.TYPE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=type_content,
                language="typescript",
                exports=[name] if is_exported or 'export' in content[max(0, match.start()-20):match.start()] else [],
            )

            # Extract fields from object-shaped type aliases
            type_fields = self._extract_ts_fields(type_content)
            if type_fields:
                type_sym.metadata = {"fields": type_fields}
            symbols.append(type_sym)

        # Extract decorators (NestJS, Angular)
        self._extract_decorators(content, cleaned, lines, rel_path, symbols)

        # Extract backend route definitions (Express/Hono/Fastify)
        self._extract_backend_routes(content, cleaned, lines, rel_path, symbols)

        # Extract API calls (fetch/axios/etc.)
        api_calls = self._extract_api_calls(content)
        for api_call in api_calls:
            dep = Dependency(
                source_id=file_source_id,
                target_id=api_call["url"],
                dep_type=DependencyType.API_CALLS,
                source_line=api_call["line"],
                metadata={
                    "method": api_call["method"],
                    "url": api_call["url"],
                    "raw_url": api_call["raw_url"],
                },
            )
            dependencies.append(dep)

        # Attribute pending calls to enclosing functions
        func_ranges = self._build_function_ranges(symbols, rel_path)
        for call in _pending_calls:
            caller_id = self._find_enclosing_function(
                call["line"], func_ranges, file_source_id
            )
            dep = Dependency(
                source_id=caller_id,
                target_id=call["name"],
                dep_type=DependencyType.CALLS,
                source_line=call["line"],
                metadata={"raw_call": True},
            )
            dependencies.append(dep)

        # Compute hash
        for symbol in symbols:
            symbol.compute_hash()

        return symbols, dependencies

    # TS field pattern: optional readonly, field name, optional ?, colon, type
    _TS_FIELD_PATTERN = re.compile(
        r'^\s+(?:readonly\s+)?(\w+)\??\s*:\s*(.+?)[\s;,]*$', re.MULTILINE
    )

    def _extract_ts_fields(self, body_content: str) -> list[dict]:
        """Extract fields from interface/type body content (legacy — operates on raw content)."""
        fields = []
        seen = set()
        for m in self._TS_FIELD_PATTERN.finditer(body_content):
            name = m.group(1)
            type_str = m.group(2).strip().rstrip(';,')
            if name in seen or name in ('export', 'import', 'return', 'const', 'let', 'var', 'function', 'type', 'interface', 'class'):
                continue
            seen.add(name)
            fields.append({"name": name, "type": type_str})
        return fields

    def _extract_ts_fields_from_body(self, clean_body: str, original_body: str) -> list[dict]:
        """Extract fields from a tokenizer-cleaned interface/type body.

        Handles nested objects, generics, union types correctly because
        comments and strings are already stripped.
        """
        fields = []
        seen = set()
        skip_keywords = frozenset({
            'export', 'import', 'return', 'const', 'let', 'var',
            'function', 'type', 'interface', 'class',
        })

        # Parse line by line from the original body, using cleaned body to detect structure
        for line in original_body.splitlines():
            stripped = line.strip()
            if not stripped or stripped in ('{', '}'):
                continue

            # Use cleaned version for matching
            clean_line = strip_comments_and_strings(stripped, "ts")

            # Match: fieldName?: type or readonly fieldName: type
            m = re.match(
                r'(?:readonly\s+)?(\w+)(\?)?:\s*(.+?)[\s;,]*$',
                clean_line.strip()
            )
            if not m:
                continue

            name = m.group(1)
            optional = bool(m.group(2))
            type_str = m.group(3).strip().rstrip(';,')

            if name in seen or name in skip_keywords:
                continue
            seen.add(name)

            field = {"name": name, "type": type_str}
            if optional:
                field["optional"] = True
            fields.append(field)

        return fields

    def _extract_class_members(self, clean_body: str, original_body: str) -> dict:
        """Extract methods and properties from a class body."""
        methods = []
        properties = []

        # Method patterns in class body
        method_re = re.compile(
            r'^\s*(?:(?:public|private|protected|static|async|abstract|override|readonly)\s+)*'
            r'(\w+)\s*(?:<[^>]*>)?\s*\([^)]*\)',
            re.MULTILINE
        )

        # Property pattern
        prop_re = re.compile(
            r'^\s*(?:(?:public|private|protected|static|readonly|override|declare)\s+)*'
            r'(\w+)(?:\?)?:\s*(.+?)[\s;]*$',
            re.MULTILINE
        )

        skip = frozenset({'if', 'for', 'while', 'switch', 'catch', 'return', 'throw', 'new', 'constructor'})

        for m in method_re.finditer(clean_body):
            name = m.group(1)
            if name not in skip:
                methods.append(name)

        # Include constructor explicitly
        if re.search(r'constructor\s*\(', clean_body):
            methods.insert(0, 'constructor')

        for m in prop_re.finditer(clean_body):
            name = m.group(1)
            type_str = m.group(2).strip().rstrip(';,')
            if name not in skip and name not in methods:
                properties.append({"name": name, "type": type_str})

        return {"methods": methods, "properties": properties}

    # Decorator patterns
    _DECORATOR_CONTROLLER = re.compile(
        r"@Controller\(\s*['\"]([^'\"]*)['\"]", re.MULTILINE
    )
    _DECORATOR_INJECTABLE = re.compile(
        r"@Injectable\(\s*\)", re.MULTILINE
    )
    _DECORATOR_MODULE = re.compile(
        r"@Module\(\s*\{", re.MULTILINE
    )
    _DECORATOR_HTTP_METHOD = re.compile(
        r"@(Get|Post|Put|Patch|Delete|Options|Head)\(\s*['\"]([^'\"]*)['\"]",
        re.MULTILINE | re.IGNORECASE,
    )

    def _extract_decorators(self, content: str, cleaned: str, lines: list[str],
                            rel_path: str, symbols: list[Symbol]) -> None:
        """Detect NestJS/Angular decorators and create API symbols."""
        # @Controller('/path') -> find class below, create API route
        controller_prefix = ""
        for match in self._DECORATOR_CONTROLLER.finditer(cleaned):
            controller_prefix = match.group(1)
            start_line = cleaned[:match.start()].count('\n') + 1
            sym = Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.API,
                name=f"CONTROLLER {controller_prefix}",
                start_line=start_line, end_line=start_line,
                content='\n'.join(lines[start_line-1:start_line]),
                summary=f"NestJS controller: {controller_prefix}",
                language="typescript",
            )
            sym.metadata = {"decorator": "Controller", "path": controller_prefix}
            sym.compute_hash()
            symbols.append(sym)

        # @Get('/path'), @Post('/path'), etc. -> API routes
        for match in self._DECORATOR_HTTP_METHOD.finditer(cleaned):
            method = match.group(1).upper()
            path = match.group(2)
            full_path = f"{controller_prefix}{path}" if controller_prefix else path
            start_line = cleaned[:match.start()].count('\n') + 1

            api_name = f"{method} {full_path}"
            sym = Symbol(
                project=self.project, path=rel_path,
                symbol_type=SymbolType.API, name=api_name,
                start_line=start_line, end_line=start_line,
                content='\n'.join(lines[start_line-1:start_line]),
                summary=f"{method} {full_path}",
                language="typescript",
            )
            sym.metadata = {"method": method, "path": full_path, "decorator": True}
            sym.compute_hash()
            symbols.append(sym)

    # Backend route pattern: app.get('/path', ...), router.post('/path', ...), etc.
    _BACKEND_ROUTE_PATTERN = re.compile(
        r'(?:app|router|server)\.(get|post|put|patch|delete|options|head|all)\s*\(\s*[\'"]([^\'"]+)[\'"]',
        re.IGNORECASE,
    )

    def _extract_backend_routes(self, content: str, cleaned: str, lines: list[str],
                                rel_path: str, symbols: list[Symbol]) -> None:
        """Detect Express/Hono/Fastify backend route definitions.
        Uses cleaned source to avoid matching inside comments/strings."""
        for match in self._BACKEND_ROUTE_PATTERN.finditer(cleaned):
            method = match.group(1).upper()
            path = match.group(2)
            start_line = cleaned[:match.start()].count('\n') + 1

            after = content[match.end():]
            handler_match = re.match(r'\s*,\s*(\w+)', after)
            handler = handler_match.group(1) if handler_match else ""

            api_name = f"{method} {path}"
            sym = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.API,
                name=api_name,
                start_line=start_line,
                end_line=start_line,
                content='\n'.join(lines[start_line-1:start_line]),
                summary=f"{method} {path} -> {handler}",
                language="typescript",
            )
            sym.metadata = {"method": method, "path": path, "handler": handler}
            sym.compute_hash()
            symbols.append(sym)

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

        # Dynamic imports: import('./module') or import("./module")
        for match in re.finditer(
            r"import\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            content
        ):
            imports.append({
                "module": match.group(1),
                "names": [],
                "line": content[:match.start()].count('\n') + 1,
            })

        # CommonJS require: require('./module') or require("./module")
        for match in re.finditer(
            r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            content
        ):
            imports.append({
                "module": match.group(1),
                "names": [],
                "line": content[:match.start()].count('\n') + 1,
            })

        # Re-exports: export { x, y } from './module'
        for match in re.finditer(
            r"export\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]",
            content
        ):
            names_str = match.group(1)
            module = match.group(2)
            names = []
            for n in names_str.split(','):
                n = n.strip()
                if ' as ' in n:
                    names.append(n.split(' as ')[-1].strip())
                elif n:
                    names.append(n)
            line = content[:match.start()].count('\n') + 1
            imports.append({
                "module": module,
                "names": names,
                "line": line,
                "re_export": True,
            })

        # Re-exports: export * from './module'
        for match in re.finditer(
            r"export\s+\*\s+from\s+['\"]([^'\"]+)['\"]",
            content
        ):
            module = match.group(1)
            line = content[:match.start()].count('\n') + 1
            imports.append({
                "module": module,
                "names": [],
                "line": line,
                "re_export": True,
                "star": True,
            })

        return imports

    def _find_block_end(self, content: str, start_pos: int, start_line: int) -> int:
        """Find end position of code block (token-aware)."""
        depth = 0
        i = start_pos
        length = len(content)

        while i < length:
            c = content[i]

            # Skip line comments
            if c == '/' and i + 1 < length and content[i + 1] == '/':
                i += 2
                while i < length and content[i] != '\n':
                    i += 1
                continue

            # Skip block comments
            if c == '/' and i + 1 < length and content[i + 1] == '*':
                i += 2
                while i < length:
                    if content[i] == '*' and i + 1 < length and content[i + 1] == '/':
                        i += 2
                        break
                    i += 1
                continue

            # Handle strings (with proper escape handling)
            if c in '"\'':
                quote = c
                i += 1
                while i < length:
                    if content[i] == '\\' and i + 1 < length:
                        i += 2
                        continue
                    if content[i] == quote:
                        i += 1
                        break
                    i += 1
                continue

            # Template literals
            if c == '`':
                i += 1
                tmpl_depth = 0
                while i < length:
                    tc = content[i]
                    if tc == '\\' and i + 1 < length:
                        i += 2
                        continue
                    if tc == '`' and tmpl_depth == 0:
                        i += 1
                        break
                    if tc == '$' and i + 1 < length and content[i + 1] == '{':
                        i += 2
                        tmpl_depth += 1
                        continue
                    if tc == '{' and tmpl_depth > 0:
                        tmpl_depth += 1
                    elif tc == '}' and tmpl_depth > 0:
                        tmpl_depth -= 1
                    i += 1
                continue

            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return content[:i+1].count('\n') + 1

            i += 1

        return min(start_line + 50, content.count('\n') + 1)

    def _extract_jsdoc(self, content: str, pos: int) -> str:
        """Extract JSDoc comments"""
        search_start = max(0, pos - 500)
        search_content = content[search_start:pos]

        match = re.search(r'/\*\*\s*(.*?)\s*\*/', search_content, re.DOTALL)
        if match:
            doc = match.group(1)
            doc = re.sub(r'\n\s*\*\s*', ' ', doc)
            doc = re.sub(r'@\w+.*', '', doc)
            return doc.strip()[:200]

        return ""

    # Patterns for detecting frontend HTTP API calls (must contain /api/)
    _API_CALL_PATTERNS = [
        re.compile(r'''(?:fetch|useFetch|useAsyncData|\$fetch)\s*\(\s*[`"']([^`"']*?/api/[^`"']*?)[`"']'''),
        re.compile(r'''axios\s*\.\s*(get|post|put|delete|patch)\s*\(\s*[`"']([^`"']*?)[`"']''', re.I),
        re.compile(r'''(?:\$?api|http|\$http|request)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*[`"']([^`"']*?)[`"']''', re.I),
        re.compile(r'''[`"']([^`"']*?/api/[^`"']*?)[`"']\s*[,)]'''),
    ]

    _TEMPLATE_VAR_RE = re.compile(r'\$\{[^}]*\}')

    @staticmethod
    def _normalize_api_url(url: str) -> str:
        """Normalize API URL: strip query params, replace template vars with *."""
        url = url.split('?')[0]
        url = TypeScriptScanner._TEMPLATE_VAR_RE.sub('*', url)
        return url

    def _extract_api_calls(self, content: str) -> list[dict]:
        """Extract frontend HTTP API calls (fetch, axios, $http, etc.)"""
        results = []
        seen: set[str] = set()

        for pattern in self._API_CALL_PATTERNS:
            for match in pattern.finditer(content):
                groups = match.groups()
                if len(groups) == 1:
                    method = "GET"
                    raw_url = groups[0]
                else:
                    method = groups[0].upper()
                    raw_url = groups[1]

                url = self._normalize_api_url(raw_url)
                line = content[:match.start()].count('\n') + 1

                if url in seen:
                    continue
                seen.add(url)

                results.append({
                    "method": method,
                    "url": url,
                    "line": line,
                    "raw_url": raw_url,
                })

        return results

    def _build_function_ranges(
        self, symbols: list, rel_path: str
    ) -> list[tuple[int, int, str]]:
        """Build (start_line, end_line, symbol_id) list from extracted symbols."""
        ranges = []
        func_types = {SymbolType.FUNCTION, SymbolType.METHOD, SymbolType.COMPOSABLE,
                      SymbolType.COMPONENT}
        for sym in symbols:
            if sym.symbol_type in func_types:
                sym_id = f"{self.project}:{rel_path}:{sym.symbol_type.value}:{sym.name}"
                ranges.append((sym.start_line, sym.end_line, sym_id))
        # Sort by start line descending so inner functions match first
        ranges.sort(key=lambda r: (-r[0], r[1]))
        return ranges

    def _find_enclosing_function(
        self,
        line: int,
        func_ranges: list[tuple[int, int, str]],
        file_source_id: str,
    ) -> str:
        """Find symbol ID of the function enclosing the given line."""
        for start, end, sym_id in func_ranges:
            if start <= line <= end:
                return sym_id
        return file_source_id

    def _extract_calls(self, content: str, cleaned: str) -> list[dict]:
        """Extract function calls using cleaned source to avoid false positives
        from strings and comments."""
        calls = []
        seen = set()

        skip_keywords = {
            'if', 'for', 'while', 'switch', 'catch', 'function', 'return',
            'new', 'typeof', 'instanceof', 'delete', 'void', 'throw',
            'async', 'await', 'import', 'export', 'from', 'class',
            'const', 'let', 'var', 'else', 'try', 'finally',
        }

        skip_builtins = {
            'console', 'Math', 'JSON', 'Object', 'Array', 'String',
            'Number', 'Boolean', 'Date', 'Promise', 'Error',
        }

        pattern = r'(\b[a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)*)\s*\('

        for match in re.finditer(pattern, cleaned):
            name = match.group(1)
            line = cleaned[:match.start()].count('\n') + 1

            first_part = name.split('.')[0]
            if first_part in skip_keywords or first_part in skip_builtins:
                continue

            key = (name, line)
            if key not in seen:
                seen.add(key)
                calls.append({
                    "name": name,
                    "line": line,
                })

        return calls
