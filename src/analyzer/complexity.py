"""
程式碼複雜度分析 - 找出過度複雜的函數

指標：
1. 行數 - 函數太長難維護
2. 巢狀深度 - if/for/while 嵌套太深
3. 參數數量 - 參數太多表示函數做太多事
4. 認知複雜度 - 邏輯分支太多
"""

import ast
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FunctionComplexity:
    """函數複雜度"""
    file_path: str
    name: str
    line_start: int
    line_end: int
    lines: int
    params: int
    max_depth: int
    branches: int  # if/elif/for/while/try/with
    returns: int

    @property
    def score(self) -> int:
        """綜合複雜度分數（越高越複雜）"""
        score = 0
        if self.lines > 50:
            score += (self.lines - 50) // 10
        if self.max_depth > 3:
            score += (self.max_depth - 3) * 5
        if self.params > 5:
            score += (self.params - 5) * 2
        if self.branches > 10:
            score += (self.branches - 10)
        return score

    @property
    def issues(self) -> list[str]:
        """問題清單"""
        issues = []
        if self.lines > 50:
            issues.append(f"太長 ({self.lines} 行)")
        if self.max_depth > 3:
            issues.append(f"巢狀太深 (depth={self.max_depth})")
        if self.params > 5:
            issues.append(f"參數太多 ({self.params} 個)")
        if self.branches > 10:
            issues.append(f"分支太多 ({self.branches} 個)")
        return issues


@dataclass
class ComplexityReport:
    """複雜度分析報告"""
    total_files: int = 0
    total_functions: int = 0
    complex_functions: list[FunctionComplexity] = field(default_factory=list)

    # 統計
    avg_lines: float = 0
    avg_depth: float = 0
    max_lines: int = 0
    max_depth: int = 0


