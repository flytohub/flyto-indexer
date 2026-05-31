"""
Pyramid aggregation engine — multi-perspective composite scoring.

Aggregates raw analyzer outputs through 5 layers into decision-grade
composite scores. Multiple pyramids provide different perspectives
on the same underlying data.

Layer 0: Raw findings (individual issues from each analyzer)
Layer 1: Analyzer scores (0-100 per analyzer, normalized from findings)
Layer 2: Dimension composites (weighted blend of related analyzers)
Layer 3: Per-repo composite (single score per pyramid per repo)
Layer 4: Org-level composite (aggregated across repos — done by engine)

Four default pyramids:
  - Security:   "Can we be attacked?"
  - Quality:    "Can we maintain this?"
  - Risk:       "What could go wrong in production?"
  - Compliance: "Can we pass an audit?"

Pure Python stdlib, no external dependencies.
"""

from dataclasses import dataclass, field
from typing import Any


# ── Pyramid definitions ───────────────────────────────────────────────

PYRAMIDS = {
    "security": {
        "label": "Security",
        "description": "Vulnerability exposure and attack surface",
        "color": "#ef4444",
        "signals": {
            "taint":   {"weight": 0.30, "source": "taint_summary.unsanitized_flows", "inverse": True, "cap": 50},
            "secrets": {"weight": 0.25, "source": "secrets.total_findings",           "inverse": True, "cap": 20},
            "cve":     {"weight": 0.20, "source": "cve_severity_score",               "inverse": True, "cap": 100},
            "iac":     {"weight": 0.15, "source": "iac_findings.total_findings",      "inverse": True, "cap": 30},
            "sast":    {"weight": 0.10, "source": "code_vulnerabilities.total_issues", "inverse": True, "cap": 50},
        },
    },
    "quality": {
        "label": "Quality",
        "description": "Code maintainability and engineering standards",
        "color": "#f97316",
        "signals": {
            "complexity":     {"weight": 0.20, "source": "complexity_summary.complex_functions", "inverse": True, "cap": 50},
            "dead_code":      {"weight": 0.15, "source": "dead_code_count",                     "inverse": True, "cap": 100},
            "error_handling": {"weight": 0.25, "source": "error_handling.coverage_pct",          "inverse": False, "cap": 100},
            "tech_debt":      {"weight": 0.20, "source": "tech_debt.high_count",                 "inverse": True, "cap": 30},
            "duplication":    {"weight": 0.10, "source": "duplication_rate",                     "inverse": True, "cap": 30},
            "documentation":  {"weight": 0.10, "source": "documentation.overall_score",          "inverse": False, "cap": 100},
        },
    },
    "risk": {
        "label": "Risk",
        "description": "Operational and deployment risk factors",
        "color": "#eab308",
        "signals": {
            "bus_factor":    {"weight": 0.20, "source": "bus_factor.bus_factor_1_pct",          "inverse": True, "cap": 100},
            "config_drift":  {"weight": 0.20, "source": "config_drift.issue_count",             "inverse": True, "cap": 20},
            "api_drift":     {"weight": 0.20, "source": "api_drift.broken_calls",               "inverse": True, "cap": 10},
            "perf_patterns": {"weight": 0.20, "source": "perf_patterns.total_issues",           "inverse": True, "cap": 20},
            "taint_risk":    {"weight": 0.20, "source": "taint_summary.high_risk_count",        "inverse": True, "cap": 10},
        },
    },
    "compliance": {
        "label": "Compliance",
        "description": "Audit readiness and regulatory posture",
        "color": "#22c55e",
        "signals": {
            "license":       {"weight": 0.25, "source": "license_policy_issues_count",          "inverse": True, "cap": 10},
            "secrets":       {"weight": 0.25, "source": "secrets.total_findings",               "inverse": True, "cap": 10},
            "iac":           {"weight": 0.20, "source": "iac_findings.critical_count",          "inverse": True, "cap": 10},
            "coverage":      {"weight": 0.15, "source": "documentation.overall_score",          "inverse": False, "cap": 100},
            "error_handling":{"weight": 0.15, "source": "error_handling.coverage_pct",          "inverse": False, "cap": 100},
        },
    },
}


