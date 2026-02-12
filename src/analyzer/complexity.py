"""
Code complexity analysis - find overly complex functions

Metrics:
1. Line count - functions too long are hard to maintain
2. Nesting depth - if/for/while nested too deep
3. Parameter count - too many params means function does too much
4. Cognitive complexity - too many logic branches
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FunctionComplexity:
    """Function complexity"""
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
        """Overall complexity score (higher = more complex)"""
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
        """Issue list"""
        issues = []
        if self.lines > 50:
            issues.append(f"Too long ({self.lines} lines)")
        if self.max_depth > 3:
            issues.append(f"Nesting too deep (depth={self.max_depth})")
        if self.params > 5:
            issues.append(f"Too many parameters ({self.params})")
        if self.branches > 10:
            issues.append(f"Too many branches ({self.branches})")
        return issues


@dataclass
class ComplexityReport:
    """Complexity analysis report"""
    total_files: int = 0
    total_functions: int = 0
    complex_functions: list[FunctionComplexity] = field(default_factory=list)

    # Statistics
    avg_lines: float = 0
    avg_depth: float = 0
    max_lines: int = 0
    max_depth: int = 0


class ComplexityAnalyzer:
    """Code complexity analyzer"""

    def __init__(
        self,
        project_root: Path,
        extensions: list[str] = None,
        ignore_patterns: list[str] = None,
        # Thresholds
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
        return any(pattern in path for pattern in self.ignore_patterns)

    def scan_directory(self) -> list[str]:
        """Scan directory"""
        files = []
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if not self._should_skip(rel_path):
                    files.append(rel_path)
        return files

    def analyze_python_file(self, rel_path: str, content: str) -> list[FunctionComplexity]:
        """Analyze Python file"""
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
        """Analyze a single Python function"""
        # Basic info
        line_start = node.lineno
        line_end = node.end_lineno or line_start
        func_lines = line_end - line_start + 1

        # Parameter count
        params = len(node.args.args) + len(node.args.kwonlyargs)
        if node.args.vararg:
            params += 1
        if node.args.kwarg:
            params += 1

        # Calculate nesting depth and branch count
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
        """Analyze TypeScript/JavaScript file (using regex)"""
        functions = []
        lines = content.split("\n")

        # Match function definitions
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

                    # Calculate parameter count
                    params = len([p.strip() for p in params_str.split(",") if p.strip()]) if params_str else 0

                    # Find function end (simplified: find matching })
                    start_line = i + 1
                    end_line = self._find_function_end(lines, i)
                    func_lines = end_line - start_line + 1

                    # Calculate nesting depth and branches
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
        """Find function end line (simplified)"""
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
        """Calculate TypeScript complexity"""
        # Simplified calculation
        branches = 0
        max_depth = 0

        keywords = ["if", "else", "for", "while", "switch", "try", "catch"]

        for line in content.split("\n"):
            stripped = line.strip()

            # Calculate depth
            indent = len(line) - len(line.lstrip())
            depth = indent // 2
            max_depth = max(max_depth, depth)

            # Calculate branches
            for kw in keywords:
                if re.search(rf'\b{kw}\b', stripped):
                    branches += 1

        return max_depth, branches

    def analyze_java_file(self, rel_path: str, content: str) -> list[FunctionComplexity]:
        """Analyze Java file"""
        functions = []
        lines = content.split("\n")

        # Match method definitions
        method_pattern = r'(?:public|private|protected)?\s*(?:static)?\s*(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{'

        for i, line in enumerate(lines):
            match = re.search(method_pattern, line)
            if match:
                method_name = match.group(1)
                params_str = match.group(2)

                # Skip constructor-style naming (starts with uppercase)
                if method_name[0].isupper() and method_name not in ["Main"]:
                    continue

                # Calculate parameters
                params = len([p.strip() for p in params_str.split(",") if p.strip()]) if params_str.strip() else 0

                # Find method end
                start_line = i + 1
                end_line = self._find_function_end(lines, i)
                func_lines = end_line - start_line + 1

                # Calculate complexity
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
        """Calculate Java complexity"""
        branches = 0
        max_depth = 0

        keywords = ["if", "else", "for", "while", "switch", "try", "catch", "case"]

        for line in content.split("\n"):
            stripped = line.strip()

            # Calculate depth (Java uses 4-space indent)
            indent = len(line) - len(line.lstrip())
            depth = indent // 4
            max_depth = max(max_depth, depth)

            # Calculate branches
            for kw in keywords:
                if re.search(rf'\b{kw}\b', stripped):
                    branches += 1

        return max_depth, branches

    def analyze_go_file(self, rel_path: str, content: str) -> list[FunctionComplexity]:
        """Analyze Go file"""
        functions = []
        lines = content.split("\n")

        # Match function definitions
        func_pattern = r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(([^)]*)\)'

        for i, line in enumerate(lines):
            match = re.search(func_pattern, line)
            if match:
                func_name = match.group(1)
                params_str = match.group(2)

                # Calculate parameters
                params = len([p.strip() for p in params_str.split(",") if p.strip()]) if params_str.strip() else 0

                # Find function end
                start_line = i + 1
                end_line = self._find_function_end(lines, i)
                func_lines = end_line - start_line + 1

                # Calculate complexity
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
        """Calculate Go complexity"""
        branches = 0
        max_depth = 0

        keywords = ["if", "else", "for", "switch", "select", "case", "defer"]

        for line in content.split("\n"):
            stripped = line.strip()

            # Calculate depth (Go uses tab indent)
            tabs = len(line) - len(line.lstrip('\t'))
            max_depth = max(max_depth, tabs)

            # Calculate branches
            for kw in keywords:
                if re.search(rf'\b{kw}\b', stripped):
                    branches += 1

        return max_depth, branches

    def analyze(self) -> ComplexityReport:
        """Run analysis"""
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
                # Extract script block (string-based to avoid regex HTML parsing pitfalls)
                script_open = content.find("<script")
                script_body_start = content.find(">", script_open) + 1 if script_open != -1 else -1
                script_end = content.find("</script>", script_body_start) if script_body_start > 0 else -1
                if script_body_start > 0 and script_end != -1:
                    functions = self.analyze_typescript_file(rel_path, content[script_body_start:script_end])
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

        # Find complex functions
        for func in all_functions:
            if (func.lines > self.max_lines or
                func.max_depth > self.max_depth or
                func.params > self.max_params or
                func.branches > self.max_branches):
                report.complex_functions.append(func)

        # Statistics
        if all_functions:
            report.avg_lines = sum(f.lines for f in all_functions) / len(all_functions)
            report.avg_depth = sum(f.max_depth for f in all_functions) / len(all_functions)
            report.max_lines = max(f.lines for f in all_functions)
            report.max_depth = max(f.max_depth for f in all_functions)

        # Sort by complexity score
        report.complex_functions.sort(key=lambda x: x.score, reverse=True)

        return report

    def print_report(self, report: ComplexityReport):
        """Print report"""
        print(f"\n{'='*70}")
        print("Code Complexity Analysis")
        print(f"{'='*70}")
        print(f"\nFiles scanned: {report.total_files}")
        print(f"Functions analyzed: {report.total_functions}")
        print(f"Complex functions: {len(report.complex_functions)}")

        print("\nStatistics:")
        print(f"  Average lines/function: {report.avg_lines:.1f}")
        print(f"  Average depth: {report.avg_depth:.1f}")
        print(f"  Max lines: {report.max_lines}")
        print(f"  Max depth: {report.max_depth}")

        if report.complex_functions:
            print(f"\n{'='*70}")
            print("COMPLEX FUNCTIONS (top 20)")
            print(f"{'='*70}")

            for func in report.complex_functions[:20]:
                print(f"\n  {func.file_path}:{func.line_start}")
                print(f"  Function: {func.name}()")
                print(f"  Lines: {func.lines}, Depth: {func.max_depth}, Params: {func.params}, Branches: {func.branches}")
                if func.issues:
                    print(f"  Issues: {', '.join(func.issues)}")
        else:
            print("\n  No overly complex functions found")


def analyze_complexity(project_path: Path) -> ComplexityReport:
    """Convenience function"""
    analyzer = ComplexityAnalyzer(project_path)
    return analyzer.analyze()