class ComplexityAnalyzer:
    """程式碼複雜度分析器"""

    def __init__(
        self,
        project_root: Path,
        extensions: list[str] = None,
        ignore_patterns: list[str] = None,
        # 閾值
        max_lines: int = 50,
        max_depth: int = 4,
        max_params: int = 5,
        max_branches: int = 10,
    ):
        self.project_root = project_root
        self.extensions = extensions or [".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".java", ".go"]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".pytest_cache", ".nuxt", ".output",
            "test", "tests", "__tests__",
        ]
        self.max_lines = max_lines
        self.max_depth = max_depth
        self.max_params = max_params
        self.max_branches = max_branches

    def _should_skip(self, path: str) -> bool:
        for pattern in self.ignore_patterns:
            if pattern in path:
                return True
        return False

    def scan_directory(self) -> list[str]:
        """掃描目錄"""
        files = []
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if not self._should_skip(rel_path):
                    files.append(rel_path)
        return files

    def analyze_python_file(self, rel_path: str, content: str) -> list[FunctionComplexity]:
        """分析 Python 檔案"""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        functions = []
        lines = content.split("\n")

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = self._analyze_python_function(node, rel_path, lines)
                if func:
                    functions.append(func)

        return functions

    def _analyze_python_function(
        self,
        node: ast.FunctionDef,
        rel_path: str,
        lines: list[str]
    ) -> Optional[FunctionComplexity]:
        """分析單個 Python 函數"""
        # 基本資訊
        line_start = node.lineno
        line_end = node.end_lineno or line_start
        func_lines = line_end - line_start + 1

        # 參數數量
        params = len(node.args.args) + len(node.args.kwonlyargs)
        if node.args.vararg:
            params += 1
        if node.args.kwarg:
            params += 1

        # 計算巢狀深度和分支數
        max_depth = 0
        branches = 0
        returns = 0

        def count_depth(n, depth=0):
            nonlocal max_depth, branches, returns

            if isinstance(n, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                depth += 1
                branches += 1
                max_depth = max(max_depth, depth)

            if isinstance(n, ast.If) and n.orelse:
                # elif/else
                for item in n.orelse:
                    if isinstance(item, ast.If):
                        branches += 1

            if isinstance(n, ast.Return):
                returns += 1

            for child in ast.iter_child_nodes(n):
                count_depth(child, depth)

        count_depth(node)

        return FunctionComplexity(
            file_path=rel_path,
            name=node.name,
            line_start=line_start,
            line_end=line_end,
            lines=func_lines,
            params=params,
            max_depth=max_depth,
            branches=branches,
            returns=returns,
        )

    def analyze_typescript_file(self, rel_path: str, content: str) -> list[FunctionComplexity]:
        """分析 TypeScript/JavaScript 檔案（使用正則）"""
        functions = []
        lines = content.split("\n")

        # 匹配函數定義
        func_patterns = [
            # function name(...) {
            r'(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)',
            # const name = (...) => {
            r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>',
            # name(...) { (class method)
            r'^\s*(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*\{',
        ]

        for i, line in enumerate(lines):
            for pattern in func_patterns:
                match = re.search(pattern, line)
                if match:
                    func_name = match.group(1)
                    params_str = match.group(2) if len(match.groups()) > 1 else ""

                    # 計算參數數量
                    params = len([p.strip() for p in params_str.split(",") if p.strip()]) if params_str else 0

                    # 找函數結束（簡化：找匹配的 }）
                    start_line = i + 1
                    end_line = self._find_function_end(lines, i)
                    func_lines = end_line - start_line + 1

                    # 計算巢狀深度和分支
                    func_content = "\n".join(lines[i:end_line])
                    max_depth, branches = self._count_ts_complexity(func_content)

                    functions.append(FunctionComplexity(
                        file_path=rel_path,
                        name=func_name,
                        line_start=start_line,
                        line_end=end_line,
                        lines=func_lines,
                        params=params,
                        max_depth=max_depth,
                        branches=branches,
                        returns=func_content.count("return "),
                    ))
                    break

        return functions

    def _find_function_end(self, lines: list[str], start: int) -> int:
        """找函數結束行（簡化版）"""
        brace_count = 0
        started = False

        for i in range(start, min(start + 500, len(lines))):
            line = lines[i]
            brace_count += line.count("{") - line.count("}")

            if "{" in line:
                started = True

            if started and brace_count <= 0:
                return i + 1

        return min(start + 50, len(lines))

    def _count_ts_complexity(self, content: str) -> tuple[int, int]:
        """計算 TypeScript 複雜度"""
        # 簡化計算
        branches = 0
        max_depth = 0
        current_depth = 0

        keywords = ["if", "else", "for", "while", "switch", "try", "catch"]

        for line in content.split("\n"):
            stripped = line.strip()

            # 計算深度
            indent = len(line) - len(line.lstrip())
            depth = indent // 2
            max_depth = max(max_depth, depth)

            # 計算分支
            for kw in keywords:
                if re.search(rf'\b{kw}\b', stripped):
                    branches += 1

        return max_depth, branches

    def analyze_java_file(self, rel_path: str, content: str) -> list[FunctionComplexity]:
        """分析 Java 檔案"""
        functions = []
        lines = content.split("\n")

        # 匹配方法定義
        method_pattern = r'(?:public|private|protected)?\s*(?:static)?\s*(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{'

        for i, line in enumerate(lines):
            match = re.search(method_pattern, line)
            if match:
                method_name = match.group(1)
                params_str = match.group(2)

                # 跳過建構子風格的命名（首字母大寫）
                if method_name[0].isupper() and method_name not in ["Main"]:
                    continue

                # 計算參數
                params = len([p.strip() for p in params_str.split(",") if p.strip()]) if params_str.strip() else 0

                # 找方法結束
                start_line = i + 1
                end_line = self._find_function_end(lines, i)
                func_lines = end_line - start_line + 1

                # 計算複雜度
                func_content = "\n".join(lines[i:end_line])
                max_depth, branches = self._count_java_complexity(func_content)

                functions.append(FunctionComplexity(
                    file_path=rel_path,
                    name=method_name,
                    line_start=start_line,
                    line_end=end_line,
                    lines=func_lines,
                    params=params,
                    max_depth=max_depth,
                    branches=branches,
                    returns=func_content.count("return "),
                ))

        return functions

    def _count_java_complexity(self, content: str) -> tuple[int, int]:
        """計算 Java 複雜度"""
        branches = 0
        max_depth = 0

        keywords = ["if", "else", "for", "while", "switch", "try", "catch", "case"]

        for line in content.split("\n"):
            stripped = line.strip()

            # 計算深度（Java 用 4 空格縮排）
            indent = len(line) - len(line.lstrip())
            depth = indent // 4
            max_depth = max(max_depth, depth)

            # 計算分支
            for kw in keywords:
                if re.search(rf'\b{kw}\b', stripped):
                    branches += 1

        return max_depth, branches

    def analyze_go_file(self, rel_path: str, content: str) -> list[FunctionComplexity]:
        """分析 Go 檔案"""
        functions = []
        lines = content.split("\n")

        # 匹配函數定義
        func_pattern = r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(([^)]*)\)'

        for i, line in enumerate(lines):
            match = re.search(func_pattern, line)
            if match:
                func_name = match.group(1)
                params_str = match.group(2)

                # 計算參數
                params = len([p.strip() for p in params_str.split(",") if p.strip()]) if params_str.strip() else 0

                # 找函數結束
                start_line = i + 1
                end_line = self._find_function_end(lines, i)
                func_lines = end_line - start_line + 1

                # 計算複雜度
                func_content = "\n".join(lines[i:end_line])
                max_depth, branches = self._count_go_complexity(func_content)

                functions.append(FunctionComplexity(
                    file_path=rel_path,
                    name=func_name,
                    line_start=start_line,
                    line_end=end_line,
                    lines=func_lines,
                    params=params,
                    max_depth=max_depth,
                    branches=branches,
                    returns=func_content.count("return "),
                ))

        return functions

    def _count_go_complexity(self, content: str) -> tuple[int, int]:
        """計算 Go 複雜度"""
        branches = 0
        max_depth = 0

        keywords = ["if", "else", "for", "switch", "select", "case", "defer"]

        for line in content.split("\n"):
            stripped = line.strip()

            # 計算深度（Go 用 tab 縮排）
            tabs = len(line) - len(line.lstrip('\t'))
            max_depth = max(max_depth, tabs)

            # 計算分支
            for kw in keywords:
                if re.search(rf'\b{kw}\b', stripped):
                    branches += 1

        return max_depth, branches

    def analyze(self) -> ComplexityReport:
        """執行分析"""
        report = ComplexityReport()
        all_functions = []

        files = self.scan_directory()
        report.total_files = len(files)

        for rel_path in files:
            full_path = self.project_root / rel_path
            try:
                content = full_path.read_text(encoding="utf-8")
            except Exception:
                continue

            ext = Path(rel_path).suffix

            if ext == ".py":
                functions = self.analyze_python_file(rel_path, content)
            elif ext in [".ts", ".tsx", ".js", ".jsx"]:
                functions = self.analyze_typescript_file(rel_path, content)
            elif ext == ".vue":
                # 提取 script 區塊
                script_match = re.search(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
                if script_match:
                    functions = self.analyze_typescript_file(rel_path, script_match.group(1))
                else:
                    functions = []
            elif ext == ".java":
                functions = self.analyze_java_file(rel_path, content)
            elif ext == ".go":
                functions = self.analyze_go_file(rel_path, content)
            else:
                functions = []

            all_functions.extend(functions)

        report.total_functions = len(all_functions)

        # 找出複雜函數
        for func in all_functions:
            if (func.lines > self.max_lines or
                func.max_depth > self.max_depth or
                func.params > self.max_params or
                func.branches > self.max_branches):
                report.complex_functions.append(func)

        # 統計
        if all_functions:
            report.avg_lines = sum(f.lines for f in all_functions) / len(all_functions)
            report.avg_depth = sum(f.max_depth for f in all_functions) / len(all_functions)
            report.max_lines = max(f.lines for f in all_functions)
            report.max_depth = max(f.max_depth for f in all_functions)

        # 按複雜度分數排序
        report.complex_functions.sort(key=lambda x: x.score, reverse=True)

        return report

    def print_report(self, report: ComplexityReport):
        """印出報告"""
        print(f"\n{'='*70}")
        print("Code Complexity Analysis")
        print(f"{'='*70}")
        print(f"\nFiles scanned: {report.total_files}")
        print(f"Functions analyzed: {report.total_functions}")
        print(f"Complex functions: {len(report.complex_functions)}")

        print(f"\nStatistics:")
        print(f"  Average lines/function: {report.avg_lines:.1f}")
        print(f"  Average depth: {report.avg_depth:.1f}")
        print(f"  Max lines: {report.max_lines}")
        print(f"  Max depth: {report.max_depth}")

        if report.complex_functions:
            print(f"\n{'='*70}")
            print(f"COMPLEX FUNCTIONS (top 20)")
            print(f"{'='*70}")

            for func in report.complex_functions[:20]:
                print(f"\n  {func.file_path}:{func.line_start}")
                print(f"  Function: {func.name}()")
                print(f"  Lines: {func.lines}, Depth: {func.max_depth}, Params: {func.params}, Branches: {func.branches}")
                if func.issues:
                    print(f"  Issues: {', '.join(func.issues)}")
        else:
            print(f"\n  No overly complex functions found")


def analyze_complexity(project_path: Path) -> ComplexityReport:
    """便捷函數"""
    analyzer = ComplexityAnalyzer(project_path)
    return analyzer.analyze()
