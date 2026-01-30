"""
Python scanner using AST analysis.
"""

import ast
from pathlib import Path
from typing import Optional

try:
    from .base import BaseScanner
    from ..models import Symbol, Dependency, SymbolType, DependencyType
except ImportError:
    from scanner.base import BaseScanner
    from models import Symbol, Dependency, SymbolType, DependencyType


class PythonScanner(BaseScanner):
    """
    Python 程式碼掃描器

    提取：
    - classes（含 methods）
    - functions
    - imports
    - 變數（module level）
    """

    supported_extensions = [".py"]

    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """掃描 Python 檔案"""
        symbols = []
        dependencies = []

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            # 語法錯誤，返回空結果
            return [], []

        lines = content.splitlines()
        rel_path = str(file_path)

        # 提取 imports（建立 dependencies）
        imports = self._extract_imports(tree)
        for imp in imports:
            dep = Dependency(
                source_id=f"{self.project}:{rel_path}:file:{file_path.stem}",
                target_id=imp["module"],  # 會在後處理時解析
                dep_type=DependencyType.IMPORTS,
                source_line=imp["line"],
                metadata={"names": imp["names"]},
            )
            dependencies.append(dep)

        # 遍歷 AST
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
                # 確保是 top-level（不是 method）
                if self._is_top_level(node, tree):
                    func_symbol = self._create_function_symbol(
                        node, rel_path, lines
                    )
                    symbols.append(func_symbol)

        # 為每個 symbol 計算 hash
        for symbol in symbols:
            symbol.compute_hash()

        return symbols, dependencies

    def _extract_imports(self, tree: ast.AST) -> list[dict]:
        """提取 import 語句"""
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        "module": alias.name,
                        "names": [alias.asname or alias.name],
                        "line": node.lineno,
                    })

            elif isinstance(node, ast.ImportFrom):
                if node.module:
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
        """建立 class symbol"""
        # 取得類內容
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        content = "\n".join(lines[start:end])

        # 取得 docstring 作為摘要
        summary = ast.get_docstring(node) or ""
        if len(summary) > 200:
            summary = summary[:200] + "..."

        # 取得 base classes
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
        """建立 function symbol"""
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        content = "\n".join(lines[start:end])

        summary = ast.get_docstring(node) or ""
        if len(summary) > 200:
            summary = summary[:200] + "..."

        # 取得參數
        params = []
        for arg in node.args.args:
            params.append(arg.arg)

        # 取得返回類型
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
        """建立 method symbol"""
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        content = "\n".join(lines[start:end])

        summary = ast.get_docstring(node) or ""
        if len(summary) > 200:
            summary = summary[:200] + "..."

        # 取得參數（排除 self/cls）
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

    def _is_top_level(self, node: ast.FunctionDef, tree: ast.Module) -> bool:
        """檢查 function 是否是 top-level"""
        for item in tree.body:
            if item is node:
                return True
        return False
