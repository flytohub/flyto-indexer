"""
Python scanner using AST analysis.
"""

import ast
from pathlib import Path
from typing import Optional

try:
    from ..models import Dependency, DependencyType, Symbol, SymbolType
    from .base import BaseScanner
except ImportError:
    from models import Dependency, DependencyType, Symbol, SymbolType
    from scanner.base import BaseScanner


class PythonScanner(BaseScanner):
    """
    Python code scanner

    Extracts:
    - classes (including methods)
    - functions
    - imports
    - calls (function invocations)
    - variables (module level)
    """

    supported_extensions = [".py"]

    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """Scan a Python file"""
        symbols = []
        dependencies = []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            # Syntax error, return empty result
            return [], []

        lines = content.splitlines()
        rel_path = str(file_path)
        file_source_id = f"{self.project}:{rel_path}:file:{file_path.stem}"

        # Extract imports (create dependencies)
        imports = self._extract_imports(tree)
        for imp in imports:
            dep = Dependency(
                source_id=file_source_id,
                target_id=imp["module"],  # Resolved during post-processing
                dep_type=DependencyType.IMPORTS,
                source_line=imp["line"],
                metadata={"names": imp["names"]},
            )
            dependencies.append(dep)

        # Extract calls (function invocations)
        calls = self._extract_calls(tree)
        for call in calls:
            dep = Dependency(
                source_id=file_source_id,
                target_id=call["name"],  # Raw call name, resolved later
                dep_type=DependencyType.CALLS,
                source_line=call["line"],
                metadata={"raw_call": True},
            )
            dependencies.append(dep)

        # Walk the AST
        for node in ast.walk(tree):
            # Classes
            if isinstance(node, ast.ClassDef):
                class_symbol = self._create_class_symbol(
                    node, rel_path, lines
                )
                symbols.append(class_symbol)

                # Methods
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        method_symbol = self._create_method_symbol(
                            item, node.name, rel_path, lines
                        )
                        symbols.append(method_symbol)

            # Top-level functions
            elif isinstance(node, ast.FunctionDef):
                # Ensure it is top-level (not a method)
                if self._is_top_level(node, tree):
                    func_symbol = self._create_function_symbol(
                        node, rel_path, lines
                    )
                    symbols.append(func_symbol)

                    # Check for API endpoint decorators
                    for decorator in node.decorator_list:
                        api_info = self._extract_api_decorator(decorator, node)
                        if api_info:
                            # Include method in name to avoid ID collision
                            # (e.g. GET /api/users vs POST /api/users)
                            api_name = f"{api_info['method']} {api_info['path']}"
                            api_symbol = Symbol(
                                project=self.project,
                                path=rel_path,
                                symbol_type=SymbolType.API,
                                name=api_name,
                                start_line=node.lineno,
                                end_line=node.end_lineno or node.lineno,
                                content="",
                                summary=f"{api_info['method']} {api_info['path']} -> {api_info['handler']}",
                                language="python",
                            )
                            api_symbol.metadata = {
                                "method": api_info["method"],
                                "url": api_info["path"],
                                "handler": api_info["handler"],
                            }
                            api_symbol.compute_hash()
                            symbols.append(api_symbol)

        # Compute hash for each symbol
        for symbol in symbols:
            symbol.compute_hash()

        return symbols, dependencies

    def _extract_imports(self, tree: ast.AST) -> list[dict]:
        """Extract import statements"""
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        "module": alias.name,
                        "names": [alias.asname or alias.name],
                        "line": node.lineno,
                    })

            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [a.name for a in node.names]
                imports.append({
                    "module": node.module,
                    "names": names,
                    "line": node.lineno,
                })

        return imports

    def _create_class_symbol(
        self,
        node: ast.ClassDef,
        rel_path: str,
        lines: list[str]
    ) -> Symbol:
        """Create class symbol"""
        # Get class content
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        content = "\n".join(lines[start:end])

        # Get docstring as summary
        summary = ast.get_docstring(node) or ""
        if len(summary) > 200:
            summary = summary[:200] + "..."

        # Get base classes
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(f"{base.value.id if hasattr(base.value, 'id') else '?'}.{base.attr}")

        return Symbol(
            project=self.project,
            path=rel_path,
            symbol_type=SymbolType.CLASS,
            name=node.name,
            start_line=node.lineno,
            end_line=end,
            content=content,
            summary=summary,
            language="python",
            exports=[node.name],
            imports=bases,  # base classes
        )

    def _create_function_symbol(
        self,
        node: ast.FunctionDef,
        rel_path: str,
        lines: list[str]
    ) -> Symbol:
        """Create function symbol"""
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        content = "\n".join(lines[start:end])

        summary = ast.get_docstring(node) or ""
        if len(summary) > 200:
            summary = summary[:200] + "..."

        # Get parameters
        params = []
        for arg in node.args.args:
            params.append(arg.arg)

        # Get return type
        returns = ""
        if node.returns:
            returns = ast.unparse(node.returns)

        return Symbol(
            project=self.project,
            path=rel_path,
            symbol_type=SymbolType.FUNCTION,
            name=node.name,
            start_line=node.lineno,
            end_line=end,
            content=content,
            summary=summary,
            language="python",
            exports=[node.name],
            params=params,
            returns=returns,
        )

    def _create_method_symbol(
        self,
        node: ast.FunctionDef,
        class_name: str,
        rel_path: str,
        lines: list[str]
    ) -> Symbol:
        """Create method symbol"""
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        content = "\n".join(lines[start:end])

        summary = ast.get_docstring(node) or ""
        if len(summary) > 200:
            summary = summary[:200] + "..."

        # Get parameters (excluding self/cls)
        params = []
        for arg in node.args.args:
            if arg.arg not in ("self", "cls"):
                params.append(arg.arg)

        returns = ""
        if node.returns:
            returns = ast.unparse(node.returns)

        return Symbol(
            project=self.project,
            path=rel_path,
            symbol_type=SymbolType.METHOD,
            name=f"{class_name}.{node.name}",
            start_line=node.lineno,
            end_line=end,
            content=content,
            summary=summary,
            language="python",
            params=params,
            returns=returns,
        )

    # HTTP method names recognized on route decorators
    _HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}

    def _extract_api_decorator(
        self, decorator: ast.expr, func_node: ast.FunctionDef
    ) -> Optional[dict]:
        """
        Extract API endpoint info from a decorator node.

        Supports:
        - @app.get("/users")          -> {method: "GET", path: "/users"}
        - @router.post("/items")      -> {method: "POST", path: "/items"}
        - @app.route("/x", methods=["GET", "POST"]) -> {method: "GET,POST", path: "/x"}
        """
        # Pattern 1: @app.get("/path"), @router.post("/path"), etc.
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
            attr_name = decorator.func.attr.lower()

            if attr_name in self._HTTP_METHODS:
                path = self._get_first_string_arg(decorator)
                if path:
                    return {
                        "method": attr_name.upper(),
                        "path": path,
                        "handler": func_node.name,
                    }

            # Pattern 2: @app.route("/path", methods=["GET", "POST"])
            if attr_name == "route":
                path = self._get_first_string_arg(decorator)
                if path:
                    methods = self._get_methods_kwarg(decorator)
                    return {
                        "method": methods or "GET",
                        "path": path,
                        "handler": func_node.name,
                    }

        return None

    def _get_first_string_arg(self, call_node: ast.Call) -> Optional[str]:
        """Get the first string argument from a Call node."""
        if call_node.args:
            arg = call_node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
            # Handle f-strings and JoinedStr — skip them
        return None

    def _get_methods_kwarg(self, call_node: ast.Call) -> Optional[str]:
        """Extract methods=["GET", "POST"] keyword argument."""
        for kw in call_node.keywords:
            if kw.arg == "methods" and isinstance(kw.value, ast.List):
                methods = []
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        methods.append(elt.value.upper())
                if methods:
                    return ",".join(methods)
        return None

    def _is_top_level(self, node: ast.FunctionDef, tree: ast.Module) -> bool:
        """Check if a function is top-level"""
        return any(item is node for item in tree.body)

    def _extract_calls(self, tree: ast.AST) -> list[dict]:
        """
        Extract function/method calls

        Returns:
            List of dicts with 'name' and 'line' keys
        """
        calls = []
        seen = set()  # Avoid duplicates for same call on same line

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = self._get_call_name(node)
                if name:
                    key = (name, node.lineno)
                    if key not in seen:
                        seen.add(key)
                        calls.append({
                            "name": name,
                            "line": node.lineno,
                        })

        return calls

    def _get_call_name(self, node: ast.Call) -> Optional[str]:
        """
        Extract call name from an ast.Call node

        Handles:
        - foo() → "foo"
        - obj.method() → "obj.method"
        - module.Class.method() → "module.Class.method"
        - Class() → "Class"
        """
        func = node.func

        if isinstance(func, ast.Name):
            # Simple function call: foo()
            return func.id

        elif isinstance(func, ast.Attribute):
            # Method/attribute call: obj.method()
            parts = []
            current = func

            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value

            if isinstance(current, ast.Name):
                parts.append(current.id)
            else:
                # Complex expression like foo().bar(), skip
                return None

            parts.reverse()
            return ".".join(parts)

        return None
