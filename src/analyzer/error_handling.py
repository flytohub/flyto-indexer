"""
Error Handling coverage analyzer — detect functions without error handling,
bare except, empty except, swallowed errors, and unhandled async patterns.

Pure Python stdlib, no external dependencies.
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", "target", "out",
})

_SKIP_TEST_DIRS = frozenset({
    "test", "tests", "__tests__", "spec", "mock", "fixture",
})


@dataclass
class ErrorHandlingIssue:
    """A single error handling issue."""
    file: str
    line: int
    func_name: str
    category: str       # bare_except, empty_except, swallowed_error, no_error_handling, unhandled_async
    severity: str       # high, medium, low
    description: str


@dataclass
class ErrorHandlingReport:
    """Error handling analysis result."""
    total_functions: int = 0
    functions_with_handling: int = 0
    coverage_pct: float = 0.0
    issues: list[ErrorHandlingIssue] = field(default_factory=list)
    by_category: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_functions": self.total_functions,
            "functions_with_handling": self.functions_with_handling,
            "coverage_pct": round(self.coverage_pct, 1),
            "issue_count": len(self.issues),
            "by_category": self.by_category,
        }


class _FunctionVisitor(ast.NodeVisitor):
    """Walk Python AST to analyze error handling per function."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.functions: list[dict] = []
        self.issues: list[ErrorHandlingIssue] = []

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._analyze_func(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._analyze_func(node, is_async=True)
        self.generic_visit(node)

    def _analyze_func(self, node, is_async=False):
        func_info = {
            "name": node.name,
            "line": node.lineno,
            "has_try": False,
            "is_async": is_async,
        }

        has_try = False
        for child in ast.walk(node):
            if isinstance(child, ast.Try):
                has_try = True
                self._check_try_quality(child, node.name)

        func_info["has_try"] = has_try

        # Skip trivial functions (< 3 statements, getters, dunder)
        body_len = len(node.body)
        if body_len < 3 or node.name.startswith("_") and not node.name.startswith("__"):
            func_info["trivial"] = True
        else:
            func_info["trivial"] = False

        # Async function with await but no try → potential unhandled
        if is_async and not has_try:
            has_await = any(isinstance(n, ast.Await) for n in ast.walk(node))
            if has_await and body_len >= 3:
                self.issues.append(ErrorHandlingIssue(
                    file=self.file_path, line=node.lineno,
                    func_name=node.name,
                    category="unhandled_async",
                    severity="medium",
                    description="Async function with await but no try/except",
                ))

        self.functions.append(func_info)

    def _check_try_quality(self, try_node: ast.Try, func_name: str):
        for handler in try_node.handlers:
            # Bare except (catches everything including KeyboardInterrupt)
            if handler.type is None:
                self.issues.append(ErrorHandlingIssue(
                    file=self.file_path, line=handler.lineno,
                    func_name=func_name,
                    category="bare_except",
                    severity="high",
                    description="Bare except catches all exceptions including KeyboardInterrupt",
                ))

            # Too broad: except Exception
            elif isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                # Only flag if it's a single generic catch
                if len(try_node.handlers) == 1:
                    pass  # Acceptable as a last resort

            # Empty except body (swallowed error)
            if len(handler.body) == 1:
                stmt = handler.body[0]
                if isinstance(stmt, ast.Pass):
                    self.issues.append(ErrorHandlingIssue(
                        file=self.file_path, line=handler.lineno,
                        func_name=func_name,
                        category="empty_except",
                        severity="high",
                        description="Empty except block (pass) — error silently swallowed",
                    ))
                elif (isinstance(stmt, ast.Expr)
                      and isinstance(stmt.value, ast.Constant)
                      and isinstance(stmt.value.value, str)):
                    # except: "ignore" — docstring-style swallow
                    self.issues.append(ErrorHandlingIssue(
                        file=self.file_path, line=handler.lineno,
                        func_name=func_name,
                        category="swallowed_error",
                        severity="medium",
                        description="Exception caught but only has a string literal (no handling)",
                    ))


def _scan_js_error_handling(project_root: Path, report: ErrorHandlingReport):
    """Regex-based error handling analysis for JS/TS files."""
    func_pattern = re.compile(
        r"(?:async\s+)?(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?"
        r"(?:\([^)]*\)|[a-zA-Z_]\w*)\s*=>)",
    )
    try_pattern = re.compile(r"\btry\s*\{")
    catch_empty = re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}")

    for ext in (".js", ".jsx", ".ts", ".tsx"):
        for fpath in sorted(project_root.rglob(f"*{ext}")):
            if any(skip in fpath.parts for skip in _SKIP_DIRS | _SKIP_TEST_DIRS):
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            rel = str(fpath.relative_to(project_root)).replace("\\", "/")

            # Count functions and try blocks
            funcs = func_pattern.findall(content)
            tries = try_pattern.findall(content)
            report.total_functions += len(funcs)
            report.functions_with_handling += min(len(tries), len(funcs))

            # Detect empty catch blocks
            for match in catch_empty.finditer(content):
                line = content[:match.start()].count("\n") + 1
                report.issues.append(ErrorHandlingIssue(
                    file=rel, line=line, func_name="(unknown)",
                    category="empty_except", severity="high",
                    description="Empty catch block — error silently swallowed",
                ))


def analyze_error_handling(project_root: str | Path) -> ErrorHandlingReport:
    """Analyze error handling coverage across the project."""
    project_root = Path(project_root)
    report = ErrorHandlingReport()

    # Python: AST-based analysis
    for fpath in sorted(project_root.rglob("*.py")):
        if any(skip in fpath.parts for skip in _SKIP_DIRS | _SKIP_TEST_DIRS):
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content)
        except (OSError, SyntaxError):
            continue

        rel = str(fpath.relative_to(project_root)).replace("\\", "/")
        visitor = _FunctionVisitor(rel)
        visitor.visit(tree)

        for func in visitor.functions:
            if func.get("trivial"):
                continue
            report.total_functions += 1
            if func["has_try"]:
                report.functions_with_handling += 1

        report.issues.extend(visitor.issues)

    # JS/TS: Regex-based
    _scan_js_error_handling(project_root, report)

    # Compute coverage
    if report.total_functions > 0:
        report.coverage_pct = (report.functions_with_handling / report.total_functions) * 100

    # Build category counts
    for issue in report.issues:
        report.by_category[issue.category] = report.by_category.get(issue.category, 0) + 1

    return report
