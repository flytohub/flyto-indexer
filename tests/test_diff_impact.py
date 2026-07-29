"""Tests for diff-based impact root resolution."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent))

from src import diff_impact


def test_impact_from_diff_resolves_relative_project_directory(monkeypatch, tmp_path):
    repo = tmp_path / "flyto-indexer"
    (repo / ".git").mkdir(parents=True)
    captured = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(diff_impact, "load_index", lambda: {"project_roots": {}})

    def fake_git_diff(root, mode, base):
        captured["root"] = root
        captured["mode"] = mode
        captured["base"] = base
        return ""

    monkeypatch.setattr(diff_impact, "_run_git_diff", fake_git_diff)
    monkeypatch.setattr(diff_impact, "_untracked_file_changes", lambda _root: {})

    result = diff_impact.impact_from_diff(mode="unstaged", project="flyto-indexer")

    assert result["total_changed_files"] == 0
    assert result["evidence_portfolio"]["schema"] == "evidence-portfolio.v1"
    assert result["verdict"]["schema"] == "evidence-verdict.v1"
    assert result["verdict"]["findings"][0]["refs"] == [
        "impact.summary",
        "evidence_portfolio.diff.D001",
    ]
    assert captured == {"root": str(repo.resolve()), "mode": "unstaged", "base": ""}


def test_impact_from_diff_includes_untracked_files(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(diff_impact, "load_index", lambda: {"project_roots": {}})
    monkeypatch.setattr(diff_impact, "_run_git_diff", lambda *_args: "")
    monkeypatch.setattr(
        diff_impact,
        "_untracked_file_changes",
        lambda _root: {"src/new_module.py": [(1, 12)]},
    )

    result = diff_impact.impact_from_diff(
        mode="unstaged",
        project=str(repo),
    )

    assert result["total_changed_files"] == 1
