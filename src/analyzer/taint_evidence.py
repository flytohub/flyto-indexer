"""Taint finding models and evidence serialization.

The analyzer computes flows; this module owns the stable evidence payload those
flows expose to verification and downstream consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from ..finding_identity import finding_evidence, suppression_provenance
except ImportError:  # Direct source imports expose analyzer as a top-level package.
    from finding_identity import finding_evidence, suppression_provenance


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
    callee_resolution: str = ""

    def to_dict(self) -> dict:
        source_file = self.source_file or self.file_path
        sink_file = self.sink_file or self.file_path
        sink_line = self.sink_line or self.line
        flow_trace = self.path or self.flow_chain
        evidence = finding_evidence(
            f"taint/{self.category}",
            sink_file,
            anchor={
                "source_file": source_file,
                "source": self.source_expr,
                "sink": self.sink_expr,
            },
            confidence=(
                "high"
                if self.path and self.callee_resolution not in ("name_only_callee", "regex_candidate")
                else "medium"
            ),
            confidence_basis=(
                ["source_to_sink_path", self.callee_resolution or "intraprocedural_ast"]
                if self.path
                else ["source_to_sink_dataflow"]
            ),
            trace=[
                {"kind": "flow", "value": step}
                for step in flow_trace
            ],
            suppression=suppression_provenance(
                suppressed=self.sanitized,
                mechanism="sanitizer" if self.sanitized else "none",
                rule_id=f"taint/{self.category}",
                reason="flow passed through a configured sanitizer"
                if self.sanitized else "",
                source="taint.sanitizers" if self.sanitized else "",
            ),
            origin="taint.ast" if self.path else "taint.dataflow",
        )
        return {
            **evidence,
            "source": self.source_expr,
            "source_file": source_file,
            "source_line": self.source_line or self.line,
            "sink": self.sink_expr,
            "sink_file": sink_file,
            "sink_line": sink_line,
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
    suppressed_taint_flows: list[TaintFlow] = field(default_factory=list)
    sanitized_flows: int = 0
    high_risk_count: int = 0
    #: How cross-function callees were resolved for this scan — name-only or
    #: language-server verified, with how many attributions were rejected.
    callee_resolution: dict = field(default_factory=dict)
    #: Functions the AST pass actually analyzed.
    functions_analyzed: int = 0
    #: Caps this scan hit. Empty means the scan finished on its own terms —
    #: "found nothing" and "stopped looking" must not look alike.
    truncation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        unsanitized = [f for f in self.taint_flows if not f.sanitized]
        return {
            "total_sources": self.total_sources,
            "total_sinks": self.total_sinks,
            "unsanitized_flows": len(unsanitized),
            "sanitized_flows": self.sanitized_flows,
            "high_risk_count": self.high_risk_count,
            "callee_resolution": self.callee_resolution,
            "functions_analyzed": self.functions_analyzed,
            "truncation": self.truncation,
            "taint_flows": [f.to_dict() for f in unsanitized],
            "suppressed_taint_flows": [
                flow.to_dict() for flow in self.suppressed_taint_flows
            ],
        }

