"""Bounded Python import binding for the existing taint caller pass.

This resolves only syntax-backed local module functions. It never imports or
executes a scanned module, starts an LSP, or reads outside the admitted AST cache.
Dynamic receivers, shadowed names, decorators and ambiguous source roots remain
unknown. A binding is static candidate evidence, not runtime exploitability.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_BOUND_CALLS = 20000
MAX_IMPORT_DEPTH = 8


@dataclass(frozen=True)
class PythonDefinition:
    file: str
    name: str
    line: int


class PythonCallBindings:
    """Resolve local imports against a single scan's already parsed sources."""

    def __init__(self, trees: dict[str, ast.Module]):
        self.trees = trees
        self.modules: dict[str, set[str]] = defaultdict(set)
        self.parents: dict[int, ast.AST] = {}
        self.scopes: dict[int, dict[str, tuple | None]] = {}
        self.exhausted = False
        self._callers: dict[tuple[str, str], list[tuple[str, str, int]]] | None = None
        for file, tree in sorted(trees.items()):
            path = PurePosixPath(file)
            if path.is_absolute() or ".." in path.parts or "\\" in file:
                continue
            parts = list(path.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            if parts:
                self.modules[".".join(parts)].add(file)
                if parts[0] == "src" and len(parts) > 1:
                    self.modules[".".join(parts[1:])].add(file)
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    self.parents[id(child)] = parent

    def _bindings(self, scope: ast.AST) -> dict[str, tuple | None]:
        cached = self.scopes.get(id(scope))
        if cached is not None:
            return cached
        out: dict[str, tuple | None] = {}

        def bind(name: str, value: tuple | None) -> None:
            if name in out and out[name] != value:
                out[name] = None
            else:
                out[name] = value

        def visit(node: ast.AST, conditional: bool = False) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bind(
                    node.name,
                    ("function", node) if not conditional and not node.decorator_list else None,
                )
                return
            if isinstance(node, ast.ClassDef):
                bind(node.name, None)
                return
            if isinstance(node, ast.Import):
                for item in node.names:
                    name = item.asname or item.name.split(".")[0]
                    module = item.name if item.asname else name
                    bind(name, ("module", module) if not conditional else None)
                return
            if isinstance(node, ast.ImportFrom):
                for item in node.names:
                    if item.name == "*":
                        bind("*", None)
                    else:
                        bind(
                            item.asname or item.name,
                            ("from", node.module or "", node.level, item.name)
                            if not conditional
                            else None,
                        )
                return
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                for name in node.names:
                    bind(name, None)
                return
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bind(node.id, None)
            if isinstance(node, ast.ExceptHandler) and node.name:
                bind(node.name, None)
            if isinstance(node, ast.Lambda):
                return
            for child in ast.iter_child_nodes(node):
                visit(
                    child,
                    conditional
                    or isinstance(
                        node,
                        (
                            ast.If,
                            ast.Try,
                            ast.TryStar,
                            ast.For,
                            ast.AsyncFor,
                            ast.While,
                            ast.With,
                            ast.AsyncWith,
                            ast.Match,
                        ),
                    ),
                )

        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = scope.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                bind(arg.arg, None)
            for variadic in (args.vararg, args.kwarg):
                if variadic is not None:
                    bind(variadic.arg, None)
        body = getattr(scope, "body", [])
        for stmt in body if isinstance(body, list) else [body]:
            visit(stmt)
        self.scopes[id(scope)] = out
        return out

    def _lookup(self, file: str, call: ast.Call, name: str) -> tuple | None:
        node: ast.AST | None = call
        while node is not None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.Module)):
                bindings = self._bindings(node)
                if name in bindings:
                    return bindings[name]
                if "*" in bindings:
                    return None
            node = self.parents.get(id(node))
        tree = self.trees.get(file)
        return self._bindings(tree).get(name) if tree is not None else None

    def _module_file(self, module: str) -> str | None:
        candidates = self.modules.get(module, set())
        return next(iter(candidates)) if len(candidates) == 1 else None

    @staticmethod
    def _from_module(file: str, module: str, level: int) -> str | None:
        if not level:
            return module
        parts = list(PurePosixPath(file).parent.parts)
        if level > len(parts):
            return None
        parts = parts[: len(parts) - level + 1]
        if module:
            parts.extend(module.split("."))
        return ".".join(parts)

    def _resolve_binding(
        self, file: str, binding: tuple | None, tail: list[str], seen: set[tuple[str, str]]
    ) -> PythonDefinition | None:
        if binding is None or len(seen) >= MAX_IMPORT_DEPTH:
            return None
        kind = binding[0]
        if kind == "function":
            node = binding[1]
            # Nested/class functions require lexical or receiver semantics not
            # supplied by this module-level import resolver.
            if tail or not isinstance(self.parents.get(id(node)), ast.Module):
                return None
            return PythonDefinition(file, node.name, node.lineno)
        if kind == "module":
            if not tail:
                return None
            module, member = ".".join([binding[1], *tail[:-1]]), tail[-1]
        elif kind == "from":
            relative_module = self._from_module(file, binding[1], binding[2])
            if relative_module is None:
                return None
            module = relative_module
            member = binding[3]
            if tail:
                module, member = ".".join([module, member, *tail[:-1]]), tail[-1]
        else:
            return None
        key = (module, member)
        if key in seen:
            return None
        seen = seen | {key}
        target = self._module_file(module)
        if target is None:
            # Ambiguous project roots are unknown; an explicitly external
            # binding is different from every local function with that name.
            if module in self.modules:
                return None
            return PythonDefinition("@external:" + module, member, 0)
        tree = self.trees[target]
        bindings = self._bindings(tree)
        if "*" in bindings:
            return None
        return self._resolve_binding(target, bindings.get(member), [], seen)

    def resolve(self, file: str, call: ast.Call) -> PythonDefinition | None:
        tail: list[str] = []
        expr = call.func
        while isinstance(expr, ast.Attribute):
            tail.insert(0, expr.attr)
            expr = expr.value
        if not isinstance(expr, ast.Name) or file not in self.trees:
            return None
        return self._resolve_binding(file, self._lookup(file, call, expr.id), tail, set())

    def callers(self) -> dict[tuple[str, str], list[tuple[str, str, int]]]:
        """Resolved reverse edges reuse the normal taint walker and budgets."""
        if self._callers is not None:
            return self._callers
        out: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
        checks = 0
        for file, tree in sorted(self.trees.items()):
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call):
                    continue
                checks += 1
                if checks > MAX_BOUND_CALLS:
                    self.exhausted = True
                    self._callers = dict(out)
                    return self._callers
                node: ast.AST | None = self.parents.get(id(call))
                while node is not None and not isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    node = self.parents.get(id(node))
                if node is None:
                    continue
                caller = node.name
                parent = self.parents.get(id(node))
                if isinstance(parent, ast.ClassDef):
                    caller = parent.name + "." + caller
                target = self.resolve(file, call)
                if target is not None and not target.file.startswith("@external:"):
                    entry = (file, caller, call.lineno)
                    if entry not in out[(target.file, target.name)]:
                        out[(target.file, target.name)].append(entry)
        self._callers = dict(out)
        return self._callers


def bound_argument(call: ast.Call, index: int, name: str) -> ast.expr | None:
    """Bind explicit keywords and literal unpacking; never guess dynamic *args."""
    positional: list[ast.expr] = []
    for arg in call.args:
        if isinstance(arg, ast.Starred):
            if not isinstance(arg.value, (ast.List, ast.Tuple)) or any(
                isinstance(v, ast.Starred) for v in arg.value.elts
            ):
                return None
            positional.extend(arg.value.elts)
        else:
            positional.append(arg)
    keywords: dict[str, ast.expr] = {}
    for kw in call.keywords:
        entries = [(kw.arg, kw.value)]
        if kw.arg is None:
            if not isinstance(kw.value, ast.Dict):
                return None
            entries = []
            for literal_key, value in zip(kw.value.keys, kw.value.values, strict=True):
                if not isinstance(literal_key, ast.Constant) or not isinstance(
                    literal_key.value, str
                ):
                    return None
                entries.append((literal_key.value, value))
        for keyword, value in entries:
            if keyword is None or keyword in keywords:
                return None
            keywords[keyword] = value
    if name in keywords:
        return None if 0 <= index < len(positional) else keywords[name]
    return positional[index] if 0 <= index < len(positional) else None
