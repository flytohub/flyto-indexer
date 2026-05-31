"""
Lens engine — per-perspective ranked findings with cross-signal hotspots.

Each lens takes a pyramid perspective (security, quality, risk, compliance)
and cross-references all relevant signals at the FILE level to produce:

1. Hotspot files — files appearing in multiple signals (highest risk)
2. Ranked findings — all findings sorted by the lens's weighted priority
3. Cross-signal correlations — "this file has X AND Y" narratives
4. Actionable items — concrete recommendations per finding

The key insight: the SAME file appearing in taint flows AND having
bare except AND bus_factor=1 is far more dangerous than three separate
files with one issue each. Hotspot score = sum of signal weights that
touch this file.

Pure Python stdlib, no external dependencies.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Which signals feed each lens, and how to extract file-level hits ──

LENS_SIGNALS: dict[str, dict[str, dict[str, Any]]] = {
    "security": {
        "taint":   {"weight": 0.30, "profile_key": "taint_flows", "file_field": "source_file", "severity_field": "severity", "desc": "taint flow"},
        "secrets": {"weight": 0.25, "profile_key": "secrets",     "file_field": "file",        "severity_field": "severity", "desc": "hardcoded secret"},
        "sast":    {"weight": 0.20, "profile_key": "code_vulnerabilities", "file_field": "file_path", "severity_field": "severity", "desc": "SAST finding"},
        "iac":     {"weight": 0.15, "profile_key": "iac_findings", "file_field": "file_path",  "severity_field": "severity", "desc": "IaC issue"},
        "error_handling": {"weight": 0.10, "profile_key": "error_handling", "file_field": "file", "severity_field": "severity", "desc": "error handling gap"},
    },
    "quality": {
        "complexity":     {"weight": 0.25, "profile_key": "complexity_findings",  "file_field": "file",  "severity_field": None, "desc": "complex function"},
        "dead_code":      {"weight": 0.15, "profile_key": "dead_code_findings",   "file_field": "path",  "severity_field": None, "desc": "dead code"},
        "error_handling": {"weight": 0.25, "profile_key": "error_handling",        "file_field": "file",  "severity_field": "severity", "desc": "error handling issue"},
        "tech_debt":      {"weight": 0.20, "profile_key": "tech_debt",            "file_field": "file",  "severity_field": "severity", "desc": "tech debt marker"},
        "import_health":  {"weight": 0.15, "profile_key": "import_health_modules", "file_field": "path", "severity_field": None, "desc": "module health issue"},
    },
    "risk": {
        "bus_factor":    {"weight": 0.25, "profile_key": "bus_factor_risks",   "file_field": "file",        "severity_field": None, "desc": "single-author file"},
        "config_drift":  {"weight": 0.20, "profile_key": "config_drift",       "file_field": None,          "severity_field": "severity", "desc": "config mismatch"},
        "api_drift":     {"weight": 0.20, "profile_key": "api_drift_issues",   "file_field": "frontend_file", "severity_field": "severity", "desc": "API contract issue"},
        "perf_patterns": {"weight": 0.20, "profile_key": "perf_patterns",      "file_field": "file",        "severity_field": "severity", "desc": "performance anti-pattern"},
        "taint_risk":    {"weight": 0.15, "profile_key": "taint_flows",        "file_field": "sink_file",   "severity_field": "severity", "desc": "taint risk"},
    },
    "compliance": {
        "license":       {"weight": 0.30, "profile_key": "license_policy_issues", "file_field": None,     "severity_field": "risk_level", "desc": "license violation"},
        "secrets":       {"weight": 0.25, "profile_key": "secrets",               "file_field": "file",   "severity_field": "severity", "desc": "exposed credential"},
        "iac":           {"weight": 0.20, "profile_key": "iac_findings",           "file_field": "file_path", "severity_field": "severity", "desc": "infrastructure risk"},
        "documentation": {"weight": 0.15, "profile_key": "documentation_gaps",     "file_field": None,    "severity_field": None, "desc": "documentation gap"},
        "error_handling":{"weight": 0.10, "profile_key": "error_handling",         "file_field": "file",  "severity_field": "severity", "desc": "error handling gap"},
    },
}

SEV_WEIGHTS = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class FileFinding:
    """A single finding tied to a file."""
    file: str
    signal: str
    description: str
    severity: str
    line: int = 0
    detail: str = ""


@dataclass
class FileHotspot:
    """A file that appears in multiple signals — cross-signal convergence."""
    file: str
    hotspot_score: float          # higher = more signals converge here
    signal_count: int             # how many different signals touch this file
    signals: list[str]            # which signals (e.g., ["taint", "secrets", "bus_factor"])
    findings: list[FileFinding]   # all findings for this file
    correlation: str              # human-readable cross-signal narrative


@dataclass
class LensResult:
    """Full lens analysis for one perspective."""
    lens_id: str
    label: str
    color: str
    hotspots: list[FileHotspot]       # ranked by hotspot_score desc
    total_findings: int
    total_files_affected: int
    signal_coverage: dict[str, int]   # signal_id -> finding count

    def to_dict(self) -> dict:
        return {
            "lens_id": self.lens_id,
            "label": self.label,
            "color": self.color,
            "total_findings": self.total_findings,
            "total_files_affected": self.total_files_affected,
            "signal_coverage": self.signal_coverage,
            "hotspots": [
                {
                    "file": h.file,
                    "hotspot_score": round(h.hotspot_score, 2),
                    "signal_count": h.signal_count,
                    "signals": h.signals,
                    "finding_count": len(h.findings),
                    "correlation": h.correlation,
                    "findings": [
                        {
                            "signal": f.signal,
                            "severity": f.severity,
                            "description": f.description,
                            "line": f.line,
                        }
                        for f in h.findings[:10]  # cap per-file findings in export
                    ],
                }
                for h in self.hotspots[:50]  # cap total hotspots in export
            ],
        }


# ── Extraction helpers ────────────────────────────────────────────────

def _extract_findings_list(profile: dict, key: str) -> list[dict]:
    """Get a list of finding dicts from various profile structures."""
    val = profile.get(key)
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        # Try common sub-keys
        for sub in ("findings", "issues", "items", "flows", "taint_flows",
                     "risk_files", "god_modules"):
            if sub in val and isinstance(val[sub], list):
                return val[sub]
        # For top_files in tech_debt: [(file, count), ...]
        if "top_files" in val:
            return [{"file": f, "count": c} for f, c in val.get("top_files", [])]
    return []


def _build_correlation(signals: list[str], finding_count: int) -> str:
    """Build a human-readable cross-signal narrative."""
    if len(signals) <= 1:
        return f"{finding_count} finding(s) from {signals[0] if signals else 'unknown'}"

    parts = " + ".join(signals[:4])
    if len(signals) > 4:
        parts += f" +{len(signals) - 4} more"
    return f"Cross-signal convergence: {parts} ({finding_count} findings)"


# ── Core engine ───────────────────────────────────────────────────────

def compute_lens(lens_id: str, profile: dict) -> LensResult | None:
    """Compute a lens analysis for one perspective.

    Args:
        lens_id: One of "security", "quality", "risk", "compliance"
        profile: The full profile dict from build_project_profile()

    Returns:
        LensResult with ranked hotspots, or None if lens_id is invalid.
    """
    signals_def = LENS_SIGNALS.get(lens_id)
    if not signals_def:
        return None

    colors = {"security": "#ef4444", "quality": "#f97316", "risk": "#eab308", "compliance": "#22c55e"}
    labels = {"security": "Security", "quality": "Quality", "risk": "Risk", "compliance": "Compliance"}

    # Collect all findings per file
    file_findings: dict[str, list[FileFinding]] = defaultdict(list)
    file_signals: dict[str, set[str]] = defaultdict(set)
    file_signal_weights: dict[str, float] = defaultdict(float)
    signal_coverage: dict[str, int] = {}
    total_findings = 0

    for signal_id, sig_cfg in signals_def.items():
        findings = _extract_findings_list(profile, sig_cfg["profile_key"])
        file_field = sig_cfg["file_field"]
        sev_field = sig_cfg["severity_field"]
        weight = sig_cfg["weight"]
        desc = sig_cfg["desc"]
        count = 0

        for f in findings:
            if not isinstance(f, dict):
                continue

            file_path = f.get(file_field, "") if file_field else "(project-level)"
            if not file_path:
                file_path = f.get("file", f.get("path", f.get("file_path", "(unknown)")))

            severity = f.get(sev_field, "medium") if sev_field else "medium"
            if not isinstance(severity, str):
                severity = "medium"
            severity = severity.lower()

            line = f.get("line", f.get("line_number", 0))
            if not isinstance(line, int):
                line = 0

            detail = f.get("description", f.get("message", f.get("name", "")))
            if not isinstance(detail, str):
                detail = str(detail)[:120]

            finding = FileFinding(
                file=file_path,
                signal=signal_id,
                description=f"{desc}: {detail[:100]}" if detail else desc,
                severity=severity,
                line=line,
            )

            file_findings[file_path].append(finding)
            file_signals[file_path].add(signal_id)
            file_signal_weights[file_path] += weight * SEV_WEIGHTS.get(severity, 1)
            count += 1
            total_findings += 1

        signal_coverage[signal_id] = count

    # Build hotspots sorted by score (multi-signal files first)
    hotspots: list[FileHotspot] = []
    for file_path, findings in file_findings.items():
        signals = sorted(file_signals[file_path])
        score = file_signal_weights[file_path]

        # Bonus for multi-signal convergence (the key insight)
        if len(signals) >= 3:
            score *= 2.0
        elif len(signals) >= 2:
            score *= 1.5

        hotspots.append(FileHotspot(
            file=file_path,
            hotspot_score=score,
            signal_count=len(signals),
            signals=signals,
            findings=sorted(findings, key=lambda f: -SEV_WEIGHTS.get(f.severity, 0)),
            correlation=_build_correlation(signals, len(findings)),
        ))

    hotspots.sort(key=lambda h: -h.hotspot_score)

    return LensResult(
        lens_id=lens_id,
        label=labels.get(lens_id, lens_id),
        color=colors.get(lens_id, "#94a3b8"),
        hotspots=hotspots,
        total_findings=total_findings,
        total_files_affected=len(file_findings),
        signal_coverage=signal_coverage,
    )


def compute_all_lenses(profile: dict) -> dict[str, dict]:
    """Compute all 4 lenses and return serializable dict."""
    result = {}
    for lens_id in ("security", "quality", "risk", "compliance"):
        lens = compute_lens(lens_id, profile)
        if lens:
            result[lens_id] = lens.to_dict()
    return result
