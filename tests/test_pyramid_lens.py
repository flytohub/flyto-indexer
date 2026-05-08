"""
Tests for pyramid composite scoring and lens cross-signal analysis.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from analyzer.pyramid import compute_pyramids, PYRAMIDS, _normalize, _grade
from analyzer.lens import compute_lens, compute_all_lenses, LENS_SIGNALS


# ===========================================================================
# Pyramid tests
# ===========================================================================


class TestPyramidNormalize:
    def test_inverse_zero_is_perfect(self):
        assert _normalize(0, 50, inverse=True) == 100.0

    def test_inverse_at_cap(self):
        assert _normalize(50, 50, inverse=True) == 0.0

    def test_inverse_above_cap(self):
        assert _normalize(100, 50, inverse=True) == 0.0

    def test_direct_zero_is_worst(self):
        assert _normalize(0, 100, inverse=False) == 0.0

    def test_direct_at_cap(self):
        assert _normalize(100, 100, inverse=False) == 100.0

    def test_direct_half(self):
        assert _normalize(50, 100, inverse=False) == 50.0


class TestPyramidGrade:
    def test_grade_a(self):
        assert _grade(95) == "A"

    def test_grade_b(self):
        assert _grade(80) == "B"

    def test_grade_c(self):
        assert _grade(65) == "C"

    def test_grade_d(self):
        assert _grade(45) == "D"

    def test_grade_f(self):
        assert _grade(20) == "F"


class TestComputePyramids:
    def test_empty_profile(self):
        report = compute_pyramids({})
        assert len(report.pyramids) == 4
        for pid, p in report.pyramids.items():
            assert p.score == 0.0
            assert p.active_signals == 0

    def test_security_pyramid_with_data(self):
        profile = {
            "taint_summary": {"unsanitized_flows": 5},
            "secrets": {"total_findings": 2},
            "code_vulnerabilities": {"total_issues": 10},
            "iac_findings": {"total_findings": 3},
        }
        report = compute_pyramids(profile)
        sec = report.pyramids["security"]
        assert sec.active_signals >= 4
        assert 0 <= sec.score <= 100
        assert sec.grade in ("A", "B", "C", "D", "F")

    def test_quality_pyramid_with_coverage(self):
        profile = {
            "complexity_summary": {"complex_functions": 5},
            "dead_code_count": 10,
            "error_handling": {"coverage_pct": 60},
            "tech_debt": {"high_count": 3},
            "documentation": {"overall_score": 70},
        }
        report = compute_pyramids(profile)
        q = report.pyramids["quality"]
        assert q.active_signals >= 4
        # High coverage + low issues = good score
        assert q.score > 50

    def test_to_dict_structure(self):
        report = compute_pyramids({"secrets": {"total_findings": 1}})
        d = report.to_dict()
        assert "security" in d
        assert "score" in d["security"]
        assert "grade" in d["security"]
        assert "signals" in d["security"]

    def test_all_four_pyramids_present(self):
        report = compute_pyramids({})
        for pid in ("security", "quality", "risk", "compliance"):
            assert pid in report.pyramids

    def test_weight_redistribution(self):
        """When some signals are missing, weights should redistribute."""
        profile = {"secrets": {"total_findings": 0}}
        report = compute_pyramids(profile)
        sec = report.pyramids["security"]
        # Only secrets signal available, so it should get 100% weight
        assert sec.active_signals == 1
        # 0 findings = perfect score for that signal
        assert sec.score == 100.0


# ===========================================================================
# Lens tests
# ===========================================================================


class TestLensBasic:
    def test_invalid_lens_returns_none(self):
        assert compute_lens("nonexistent", {}) is None

    def test_empty_profile_produces_empty_lens(self):
        lens = compute_lens("security", {})
        assert lens is not None
        assert lens.total_findings == 0
        assert lens.total_files_affected == 0
        assert len(lens.hotspots) == 0

    def test_all_four_lenses(self):
        result = compute_all_lenses({})
        assert len(result) == 4
        for lid in ("security", "quality", "risk", "compliance"):
            assert lid in result
            assert "hotspots" in result[lid]


class TestLensHotspot:
    def test_single_signal_finding(self):
        profile = {
            "secrets": {
                "findings": [
                    {"file": "config.py", "severity": "high", "description": "AWS key"},
                ]
            }
        }
        lens = compute_lens("security", profile)
        assert lens.total_findings >= 1
        assert lens.total_files_affected >= 1
        assert any(h.file == "config.py" for h in lens.hotspots)

    def test_multi_signal_hotspot_scores_higher(self):
        """Files appearing in multiple signals should score higher."""
        profile = {
            "taint_flows": {
                "flows": [
                    {"source_file": "handler.py", "severity": "critical", "description": "SQLi"},
                ]
            },
            "secrets": {
                "findings": [
                    {"file": "handler.py", "severity": "high", "description": "leaked key"},
                ]
            },
            "code_vulnerabilities": {
                "findings": [
                    {"file_path": "other.py", "severity": "medium", "description": "eval"},
                ]
            },
        }
        lens = compute_lens("security", profile)

        handler = next((h for h in lens.hotspots if h.file == "handler.py"), None)
        other = next((h for h in lens.hotspots if h.file == "other.py"), None)

        assert handler is not None
        assert handler.signal_count >= 2
        assert handler.hotspot_score > 0

        if other:
            assert handler.hotspot_score > other.hotspot_score, (
                "Multi-signal file should score higher than single-signal file"
            )

    def test_cross_signal_correlation_text(self):
        profile = {
            "taint_flows": {
                "flows": [
                    {"source_file": "app.py", "severity": "high", "description": "flow"},
                ]
            },
            "secrets": {
                "findings": [
                    {"file": "app.py", "severity": "high", "description": "secret"},
                ]
            },
        }
        lens = compute_lens("security", profile)
        app = next((h for h in lens.hotspots if h.file == "app.py"), None)
        assert app is not None
        assert "Cross-signal" in app.correlation
        assert app.signal_count >= 2

    def test_quality_lens_detects_complexity_plus_debt(self):
        profile = {
            "complexity_findings": [
                {"file": "utils.py", "name": "parse", "score": 15},
            ],
            "tech_debt": {
                "items": [
                    {"file": "utils.py", "tag": "FIXME", "severity": "high", "message": "refactor"},
                ]
            },
        }
        lens = compute_lens("quality", profile)
        utils = next((h for h in lens.hotspots if h.file == "utils.py"), None)
        assert utils is not None
        assert utils.signal_count >= 2

    def test_risk_lens_bus_factor_plus_perf(self):
        profile = {
            "bus_factor_risks": [
                {"file": "billing.py", "bus_factor": 1, "primary_author": "alice"},
            ],
            "perf_patterns": {
                "issues": [
                    {"file": "billing.py", "category": "n_plus_1", "severity": "high",
                     "description": "db.query in loop"},
                ]
            },
        }
        lens = compute_lens("risk", profile)
        billing = next((h for h in lens.hotspots if h.file == "billing.py"), None)
        assert billing is not None
        assert billing.signal_count >= 2
        assert "Cross-signal" in billing.correlation


class TestLensSerialization:
    def test_to_dict_structure(self):
        profile = {
            "secrets": {
                "findings": [
                    {"file": "a.py", "severity": "critical", "description": "key"},
                ]
            }
        }
        lens = compute_lens("security", profile)
        d = lens.to_dict()

        assert d["lens_id"] == "security"
        assert d["total_findings"] >= 1
        assert isinstance(d["hotspots"], list)
        if d["hotspots"]:
            h = d["hotspots"][0]
            assert "file" in h
            assert "hotspot_score" in h
            assert "signals" in h
            assert "findings" in h
            assert "correlation" in h

    def test_compute_all_lenses_dict(self):
        d = compute_all_lenses({"secrets": {"findings": [{"file": "x.py", "severity": "high"}]}})
        assert isinstance(d, dict)
        assert "security" in d
        assert d["security"]["total_findings"] >= 1


class TestLensEdgeCases:
    def test_no_file_field_uses_project_level(self):
        """Signals without file_field (like license) should still work."""
        profile = {
            "license_policy_issues": [
                {"risk_level": "high", "package": "gpl-lib", "license": "GPL-3.0"},
            ]
        }
        lens = compute_lens("compliance", profile)
        assert lens.total_findings >= 1

    def test_missing_severity_defaults_to_medium(self):
        profile = {
            "secrets": {
                "findings": [
                    {"file": "a.py"},  # no severity field
                ]
            }
        }
        lens = compute_lens("security", profile)
        if lens.hotspots:
            assert lens.hotspots[0].findings[0].severity == "medium"

    def test_findings_capped_in_export(self):
        """to_dict() should cap findings per file and total hotspots."""
        profile = {
            "secrets": {
                "findings": [{"file": f"f{i}.py", "severity": "low"} for i in range(100)]
            }
        }
        lens = compute_lens("security", profile)
        d = lens.to_dict()
        assert len(d["hotspots"]) <= 50
