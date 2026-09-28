"""Cross-file caller attribution and bounded taint propagation."""

from __future__ import annotations

import ast
from collections import defaultdict

from .taint_bindings import PythonCallBindings, bound_argument
from .taint_common import (
    MAX_CALLERS, MAX_CROSS_DEPTH, MAX_FINDINGS, SKIP_DIR_PATTERNS,
    _call_short_name, _functions_with_qualnames, _unwrap_await, _with_returned_calls,
)
from .taint_evidence import TaintFlow


class TaintCrossFileMixin:
    _active_sink_category: str | None

    def _scan_cross_function_via_index(self):
        """Trace callers of dangerous functions using the index dependency graph.

        Uses the index's dependency data (type=calls) and reverse_index to find
        callers that pass tainted data to functions whose params reach sinks.
        Supports multi-level propagation up to MAX_CROSS_DEPTH.
        """
        if not self._dangerous_functions:
            return

        dependencies = self.index.get("dependencies", {})
        symbols = self.index.get("symbols", {})
        reverse_index = self.index.get("reverse_index", {})

        # Tracing a caller can discover that the caller is itself dangerous,
        # one hop further out. That was recorded and then never traced: the
        # name map was built once, before the round that grows it, so a chain
        # of three functions stopped at the second no matter what
        # MAX_CROSS_DEPTH said. Repeat while the set grows; `_cross_visited`
        # keeps each (caller, callee, depth) from being walked twice.
        self._bindings = PythonCallBindings(self._ast_cache)
        bound_callers = self._bindings.callers()
        if self._bindings.exhausted:
            self._truncation.add("static_binding_call_cap")
        seen: dict[tuple[str, str], tuple] = {}
        for depth in range(1, MAX_CROSS_DEPTH + 1):
            pending = {}
            for key, value in self._dangerous_functions.items():
                signature = self._dangerous_signature(key, value)
                if seen.get(key) != signature:
                    pending[key] = list(value)
                    seen[key] = signature
            if not pending:
                break
            dangerous_by_name: dict[str, list[tuple[str, str, list]]] = defaultdict(list)
            for (file_path, func_name), param_info in pending.items():
                if SKIP_DIR_PATTERNS.search(file_path.replace("\\", "/")):
                    continue
                dangerous_by_name[func_name].append((file_path, func_name, param_info))
                for caller_file, caller_func, line in bound_callers.get((file_path, func_name), []):
                    self._check_caller_for_taint(caller_file, caller_func, func_name,
                                                 param_info, line, depth, file_path)
            if dependencies:
                self._trace_via_dependencies(dangerous_by_name, dependencies, symbols, depth)
            if reverse_index and not dependencies:
                self._trace_via_reverse_index(dangerous_by_name, reverse_index)
        if any(seen.get(key) != self._dangerous_signature(key, value)
               for key, value in self._dangerous_functions.items()):
            self._truncation.add("cross_depth_cap")

    def _dangerous_signature(self, key: tuple[str, str], params: list) -> tuple:
        """A new terminal sink is new evidence even when the parameter is unchanged."""
        return tuple(
            (item, tuple(sorted(self._dangerous_traces.get((*key, item[1], item[2]), {}).items())))
            for item in params
        )

    def _trace_via_dependencies(
        self,
        dangerous_by_name: dict,
        dependencies: dict,
        symbols: dict,
        depth: int = 1,
    ):
        """Use index dependency graph (type=calls) to find callers."""
        # Build caller -> callee map from dependencies
        # dep: {source: caller_sym_id, target: callee_name, type: "calls"}
        callee_to_callers: dict[str, list[tuple[str, str, int]]] = defaultdict(list)

        for _dep_id, dep in dependencies.items():
            if dep.get("type", "") != "calls":
                continue
            caller_id = dep.get("source", "")
            callee_raw = dep.get("target", "")
            call_line = dep.get("source_line", 0)
            if caller_id and callee_raw:
                # callee_raw might be "module.func" or "func"
                callee_name = callee_raw.rsplit(".", 1)[-1] if "." in callee_raw else callee_raw
                callee_to_callers[callee_name].append((caller_id, callee_raw, call_line))

        checks = 0
        # For each dangerous function, find its callers
        for func_name, entries in dangerous_by_name.items():
            callers = callee_to_callers.get(func_name, [])
            if not callers:
                continue

            for caller_sym_id, _callee_raw, call_line in callers:
                if checks >= self._max_callers:
                    self._truncation.add("cross_caller_cap")
                    return
                if len(self.findings) >= MAX_FINDINGS:
                    self._truncation.add("finding_cap")
                    return

                checks += 1
                # Extract file path from symbol ID (format: project:path:type:name)
                parts = caller_sym_id.split(":")
                if len(parts) >= 4:
                    caller_file = parts[1]
                    caller_func = parts[-1]
                else:
                    continue

                # Get param info from any matching dangerous function entry
                for df_file, _df_name, param_info_list in entries:
                    self._check_caller_for_taint(
                        caller_file, caller_func, func_name,
                        param_info_list, call_line,
                        depth=depth,
                        callee_file=df_file,
                    )

    def _trace_via_reverse_index(
        self,
        dangerous_by_name: dict,
        reverse_index: dict,
    ):
        """Fallback: use reverse_index to find callers of dangerous functions."""
        caller_checks = 0

        for func_name, entries in dangerous_by_name.items():
            callers = reverse_index.get(func_name, [])
            if not callers:
                continue

            for caller_ref in callers:
                if caller_checks >= self._max_callers:
                    self._truncation.add("cross_caller_cap")
                    return
                if len(self.findings) >= MAX_FINDINGS:
                    self._truncation.add("finding_cap")
                    return

                caller_file = (
                    caller_ref if isinstance(caller_ref, str) else caller_ref.get("file", "")
                )
                if not caller_file:
                    continue

                caller_checks += 1
                for df_file, _df_name, param_info_list in entries:
                    self._check_caller(
                        caller_file, func_name, param_info_list, callee_file=df_file,
                    )

    def _check_caller_for_taint(
        self,
        caller_file: str,
        caller_func_name: str,
        callee_name: str,
        param_info_list: list[tuple[int, str, str, str, str]],
        call_line: int,
        depth: int = 1,
        callee_file: str = "",
    ):
        """Parse a caller file and check if tainted data flows to dangerous param positions.

        Supports multi-level: if the caller itself receives the tainted data via
        its own parameter, we register the caller as dangerous too (up to MAX_CROSS_DEPTH).
        """
        if depth > MAX_CROSS_DEPTH:
            return

        # Cycle detection — skip if we've already visited this exact traversal
        visit_key = (
            caller_file,
            caller_func_name,
            callee_file,
            callee_name,
            self._dangerous_signature((callee_file, callee_name), param_info_list),
            depth,
        )
        if visit_key in self._cross_visited:
            return
        self._cross_visited.add(visit_key)
        if self._cross_checks >= self._max_callers:
            self._truncation.add("cross_caller_cap")
            return
        self._cross_checks += 1

        tree = self._python_tree(caller_file)
        if tree is None:
            return

        # Find the specific function in the AST. The index names a method
        # `Class.method`, and comparing that to the AST's bare `method` matched
        # nothing -- so a caller that was a method was never scanned, which is
        # most callers in code that uses classes.
        matches = [
            (node, qualified)
            for node, qualified in _functions_with_qualnames(tree)
            if caller_func_name in (node.name, qualified)
        ]
        if len(matches) != 1:
            self._truncation.add("ambiguous_caller_definition")
            return
        node, _ = matches[0]
        self._current_file = caller_file
        self._current_class = self._func_class.get((caller_file, node.lineno), "")
        grouped: dict[str, list] = defaultdict(list)
        for info in param_info_list:
            grouped[info[2]].append(info)
        for category, group_info in grouped.items():
            taint_state: dict[str, tuple[str, list[str]]] = {}
            framework = self._framework_source_params(node)
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if arg.arg in ("self", "cls"):
                    continue
                tag = framework.get(arg.arg, f"param:{arg.arg}")
                taint_state[arg.arg] = (tag, [tag])
            previous = self._active_sink_category
            self._active_sink_category = category
            try:
                self._check_caller_body_v2(
                    node.body,
                    taint_state,
                    caller_file,
                    caller_func_name,
                    callee_name,
                    group_info,
                    depth,
                    callee_file,
                    {
                        arg.arg: arg.lineno
                        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                    },
                )
            finally:
                self._active_sink_category = previous

    @staticmethod
    def _cross_origin(expr: ast.AST, state: dict, origins: dict[str, int]) -> int:
        for node in ast.walk(expr):
            if isinstance(node, ast.Name) and node.id in state and node.id in origins:
                return origins[node.id]
        return getattr(expr, "lineno", 0)

    def _cross_binding(self, file: str, call: ast.Call, target_file: str, target_name: str) -> str:
        resolved = self._bindings.resolve(file, call) if self._bindings else None
        if resolved is not None:
            if (resolved.file, resolved.name) != (target_file, target_name):
                self._static_rejected += 1
                return ""
            self._static_verified += 1
            return "python_import_binding"
        if _call_short_name(call) != target_name.rsplit(".", 1)[-1]:
            return ""
        verdict = self._callee_verifier().verify_call(file, call, target_file, target_name)
        if verdict is False:
            return ""
        if verdict is True:
            return "lsp_call_definition"
        self._name_only_calls += 1
        return "name_only_callee"

    def _check_caller_body_v2(
        self,
        stmts: list[ast.stmt],
        taint_state: dict,
        caller_file: str,
        caller_func: str,
        callee_name: str,
        param_info_list: list,
        depth: int,
        callee_file: str = "",
        origins: dict[str, int] | None = None,
    ):
        """Use the existing taint evaluator with resolved edges and real sink provenance."""
        if origins is None:
            origins = {}
        for stmt in _with_returned_calls(stmts):
            if len(self.findings) >= MAX_FINDINGS:
                self._truncation.add("finding_cap")
                return
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = [stmt.target] if isinstance(stmt, ast.AnnAssign) else stmt.targets
                value = stmt.value
                if value is None:
                    continue
                value = _unwrap_await(value)
                tainted, src, chain = self._expr_is_tainted(value, taint_state)
                line = self._cross_origin(value, taint_state, origins)
                if self._is_sanitizer_expr(value):
                    tainted = False
                for target in targets:
                    name = self._target_name(target)
                    if not name:
                        continue
                    if tainted:
                        taint_state[name] = (src, chain + [name])
                        origins[name] = line
                    else:
                        taint_state.pop(name, None)
                        origins.pop(name, None)
            elif isinstance(stmt, ast.Expr):
                call = _unwrap_await(stmt.value)
                if not isinstance(call, ast.Call):
                    continue
                binding = self._cross_binding(caller_file, call, callee_file, callee_name)
                if not binding:
                    continue
                for idx, name, category, severity, rec in param_info_list:
                    arg = bound_argument(call, idx, name)
                    if arg is None:
                        if any(isinstance(a, ast.Starred) for a in call.args) or any(
                            k.arg is None for k in call.keywords
                        ):
                            self._truncation.add("dynamic_argument_binding")
                        continue
                    tainted, src, chain = self._expr_is_tainted(arg, taint_state)
                    if not tainted or self._is_sanitized_for(arg, category):
                        continue
                    key = (callee_file, callee_name, name, category)
                    traces = self._dangerous_traces.get(key, {})
                    if not traces:
                        self._truncation.add("missing_terminal_sink")
                        continue
                    for trace in list(traces.values()):
                        if len(self.findings) >= MAX_FINDINGS:
                            self._truncation.add("finding_cap")
                            return
                        path = (f"{caller_file}:{caller_func}:{call.lineno}", *trace[3])
                        resolution = (
                            "name_only_callee"
                            if "name_only_callee" in (binding, trace[4])
                            else binding
                        )
                        if src.startswith("param:"):
                            param = src[len("param:") :]
                            param_idx = self._find_param_index(caller_func, param, caller_file)
                            if param_idx is not None:
                                if depth >= MAX_CROSS_DEPTH:
                                    self._truncation.add("cross_depth_cap")
                                    continue
                                self._record_dangerous_param(
                                    caller_file,
                                    caller_func,
                                    param_idx,
                                    param,
                                    category,
                                    severity,
                                    rec,
                                    (trace[0], trace[1], trace[2], path, resolution),
                                )
                        else:
                            self.findings.append(
                                TaintFlow(
                                    file_path=caller_file,
                                    line=call.lineno,
                                    severity=severity,
                                    category=category,
                                    source_expr=src,
                                    sink_expr=trace[2],
                                    flow_chain=chain + [f"-> {callee_name}()", trace[2]],
                                    recommendation=rec,
                                    source_file=caller_file,
                                    source_line=self._cross_origin(arg, taint_state, origins),
                                    sink_file=trace[0],
                                    sink_line=trace[1],
                                    path=list(path),
                                    sanitized=False,
                                    callee_resolution=resolution,
                                )
                            )
            elif isinstance(stmt, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.Try)):
                # Join alternatives: a sanitizer/constant on one path cannot
                # erase the untrusted value on another or on a zero-trip loop.
                branches = [stmt.body, getattr(stmt, "orelse", [])]
                if isinstance(stmt, ast.Try):
                    branches += [h.body for h in stmt.handlers]
                merged, merged_origins = {}, {}
                for body in branches:
                    branch, branch_origins = dict(taint_state), dict(origins)
                    if isinstance(stmt, (ast.For, ast.AsyncFor)):
                        tainted, src, chain = self._expr_is_tainted(stmt.iter, branch)
                        if tainted and isinstance(stmt.target, ast.Name):
                            branch[stmt.target.id] = (src, chain + [stmt.target.id])
                            branch_origins[stmt.target.id] = self._cross_origin(
                                stmt.iter, branch, branch_origins
                            )
                    self._check_caller_body_v2(
                        body,
                        branch,
                        caller_file,
                        caller_func,
                        callee_name,
                        param_info_list,
                        depth,
                        callee_file,
                        branch_origins,
                    )
                    merged.update(branch)
                    merged_origins.update(branch_origins)
                taint_state.clear()
                taint_state.update(merged)
                origins.clear()
                origins.update(merged_origins)
                if isinstance(stmt, ast.Try):
                    self._check_caller_body_v2(
                        stmt.finalbody,
                        taint_state,
                        caller_file,
                        caller_func,
                        callee_name,
                        param_info_list,
                        depth,
                        callee_file,
                        origins,
                    )
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                self._check_caller_body_v2(
                    stmt.body,
                    taint_state,
                    caller_file,
                    caller_func,
                    callee_name,
                    param_info_list,
                    depth,
                    callee_file,
                    origins,
                )

    def _check_caller(
        self,
        caller_file: str,
        callee_name: str,
        param_info_list: list[tuple[int, str, str, str, str]],
        callee_file: str = "",
    ):
        """Parse a caller file and check if tainted data is passed at dangerous param positions."""
        if SKIP_DIR_PATTERNS.search(caller_file.replace("\\", "/")):
            return
        if not self._gitignore.includes_cached(caller_file):
            return
        tree = self._python_tree(caller_file)
        if tree is None:
            return

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            taint_state: dict[str, tuple[str, list[str]]] = {}
            self._check_caller_body(
                node.body, taint_state, caller_file, callee_name,
                param_info_list, callee_file,
            )

    def _check_caller_body(
        self,
        stmts: list[ast.stmt],
        taint_state: dict,
        caller_file: str,
        callee_name: str,
        param_info_list: list,
        callee_file: str = "",
    ):
        """Compatibility adapter for old reverse indexes; no second projection."""
        self._check_caller_body_v2(
            stmts, taint_state, caller_file, "", callee_name, param_info_list, 1, callee_file
        )

