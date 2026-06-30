"""
AST-based taint analysis engine.

Tracks data flow from untrusted sources (e.g., request.args) to dangerous
sinks (e.g., cursor.execute()), with sanitizer awareness to reduce false
positives.

Four phases:
  1. Single-function AST taint tracking (Python)
  2. Cross-function taint propagation via index call graph
  3. YAML custom rule loading
  4. Regex-based fallback for JS/TS/Go

Cross-function flow tracking:
  - Phase 1 identifies functions whose parameters reach sinks
  - Phase 2 traces callers from the index dependency graph
  - Follows data through: A receives tainted input -> A calls B(input) -> B calls sink
"""

import ast
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .taint_rules import (
    GO_TAINT_PATTERNS,
    JS_TAINT_PATTERNS,
    NON_UNTRUSTED_SOURCE_MARKERS,
    REDOS_REGEX_CALLS,
    SANITIZERS,
    SINKS,
    SOURCES,
)

logger = logging.getLogger(__name__)

# ── Performance limits ──────────────────────────────────────────────────────
MAX_FUNCTIONS = 1000
MAX_FINDINGS = 200
MAX_CALLERS = 2000
MAX_CROSS_DEPTH = 6
SKIP_DIR_PATTERNS = re.compile(
    r"(?:^|/)(?:test|tests|__tests__|mock|fixture|node_modules|__pycache__|"
    r"\.git|dist|dist-next|build|\.venv[^/]*|venv[^/]*|site-packages|"
    r"\.next|\.nuxt|\.output|\.open-next|\.wrangler|\.cloudflare|out|coverage)(?:/|$)|"
    r"(?:^|/)[^/]*(?:_test\.go|_test\.py|\.test\.[jt]sx?|\.spec\.[jt]sx?)$"
)

# Severity ranking for category defaults
CATEGORY_SEVERITY = {
    "sql_injection": "critical",
    "rce": "critical",
    "xss": "high",
    "path_traversal": "high",
    "deserialization": "critical",
    "ssrf": "high",
    "ssti": "high",
    "open_redirect": "medium",
    "xxe": "high",
    "ldap_injection": "high",
    "nosql_injection": "high",
    "crlf_injection": "medium",
    "redos": "medium",
    "prototype_pollution": "high",
}


@dataclass
class TaintFlow:
    """A single taint-flow finding."""

    file_path: str
    line: int
    severity: str
    category: str  # vuln type: sql_injection, rce, xss, ...
    source_expr: str
    sink_expr: str
    flow_chain: list[str] = field(default_factory=list)
    recommendation: str = ""
    source_file: str = ""
    source_line: int = 0
    sink_file: str = ""
    sink_line: int = 0
    path: list[str] = field(default_factory=list)  # ["file:func:line", ...]
    sanitized: bool = False

    def to_dict(self) -> dict:
        return {
            "source": self.source_expr,
            "source_file": self.source_file or self.file_path,
            "source_line": self.source_line or self.line,
            "sink": self.sink_expr,
            "sink_file": self.sink_file or self.file_path,
            "sink_line": self.line,
            "path": self.path or self.flow_chain,
            "sanitized": self.sanitized,
            "severity": self.severity,
            "category": self.category,
            "recommendation": self.recommendation,
        }


@dataclass
class DataFlowResult:
    """Aggregate result of taint analysis."""

    total_sources: int = 0
    total_sinks: int = 0
    taint_flows: list[TaintFlow] = field(default_factory=list)
    sanitized_flows: int = 0
    high_risk_count: int = 0

    def to_dict(self) -> dict:
        unsanitized = [f for f in self.taint_flows if not f.sanitized]
        return {
            "total_sources": self.total_sources,
            "total_sinks": self.total_sinks,
            "unsanitized_flows": len(unsanitized),
            "sanitized_flows": self.sanitized_flows,
            "high_risk_count": self.high_risk_count,
            "taint_flows": [f.to_dict() for f in unsanitized],
        }


# ── Helpers ─────────────────────────────────────────────────────────────────

