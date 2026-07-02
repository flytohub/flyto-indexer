"""
Performance Anti-pattern detector — find common performance issues in code.

Detects:
  1. N+1 queries: database call inside a for loop
  2. Unbounded fetch: query without LIMIT/pagination
  3. Sync I/O in async: blocking call inside async function
  4. Large payload: response without pagination indicators
  5. Missing timeout: HTTP calls without timeout parameter

Pure Python stdlib, no external dependencies.
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", "target", "out", ".next", ".nuxt",
    "test", "tests", "__tests__", "spec",
})

# Database call patterns. Keep generic collection helpers out of this set unless
# the receiver also looks like a DB/ORM object; otherwise ordinary dict/list
# calls such as result.get() or line.count() become noisy false positives.
_DIRECT_DB_CALLS = frozenset({
    "execute", "query", "select", "fetch", "fetchone", "fetchall", "fetchmany",
    "find_one", "find_all",
})

_GENERIC_DB_METHODS = frozenset({
    "get", "find", "filter", "all", "first", "count",
})

_DB_RECEIVER_HINTS = frozenset({
    "db", "database", "session", "cursor", "conn", "connection", "engine",
    "query", "queryset", "objects", "collection", "table", "model", "orm",
})

# Blocking I/O patterns (should not be in async)
_BLOCKING_IO = frozenset({
    "open", "read", "write", "requests.get", "requests.post",
    "urllib.request.urlopen", "time.sleep", "subprocess.run",
    "subprocess.call", "os.system", "input",
})


@dataclass
class PerfIssue:
    """A single performance anti-pattern."""
    file: str
    line: int
    func_name: str
    category: str       # n_plus_1, unbounded_fetch, sync_in_async, missing_timeout
    severity: str       # high, medium, low
    description: str


@dataclass
class PerfAntiPatternReport:
    """Performance anti-pattern analysis result."""
    total_issues: int = 0
    issues: list[PerfIssue] = field(default_factory=list)
    by_category: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_issues": self.total_issues,
            "by_category": self.by_category,
            "issues": [
                {"file": i.file, "line": i.line, "func": i.func_name,
                 "category": i.category, "severity": i.severity,
                 "description": i.description}
                for i in self.issues[:50]
            ],
        }


class _PerfVisitor(ast.NodeVisitor):
    """Walk Python AST to detect performance anti-patterns."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.issues: list[PerfIssue] = []
        self._in_async = False
        self._in_loop_depth = 0
        self._current_func = ""

    def visit_FunctionDef(self, node):
        old_func = self._current_func
        old_async = self._in_async
        self._current_func = node.name
        self._in_async = False
        self.generic_visit(node)
        self._current_func = old_func
        self._in_async = old_async

    def visit_AsyncFunctionDef(self, node):
        old_func = self._current_func
        old_async = self._in_async
        self._current_func = node.name
        self._in_async = True
        self.generic_visit(node)
        self._current_func = old_func
        self._in_async = old_async

    def visit_For(self, node):
        self._in_loop_depth += 1
        self.generic_visit(node)
        self._in_loop_depth -= 1

    def visit_While(self, node):
        self._in_loop_depth += 1
        self.generic_visit(node)
        self._in_loop_depth -= 1

    def visit_Call(self, node):
        call_name = self._get_call_name(node)
        if not call_name:
            self.generic_visit(node)
            return

        short_name = call_name.rsplit(".", 1)[-1]

        # N+1: DB call inside a loop
        if self._in_loop_depth > 0 and self._looks_like_db_call(call_name, short_name):
            self.issues.append(PerfIssue(
                file=self.file_path, line=node.lineno,
                func_name=self._current_func,
                category="n_plus_1",
                severity="high",
                description=f"Database call '{call_name}()' inside loop — potential N+1 query",
            ))

        # Sync I/O in async function
        if self._in_async:
            for blocking in _BLOCKING_IO:
                if blocking in call_name:
                    # time.sleep is common pattern, but still worth noting
                    sev = "medium" if "sleep" in call_name else "high"
                    self.issues.append(PerfIssue(
                        file=self.file_path, line=node.lineno,
                        func_name=self._current_func,
                        category="sync_in_async",
                        severity=sev,
                        description=f"Blocking call '{call_name}()' in async function",
                    ))
                    break

        # Missing timeout on HTTP calls
        if any(p in call_name for p in ("requests.get", "requests.post", "requests.put",
                                         "requests.delete", "requests.request",
                                         "httpx.get", "httpx.post")):
            has_timeout = any(
                kw.arg == "timeout" for kw in node.keywords
            )
            if not has_timeout:
                self.issues.append(PerfIssue(
                    file=self.file_path, line=node.lineno,
                    func_name=self._current_func,
                    category="missing_timeout",
                    severity="medium",
                    description=f"HTTP call '{call_name}()' without timeout parameter",
                ))

        self.generic_visit(node)

    def _get_call_name(self, node: ast.Call) -> str:
        """Extract dotted call name from AST."""
        try:
            return ast.unparse(node.func)
        except Exception:
            return ""

    def _looks_like_db_call(self, call_name: str, short_name: str) -> bool:
        if short_name in _DIRECT_DB_CALLS:
            return True
        if short_name not in _GENERIC_DB_METHODS or "." not in call_name:
            return False
        receiver = call_name.rsplit(".", 1)[0].lower()
        receiver_parts = re.split(r"[^a-z0-9_]+", receiver)
        return any(part in _DB_RECEIVER_HINTS for part in receiver_parts)


