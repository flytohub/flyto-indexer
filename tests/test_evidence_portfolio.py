"""Tests for bounded Git evidence and evidence-linked verdicts."""

import subprocess

from src.tools.evidence_portfolio import (
    build_audit_verdict,
    build_evidence_portfolio,
    build_impact_verdict,
)


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo, message):
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def test_portfolio_ranks_code_and_excludes_machine_noise(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Evidence Test")

    (repo / "package-lock.json").write_text('{"lockfileVersion": 3}', encoding="utf-8")
    _commit(repo, "refresh lock")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _commit(repo, "add implementation")
    (repo / "src" / "app.py").write_text(
        "def value():\n    return 2  # UNIQUE_PATCH_TEXT\n",
        encoding="utf-8",
    )

    portfolio = build_evidence_portfolio(
        str(repo),
        max_commits=1,
        max_files=2,
    )

    assert portfolio["status"] == "captured"
    assert portfolio["commits"][0]["subject"] == "add implementation"
    assert portfolio["commits"][0]["id"] == "E001"
    assert portfolio["files"][0]["id"] == "F001"
    assert portfolio["files"][0]["path"] == "src/app.py"
    assert portfolio["diff"]["id"] == "D001"
    assert portfolio["diff"]["changed_file_count"] == 1
    assert portfolio["summary"]["noise_files_excluded"] == 1
    assert "UNIQUE_PATCH_TEXT" not in str(portfolio)


def test_portfolio_enforces_hard_output_bounds(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Evidence Test")
    for index in range(8):
        path = repo / f"module_{index}.py"
        path.write_text(f"VALUE = {index}\n", encoding="utf-8")
        _commit(repo, f"change {index}")

    portfolio = build_evidence_portfolio(
        str(repo),
        max_commits=999,
        max_files=999,
    )

    assert portfolio["limits"] == {"commits": 6, "files": 14}
    assert len(portfolio["commits"]) <= 6
    assert len(portfolio["files"]) <= 14


def test_verdicts_are_concise_and_reference_evidence():
    portfolio = {
        "status": "captured",
        "summary": {"selected_commits": 1},
        "commits": [{"id": "E001"}],
    }
    audit = {
        "health": {
            "score": 81,
            "grade": "B",
            "breakdown": {
                "complexity": {"score": 6, "max": 25},
                "security": {"score": 25, "max": 25},
            },
        },
    }
    impact = {
        "total_changed_files": 2,
        "summary": {"high_risk": 1, "moderate_risk": 0},
        "symbols": [{"name": "run"}],
        "next_action": "Review run.",
    }

    audit_verdict = build_audit_verdict(audit, portfolio)
    impact_verdict = build_impact_verdict(impact, portfolio)

    assert audit_verdict["status"] == "clear"
    assert audit_verdict["findings"][1]["refs"] == [
        "audit.health.breakdown.complexity"
    ]
    assert impact_verdict["status"] == "attention"
    assert "evidence_portfolio.diff.D001" in impact_verdict["findings"][0]["refs"]
    assert len(audit_verdict["findings"]) <= 3
    assert len(impact_verdict["findings"]) <= 3


def test_audit_verdict_cannot_clear_high_severity_security_findings():
    audit = {
        "health": {"score": 95, "grade": "A", "breakdown": {}},
        "security_findings": {
            "by_severity": {"critical": 0, "high": 1, "medium": 0},
        },
    }
    portfolio = {"status": "captured", "summary": {}, "commits": []}

    verdict = build_audit_verdict(audit, portfolio)

    assert verdict["status"] == "attention"
    assert verdict["findings"][-1]["refs"] == [
        "audit.security_findings.by_severity"
    ]
