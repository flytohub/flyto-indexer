"""Intraprocedural Python taint propagation and sink analysis."""

from __future__ import annotations

import ast
import logging
from collections import defaultdict
from dataclasses import replace

from .taint_common import (
    MAX_FINDINGS, MAX_FUNCTIONS, MAX_RETURN_SOURCE_FUNCS, MAX_RETURN_TAINT_ROUNDS,
    MAX_TOTAL_FUNCTIONS, SKIP_DIR_PATTERNS, _builds_sql_string,
    _call_matches_sink_pattern, _call_short_name, _functions_with_qualnames, _in_hidden_dir,
    _is_orm_expression, _safe_unparse, _unwrap_await,
)
from .taint_evidence import TaintFlow
from .taint_policy import _source_matches
from .taint_rules import NON_UNTRUSTED_SOURCE_MARKERS, OPERATOR_SOURCES, REDOS_REGEX_CALLS
from .taint_shapes import call_satisfies, is_constant_literal, provable_shape, receiver_satisfies

logger = logging.getLogger(__name__)


class TaintPythonFlowMixin:
    findings: list[TaintFlow]

    def _build_return_source_registry(self) -> None:
        """Find functions whose return value carries untrusted input.

        The intra-procedural pass only taints a call result when one of the
        call's own arguments is tainted. A function that reads a source itself
        and hands it back — `def read_body(): return request.get_json()` — has
        no tainted argument, so `body = read_body()` used to stay clean and
        every sink it reached was missed. This pass closes that: it records
        which functions return untrusted data (directly, or by returning a call
        to another such function), and `_is_source` then treats a call to one
        as a source at the call site.

        Name-based, like the rest of the cross-function engine: two functions
        that share a short name share a verdict. That over-approximates for
        recall; the ranking layer and (when available) LSP verification carry
        the precision.
        """
        # Collect every function node once, plus a per-short-name definition
        # count (a name with more than one definition cannot be attributed to a
        # call site — that is what turned an unrelated `predict(...)` in a demo
        # into a false positive).
        func_nodes: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        def_counts: dict[str, int] = defaultdict(int)
        seen_funcs = 0
        for py_path in self._filesystem_paths("*.py"):
            if seen_funcs >= MAX_RETURN_SOURCE_FUNCS:
                self._truncation.add("return_registry_cap")
                break
            rel = str(py_path.relative_to(self.project_root)).replace("\\", "/")
            if SKIP_DIR_PATTERNS.search(rel):
                continue
            tree = self._python_tree(rel)
            if tree is None:
                continue

            self._collect_tainted_self_attrs(rel, tree)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if seen_funcs >= MAX_RETURN_SOURCE_FUNCS:
                    break
                seen_funcs += 1
                def_counts[node.name] += 1
                func_nodes.append((node.name, node))

        def _attributable(name: str) -> bool:
            """Whether a call to this name resolves to one known definition."""
            return (
                def_counts.get(name, 0) == 1
                and not (name.startswith("__") and name.endswith("__"))
                and name not in {"_", ""}
            )

        def _gate(names: set[str]) -> set[str]:
            return {name for name in names if _attributable(name)}

        # Global fixpoint (Pysa-style): re-extract every function's return
        # signature using the return-source set found so far, until it stops
        # growing. Each round lets taint cross one more hop, so a chain like
        # read() -> _get_normalized_request_json() ->
        # parse_dict(json, proto); return proto converges in a few rounds.
        self._return_source_funcs = set()
        for _ in range(MAX_RETURN_TAINT_ROUNDS):
            direct: set[str] = set()
            forwards: dict[str, set[str]] = defaultdict(set)
            for name, node in func_nodes:
                is_direct, callees = self._extract_return_signature(node)
                if is_direct:
                    direct.add(name)
                if callees:
                    forwards[name] |= callees

            # Forward only along a callee that resolves to one definition.
            # `A` returning `B(...)` says nothing about `A` when the project
            # has nineteen functions named `B`: that is how `typing.cast`
            # marked every function calling it as returning untrusted input,
            # `cast` having one tainted definition among its nineteen. The end
            # gate below already refuses to report such a name as a source; it
            # has to be refused as evidence too.
            tainting = set(direct)
            for _ in range(MAX_RETURN_TAINT_ROUNDS):
                grew = False
                for name, callees in forwards.items():
                    if name in tainting:
                        continue
                    if any(c in tainting and _attributable(c) for c in callees):
                        tainting.add(name)
                        grew = True
                if not grew:
                    break

            gated = _gate(tainting)
            if gated == self._return_source_funcs:
                break
            self._return_source_funcs = gated

        # Of those, the ones whose only source is the operator's own input.
        # A flow through such a function names the function, not argv, so the
        # severity cap needs the set rather than a string match.
        operator_only: set[str] = set()
        for name, node in func_nodes:
            if name not in self._return_source_funcs:
                continue
            text = _safe_unparse(node)
            present = [
                pattern for pattern in self._sources.get("python", [])
                if _source_matches(pattern, text)
            ]
            if present and all(
                pattern in OPERATOR_SOURCES for pattern in present
            ):
                operator_only.add(name)
        self._operator_return_funcs = operator_only

    def _collect_tainted_self_attrs(self, rel: str, tree: ast.Module) -> None:
        """Record instance attributes a class assigns untrusted input to.

        Maps every method to its class (so the scan knows which attribute set
        applies), then finds `self.<attr> = <reads a source>` in any method and
        marks `<attr>` tainted for that (file, class). A later method reading
        `self.<attr>` is then a source.
        """
        for cnode in ast.walk(tree):
            if not isinstance(cnode, ast.ClassDef):
                continue
            attrs: set[str] = set()
            for m in cnode.body:
                if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                self._func_class[(rel, m.lineno)] = cnode.name
                for stmt in ast.walk(m):
                    if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = stmt.value
                    if value is None:
                        continue
                    value = _unwrap_await(value)
                    targets = (
                        stmt.targets if isinstance(stmt, ast.Assign)
                        else ([stmt.target] if stmt.target else [])
                    )
                    for t in targets:
                        if (
                            isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name)
                            and t.value.id == "self"
                            and self._reads_raw_source(value, set())
                        ):
                            attrs.add(t.attr)
            if attrs:
                self._tainted_self_attrs[(rel, cnode.name)] = attrs

    def _demote_operator_sourced(self, flow: "TaintFlow") -> "TaintFlow":
        """Cap the severity of a flow an operator fed, not a remote request.

        `parse_args()` reading `sys.argv` is real input, and reaching a path
        join with it is worth seeing -- but it is the operator's own
        `--config`, and calling that high risk is what made this package's own
        verify gate fail once the cross-function trace grew long enough to
        reach it. research_priority already demotes this tier; the engine now
        says the same thing, including through a function that only returns
        operator input, where the flow names the function rather than argv.
        """
        if flow.severity not in ("critical", "high"):
            return flow
        source = flow.source_expr
        # Boundary-matched, not substring-matched: a project's own
        # `custom_sdk.get_input()` source is not the stdlib prompt, and
        # demoting it silently is the very mistake the source patterns had.
        if any(_source_matches(marker, source) for marker in OPERATOR_SOURCES):
            return replace(flow, severity="medium")
        if any(source.startswith(f"{name}(") for name in self._operator_return_funcs):
            return replace(flow, severity="medium")
        return flow

    def _extract_return_signature(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[bool, set[str]]:
        """Return (returns_untrusted_directly, forwarded_callee_short_names).

        Deliberately does not consider parameters: a return that depends on a
        parameter is already covered by the existing "a tainted argument taints
        the call result" rule. This pass only adds the case that rule misses —
        a source the function reaches on its own.
        """
        local_tainted: set[str] = set()
        # var name -> short callee name, for `x = g(...)` then `return x`
        var_from_call: dict[str, str] = {}

        assigns: list[tuple[list[str], ast.expr]] = []
        returns: list[ast.expr] = []
        prop_calls: list[ast.Call] = []
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call) and _call_short_name(node) in (
                self._receiver_propagators | set(self._positional_propagators)
            ):
                prop_calls.append(node)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if value is None:
                    continue
                value = _unwrap_await(value)
                targets = (
                    node.targets if isinstance(node, ast.Assign)
                    else ([node.target] if node.target else [])
                )
                names = [
                    name for t in targets if (name := self._target_name(t))
                ]
                assigns.append((names, value))
                if isinstance(value, ast.Call):
                    callee = _call_short_name(value)
                    if callee:
                        for n in names:
                            var_from_call[n] = callee
            elif isinstance(node, (ast.Return, ast.Yield)):
                if node.value is not None:
                    returns.append(_unwrap_await(node.value))

        # Bounded local fixpoint so `a = source; b = a; return b`, and
        # `parse_dict(source, out); return out` (mlflow's request path), are
        # both caught.
        for _ in range(4):
            grew = False
            for names, value in assigns:
                if any(n in local_tainted for n in names):
                    continue
                if self._reads_raw_source(value, local_tainted):
                    for n in names:
                        local_tainted.add(n)
                        grew = True
            for call in prop_calls:
                dst = self._propagator_dest_name(call, local_tainted)
                if dst and dst not in local_tainted:
                    local_tainted.add(dst)
                    grew = True
            if not grew:
                break

        direct = False
        callees: set[str] = set()
        for value in returns:
            if self._reads_raw_source(value, local_tainted):
                direct = True
            if isinstance(value, ast.Name) and value.id in var_from_call:
                callees.add(var_from_call[value.id])
            if isinstance(value, ast.Call):
                callee = _call_short_name(value)
                if callee:
                    callees.add(callee)
        return direct, callees

    def _propagator_dest_name(
        self, call: ast.Call, local_tainted: set[str],
    ) -> str:
        """Destination variable name of a propagator call whose source reads
        untrusted input, for the return-source registry. Empty if not tainted.
        """
        short = _call_short_name(call)
        if (
            short in self._receiver_propagators
            and isinstance(call.func, ast.Attribute)
            and any(self._reads_raw_source(a, local_tainted) for a in call.args)
        ):
            recv = call.func.value
            if isinstance(recv, ast.Name):
                return recv.id
        spec = self._positional_propagators.get(short)
        if spec is not None:
            src_idx, dst_idx = spec
            if (
                src_idx < len(call.args) and dst_idx < len(call.args)
                and self._reads_raw_source(call.args[src_idx], local_tainted)
            ):
                dst = call.args[dst_idx]
                if isinstance(dst, ast.Name):
                    return dst.id
        return ""

    def _reads_raw_source(self, node: ast.expr, local_tainted: set[str]) -> bool:
        """True if the expression reads a source pattern or a known-tainted local.

        Registry-free and param-free by design — it must run before the registry
        exists, and it is the raw "reaches untrusted input" signal.
        """
        node = _unwrap_await(node)

        if isinstance(node, ast.Name):
            return node.id in local_tainted
        if isinstance(node, ast.Constant):
            return False

        text = _safe_unparse(node)
        if text:
            if any(marker in text for marker in NON_UNTRUSTED_SOURCE_MARKERS):
                # An env/interpreter marker anywhere kills the raw signal, same
                # conservative rule _is_source applies.
                return False
            for source in self._sources.get("python", []):
                if _source_matches(source, text):
                    return True

        if isinstance(node, ast.Attribute):
            return self._reads_raw_source(node.value, local_tainted)
        if isinstance(node, ast.Subscript):
            return self._reads_raw_source(node.value, local_tainted)
        if isinstance(node, ast.BinOp):
            return (
                self._reads_raw_source(node.left, local_tainted)
                or self._reads_raw_source(node.right, local_tainted)
            )
        if isinstance(node, ast.BoolOp):
            return any(self._reads_raw_source(v, local_tainted) for v in node.values)
        if isinstance(node, ast.IfExp):
            return (
                self._reads_raw_source(node.body, local_tainted)
                or self._reads_raw_source(node.orelse, local_tainted)
            )
        if isinstance(node, ast.JoinedStr):
            return any(
                isinstance(v, ast.FormattedValue)
                and self._reads_raw_source(v.value, local_tainted)
                for v in node.values
            )
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return any(self._reads_raw_source(e, local_tainted) for e in node.elts)
        if isinstance(node, ast.Call):
            # A call to a function already known to return untrusted input is a
            # source here too — this is what lets the registry converge over
            # multi-hop return chains across the global fixpoint below.
            if _call_short_name(node) in self._return_source_funcs:
                return True
            if any(self._reads_raw_source(a, local_tainted) for a in node.args):
                return True
            if isinstance(node.func, ast.Attribute):
                return self._reads_raw_source(node.func.value, local_tainted)
        return False

    def _scan_python_files(self):
        """Walk project for .py files and analyze each function."""
        total_funcs = 0
        py_files = self._filesystem_paths("*.py")

        for py_path in py_files:
            if len(self.findings) >= MAX_FINDINGS:
                break
            rel = str(py_path.relative_to(self.project_root)).replace("\\", "/")
            if SKIP_DIR_PATTERNS.search(rel) or _in_hidden_dir(rel):
                continue

            tree = self._python_tree(rel)
            if tree is None:
                continue
            content = self._content_cache[rel]
            self._current_file = rel

            # Count sources and sinks in this file
            self._count_sources_sinks(content, "python")

            file_funcs = 0
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if file_funcs >= MAX_FUNCTIONS:
                        self._truncation.add(f"file_function_cap:{rel}")
                        break
                    if total_funcs >= MAX_TOTAL_FUNCTIONS:
                        self._truncation.add("project_function_cap")
                        return
                    if len(self.findings) >= MAX_FINDINGS:
                        self._truncation.add("finding_cap")
                        return
                    file_funcs += 1
                    total_funcs += 1
                    self._current_class = self._func_class.get((rel, node.lineno), "")
                    self._analyze_function_ast(node, rel, content)
            self._functions_analyzed = total_funcs

    def _count_sources_sinks(self, content: str, lang: str):
        """Count source and sink occurrences in file content."""
        for source in self._sources.get(lang, []):
            src_clean = source.rstrip("(")
            self._source_count += content.count(src_clean)
        for pattern, _vt, _sev, _rec, requires in self._flat_sinks:
            if requires:
                # This is a text count, and a gated rule is not a text match.
                # `.find(` counts only when its argument is a mapping, so
                # counting every occurrence reports `str.find` as a sink and
                # inflated the number verify prints: 1554 -> 1890 on this
                # repository, none of it a sink the analysis would report.
                continue
            pat_clean = pattern.rstrip("(")
            self._sink_count += content.count(pat_clean)

    def _analyze_function_ast(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef, file_path: str, content: str,
    ):
        """Analyze a single function for taint flows."""
        # taint_state: var_name -> (source_expr, flow_chain)
        taint_state: dict[str, tuple[str, list[str]]] = {}
        self._orm_expressions = set()
        self._literal_bindings = {}

        # Mark all function params as "param-tainted" for cross-function analysis.
        param_names: list[str] = []
        framework_params = self._framework_source_params(func_node)
        for arg in [*func_node.args.posonlyargs, *func_node.args.args, *func_node.args.kwonlyargs]:
            name = arg.arg
            if name == "self" or name == "cls":
                continue
            param_names.append(name)
            injected = framework_params.get(name)
            if injected:
                # The framework hands this parameter the request data itself.
                # Treating it as `param:` would make the flow conditional on a
                # caller that never exists — a route handler is called by the
                # framework, so every web handler's input was invisible.
                taint_state[name] = (injected, [injected, name])
            else:
                taint_state[name] = (f"param:{name}", [f"param:{name}"])

        self._visit_body(func_node.body, taint_state, file_path, func_node.name)

        # After visiting: remove findings that came from param-only taint
        # (those are only real if a caller passes tainted data — Phase 2).
        self.findings = [
            f for f in self.findings
            if not f.source_expr.startswith("param:")
            or f.file_path != file_path
        ]

    def _framework_source_params(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, str]:
        """Parameters a web framework fills with request data.

        Matches the declaration, not a call: `limit: str = Query(...)`,
        `body: Item = Body(...)`, `x: Annotated[str, Form()]`. The marker must
        be one of the configured sources, so a project's own `taint.sources`
        additions work here too.
        """
        found: dict[str, str] = {}
        source_patterns = [
            pat for pat in self._sources.get("python", []) if pat.endswith("(")
        ]
        if not source_patterns:
            return found

        args = func_node.args
        positional = list(args.args) + list(getattr(args, "posonlyargs", []))
        defaults = list(args.defaults)
        # defaults align to the tail of the positional parameter list
        paired = list(zip(
            positional[len(positional) - len(defaults):], defaults, strict=False,
        ))
        paired += [
            (arg, default)
            for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False)
            if default is not None
        ]

        for arg, default in paired:
            for expr in (default, arg.annotation):
                if expr is None:
                    continue
                text = _safe_unparse(expr)
                if not text:
                    continue
                for pattern in source_patterns:
                    if pattern in text:
                        found[arg.arg] = text[:120]
                        break
                if arg.arg in found:
                    break

        # An annotation-only declaration (`x: Annotated[str, Query()]`) has no
        # default, so check the remaining annotations too.
        for arg in positional + list(args.kwonlyargs):
            if arg.arg in found or arg.annotation is None:
                continue
            text = _safe_unparse(arg.annotation)
            for pattern in source_patterns:
                if pattern in text:
                    found[arg.arg] = text[:120]
                    break

        return found

    def _visit_body(
        self,
        stmts: list[ast.stmt],
        taint_state: dict,
        file_path: str,
        func_name: str,
    ):
        """Walk a list of statements in order."""
        for stmt in stmts:
            if len(self.findings) >= MAX_FINDINGS:
                return
            self._visit_stmt(stmt, taint_state, file_path, func_name)

    def _visit_stmt(
        self,
        stmt: ast.stmt,
        taint_state: dict,
        file_path: str,
        func_name: str,
    ):
        """Handle a single statement."""
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            self._handle_assign(stmt, taint_state, file_path, func_name)

        elif isinstance(stmt, ast.Expr):
            called = _unwrap_await(stmt.value)
            if isinstance(called, ast.Call):
                self._handle_call_stmt(called, taint_state, file_path, func_name)

        elif isinstance(stmt, ast.Return):
            if stmt.value:
                # Check if the return value is a sink call (e.g., return render_template_string(x))
                returned = _unwrap_await(stmt.value)
                if isinstance(returned, ast.Call):
                    self._handle_call_stmt(returned, taint_state, file_path, func_name)
                tainted, src, chain = self._expr_is_tainted(stmt.value, taint_state)
                if tainted:
                    # Record that this function returns tainted data
                    pass

        elif isinstance(stmt, (ast.If, ast.While)):
            self._visit_body(stmt.body, taint_state, file_path, func_name)
            self._visit_body(stmt.orelse, taint_state, file_path, func_name)

        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            # Check if the iterator is tainted
            tainted, src, chain = self._expr_is_tainted(stmt.iter, taint_state)
            if tainted and isinstance(stmt.target, ast.Name):
                taint_state[stmt.target.id] = (src, chain + [stmt.target.id])
            self._visit_body(stmt.body, taint_state, file_path, func_name)
            self._visit_body(stmt.orelse, taint_state, file_path, func_name)

        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            # The context expression is where the sink usually is —
            # `with open(tainted) as f:`, `with db.cursor() as c:` — and it was
            # never analyzed. AsyncWith was not matched at all, dropping every
            # `async with` sink in FastAPI-style code.
            for item in stmt.items:
                ctx = _unwrap_await(item.context_expr)
                if isinstance(ctx, ast.Call):
                    self._handle_call_stmt(ctx, taint_state, file_path, func_name)
                c_tainted, c_src, c_chain = self._expr_is_tainted(ctx, taint_state)
                if c_tainted and isinstance(item.optional_vars, ast.Name):
                    taint_state[item.optional_vars.id] = (
                        c_src, c_chain + [item.optional_vars.id],
                    )
            self._visit_body(stmt.body, taint_state, file_path, func_name)

        elif isinstance(stmt, ast.Try):
            self._visit_body(stmt.body, taint_state, file_path, func_name)
            for handler in stmt.handlers:
                self._visit_body(handler.body, taint_state, file_path, func_name)
            self._visit_body(stmt.orelse, taint_state, file_path, func_name)
            self._visit_body(stmt.finalbody, taint_state, file_path, func_name)

    def _handle_assign(
        self,
        stmt: ast.stmt,
        taint_state: dict,
        file_path: str,
        func_name: str,
    ):
        """Handle assignment — propagate or introduce taint."""
        if isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target] if stmt.target else []
            value = stmt.value
        else:
            targets = stmt.targets
            value = stmt.value
        value = _unwrap_await(value) if value is not None else value

        # Track ORM query objects so a parameterized `db.execute(query)` is not
        # reported as SQL injection. The tainted value is real; what it reaches
        # is a bound parameter, not concatenated SQL.
        if value is not None:
            for target in targets:
                name = self._target_name(target)
                if not name:
                    continue
                if _is_orm_expression(value):
                    self._orm_expressions.add(name)
                elif _builds_sql_string(value):
                    self._orm_expressions.discard(name)
                if provable_shape(value) is not None or is_constant_literal(value):
                    self._literal_bindings[name] = value
                else:
                    self._literal_bindings.pop(name, None)

        if value is None:
            return

        # Some sinks are written to, not called: response.headers["X"] = v and
        # its Express and Go equivalents. Their patterns end in "[" and were
        # only ever matched against call expressions, so the whole
        # crlf_injection category never fired on the idiom it describes.
        self._check_subscript_sinks(targets, value, taint_state, file_path, func_name)

        # Check sanitizer FIRST — e.g., int(request.args.get('id')) is safe
        if self._is_sanitizer_expr(value):
            for target in targets:
                name = self._target_name(target)
                if name and name in taint_state:
                    del taint_state[name]
            return

        # Check if RHS is a source
        source = self._is_source(value)
        if source:
            for target in targets:
                name = self._target_name(target)
                if name:
                    taint_state[name] = (source, [source, name])
                else:
                    # `d[k] = request...` / `obj.attr = request...` taints the
                    # container or object, so a later read of it is tainted.
                    self._taint_expr_target(target, source, [source], taint_state)
            return

        # Check if RHS is a sink call with tainted args
        if isinstance(value, ast.Call):
            self._handle_call_stmt(value, taint_state, file_path, func_name)

        # Check if RHS is tainted (propagation)
        tainted, src, chain = self._expr_is_tainted(value, taint_state)
        if tainted:
            for target in targets:
                name = self._target_name(target)
                if name:
                    taint_state[name] = (src, chain + [name])
                else:
                    self._taint_expr_target(target, src, chain, taint_state)

    def _sql_arg_is_dynamic(self, call: ast.Call) -> bool:
        """True when some argument is a SQL string built at runtime."""
        args = list(call.args) + [kw.value for kw in call.keywords]
        if not args:
            return False
        for arg in args:
            arg = _unwrap_await(arg)
            if _builds_sql_string(arg):
                return True
            if isinstance(arg, ast.Name):
                if arg.id in self._orm_expressions:
                    continue
                # An unknown variable could be either; keep it rather than
                # silently dropping a real flow.
                return True
            if _is_orm_expression(arg):
                continue
        return False

    def _taint_expr_target(
        self, expr: ast.expr, src: str, chain: list[str], taint_state: dict,
    ) -> None:
        """Mark the variable an expression denotes as tainted.

        Handles a plain name, a subscript base (`d[k]` taints `d`), and an
        attribute (`obj.attr`, keyed by its dotted text so a later read of the
        same dotted name resolves).
        """
        if isinstance(expr, ast.Name):
            taint_state[expr.id] = (src, chain + [expr.id])
        elif isinstance(expr, ast.Subscript):
            self._taint_expr_target(expr.value, src, chain, taint_state)
        elif isinstance(expr, ast.Attribute):
            dotted = _safe_unparse(expr)
            if dotted:
                taint_state[dotted] = (src, chain + [dotted])

    def _apply_propagators(self, call: ast.Call, taint_state: dict) -> None:
        """Spread taint through in-place mutation (Semgrep-style propagators).

        `dst.append(taint)` / `proto.MergeFrom(taint)` taints the receiver;
        `parse_dict(json, proto)` taints the destination argument. Value-flow
        taint cannot see these because the tainted data never appears on the
        left of an assignment.
        """
        short = _call_short_name(call)
        if not short:
            return

        if short in self._receiver_propagators and isinstance(
            call.func, ast.Attribute
        ):
            for arg in call.args:
                tainted, src, chain = self._expr_is_tainted(arg, taint_state)
                if tainted:
                    self._taint_expr_target(
                        call.func.value, src, chain, taint_state,
                    )
                    break

        spec = self._positional_propagators.get(short)
        if spec is not None:
            src_idx, dst_idx = spec
            if src_idx < len(call.args) and dst_idx < len(call.args):
                tainted, src, chain = self._expr_is_tainted(
                    call.args[src_idx], taint_state,
                )
                if tainted:
                    self._taint_expr_target(
                        call.args[dst_idx], src, chain, taint_state,
                    )

    def _handle_call_stmt(
        self,
        call: ast.Call,
        taint_state: dict,
        file_path: str,
        func_name: str,
    ):
        """Handle a call expression as a statement — check if it's a sink."""
        # A sink is often not the outermost call: open(p).read(), and
        # requests.get(url).json(), put it in the receiver, where only the
        # trailing method was ever examined. Walk down the chain first so the
        # call that actually reaches the resource is seen.
        receiver = getattr(call.func, "value", None)
        inner = _unwrap_await(receiver) if receiver is not None else None
        if isinstance(inner, ast.Call):
            self._handle_call_stmt(inner, taint_state, file_path, func_name)

        self._apply_propagators(call, taint_state)
        call_str = _safe_unparse(call.func)

        if self._is_subprocess_sink(call_str):
            self._handle_subprocess_shell_call(call, taint_state, file_path, func_name)
            return

        for pattern, vuln_type, severity, rec, requires in self._flat_sinks:
            match_pat = pattern.rstrip("(")
            if not _call_matches_sink_pattern(call, pattern):
                continue

            # subprocess.* is only an RCE sink in this AST pass when shell=True.
            # Arg-list subprocess usage is handled as safe by default; shell=True
            # is checked explicitly in the keyword-arg block below.
            if self._is_subprocess_sink(match_pat):
                continue

            # ReDoS requires an ACTUAL regex operation. The substring matcher
            # would otherwise flag look-alikes such as ``store.search(...)`` or
            # ``vec.compile(...)`` because ``re.search`` is a substring of
            # ``sto<re.search>`` etc. Gate the redos category on the call's real
            # dotted name being a known regex entry point.
            if vuln_type == "redos" and not self._is_real_regex_call(call_str):
                continue

            # ReDoS sinks also need a non-trivial / dynamic pattern argument.
            # A regex over a constant literal (or no args) cannot be attacker-
            # influenced into catastrophic backtracking via the source.
            if vuln_type == "redos" and not self._redos_pattern_is_dynamic(call):
                continue

            # SQL sinks accept both strings and ORM expression objects. Only a
            # string assembled at runtime can carry an injection; a bound
            # `select(...).where(...)` cannot, and reporting it buries the real
            # leads under every list endpoint in the project.
            if vuln_type == "sql_injection" and not self._sql_arg_is_dynamic(call):
                continue

            # path_traversal via os.path.join is only real when an actual
            # tainted component is a path segment. When every non-source segment
            # is a string literal (e.g. join(env_path, 'src', 'mcp_server.py'))
            # there is no attacker-controlled path component to traverse with.
            if (
                vuln_type == "path_traversal"
                and "os.path.join" in match_pat
                and self._join_has_only_constant_extra_segments(call)
            ):
                continue

            # Declarative gates. A rule that cannot name its receiver -- a
            # Mongo collection is called whatever the project called it --
            # states the argument shape instead, and `.find(` counts only when
            # it is given a mapping rather than the string `str.find` takes.
            if requires and not call_satisfies(
                call, call_str, requires, self._literal_bindings,
                self._current_class,
            ):
                continue

            # Parameterized query detection: execute(sql, params) is safe
            if "execute" in pattern and len(call.args) >= 2:
                continue

            # Check if any argument is tainted
            for i, arg in enumerate(call.args):
                tainted, src, chain = self._expr_is_tainted(arg, taint_state)
                if tainted:
                    # Check if sanitized for this vuln type
                    if self._is_sanitized_for(arg, vuln_type):
                        self._sanitized_findings.append(TaintFlow(
                            file_path=file_path,
                            line=getattr(call, "lineno", 0),
                            severity=severity,
                            category=vuln_type,
                            source_expr=src,
                            sink_expr=_safe_unparse(call),
                            flow_chain=chain + [_safe_unparse(call)],
                            recommendation=rec,
                            source_file=file_path,
                            source_line=getattr(call, "lineno", 0),
                            sink_file=file_path,
                            sink_line=getattr(call, "lineno", 0),
                            path=[f"{file_path}:{func_name}:{getattr(call, 'lineno', 0)}"],
                            sanitized=True,
                        ))
                        continue

                    sink_str = _safe_unparse(call)
                    flow = TaintFlow(
                        file_path=file_path,
                        line=getattr(call, "lineno", 0),
                        severity=severity,
                        category=vuln_type,
                        source_expr=src,
                        sink_expr=sink_str,
                        flow_chain=chain + [sink_str],
                        recommendation=rec,
                        source_file=file_path,
                        source_line=getattr(call, "lineno", 0),
                        sink_file=file_path,
                        sink_line=getattr(call, "lineno", 0),
                        path=[f"{file_path}:{func_name}:{getattr(call, 'lineno', 0)}"],
                        sanitized=False,
                    )
                    self.findings.append(flow)

                    # Track dangerous function params for cross-function
                    # analysis. Every parameter the call passes counts, not
                    # only whichever one the finding happens to name: the
                    # finding stops at the first tainted argument, and a
                    # callee registered on one parameter is unreachable
                    # through the others.
                    self._register_dangerous_params(
                        call, taint_state, file_path, func_name,
                        vuln_type, severity, rec,
                    )
                    break  # one finding per call site

    def _record_dangerous_param(
        self,
        file_path: str,
        func_name: str,
        param_idx: int,
        param_name: str,
        vuln_type: str,
        severity: str,
        rec: str,
        trace: tuple,
    ) -> None:
        """Retain one shortest path per terminal sink, within the finding budget."""
        params = self._dangerous_functions.setdefault((file_path, func_name), [])
        item = (param_idx, param_name, vuln_type, severity, rec)
        if item not in params:
            params.append(item)
        traces = self._dangerous_traces.setdefault(
            (file_path, func_name, param_name, vuln_type), {}
        )
        identity = (trace[0], trace[1], trace[2])
        previous = traces.get(identity)
        if previous is None and len(traces) >= MAX_FINDINGS:
            self._truncation.add("cross_sink_cap")
            return
        if previous is None or len(trace[3]) < len(previous[3]):
            traces[identity] = trace

    def _register_dangerous_params(
        self,
        call: ast.Call,
        taint_state: dict,
        file_path: str,
        func_name: str,
        vuln_type: str,
        severity: str,
        rec: str,
    ) -> None:
        """Carry each parameter's real terminal sink into the existing caller pass."""
        line = getattr(call, "lineno", 0)
        trace = (
            file_path,
            line,
            _safe_unparse(call),
            (f"{file_path}:{func_name}:{line}",),
            "intraprocedural_ast",
        )
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            for node in self._unsanitized_names(arg, vuln_type):
                state = taint_state.get(node.id)
                if not state or not state[0].startswith("param:"):
                    continue
                name = state[0][len("param:") :]
                idx = self._find_param_index(func_name, name, file_path)
                if idx is not None:
                    self._record_dangerous_param(
                        file_path, func_name, idx, name, vuln_type, severity, rec, trace
                    )

    def _unsanitized_names(self, expr: ast.expr, vuln_type: str):
        """Names in `expr` that a sanitizer does not already cover.

        Sanitization is usually applied to one part of an argument --
        `f"run {table} " + shlex.quote(cmd)` -- so asking about the whole
        argument answers no and would register a parameter that is cleaned.
        """
        if self._is_sanitized_for(expr, vuln_type):
            return
        if isinstance(expr, ast.Name):
            yield expr
            return
        for child in ast.iter_child_nodes(expr):
            if isinstance(child, ast.expr):
                yield from self._unsanitized_names(child, vuln_type)

    def _check_subscript_sinks(
        self,
        targets: list,
        value: ast.expr,
        taint_state: dict,
        file_path: str,
        func_name: str,
    ) -> None:
        """Report a tainted value written into a subscript-shaped sink."""
        subscripts = [tg for tg in targets if isinstance(tg, ast.Subscript)]
        if not subscripts:
            return
        tainted, src, chain = self._expr_is_tainted(value, taint_state)
        if not tainted:
            return
        for target in subscripts:
            # "response.headers[" describes the receiver, so compare against
            # the part before the index rather than the whole expression.
            receiver = _safe_unparse(target.value) + "["
            for pattern, vuln_type, severity, rec, requires in self._flat_sinks:
                if not pattern.endswith("["):
                    continue
                if pattern not in receiver:
                    continue
                if not receiver_satisfies(
                    target.value, requires, self._current_class,
                ):
                    # A subscript assignment has no arguments, so a rule that
                    # asks about them does not apply. It does have a receiver:
                    # `request.headers[k] = v` is a client building its own
                    # outgoing request, not a response header being written.
                    continue
                if self._is_sanitized_for(value, vuln_type):
                    continue
                line = getattr(target, "lineno", 0)
                sink_str = f"{_safe_unparse(target)} = {_safe_unparse(value)}"
                self.findings.append(TaintFlow(
                    file_path=file_path,
                    line=line,
                    severity=severity,
                    category=vuln_type,
                    source_expr=src,
                    sink_expr=sink_str,
                    flow_chain=chain + [sink_str],
                    recommendation=rec,
                    source_file=file_path,
                    source_line=line,
                    sink_file=file_path,
                    sink_line=line,
                    path=[f"{file_path}:{func_name}:{line}"],
                    sanitized=False,
                ))

                # A parameter reaching this sink makes the function dangerous
                # to call, exactly as it does for a call sink. Only the call
                # sink recorded it, so `def echo(resp, origin):
                # resp.headers[k] = origin` was a finding in itself and
                # invisible to every caller that passed a tainted origin in.
                if src.startswith("param:"):
                    param_name = src[len("param:"):]
                    param_idx = self._find_param_index(
                        func_name, param_name, file_path,
                    )
                    if param_idx is not None:
                        self._record_dangerous_param(
                            file_path, func_name, param_idx, param_name,
                            vuln_type, severity, rec,
                            (file_path, line, sink_str,
                             (f"{file_path}:{func_name}:{line}",), "intraprocedural_ast"),
                        )
                return

    @staticmethod
    def _is_subprocess_sink(match_pat: str) -> bool:
        return match_pat in {
            "subprocess.run",
            "subprocess.call",
            "subprocess.Popen",
            "subprocess.check_output",
        }

    @staticmethod
    def _is_real_regex_call(call_str: str) -> bool:
        """True iff the call's dotted func name is an actual regex operation.

        Guards the ``redos`` category against substring look-alikes like
        ``store.search`` (``re.search`` is a substring of ``sto+re.search``).
        Matches on the trailing dotted segment so aliased imports such as
        ``import re as regex`` still resolve via the known-call table, while a
        bare attribute on an unrelated object (``store.search``) does not.
        """
        for known in REDOS_REGEX_CALLS:
            # Exact full match (e.g. "re.search") ...
            if call_str == known:
                return True
            # ... or the call ends with ".<known-tail>" where the segment
            # immediately before the tail is the regex module/alias, not an
            # arbitrary receiver. "re.search" -> require call to end with
            # "re.search" preceded by a boundary (start or '.').
            if call_str.endswith(known):
                prefix = call_str[: -len(known)]
                if prefix == "" or prefix.endswith("."):
                    return True
        return False

    @staticmethod
    def _redos_pattern_is_dynamic(call: ast.Call) -> bool:
        """True iff the regex pattern argument is not a constant literal.

        A regex compiled/searched over a string literal (or with no pattern
        arg at all) cannot be steered into catastrophic backtracking by the
        tainted *subject* string, so it is not a ReDoS sink. For ``re.sub`` the
        pattern is still arg 0.
        """
        if not call.args:
            return False
        pattern_arg = call.args[0]
        # A plain string/bytes constant pattern is static -> not ReDoS.
        if isinstance(pattern_arg, ast.Constant) and isinstance(
            pattern_arg.value, (str, bytes)
        ):
            return False
        return True

    @staticmethod
    def _join_has_only_constant_extra_segments(call: ast.Call) -> bool:
        """True iff an os.path.join has >1 arg and every arg after the first is
        a string literal.

        Shape: ``os.path.join(base, 'src', 'mcp_server.py')``. When the only
        non-literal component is the base path, there is no separately
        attacker-controlled path *segment* being appended, so this is not a
        path-traversal sink. Genuine cases like ``os.path.join(root, user_file)``
        keep a non-literal extra segment and are NOT suppressed.
        """
        if call.keywords:
            return False
        if len(call.args) < 2:
            return False
        for extra in call.args[1:]:
            if not (
                isinstance(extra, ast.Constant) and isinstance(extra.value, str)
            ):
                return False
        return True

    def _handle_subprocess_shell_call(
        self,
        call: ast.Call,
        taint_state: dict,
        file_path: str,
        func_name: str,
    ) -> None:
        for kw in call.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                if not call.args:
                    return
                tainted, src, chain = self._expr_is_tainted(call.args[0], taint_state)
                if not tainted:
                    return
                self.findings.append(TaintFlow(
                    file_path=file_path,
                    line=getattr(call, "lineno", 0),
                    severity="critical",
                    category="rce",
                    source_expr=src,
                    sink_expr=_safe_unparse(call),
                    flow_chain=chain + [_safe_unparse(call)],
                    recommendation="Do not pass shell=True with user input; use arg list",
                    source_file=file_path,
                    source_line=getattr(call, "lineno", 0),
                    sink_file=file_path,
                    sink_line=getattr(call, "lineno", 0),
                    path=[f"{file_path}:{func_name}:{getattr(call, 'lineno', 0)}"],
                    sanitized=False,
                ))
                return

    def _tainted_part(
        self, parts, taint_state: dict,
    ) -> tuple[bool, str, list[str]]:
        """The taint a composite expression carries, preferring a real source.

        Every composite branch used to return the first tainted part it found.
        When that part was a function parameter, the finding it produced was
        discarded later as param-only taint -- so
        `f"report {table} --name '{value}'"` reported nothing while the same
        f-string with its two interpolations swapped reported the injection.
        A parameter is still returned when nothing better is there, since that
        is what carries the flow deeper into the caller.
        """
        parameter = None
        for part in parts:
            if part is None:
                continue
            tainted, src, chain = self._expr_is_tainted(part, taint_state)
            if not tainted:
                continue
            if not src.startswith("param:"):
                return True, src, chain
            if parameter is None:
                parameter = (True, src, chain)
        return parameter or (False, "", [])

    def _expr_is_tainted(
        self, node: ast.AST, taint_state: dict,
    ) -> tuple[bool, str, list[str]]:
        """Check if an AST expression references tainted data.

        Returns (is_tainted, source_expr, flow_chain).
        """
        if isinstance(node, ast.expr):
            node = _unwrap_await(node)

        if isinstance(node, ast.Name):
            if node.id in taint_state:
                src, chain = taint_state[node.id]
                return True, src, chain
            return False, "", []

        if isinstance(node, ast.Attribute):
            # Instance attribute holding untrusted input, assigned in another
            # method of the same class (field sensitivity across methods).
            if (
                self._current_class
                and self._current_file
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                attrs = self._tainted_self_attrs.get(
                    (self._current_file, self._current_class)
                )
                if attrs and node.attr in attrs:
                    tag = f"self.{node.attr}"
                    return True, tag, [tag]
            # Check full dotted name (e.g., "user.email")
            full = _safe_unparse(node)
            # 1. Check if the full dotted name is in taint_state
            #    (e.g., "user.email" was assigned from a tainted source)
            if full and full in taint_state:
                src, chain = taint_state[full]
                return True, src, chain
            # 2. Check if it's a source itself
            for s in self._sources.get("python", []):
                if _source_matches(s, full):
                    return True, full, [full]
            # 3. Check if the value part is tainted (property propagation)
            #    e.g., user is tainted → user.email is also tainted
            return self._expr_is_tainted(node.value, taint_state)

        if isinstance(node, ast.Subscript):
            return self._expr_is_tainted(node.value, taint_state)

        if isinstance(node, ast.Call):
            # Check if it's a source
            source = self._is_source(node)
            if source:
                return True, source, [source]
            # Check if sanitizer — breaks taint
            if self._is_sanitizer_expr(node):
                return False, "", []
            # Check if any arg is tainted (taint propagates through calls)
            for arg in node.args:
                t, s, c = self._expr_is_tainted(arg, taint_state)
                if t:
                    return True, s, c
            # Check if the receiver (method call) is tainted:
            # e.g., data.get("x") where data is tainted → result is tainted
            if isinstance(node.func, ast.Attribute):
                t, s, c = self._expr_is_tainted(node.func.value, taint_state)
                if t:
                    return True, s, c
            return False, "", []

        if isinstance(node, ast.JoinedStr):
            # f-string: tainted if any value is tainted
            return self._tainted_part(
                [v.value for v in node.values if isinstance(v, ast.FormattedValue)],
                taint_state,
            )

        if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
            # A container carries the taint of what is put in it. Without this
            # the Mongo idiom -- collection.find({"name": untrusted}) -- read
            # as clean, which is the only way anyone writes that query, so the
            # whole nosql category was effectively unreachable.
            if isinstance(node, ast.Dict):
                parts = [v for v in node.values if v is not None]
                parts += [k for k in node.keys if k is not None]
            else:
                parts = list(node.elts)
            return self._tainted_part(parts, taint_state)

        if isinstance(node, ast.BinOp):
            # String concat or other binop: tainted if either side is
            return self._tainted_part([node.left, node.right], taint_state)

        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return self._tainted_part(node.elts, taint_state)

        if isinstance(node, ast.IfExp):
            return self._tainted_part([node.body, node.orelse], taint_state)

        return False, "", []

    def _is_source(self, node: ast.AST) -> str | None:
        """Check if node is a taint source. Returns source string or None.

        When an LSP server is available for the current file, post-filters
        the match by querying the type at the node's position — sources that
        resolve to int / bool / datetime / etc. (non-string types) are dropped
        because string-injection sinks cannot be exploited with them.
        """
        text = _safe_unparse(node)
        if not text:
            return None

        # Operator/interpreter-controlled expressions are not attacker-controlled
        # sources. This guard runs BEFORE pattern matching so that env vars and
        # __file__ are never tainted even if a broad/custom source pattern would
        # otherwise match them (e.g. os.path.join(os.environ[...], 'lit') or
        # Path(__file__)). Keeps genuine remote sources (request/argv/stdin).
        for marker in NON_UNTRUSTED_SOURCE_MARKERS:
            if marker in text:
                return None

        # A call to a function that returns untrusted input is a source at the
        # call site, even with no tainted arguments. This is the return-value
        # taint the intra-procedural pass cannot see on its own.
        if isinstance(node, ast.Call) and self._return_source_funcs:
            callee = _call_short_name(node)
            if callee and callee in self._return_source_funcs:
                return f"{callee}(...) [returns untrusted input]"

        matched = None
        for source in self._sources.get("python", []):
            if _source_matches(source, text):
                matched = text
                break
        if matched is None:
            return None

        # Type-aware filter (LSP) — only suppresses, never adds
        if self._current_file and hasattr(node, "lineno") and hasattr(node, "col_offset"):
            try:
                from .type_filter import source_is_taintable
                source_path = self.project_root / self._current_file
                if not source_is_taintable(
                    self.project_root, source_path,
                    node.lineno - 1, node.col_offset,
                ):
                    self._type_filtered += 1
                    return None
            except Exception as e:
                logger.debug("type_filter skipped: %s", e)

        return matched

    def _is_sanitizer_expr(self, node: ast.AST) -> bool:
        """Check if node is a sanitizer call."""
        if not isinstance(node, ast.Call):
            return False
        text = _safe_unparse(node.func)
        category = getattr(self, "_active_sink_category", None)
        for pattern, cleanses in self._sanitizers:
            name = pattern.rstrip("(")
            if (text == name or text.endswith("." + name)) and (
                category is None or "*" in cleanses or category in cleanses
            ):
                return True
        return False

    def _is_sanitized_for(self, node: ast.AST, vuln_type: str) -> bool:
        """Check if expression is wrapped in a sanitizer for given vuln type."""
        if not isinstance(node, ast.Call):
            return False
        text = _safe_unparse(node.func)
        for pattern, cleanses in self._sanitizers:
            name = pattern.rstrip("(")
            if text == name or text.endswith("." + name):
                if "*" in cleanses or vuln_type in cleanses:
                    return True
        return False

    def _target_name(self, target: ast.AST) -> str | None:
        """Extract variable name from an assignment target.

        Handles:
          - Name: ``x = ...``  → ``"x"``
          - Attribute: ``self.x = ...``  → ``"self.x"``  (enables property taint)
          - Tuple: ``(x, y) = ...``  → ``"x"``  (first element only)
        """
        if isinstance(target, ast.Name):
            return target.id
        if isinstance(target, ast.Attribute):
            # Track attribute assignments: user.email = ... → "user.email"
            full = _safe_unparse(target)
            return full if full else None
        if isinstance(target, ast.Tuple):
            # Only handle first element for simplicity
            if target.elts and isinstance(target.elts[0], ast.Name):
                return target.elts[0].id
        return None

    def _find_param_index(self, func_name: str, param_name: str, file_path: str) -> int | None:
        """Find index of param_name in func_name's signature (excluding self/cls)."""
        if not self._gitignore.includes_cached(file_path):
            return None
        tree = self._python_tree(file_path)
        if tree is None:
            return None

        candidates = [
            (node, qualified)
            for node, qualified in _functions_with_qualnames(tree)
            if func_name in (node.name, qualified)
        ]
        if len(candidates) != 1:
            return None
        node, qualified = candidates[0]
        positional = [*node.args.posonlyargs, *node.args.args]
        if "." in qualified and positional and positional[0].arg in ("self", "cls"):
            positional = positional[1:]
        for idx, arg in enumerate(positional):
            if arg.arg == param_name:
                return idx
        if any(arg.arg == param_name for arg in node.args.kwonlyargs):
            return -1  # keyword-only: never bind a positional argument
        return None