def _safe_unparse(node: ast.AST) -> str:
    """ast.unparse with fallback for older Python."""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _flatten_sinks() -> list[tuple[str, str, str, str]]:
    """Return flat list: (pattern, vuln_type, severity, recommendation)."""
    out = []
    for vuln_type, entries in SINKS.items():
        for pattern, severity, rec in entries:
            out.append((pattern, vuln_type, severity, rec))
    return out


FLAT_SINKS = _flatten_sinks()


# ── YAML rule loading ──────────────────────────────────────────────────────

def _load_yaml_rules(project_root: Path) -> dict | None:
    """Load taint rules from .flyto-rules.yaml (taint: block) or taint_rules.yaml.

    Lookup order (first hit wins):
      1. .flyto-rules.yaml `taint:` block  — preferred, unified with other rules
      2. .flyto-index/taint_rules.yaml     — legacy location
      3. taint_rules.yaml at project root  — legacy location
    """
    try:
        import yaml  # optional dependency
    except ImportError:
        logger.debug("PyYAML not installed; skipping taint yaml rules")
        return None

    unified = project_root / ".flyto-rules.yaml"
    if unified.is_file():
        try:
            with open(unified) as f:
                data = yaml.safe_load(f) or {}
            taint_block = data.get("taint")
            if isinstance(taint_block, dict) and taint_block:
                return taint_block
        except Exception as e:
            logger.debug("Failed to load %s: %s", unified, e)

    for path in (
        project_root / ".flyto-index" / "taint_rules.yaml",
        project_root / "taint_rules.yaml",
    ):
        if path.is_file():
            try:
                with open(path) as f:
                    return yaml.safe_load(f)
            except Exception as e:
                logger.debug("Failed to load %s: %s", path, e)
                return None
    return None


def _apply_yaml_rules(
    yaml_cfg: dict,
    sources: dict[str, list[str]],
    flat_sinks: list[tuple[str, str, str, str]],
    sanitizers: list[tuple[str, list[str]]],
) -> tuple[dict, list, list]:
    """Merge YAML rules into working copies of sources/sinks/sanitizers."""
    # Extra sources
    for entry in yaml_cfg.get("sources", []):
        pat = entry.get("pattern", "")
        lang = entry.get("language", "python")
        if pat:
            sources.setdefault(lang, []).append(pat)

    # Extra sinks
    for entry in yaml_cfg.get("sinks", []):
        pat = entry.get("pattern", "")
        vuln = entry.get("vuln_type", "custom")
        sev = entry.get("severity", "high")
        rec = entry.get("recommendation", "Review this sink for taint flow")
        if pat:
            flat_sinks.append((pat, vuln, sev, rec))

    # Extra sanitizers
    for entry in yaml_cfg.get("sanitizers", []):
        pat = entry.get("pattern", "")
        cleanses = entry.get("cleanses", ["*"])
        if pat:
            sanitizers.append((pat, cleanses))

    # Overrides: remove
    overrides = yaml_cfg.get("overrides", {})
    remove_src = set(overrides.get("remove_sources", []))
    remove_snk = set(overrides.get("remove_sinks", []))

    if remove_src:
        for lang in sources:
            sources[lang] = [s for s in sources[lang] if s not in remove_src]
    if remove_snk:
        flat_sinks = [s for s in flat_sinks if s[0] not in remove_snk]

    return sources, flat_sinks, sanitizers


# ── Core engine ─────────────────────────────────────────────────────────────