# ── Data classes ──────────────────────────────────────────────────────

@dataclass
class SignalScore:
    """Layer 1: individual signal score."""
    signal_id: str
    raw_value: float        # original value from analyzer
    normalized: float       # 0-100 (higher = better)
    weight: float
    source_path: str
    available: bool = True  # False if data missing


@dataclass
class PyramidResult:
    """Layer 2-3: computed pyramid for one repo."""
    pyramid_id: str
    label: str
    color: str
    score: float            # 0-100 composite
    grade: str              # A-F
    signals: list[SignalScore] = field(default_factory=list)
    active_signals: int = 0
    total_signals: int = 0


@dataclass
class PyramidReport:
    """Full pyramid report for one repo (all 4 pyramids)."""
    pyramids: dict[str, PyramidResult] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            pid: {
                "label": p.label,
                "color": p.color,
                "score": round(p.score, 1),
                "grade": p.grade,
                "active_signals": p.active_signals,
                "total_signals": p.total_signals,
                "signals": {
                    s.signal_id: {
                        "raw": round(s.raw_value, 2) if s.available else None,
                        "normalized": round(s.normalized, 1) if s.available else None,
                        "weight": s.weight,
                    }
                    for s in p.signals
                },
            }
            for pid, p in self.pyramids.items()
        }


# ── Core engine ───────────────────────────────────────────────────────

def _resolve_path(profile: dict, path: str) -> float | None:
    """Resolve a dotted path like 'taint_summary.unsanitized_flows' from profile dict."""
    parts = path.split(".")
    current: Any = profile
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    try:
        return float(current)
    except (TypeError, ValueError):
        return None


def _normalize(value: float, cap: float, inverse: bool) -> float:
    """Normalize a raw value to 0-100 scale.

    inverse=True: lower raw value = better (e.g., fewer bugs = higher score)
    inverse=False: higher raw value = better (e.g., higher coverage = higher score)
    """
    clamped = min(value, cap)
    ratio = clamped / cap if cap > 0 else 0
    if inverse:
        return (1 - ratio) * 100
    return ratio * 100


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def compute_pyramids(profile: dict) -> PyramidReport:
    """Compute all 4 pyramids from a single repo profile.

    Args:
        profile: The full profile dict from build_project_profile().

    Returns:
        PyramidReport with per-pyramid scores and signal breakdowns.
    """
    report = PyramidReport()

    for pyramid_id, pyramid_def in PYRAMIDS.items():
        signals: list[SignalScore] = []
        active_weight = 0.0

        for signal_id, signal_cfg in pyramid_def["signals"].items():
            raw = _resolve_path(profile, signal_cfg["source"])
            available = raw is not None

            if available:
                normalized = _normalize(raw, signal_cfg["cap"], signal_cfg["inverse"])
                active_weight += signal_cfg["weight"]
            else:
                normalized = 0.0

            signals.append(SignalScore(
                signal_id=signal_id,
                raw_value=raw if raw is not None else 0.0,
                normalized=normalized,
                weight=signal_cfg["weight"],
                source_path=signal_cfg["source"],
                available=available,
            ))

        # Composite score: weighted average of available signals,
        # with weights redistributed proportionally
        active = [s for s in signals if s.available]
        if active and active_weight > 0:
            score = sum(s.normalized * (s.weight / active_weight) for s in active)
        else:
            score = 0.0

        report.pyramids[pyramid_id] = PyramidResult(
            pyramid_id=pyramid_id,
            label=pyramid_def["label"],
            color=pyramid_def["color"],
            score=score,
            grade=_grade(score),
            signals=signals,
            active_signals=len(active),
            total_signals=len(signals),
        )

    return report