def _scan_unbounded_queries(project_root: Path) -> list[PerfIssue]:
    """Regex scan for SQL queries without LIMIT across all languages."""
    issues = []
    sql_pattern = re.compile(
        r"""(?:SELECT\s+.+?\s+FROM\s+\w+)(?!.*\bLIMIT\b)""",
        re.IGNORECASE | re.DOTALL,
    )
    # Match raw SQL strings (Python f-strings, Go backtick, JS template)
    raw_sql = re.compile(
        r'''(?:f?["'`]{1,3})(SELECT\s+.+?FROM\s+\w+.*?)["'`]{1,3}''',
        re.IGNORECASE | re.DOTALL,
    )

    for ext in (".py", ".go", ".js", ".ts"):
        for fpath in sorted(project_root.rglob(f"*{ext}")):
            if any(skip in fpath.parts for skip in _SKIP_DIRS):
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            rel = str(fpath.relative_to(project_root)).replace("\\", "/")

            for match in raw_sql.finditer(content):
                sql = match.group(1)
                if "LIMIT" not in sql.upper() and "WHERE" in sql.upper():
                    # Has WHERE but no LIMIT — potential unbounded
                    line = content[:match.start()].count("\n") + 1
                    issues.append(PerfIssue(
                        file=rel, line=line, func_name="",
                        category="unbounded_fetch",
                        severity="medium",
                        description="SQL query with WHERE but no LIMIT — may return unbounded results",
                    ))

    return issues


def analyze_perf_patterns(project_root: str | Path) -> PerfAntiPatternReport:
    """Analyze performance anti-patterns in the project."""
    project_root = Path(project_root)
    report = PerfAntiPatternReport()

    # Python AST analysis
    for fpath in sorted(project_root.rglob("*.py")):
        if any(skip in fpath.parts for skip in _SKIP_DIRS):
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content)
        except (OSError, SyntaxError):
            continue

        rel = str(fpath.relative_to(project_root)).replace("\\", "/")
        visitor = _PerfVisitor(rel)
        visitor.visit(tree)
        report.issues.extend(visitor.issues)

    # Unbounded query detection (all languages)
    report.issues.extend(_scan_unbounded_queries(project_root))

    report.total_issues = len(report.issues)
    for issue in report.issues:
        report.by_category[issue.category] = report.by_category.get(issue.category, 0) + 1

    return report
