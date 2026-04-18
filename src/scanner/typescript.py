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

try:
    from ..models import Dependency, DependencyType, Symbol, SymbolType
    from .base import BaseScanner
except ImportError:
    from models import Dependency, DependencyType, Symbol, SymbolType
    from scanner.base import BaseScanner


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

            # Create RE_EXPORTS dependency for barrel re-exports
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
            match.group(3)
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

            iface_sym = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.INTERFACE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=interface_content,
                language="typescript",
                exports=[name] if 'export' in content[max(0, match.start()-20):match.start()] else [],
            )
            iface_fields = self._extract_ts_fields(interface_content)
            if iface_fields:
                iface_sym.metadata = {"fields": iface_fields}
            symbols.append(iface_sym)

        # Extract type aliases
        for match in re.finditer(
            r'^(?:export\s+)?type\s+(\w+)(?:<[^>]*>)?\s*=',
            content, re.MULTILINE
        ):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            # Type aliases usually end at semicolon or newline
            end_match = re.search(r';|\n\n', content[match.end():])
            end_pos = match.end() + end_match.end() if end_match else match.end() + 100
            end_line = content[:end_pos].count('\n') + 1
            type_content = '\n'.join(lines[start_line-1:end_line])

            type_sym = Symbol(
                project=self.project,
                path=rel_path,
                symbol_type=SymbolType.TYPE,
                name=name,
                start_line=start_line,
                end_line=end_line,
                content=type_content,
                language="typescript",
                exports=[name] if 'export' in content[max(0, match.start()-20):match.start()] else [],
            )
            # Extract fields from object-shaped type aliases
            type_fields = self._extract_ts_fields(type_content)
            if type_fields:
                type_sym.metadata = {"fields": type_fields}
            symbols.append(type_sym)

        # Extract backend route definitions (Express/Hono/Fastify)
        self._extract_backend_routes(content, lines, rel_path, symbols)

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

        # Compute hash
        for symbol in symbols:
            symbol.compute_hash()

        return symbols, dependencies

    # TS field pattern: optional readonly, field name, optional ?, colon, type
    _TS_FIELD_PATTERN = re.compile(
        r'^\s+(?:readonly\s+)?(\w+)\??\s*:\s*(.+?)[\s;,]*$', re.MULTILINE
    )

    def _extract_ts_fields(self, body_content: str) -> list[dict]:
        """Extract fields from interface/type body content."""
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

    # Backend route pattern: app.get('/path', ...), router.post('/path', ...), etc.
    _BACKEND_ROUTE_PATTERN = re.compile(
        r'(?:app|router|server)\.(get|post|put|patch|delete|options|head|all)\s*\(\s*[\'"]([^\'"]+)[\'"]',
        re.IGNORECASE,
    )

    def _extract_backend_routes(self, content: str, lines: list[str],
                                rel_path: str, symbols: list[Symbol]) -> None:
        """Detect Express/Hono/Fastify backend route definitions."""
        for match in self._BACKEND_ROUTE_PATTERN.finditer(content):
            method = match.group(1).upper()
            path = match.group(2)
            start_line = content[:match.start()].count('\n') + 1

            # Try to extract handler name
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
                    # export { default as Name } or { x as y }
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

    # Patterns for detecting frontend HTTP API calls (must contain /api/)
    _API_CALL_PATTERNS = [
        # fetch("/api/..."), useFetch("/api/..."), useAsyncData("/api/..."), $fetch("/api/...")
        re.compile(r'''(?:fetch|useFetch|useAsyncData|\$fetch)\s*\(\s*[`"']([^`"']*?/api/[^`"']*?)[`"']'''),
        # axios.get("/path"), axios.post("/path"), etc. — known HTTP client, any URL
        re.compile(r'''axios\s*\.\s*(get|post|put|delete|patch)\s*\(\s*[`"']([^`"']*?)[`"']''', re.I),
        # api.get("/path"), $api.post("/path"), http.get("/path") — known HTTP client, any URL
        re.compile(r'''(?:\$?api|http|\$http|request)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*[`"']([^`"']*?)[`"']''', re.I),
        # Generic: any string literal with /api/ in a function call context
        re.compile(r'''[`"']([^`"']*?/api/[^`"']*?)[`"']\s*[,)]'''),
    ]

    # Regex to strip query params and normalize template variables
    _TEMPLATE_VAR_RE = re.compile(r'\$\{[^}]*\}')

    @staticmethod
    def _normalize_api_url(url: str) -> str:
        """Normalize API URL: strip query params, replace template vars with *."""
        # Strip query params
        url = url.split('?')[0]
        # Replace ${...} template variables with *
        url = TypeScriptScanner._TEMPLATE_VAR_RE.sub('*', url)
        return url

    def _extract_api_calls(self, content: str) -> list[dict]:
        """
        Extract frontend HTTP API calls (fetch, axios, $http, etc.)

        Only detects URLs containing '/api/' to avoid false positives.
        Returns list of {method, url, line}.
        """
        results = []
        seen: set[str] = set()

        for pattern in self._API_CALL_PATTERNS:
            for match in pattern.finditer(content):
                groups = match.groups()
                if len(groups) == 1:
                    # fetch / generic pattern: only URL captured
                    method = "GET"
                    raw_url = groups[0]
                else:
                    method = groups[0].upper()
                    raw_url = groups[1]

                url = self._normalize_api_url(raw_url)
                line = content[:match.start()].count('\n') + 1

                # Deduplicate by normalized URL per file
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
