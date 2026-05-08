"""
API Contract Drift detector — compare backend route definitions vs frontend call sites.

Uses data already extracted by project_profile:
  - api_definitions: backend routes (method, path, file)
  - api_calls_internal: frontend HTTP calls (method, path, file)

Detects:
  1. Endpoint called by frontend but not defined in backend (broken call)
  2. Endpoint defined but never called (dead endpoint)
  3. Method mismatch (frontend calls GET, backend defines POST)
  4. Path pattern mismatch (frontend calls /api/users, backend defines /api/v2/users)

Pure Python stdlib, no external dependencies.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class APIDriftIssue:
    """A single API drift issue."""
    category: str       # "broken_call", "dead_endpoint", "method_mismatch", "version_drift"
    severity: str       # "high", "medium", "low"
    description: str
    frontend_file: str = ""
    backend_file: str = ""
    method: str = ""
    path: str = ""


@dataclass
class APIDriftReport:
    """API drift analysis result."""
    total_definitions: int = 0
    total_calls: int = 0
    matched: int = 0
    issues: list[APIDriftIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_definitions": self.total_definitions,
            "total_calls": self.total_calls,
            "matched": self.matched,
            "issue_count": len(self.issues),
            "broken_calls": sum(1 for i in self.issues if i.category == "broken_call"),
            "dead_endpoints": sum(1 for i in self.issues if i.category == "dead_endpoint"),
            "method_mismatches": sum(1 for i in self.issues if i.category == "method_mismatch"),
        }


def _normalize_path(path: str) -> str:
    """Normalize API path for comparison.

    /api/users/:id  → /api/users/{id}
    /api/users/<id> → /api/users/{id}
    /api/users/[id] → /api/users/{id}
    Remove trailing slash.
    """
    path = path.rstrip("/")
    # Express :param → {param}
    path = re.sub(r":(\w+)", r"{\1}", path)
    # Flask <param> → {param}
    path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", path)
    # Next.js [param] → {param}
    path = re.sub(r"\[(\w+)\]", r"{\1}", path)
    return path.lower()


def _path_matches(def_path: str, call_path: str) -> bool:
    """Check if a definition path matches a call path, considering params."""
    norm_def = _normalize_path(def_path)
    norm_call = _normalize_path(call_path)

    if norm_def == norm_call:
        return True

    # Split and compare segments
    def_parts = norm_def.strip("/").split("/")
    call_parts = norm_call.strip("/").split("/")

    if len(def_parts) != len(call_parts):
        return False

    for dp, cp in zip(def_parts, call_parts):
        if dp.startswith("{") or cp.startswith("{"):
            continue  # param segment — matches anything
        if dp != cp:
            return False

    return True


def _detect_version_drift(def_path: str, call_path: str) -> str | None:
    """Detect API version mismatch like /api/v1/users vs /api/v2/users."""
    ver_pattern = re.compile(r"/v(\d+)/")
    def_match = ver_pattern.search(def_path)
    call_match = ver_pattern.search(call_path)

    if def_match and call_match:
        if def_match.group(1) != call_match.group(1):
            return f"Backend defines v{def_match.group(1)}, frontend calls v{call_match.group(1)}"
    return None


def analyze_api_drift(
    api_definitions: list[dict],
    api_calls_internal: list[dict],
) -> APIDriftReport:
    """Analyze API contract drift.

    Args:
        api_definitions: List of backend route dicts with {method, path, file, ...}
        api_calls_internal: List of frontend call dicts with {method, path/url, file, ...}

    Both are typically from project_profile output.
    """
    report = APIDriftReport()
    report.total_definitions = len(api_definitions)
    report.total_calls = len(api_calls_internal)

    if not api_definitions and not api_calls_internal:
        return report

    # Build lookup structures
    def_set: dict[str, list[dict]] = {}  # normalized_path -> [definitions]
    for d in api_definitions:
        path = d.get("path", d.get("url", ""))
        if not path:
            continue
        norm = _normalize_path(path)
        def_set.setdefault(norm, []).append(d)

    call_set: dict[str, list[dict]] = {}  # normalized_path -> [calls]
    for c in api_calls_internal:
        path = c.get("path", c.get("url", ""))
        if not path:
            continue
        norm = _normalize_path(path)
        call_set.setdefault(norm, []).append(c)

    matched_defs = set()
    matched_calls = set()

    # Match calls to definitions
    for call_norm, calls in call_set.items():
        found_match = False

        for def_norm, defs in def_set.items():
            if _path_matches(def_norm, call_norm):
                found_match = True
                matched_defs.add(def_norm)
                matched_calls.add(call_norm)

                # Check method mismatch
                for c in calls:
                    call_method = (c.get("method", "") or "").upper()
                    if not call_method:
                        continue
                    for d in defs:
                        def_method = (d.get("method", "") or "").upper()
                        if not def_method:
                            continue
                        if call_method != def_method:
                            report.issues.append(APIDriftIssue(
                                category="method_mismatch",
                                severity="high",
                                description=f"Frontend calls {call_method} but backend defines {def_method}",
                                frontend_file=c.get("file", ""),
                                backend_file=d.get("file", ""),
                                method=call_method,
                                path=c.get("path", c.get("url", "")),
                            ))

                # Check version drift
                for c in calls:
                    c_path = c.get("path", c.get("url", ""))
                    for d in defs:
                        d_path = d.get("path", d.get("url", ""))
                        drift = _detect_version_drift(d_path, c_path)
                        if drift:
                            report.issues.append(APIDriftIssue(
                                category="version_drift",
                                severity="medium",
                                description=drift,
                                frontend_file=c.get("file", ""),
                                backend_file=d.get("file", ""),
                                path=c_path,
                            ))
                break

        if not found_match:
            for c in calls:
                report.issues.append(APIDriftIssue(
                    category="broken_call",
                    severity="high",
                    description="Frontend calls endpoint not defined in backend",
                    frontend_file=c.get("file", ""),
                    method=(c.get("method", "") or "").upper(),
                    path=c.get("path", c.get("url", "")),
                ))

    # Dead endpoints (defined but never called)
    for def_norm, defs in def_set.items():
        if def_norm not in matched_defs:
            for d in defs:
                path = d.get("path", d.get("url", ""))
                # Skip common infra endpoints
                if any(p in path.lower() for p in ("/health", "/ready", "/metrics",
                                                    "/swagger", "/docs", "/openapi")):
                    continue
                report.issues.append(APIDriftIssue(
                    category="dead_endpoint",
                    severity="low",
                    description="Backend endpoint never called by frontend",
                    backend_file=d.get("file", ""),
                    method=(d.get("method", "") or "").upper(),
                    path=path,
                ))

    report.matched = len(matched_calls)
    return report
