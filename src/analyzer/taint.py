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
import importlib
import logging
import os
import re
import stat
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

try:
    gitignore_module = importlib.import_module("..gitignore", __package__)
except (ImportError, TypeError):
    gitignore_module = importlib.import_module("gitignore")

from .taint_bindings import PythonCallBindings, bound_argument
from .taint_evidence import DataFlowResult, TaintFlow
from .taint_policy import (
    CATEGORY_SEVERITY,
    FLAT_SINKS,
    _apply_yaml_rules,
    _flatten_sinks,
    _load_yaml_rules,
    _source_matches,
)
from .taint_propagation import (
    _POSITIONAL_PROPAGATORS,
    _RECEIVER_PROPAGATORS,
    _yaml_propagators,
)
from .taint_rules import (
    GO_TAINT_PATTERNS,
    JS_TAINT_PATTERNS,
    NON_UNTRUSTED_SOURCE_MARKERS,
    OPERATOR_SOURCES,
    REDOS_REGEX_CALLS,
    SANITIZERS,
    SINKS,
    SOURCES,
)
from .taint_shapes import (
    call_satisfies,
    is_constant_literal,
    normalize_requirements,
    provable_shape,
    receiver_satisfies,
)
from .taint_common import (
    MAX_CALLERS, MAX_CROSS_DEPTH, MAX_FINDINGS, MAX_FUNCTIONS,
    MAX_RETURN_SOURCE_FUNCS, MAX_RETURN_TAINT_ROUNDS, MAX_TAINT_FILE_BYTES,
    MAX_TAINT_SOURCE_BYTES, MAX_TAINT_SOURCE_FILES, MAX_TOTAL_FUNCTIONS,
    SKIP_DIR_PATTERNS, _ORM_BUILDERS, _ORM_CHAINS, _builds_sql_string,
    _call_short_name, _functions_with_qualnames, _in_hidden_dir,
    _is_generated_asset, _is_orm_expression, _safe_unparse, _unwrap_await,
    _with_returned_calls, _without_duplicates,
)
from .taint_source import TaintSourceMixin
from .taint_python_flow import TaintPythonFlowMixin
from .taint_cross_file import TaintCrossFileMixin
from .taint_regex import TaintRegexMixin

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .taint_lsp import CalleeVerifier

logger = logging.getLogger(__name__)

# ── Performance limits ──────────────────────────────────────────────────────
#: Functions analyzed per file. This used to be a project-wide counter that
#: silently returned from the whole scan on the 1000th function, in alphabetical
#: file order — flyto-core reached 21% of its 4778 functions and reported
#: nothing about the other 79%. Per-file is what the name always implied.
#: Project-wide budget, so a huge repository still terminates. Unlike the old
#: cap, hitting this is reported in the result instead of looking like a clean
#: scan.
#: Functions whose return signature is extracted for the return-taint registry.
#: Broader than the finding scan: a caller in scope may call a helper that is
#: not itself in scope, and we still need that helper's return taint.
#: Fixpoint rounds for "a function returning a call to a tainting function is
#: itself tainting". Real return chains are shallow; this only bounds pathology.
# One admitted source snapshot for all taint phases. These are analysis budgets,
# not a claim that the whole interpreter process stays below this byte count.





# Severity ranking for category defaults






# ── Helpers ─────────────────────────────────────────────────────────────────



#: Methods that mutate their receiver with argument data: `dst.append(taint)`,
#: `proto.MergeFrom(taint)`, `d.update(taint)`. A tainted argument taints the
#: receiver. This is Semgrep's propagator concept — taint spreading through
#: in-place mutation, which value-flow taint cannot see on its own.

#: Free functions that populate a destination argument from a source argument:
#: short name -> (source arg index, destination arg index). `parse_dict(json,
#: proto)` is mlflow's request path — it taints `proto` in place from `json`.

#: These two tables are the built-in DEFAULTS. A project extends them through
#: the `taint.propagators` block in .flyto-rules.yaml — the same file that
#: already configures sources, sinks and sanitizers — so a custom mutation
#: helper is declarable without editing the engine.


























# ── YAML rule loading ──────────────────────────────────────────────────────





# ── Core engine ─────────────────────────────────────────────────────────────

