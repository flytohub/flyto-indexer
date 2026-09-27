"""Shared constants and pure helpers for taint analysis.

The helpers here have no analyzer lifecycle authority; mixins and the public
TaintAnalyzer compose them into bounded scans.
"""

from __future__ import annotations

import ast
import re

from .taint_evidence import TaintFlow


MAX_FUNCTIONS = 1000

MAX_TOTAL_FUNCTIONS = 20000

MAX_RETURN_SOURCE_FUNCS = 60000

MAX_RETURN_TAINT_ROUNDS = 8

MAX_FINDINGS = 200

MAX_CALLERS = 2000

MAX_CROSS_DEPTH = 6

MAX_TAINT_FILE_BYTES = 1 << 20

MAX_TAINT_SOURCE_BYTES = 32 << 20

MAX_TAINT_SOURCE_FILES = 4000

SKIP_DIR_PATTERNS = re.compile(
    r"(?:^|/)(?:test|tests|__tests__|mock|fixture|benchmark|benchmarks|"
    r"node_modules|__pycache__|"
    r"\.git|dist|dist-next|build|\.venv[^/]*|venv[^/]*|site-packages|"
    r"\.next|\.nuxt|\.output|\.open-next|\.wrangler|\.cloudflare|out|coverage)(?:/|$)|"
    r"(?:^|/)[^/]*(?:_test\.go|_test\.py|\.test\.[jt]sx?|\.spec\.[jt]sx?)$"
)

_ORM_BUILDERS = ("select(", "insert(", "update(", "delete(", "query(")

_ORM_CHAINS = (".where(", ".filter(", ".filter_by(", ".order_by(", ".offset(", ".limit(")

def _is_generated_asset(rel_path: str) -> bool:
    """True for vendored or generated bundles, which are not project code.

    Reuses the profile classifier (which already knows `.min.js`, lockfiles and
    generated directories) and adds the vendor/third-party trees a regex scan
    would otherwise mine for noise.
    """
    parts = [part.lower() for part in rel_path.split("/")]
    if any(
        parts[index:index + 2] == ["static", "assets"]
        for index in range(len(parts) - 1)
    ):
        return True
    if {"vendor", "vendors", "third_party", "thirdparty", "bundle", "bundles"} & set(parts[:-1]):
        return True
    try:
        try:
            from ..profile.filesystem import classify_path
        except ImportError:  # pragma: no cover - flat-layout fallback
            from profile.filesystem import classify_path  # type: ignore
        return classify_path(rel_path) == "generated"
    except Exception:  # pragma: no cover - defensive
        return False

def _in_hidden_dir(rel_path: str) -> bool:
    """True when any *directory* in the path is hidden.

    Agent worktrees under `.claude/`, vendored `.venv` copies and similar
    shadow trees hold duplicates of the real source. Scanning them spends the
    budget on copies and reports the same lead several times.
    """
    parts = rel_path.split("/")
    return any(part.startswith(".") for part in parts[:-1])

def _safe_unparse(node: ast.AST) -> str:
    """ast.unparse with fallback for older Python."""
    try:
        return ast.unparse(node)
    except Exception:
        return ""

def _call_short_name(call: ast.Call) -> str:
    """Final identifier of a call target: `a.b.execute(x)` -> `execute`."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""

def _unwrap_await(node: ast.expr) -> ast.expr:
    """Strip `await` so an awaited call is the same call.

    Without this, every `await db.execute(...)` / `await run(cmd)` was invisible
    to the statement visitor — which is most sink calls in an async codebase.
    """
    while isinstance(node, ast.Await):
        node = node.value
    return node

def _is_orm_expression(node: ast.AST) -> bool:
    """True for SQLAlchemy-style query objects: `select(X).where(...)`."""
    text = _safe_unparse(node)
    if not text:
        return False
    if any(builder in text for builder in _ORM_BUILDERS):
        return True
    return any(chain in text for chain in _ORM_CHAINS)

def _builds_sql_string(node: ast.AST) -> bool:
    """True when the expression assembles a string at runtime."""
    if isinstance(node, ast.expr):
        node = _unwrap_await(node)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    if isinstance(node, ast.Call):
        called = _safe_unparse(node.func)
        if called.endswith(".format") or called.endswith(".join"):
            return True
        if called in ("text", "sqlalchemy.text") or called.endswith(".text"):
            return True
    return False

def _with_returned_calls(stmts: "list[ast.stmt]") -> "list[ast.stmt]":
    """The statements, plus the calls that a return or an assignment makes.

    Cross-function tracking matched `ast.Expr` only, so
    `handle(request.args.get("x"))` was followed into `handle` while
    `return handle(...)` and `y = handle(...)` were not -- and returning the
    result is the ordinary shape of a web handler, not an edge case.

    Rather than duplicate the check, the call is presented a second time as
    the expression statement the walker already understands. It is yielded
    before the assignment it came from, because the right-hand side is
    evaluated before the target is bound.
    """
    out: list[ast.stmt] = []
    for stmt in stmts:
        if isinstance(stmt, (ast.Return, ast.Assign, ast.AnnAssign)):
            value = _unwrap_await(stmt.value) if stmt.value is not None else None
            if isinstance(value, ast.Call):
                surfaced = ast.Expr(value=value)
                surfaced.lineno = getattr(stmt, "lineno", 0)
                surfaced.col_offset = getattr(stmt, "col_offset", 0)
                out.append(surfaced)
        out.append(stmt)
    return out

def _functions_with_qualnames(
    tree: ast.AST,
) -> "list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]":
    """Every function in the tree, paired with its `Class.method` name.

    The index identifies a method by its qualified name, so matching a caller
    needs the same shape alongside the AST node.
    """
    found: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((child, f"{prefix}{child.name}"))
                walk(child, prefix)
            else:
                walk(child, prefix)

    walk(tree, "")
    return found

def _without_duplicates(flows: "list[TaintFlow]") -> "list[TaintFlow]":
    """One finding per (file, line, category, source, sink).

    Two rules can name the same call -- a project that declares `.find(` for
    its own driver now overlaps the built-in one -- and reporting the same flow
    twice inflates every count downstream. First occurrence wins, so order is
    unchanged.
    """
    seen = set()
    unique = []
    for flow in flows:
        key = (
            flow.file_path, flow.line, flow.category,
            flow.source_expr, flow.sink_expr, flow.sanitized,
            flow.sink_file or flow.file_path, flow.sink_line or flow.line,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(flow)
    return unique