class TaintAnalyzer:
    """AST-based taint analysis engine with cross-function flow tracking."""

    def __init__(self, project_root: Path, index: dict | None = None):
        self.project_root = project_root
        self.index = index or {}
        self.findings: list[TaintFlow] = []
        self._sanitized_findings: list[TaintFlow] = []

        # Working copies of rules (may be extended by YAML)
        self._sources = {k: list(v) for k, v in SOURCES.items()}
        self._flat_sinks = list(FLAT_SINKS)
        self._sanitizers = list(SANITIZERS)

        # Load optional YAML overrides
        yaml_cfg = _load_yaml_rules(project_root)
        if yaml_cfg:
            self._sources, self._flat_sinks, self._sanitizers = _apply_yaml_rules(
                yaml_cfg, self._sources, self._flat_sinks, self._sanitizers,
            )

        # Cross-function: functions whose param reaches a sink
        # Maps (file, func_name) -> list of (param_index, param_name, vuln_type, severity, rec)
        self._dangerous_functions: dict[
            tuple[str, str], list[tuple[int, str, str, str, str]]
        ] = {}

        # Visited set for cross-function traversal — prevents exponential
        # blowup and infinite loops when call graph has cycles.
        # Key: (caller_file, caller_func, callee_name, depth)
        self._cross_visited: set[tuple[str, str, str, int]] = set()

        # Source/sink counts for DataFlowResult
        self._source_count = 0
        self._sink_count = 0

        # Parsed AST cache for cross-function analysis
        self._ast_cache: dict[str, ast.Module] = {}
        self._content_cache: dict[str, str] = {}

        # Current file context (set during scan) — enables LSP type-aware filtering
        self._current_file: str | None = None

        # Type-aware FP suppression: counts how many sources LSP filtered out
        self._type_filtered: int = 0

    # ── Public API ──────────────────────────────────────────────────────────

    def analyze(self) -> list[TaintFlow]:
        """Run full taint analysis. Returns list of TaintFlow findings."""
        self.findings = []
        self._sanitized_findings = []
        self._source_count = 0
        self._sink_count = 0
        self._scan_python_files()
        self._scan_cross_function_via_index()
        self._scan_regex_languages()
        return self.findings

    def analyze_full(self) -> "DataFlowResult":
        """Run full analysis and return structured DataFlowResult."""
        self.analyze()

        high_risk = sum(
            1 for f in self.findings
            if f.severity in ("critical", "high") and not f.sanitized
        )

        return DataFlowResult(
            total_sources=self._source_count,
            total_sinks=self._sink_count,
            taint_flows=self.findings,
            sanitized_flows=len(self._sanitized_findings),
            high_risk_count=high_risk,
        )

    # ── Phase 1: Python AST analysis ────────────────────────────────────────

    def _scan_python_files(self):
        """Walk project for .py files and analyze each function."""
        func_count = 0
        py_files = sorted(self.project_root.rglob("*.py"))

        for py_path in py_files:
            if len(self.findings) >= MAX_FINDINGS:
                break
            rel = str(py_path.relative_to(self.project_root)).replace("\\", "/")
            if SKIP_DIR_PATTERNS.search(rel):
                continue

            try:
                content = py_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            try:
                tree = ast.parse(content, filename=rel)
            except SyntaxError:
                continue

            # Cache for cross-function use
            self._ast_cache[rel] = tree
            self._content_cache[rel] = content
            self._current_file = rel

            # Count sources and sinks in this file
            self._count_sources_sinks(content, "python")

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if func_count >= MAX_FUNCTIONS:
                        return
                    if len(self.findings) >= MAX_FINDINGS:
                        return
                    func_count += 1
                    self._analyze_function_ast(node, rel, content)

    def _count_sources_sinks(self, content: str, lang: str):
        """Count source and sink occurrences in file content."""
        for source in self._sources.get(lang, []):
            src_clean = source.rstrip("(")
            self._source_count += content.count(src_clean)
        for pattern, _vt, _sev, _rec in self._flat_sinks:
            pat_clean = pattern.rstrip("(")
            self._sink_count += content.count(pat_clean)

    def _analyze_function_ast(
        self, func_node: ast.FunctionDef, file_path: str, content: str,
    ):
        """Analyze a single function for taint flows."""
        # taint_state: var_name -> (source_expr, flow_chain)
        taint_state: dict[str, tuple[str, list[str]]] = {}

        # Mark all function params as "param-tainted" for cross-function analysis.
        param_names: list[str] = []
        for arg in func_node.args.args:
            name = arg.arg
            if name == "self" or name == "cls":
                continue
            param_names.append(name)
            taint_state[name] = (f"param:{name}", [f"param:{name}"])

        self._visit_body(func_node.body, taint_state, file_path, func_node.name)

        # After visiting: remove findings that came from param-only taint
        # (those are only real if a caller passes tainted data — Phase 2).
        self.findings = [
            f for f in self.findings
            if not f.source_expr.startswith("param:")
            or f.file_path != file_path
        ]

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

        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            self._handle_call_stmt(stmt.value, taint_state, file_path, func_name)

        elif isinstance(stmt, ast.Return):
            if stmt.value:
                # Check if the return value is a sink call (e.g., return render_template_string(x))
                if isinstance(stmt.value, ast.Call):
                    self._handle_call_stmt(stmt.value, taint_state, file_path, func_name)
                tainted, src, chain = self._expr_is_tainted(stmt.value, taint_state)
                if tainted:
                    # Record that this function returns tainted data
                    pass

        elif isinstance(stmt, (ast.If, ast.While)):
            self._visit_body(stmt.body, taint_state, file_path, func_name)
            self._visit_body(stmt.orelse, taint_state, file_path, func_name)

        elif isinstance(stmt, ast.For):
            # Check if the iterator is tainted
            tainted, src, chain = self._expr_is_tainted(stmt.iter, taint_state)
            if tainted and isinstance(stmt.target, ast.Name):
                taint_state[stmt.target.id] = (src, chain + [stmt.target.id])
            self._visit_body(stmt.body, taint_state, file_path, func_name)
            self._visit_body(stmt.orelse, taint_state, file_path, func_name)

        elif isinstance(stmt, ast.With):
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

        if value is None:
            return

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

    def _handle_call_stmt(
        self,
        call: ast.Call,
        taint_state: dict,
        file_path: str,
        func_name: str,
    ):
        """Handle a call expression as a statement — check if it's a sink."""
        call_str = _safe_unparse(call.func)

        if self._is_subprocess_sink(call_str):
            self._handle_subprocess_shell_call(call, taint_state, file_path, func_name)
            return

        for pattern, vuln_type, severity, rec in self._flat_sinks:
            # Strip trailing ( for matching against unparsed func name
            match_pat = pattern.rstrip("(")
            if match_pat not in call_str:
                continue
            # Avoid partial matches: "exec" should not match "execute"
            idx = call_str.find(match_pat)
            end_idx = idx + len(match_pat)
            if end_idx < len(call_str) and call_str[end_idx].isalnum():
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

                    # Track dangerous function params for cross-function analysis.
                    if src.startswith("param:"):
                        param_name = src[len("param:"):]
                        param_idx = self._find_param_index(func_name, param_name, file_path)
                        if param_idx is not None:
                            self._dangerous_functions.setdefault(
                                (file_path, func_name), []
                            ).append((param_idx, param_name, vuln_type, severity, rec))
                    break  # one finding per call site

    @staticmethod
    def _is_subprocess_sink(match_pat: str) -> bool:
        return match_pat in {
            "subprocess.run",
            "subprocess.call",
            "subprocess.Popen",
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

    def _expr_is_tainted(
        self, node: ast.AST, taint_state: dict,
    ) -> tuple[bool, str, list[str]]:
        """Check if an AST expression references tainted data.

        Returns (is_tainted, source_expr, flow_chain).
        """
        if isinstance(node, ast.Name):
            if node.id in taint_state:
                src, chain = taint_state[node.id]
                return True, src, chain
            return False, "", []

        if isinstance(node, ast.Attribute):
            # Check full dotted name (e.g., "user.email")
            full = _safe_unparse(node)
            # 1. Check if the full dotted name is in taint_state
            #    (e.g., "user.email" was assigned from a tainted source)
            if full and full in taint_state:
                src, chain = taint_state[full]
                return True, src, chain
            # 2. Check if it's a source itself
            for s in self._sources.get("python", []):
                if s in full:
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
            for val in node.values:
                if isinstance(val, ast.FormattedValue):
                    t, s, c = self._expr_is_tainted(val.value, taint_state)
                    if t:
                        return True, s, c
            return False, "", []

        if isinstance(node, ast.BinOp):
            # String concat or other binop: tainted if either side is
            t_l, s_l, c_l = self._expr_is_tainted(node.left, taint_state)
            if t_l:
                return True, s_l, c_l
            return self._expr_is_tainted(node.right, taint_state)

        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for elt in node.elts:
                t, s, c = self._expr_is_tainted(elt, taint_state)
                if t:
                    return True, s, c
            return False, "", []

        if isinstance(node, ast.IfExp):
            t, s, c = self._expr_is_tainted(node.body, taint_state)
            if t:
                return True, s, c
            return self._expr_is_tainted(node.orelse, taint_state)

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

        matched = None
        for source in self._sources.get("python", []):
            if source in text:
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
        for pattern, _ in self._sanitizers:
            if pattern.rstrip("(") in text:
                return True
        return False

    def _is_sanitized_for(self, node: ast.AST, vuln_type: str) -> bool:
        """Check if expression is wrapped in a sanitizer for given vuln type."""
        if not isinstance(node, ast.Call):
            return False
        text = _safe_unparse(node.func)
        for pattern, cleanses in self._sanitizers:
            if pattern.rstrip("(") in text:
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
        tree = self._ast_cache.get(file_path)
        if tree is None:
            py_path = self.project_root / file_path
            if not py_path.is_file():
                return None
            try:
                content = py_path.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(content)
                self._ast_cache[file_path] = tree
                self._content_cache[file_path] = content
            except (OSError, SyntaxError):
                return None

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                idx = 0
                for arg in node.args.args:
                    if arg.arg in ("self", "cls"):
                        continue
                    if arg.arg == param_name:
                        return idx
                    idx += 1
        return None

    # ── Phase 2: Cross-function taint via index call graph ─────────────────

    def _scan_cross_function_via_index(self):
        """Trace callers of dangerous functions using the index dependency graph.

        Uses the index's dependency data (type=calls) and reverse_index to find
        callers that pass tainted data to functions whose params reach sinks.
        Supports multi-level propagation up to MAX_CROSS_DEPTH.
        """
        if not self._dangerous_functions:
            return

        # Build a map from function name -> [(file, func_name, param_info)]
        # for quick lookup
        dangerous_by_name: dict[str, list[tuple[str, str, list]]] = defaultdict(list)
        for (file_path, func_name), param_info_list in self._dangerous_functions.items():
            if SKIP_DIR_PATTERNS.search(file_path.replace("\\", "/")):
                continue
            dangerous_by_name[func_name].append((file_path, func_name, param_info_list))

        # Strategy 1: Use index dependencies (call graph)
        dependencies = self.index.get("dependencies", {})
        symbols = self.index.get("symbols", {})

        if dependencies:
            self._trace_via_dependencies(dangerous_by_name, dependencies, symbols)

        # Strategy 2: Use reverse_index as fallback
        reverse_index = self.index.get("reverse_index", {})
        if reverse_index:
            self._trace_via_reverse_index(dangerous_by_name, reverse_index)

    def _trace_via_dependencies(
        self,
        dangerous_by_name: dict,
        dependencies: dict,
        symbols: dict,
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
                if checks >= MAX_CALLERS:
                    return
                if len(self.findings) >= MAX_FINDINGS:
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
                for _df_file, _df_name, param_info_list in entries:
                    self._check_caller_for_taint(
                        caller_file, caller_func, func_name,
                        param_info_list, call_line,
                        depth=1,
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
                if caller_checks >= MAX_CALLERS:
                    return
                if len(self.findings) >= MAX_FINDINGS:
                    return

                caller_file = caller_ref if isinstance(caller_ref, str) else caller_ref.get("file", "")
                if not caller_file:
                    continue

                caller_checks += 1
                for _df_file, _df_name, param_info_list in entries:
                    self._check_caller(caller_file, func_name, param_info_list)

    def _check_caller_for_taint(
        self,
        caller_file: str,
        caller_func_name: str,
        callee_name: str,
        param_info_list: list[tuple[int, str, str, str, str]],
        call_line: int,
        depth: int = 1,
    ):
        """Parse a caller file and check if tainted data flows to dangerous param positions.

        Supports multi-level: if the caller itself receives the tainted data via
        its own parameter, we register the caller as dangerous too (up to MAX_CROSS_DEPTH).
        """
        if depth > MAX_CROSS_DEPTH:
            return

        # Cycle detection — skip if we've already visited this exact traversal
        visit_key = (caller_file, caller_func_name, callee_name, depth)
        if visit_key in self._cross_visited:
            return
        self._cross_visited.add(visit_key)

        # Try AST cache first, then read from disk
        tree = self._ast_cache.get(caller_file)
        if SKIP_DIR_PATTERNS.search(caller_file.replace("\\", "/")):
            return
        if tree is None:
            caller_path = self.project_root / caller_file
            if not caller_path.is_file():
                return
            try:
                content = caller_path.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(content, filename=caller_file)
                self._ast_cache[caller_file] = tree
                self._content_cache[caller_file] = content
            except (OSError, SyntaxError):
                return

        # Find the specific function in the AST
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != caller_func_name:
                continue

            taint_state: dict[str, tuple[str, list[str]]] = {}

            # Mark params as param-tainted for deeper propagation
            for arg in node.args.args:
                if arg.arg in ("self", "cls"):
                    continue
                taint_state[arg.arg] = (f"param:{arg.arg}", [f"param:{arg.arg}"])

            # Walk the function body, building taint state
            self._check_caller_body_v2(
                node.body, taint_state, caller_file, caller_func_name,
                callee_name, param_info_list, depth,
            )

    def _check_caller_body_v2(
        self,
        stmts: list[ast.stmt],
        taint_state: dict,
        caller_file: str,
        caller_func: str,
        callee_name: str,
        param_info_list: list[tuple[int, str, str, str, str]],
        depth: int,
    ):
        """Walk caller function body, build taint state, check callee calls."""
        for stmt in stmts:
            if len(self.findings) >= MAX_FINDINGS:
                return

            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                if isinstance(stmt, ast.AnnAssign):
                    targets = [stmt.target] if stmt.target else []
                    value = stmt.value
                else:
                    targets = stmt.targets
                    value = stmt.value

                if value is None:
                    continue

                # Check sanitizer first
                if self._is_sanitizer_expr(value):
                    for t in targets:
                        name = self._target_name(t)
                        if name and name in taint_state:
                            del taint_state[name]
                    continue

                # Check source
                source = self._is_source(value)
                if source:
                    for t in targets:
                        name = self._target_name(t)
                        if name:
                            taint_state[name] = (source, [source, name])
                    continue

                # Propagate taint
                tainted, src, chain = self._expr_is_tainted(value, taint_state)
                if tainted:
                    for t in targets:
                        name = self._target_name(t)
                        if name:
                            taint_state[name] = (src, chain + [name])

            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call = stmt.value
                call_name = _safe_unparse(call.func)
                call_name_short = call_name.rsplit(".", 1)[-1] if "." in call_name else call_name

                if callee_name == call_name_short or callee_name in call_name:
                    for param_idx, param_name, vuln_type, severity, rec in param_info_list:
                        if param_idx < len(call.args):
                            tainted, src, chain = self._expr_is_tainted(
                                call.args[param_idx], taint_state,
                            )
                            if tainted:
                                # Build path showing the cross-function flow
                                path_steps = [
                                    f"{caller_file}:{caller_func}:{getattr(call, 'lineno', 0)}",
                                    f"-> {callee_name}(param:{param_name})",
                                ]

                                if src.startswith("param:"):
                                    # Taint comes from caller's own param — propagate deeper
                                    caller_param = src[len("param:"):]
                                    caller_param_idx = self._find_param_index(
                                        caller_func, caller_param, caller_file,
                                    )
                                    if caller_param_idx is not None and depth < MAX_CROSS_DEPTH:
                                        self._dangerous_functions.setdefault(
                                            (caller_file, caller_func), []
                                        ).append((caller_param_idx, caller_param, vuln_type, severity, rec))
                                else:
                                    # Direct source in caller — this is a real finding
                                    self.findings.append(TaintFlow(
                                        file_path=caller_file,
                                        line=getattr(call, "lineno", 0),
                                        severity=severity,
                                        category=vuln_type,
                                        source_expr=src,
                                        sink_expr=f"{callee_name}({param_name}=...)",
                                        flow_chain=chain + [f"-> {callee_name}()"],
                                        recommendation=rec,
                                        source_file=caller_file,
                                        source_line=0,  # source line from chain
                                        sink_file=caller_file,
                                        sink_line=getattr(call, "lineno", 0),
                                        path=path_steps,
                                        sanitized=False,
                                    ))

            elif isinstance(stmt, (ast.If, ast.While)):
                self._check_caller_body_v2(
                    stmt.body, taint_state, caller_file, caller_func,
                    callee_name, param_info_list, depth,
                )
                self._check_caller_body_v2(
                    stmt.orelse, taint_state, caller_file, caller_func,
                    callee_name, param_info_list, depth,
                )

            elif isinstance(stmt, ast.For):
                tainted, src, chain = self._expr_is_tainted(stmt.iter, taint_state)
                if tainted and isinstance(stmt.target, ast.Name):
                    taint_state[stmt.target.id] = (src, chain + [stmt.target.id])
                self._check_caller_body_v2(
                    stmt.body, taint_state, caller_file, caller_func,
                    callee_name, param_info_list, depth,
                )

    # Keep old method for backward compat with reverse_index path
    def _check_caller(
        self,
        caller_file: str,
        callee_name: str,
        param_info_list: list[tuple[int, str, str, str, str]],
    ):
        """Parse a caller file and check if tainted data is passed at dangerous param positions."""
        if SKIP_DIR_PATTERNS.search(caller_file.replace("\\", "/")):
            return
        caller_path = self.project_root / caller_file
        if not caller_path.is_file():
            return

        try:
            content = caller_path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content, filename=caller_file)
        except (OSError, SyntaxError):
            return

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            taint_state: dict[str, tuple[str, list[str]]] = {}
            self._check_caller_body(node.body, taint_state, caller_file, callee_name, param_info_list)

    def _check_caller_body(
        self,
        stmts: list[ast.stmt],
        taint_state: dict,
        caller_file: str,
        callee_name: str,
        param_info_list: list[tuple[int, str, str, str, str]],
    ):
        """Walk caller function body in order, building taint state and checking callee calls."""
        for stmt in stmts:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                if isinstance(stmt, ast.AnnAssign):
                    targets = [stmt.target] if stmt.target else []
                    value = stmt.value
                else:
                    targets = stmt.targets
                    value = stmt.value

                if value is None:
                    continue

                # Check sanitizer first
                if self._is_sanitizer_expr(value):
                    for t in targets:
                        name = self._target_name(t)
                        if name and name in taint_state:
                            del taint_state[name]
                    continue

                # Check source
                source = self._is_source(value)
                if source:
                    for t in targets:
                        name = self._target_name(t)
                        if name:
                            taint_state[name] = (source, [source, name])
                    continue

                # Propagate taint
                tainted, src, chain = self._expr_is_tainted(value, taint_state)
                if tainted:
                    for t in targets:
                        name = self._target_name(t)
                        if name:
                            taint_state[name] = (src, chain + [name])

            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call = stmt.value
                call_name = _safe_unparse(call.func)
                if callee_name in call_name:
                    for param_idx, param_name, vuln_type, severity, rec in param_info_list:
                        if param_idx < len(call.args):
                            tainted, src, chain = self._expr_is_tainted(
                                call.args[param_idx], taint_state,
                            )
                            if tainted:
                                self.findings.append(TaintFlow(
                                    file_path=caller_file,
                                    line=getattr(call, "lineno", 0),
                                    severity=severity,
                                    category=vuln_type,
                                    source_expr=src,
                                    sink_expr=f"{callee_name}(...)",
                                    flow_chain=chain + [f"-> {callee_name}()"],
                                    recommendation=rec,
                                    source_file=caller_file,
                                    source_line=0,
                                    sink_file=caller_file,
                                    sink_line=getattr(call, "lineno", 0),
                                    path=[f"{caller_file}:{getattr(call, 'lineno', 0)}"],
                                    sanitized=False,
                                ))

            elif isinstance(stmt, (ast.If, ast.While)):
                self._check_caller_body(stmt.body, taint_state, caller_file, callee_name, param_info_list)
                self._check_caller_body(stmt.orelse, taint_state, caller_file, callee_name, param_info_list)

            elif isinstance(stmt, ast.For):
                tainted, src, chain = self._expr_is_tainted(stmt.iter, taint_state)
                if tainted and isinstance(stmt.target, ast.Name):
                    taint_state[stmt.target.id] = (src, chain + [stmt.target.id])
                self._check_caller_body(stmt.body, taint_state, caller_file, callee_name, param_info_list)

    # ── Phase 3: Regex-based fallback for JS/TS/Go ─────────────────────────

    def _scan_regex_languages(self):
        """Scan non-Python files with targeted regex patterns."""
        ext_map = {
            ".js": JS_TAINT_PATTERNS,
            ".jsx": JS_TAINT_PATTERNS,
            ".ts": JS_TAINT_PATTERNS,
            ".tsx": JS_TAINT_PATTERNS,
            ".go": GO_TAINT_PATTERNS,
        }

        for ext, patterns in ext_map.items():
            if len(self.findings) >= MAX_FINDINGS:
                return
            for fpath in sorted(self.project_root.rglob(f"*{ext}")):
                if len(self.findings) >= MAX_FINDINGS:
                    return
                rel = str(fpath.relative_to(self.project_root))
                if SKIP_DIR_PATTERNS.search(rel):
                    continue

                try:
                    content = fpath.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue

                # Count sources/sinks for non-Python
                lang = "javascript" if ext in (".js", ".jsx", ".ts", ".tsx") else "go"
                self._count_sources_sinks(content, lang)

                self._scan_file_regex(rel, content, patterns)

    def _scan_file_regex(
        self,
        file_path: str,
        content: str,
        patterns: list[tuple[str, str, str, str]],
    ):
        """Scan a file's lines with regex taint patterns."""
        lines = content.split("\n")
        # For multi-line patterns, also scan consecutive line pairs
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("#"):
                continue

            # Check single line
            for pat, vuln_type, severity, rec in patterns:
                if re.search(pat, line, re.IGNORECASE):
                    self.findings.append(TaintFlow(
                        file_path=file_path,
                        line=i + 1,
                        severity=severity,
                        category=vuln_type,
                        source_expr="(regex match)",
                        sink_expr=line.strip()[:120],
                        flow_chain=[line.strip()[:120]],
                        recommendation=rec,
                        source_file=file_path,
                        source_line=i + 1,
                        sink_file=file_path,
                        sink_line=i + 1,
                        path=[f"{file_path}:{i + 1}"],
                        sanitized=False,
                    ))
                    break

            # Check two-line window for flows split across lines
            if i + 1 < len(lines):
                two_lines = line + " " + lines[i + 1]
                for pat, vuln_type, severity, rec in patterns:
                    if re.search(pat, two_lines, re.IGNORECASE):
                        # Avoid duplicate if single-line already matched
                        if not re.search(pat, line, re.IGNORECASE):
                            self.findings.append(TaintFlow(
                                file_path=file_path,
                                line=i + 1,
                                severity=severity,
                                category=vuln_type,
                                source_expr="(regex match)",
                                sink_expr=two_lines.strip()[:120],
                                flow_chain=[two_lines.strip()[:120]],
                                recommendation=rec,
                                source_file=file_path,
                                source_line=i + 1,
                                sink_file=file_path,
                                sink_line=i + 1,
                                path=[f"{file_path}:{i + 1}"],
                                sanitized=False,
                            ))
                        break