class TaintAnalyzer(TaintSourceMixin, TaintPythonFlowMixin, TaintCrossFileMixin, TaintRegexMixin):
    """AST-based taint analysis engine with cross-function flow tracking."""

    def __init__(self, project_root: Path, index: dict | None = None):
        self.project_root = project_root
        # Preserve the historical analyzer.taint monkeypatch seam: callers and
        # tests may override facade budget constants before construction. Mixins
        # read these per-instance values instead of frozen module copies.
        self._max_callers = MAX_CALLERS
        self._max_taint_file_bytes = MAX_TAINT_FILE_BYTES
        self._max_taint_source_bytes = MAX_TAINT_SOURCE_BYTES
        self._max_taint_source_files = MAX_TAINT_SOURCE_FILES
        self._gitignore = gitignore_module.GitIgnoreFilter(project_root)
        self.index = index or {}
        self._verifier: "CalleeVerifier | None" = None
        #: Variables in the current function that hold an ORM expression object
        #: (`select(...).where(...)`) rather than a SQL string.
        self._orm_expressions: set[str] = set()
        #: Functions that return the operator's own input and nothing worse.
        self._operator_return_funcs: set[str] = set()
        # name -> the literal it was last assigned in the function being
        # visited, so an argument-shape gate can resolve it.
        self._literal_bindings: dict[str, ast.expr] = {}
        self._truncation: set[str] = set()
        self._functions_analyzed = 0
        self.findings: list[TaintFlow] = []
        self._sanitized_findings: list[TaintFlow] = []

        # Working copies of rules (may be extended by YAML)
        self._sources = {k: list(v) for k, v in SOURCES.items()}
        self._flat_sinks = list(FLAT_SINKS)
        self._sanitizers = list(SANITIZERS)

        # Propagator working copies (built-in defaults, extended by YAML).
        self._receiver_propagators = set(_RECEIVER_PROPAGATORS)
        self._positional_propagators = dict(_POSITIONAL_PROPAGATORS)

        # Load optional YAML overrides
        yaml_cfg = _load_yaml_rules(project_root)
        if yaml_cfg:
            self._sources, self._flat_sinks, self._sanitizers = _apply_yaml_rules(
                yaml_cfg, self._sources, self._flat_sinks, self._sanitizers,
            )
            extra_recv, extra_pos = _yaml_propagators(yaml_cfg)
            self._receiver_propagators |= extra_recv
            self._positional_propagators.update(extra_pos)

        # Cross-function: functions whose param reaches a sink
        # Maps (file, func_name) -> list of (param_index, param_name, vuln_type, severity, rec)
        self._dangerous_functions: dict[
            tuple[str, str], list[tuple[int, str, str, str, str]]
        ] = {}

        # Visited set for cross-function traversal — prevents exponential
        # blowup and infinite loops when call graph has cycles.
        # Includes the target file and parameter summary: names alone are not identity.
        self._cross_visited: set[tuple] = set()

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
        if self._verifier is not None:
            self._verifier.reset_scan()
        self._truncation = set()
        self._functions_analyzed = 0
        self._dangerous_functions = {}
        self._dangerous_traces: dict[
            tuple[str, str, str, str], dict[tuple[str, int, str], tuple]
        ] = {}
        self._cross_visited = set()
        self._ast_cache = {}
        self._content_cache = {}
        self._source_bytes = 0
        self._rejected_sources: set[str] = set()
        self._parse_failures: set[str] = set()
        self._bindings: PythonCallBindings | None = None
        self._static_verified = 0
        self._static_rejected = 0
        self._name_only_calls = 0
        self._cross_checks = 0
        self._active_sink_category: str | None = None
        self._return_source_funcs: set[str] = set()
        self._tainted_self_attrs: dict[tuple[str, str], set[str]] = {}
        self._func_class: dict[tuple[str, int], str] = {}
        self._current_class = ""
        self.findings = []
        self._sanitized_findings = []
        self._source_count = 0
        self._sink_count = 0
        self._build_return_source_registry()
        self._scan_python_files()
        self._scan_cross_function_via_index()
        self._scan_regex_languages()
        self.findings = _without_duplicates(
            [self._demote_operator_sourced(flow) for flow in self.findings]
        )
        return self.findings

    def analyze_full(self) -> "DataFlowResult":
        """Run full analysis and return structured DataFlowResult."""
        self.analyze()

        high_risk = sum(
            1 for f in self.findings
            if f.severity in ("critical", "high") and not f.sanitized
        )

        resolution = self._callee_verifier().stats()
        resolution.update(static_verified=self._static_verified,
                          static_rejected=self._static_rejected,
                          name_only_calls=self._name_only_calls)
        if self._static_verified and not self._name_only_calls:
            resolution["mode"] = "python_import_bound"
        return DataFlowResult(
            total_sources=self._source_count,
            total_sinks=self._sink_count,
            taint_flows=self.findings,
            suppressed_taint_flows=self._sanitized_findings,
            sanitized_flows=len(self._sanitized_findings),
            high_risk_count=high_risk,
            callee_resolution=resolution,
            functions_analyzed=self._functions_analyzed,
            truncation=sorted(self._truncation),
        )

    # ── Phase 0: return-taint registry ──────────────────────────────────────







    # ── Phase 1: Python AST analysis ────────────────────────────────────────




























    # ── Phase 2: Cross-function taint via index call graph ─────────────────









    # Keep old method for backward compat with reverse_index path


    # ── Phase 3: Regex-based fallback for JS/TS/Go ─────────────────────────
