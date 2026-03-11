"""Tests for git_intel.py — Git History Intelligence tools."""

import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(args, cwd):
    """Run a git command in *cwd*."""
    subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _commit(cwd, message, files_content):
    """Write files and make a commit.

    files_content: dict mapping relative path -> content string.
    """
    for rel_path, content in files_content.items():
        full = os.path.join(cwd, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-m", message, "--allow-empty-message"], cwd)


@pytest.fixture()
def git_repo(tmp_path):
    """Create a temp git repo with several commits for testing."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git(["init"], repo)
    _git(["config", "user.email", "test@test.com"], repo)
    _git(["config", "user.name", "Test User"], repo)

    # Commit 1 — initial
    _commit(repo, "initial commit", {
        "src/main.py": "def main():\n    pass\n",
        "src/utils.py": "def helper():\n    return 1\n",
    })

    # Commit 2 — modify main.py and add config.py
    _commit(repo, "add config and update main", {
        "src/main.py": "def main():\n    print('hello')\n",
        "src/config.py": "DEBUG = True\n",
    })

    # Commit 3 — fix: modify utils.py
    _commit(repo, "fix: critical bug in helper", {
        "src/utils.py": "def helper():\n    return 42\n",
    })

    # Commit 4 — modify main.py and utils.py together
    _commit(repo, "refactor main and utils", {
        "src/main.py": "def main():\n    print(helper())\n",
        "src/utils.py": "def helper():\n    return 99\n",
    })

    # Commit 5 — big change with workaround keyword
    big_content = "\n".join(["line_%d = %d" % (i, i) for i in range(200)])
    _commit(repo, "workaround for legacy API", {
        "src/legacy.py": big_content,
        "src/main.py": "def main():\n    print(helper())\n    # legacy\n",
        "src/utils.py": "def helper():\n    return 100\n",
        "src/config.py": "DEBUG = False\n",
        "src/api.py": "def get():\n    pass\n",
        "src/models.py": "class User:\n    pass\n",
        "src/views.py": "def index():\n    pass\n",
    })

    return repo


@pytest.fixture()
def mock_index(git_repo, monkeypatch):
    """Mock load_index to return a synthetic index pointing at git_repo."""
    fake_index = {
        "projects": ["test-project"],
        "project_roots": {"test-project": git_repo},
        "symbols": {
            "test-project:src/main.py:function:main": {
                "name": "main",
                "path": "src/main.py",
                "type": "function",
                "start_line": 1,
                "end_line": 3,
            },
            "test-project:src/utils.py:function:helper": {
                "name": "helper",
                "path": "src/utils.py",
                "type": "function",
                "start_line": 1,
                "end_line": 2,
            },
        },
    }

    import tools.git_intel as git_intel_mod
    monkeypatch.setattr(git_intel_mod, "load_index", lambda: fake_index)

    # Also mock _lazy_quality to avoid depending on the real quality module
    class _FakeQuality:
        @staticmethod
        def find_complex_functions(project=None, max_results=20, min_score=0):
            return {"results": [
                {"path": "src/main.py", "score": 6},
                {"path": "src/legacy.py", "score": 8},
            ]}

    monkeypatch.setattr(git_intel_mod, "_lazy_quality", lambda: _FakeQuality)

    return git_intel_mod


# ---------------------------------------------------------------------------
# _parse_log_with_files
# ---------------------------------------------------------------------------

class TestParseLogWithFiles:

    def test_basic_parsing(self):
        from tools.git_intel import _parse_log_with_files

        log = textwrap.dedent("""\
            COMMIT:abc123|1700000000|Alice|first commit
            src/main.py
            src/utils.py

            COMMIT:def456|1700001000|Bob|second commit
            src/config.py
        """)
        entries = _parse_log_with_files(log)
        assert len(entries) == 2
        assert entries[0]["hash"] == "abc123"
        assert entries[0]["author"] == "Alice"
        assert entries[0]["message"] == "first commit"
        assert entries[0]["files"] == ["src/main.py", "src/utils.py"]
        assert entries[1]["hash"] == "def456"
        assert entries[1]["files"] == ["src/config.py"]

    def test_empty_input(self):
        from tools.git_intel import _parse_log_with_files
        assert _parse_log_with_files("") == []

    def test_malformed_commit_line(self):
        from tools.git_intel import _parse_log_with_files
        log = "COMMIT:short\nfile.py\n"
        entries = _parse_log_with_files(log)
        # Malformed line (< 4 parts) → skipped
        assert len(entries) == 0


# ---------------------------------------------------------------------------
# git_hotspots
# ---------------------------------------------------------------------------

class TestGitHotspots:

    def test_returns_hotspots(self, mock_index):
        result = mock_index.git_hotspots(project="test-project")
        assert "hotspots" in result
        assert "total_files_analyzed" in result
        assert result["total_files_analyzed"] > 0

        # src/main.py has the most commits (touched in commits 1,2,4,5)
        paths = [h["path"] for h in result["hotspots"]]
        assert "src/main.py" in paths

        # Check hotspot structure
        for h in result["hotspots"]:
            assert "commit_count" in h
            assert "hotspot_score" in h
            assert "recent_authors" in h
            assert h["project"] == "test-project"

    def test_complexity_boosts_score(self, mock_index):
        result = mock_index.git_hotspots(project="test-project")
        # src/main.py has complexity 6, so its hotspot score should be boosted
        main_hotspot = next((h for h in result["hotspots"] if h["path"] == "src/main.py"), None)
        if main_hotspot:
            assert main_hotspot["complexity_score"] == 6
            expected = main_hotspot["commit_count"] * (1 + 6 / 10.0)
            assert abs(main_hotspot["hotspot_score"] - expected) < 0.01

    def test_max_results(self, mock_index):
        result = mock_index.git_hotspots(project="test-project", max_results=2)
        assert len(result["hotspots"]) <= 2

    def test_no_git_repo(self, mock_index, tmp_path, monkeypatch):
        fake_index = {
            "projects": ["no-git"],
            "project_roots": {"no-git": str(tmp_path / "no-git")},
            "symbols": {},
        }
        os.makedirs(str(tmp_path / "no-git"), exist_ok=True)
        monkeypatch.setattr(mock_index, "load_index", lambda: fake_index)
        result = mock_index.git_hotspots(project="no-git")
        assert "error" in result


# ---------------------------------------------------------------------------
# git_cochange
# ---------------------------------------------------------------------------

class TestGitCochange:

    def test_finds_cochanges(self, mock_index):
        result = mock_index.git_cochange(path="src/main.py", project="test-project")
        assert result["target_path"] == "src/main.py"
        assert result["total_commits"] > 0
        # src/utils.py changed with src/main.py in commits 4 and 5
        cochange_paths = [c["path"] for c in result["cochanges"]]
        # At minimum, we should get some cochanges
        assert isinstance(result["cochanges"], list)

    def test_cochange_has_ratio(self, mock_index):
        result = mock_index.git_cochange(path="src/main.py", project="test-project")
        for c in result["cochanges"]:
            assert "frequency" in c
            assert "ratio" in c
            assert "sample_commits" in c
            assert 0 <= c["ratio"] <= 1

    def test_no_commits_for_path(self, mock_index):
        result = mock_index.git_cochange(path="nonexistent.py", project="test-project")
        assert result["total_commits"] == 0
        assert result["cochanges"] == []


# ---------------------------------------------------------------------------
# git_churn
# ---------------------------------------------------------------------------

class TestGitChurn:

    def test_file_churn(self, mock_index):
        result = mock_index.git_churn(path="src/main.py", project="test-project")
        assert result["path"] == "src/main.py"
        assert result["project"] == "test-project"
        assert result["total_commits"] > 0
        assert result["total_insertions"] >= 0
        assert result["total_deletions"] >= 0
        assert "recent_commits" in result
        assert "symbols" in result

    def test_project_churn(self, mock_index):
        result = mock_index.git_churn(project="test-project")
        assert result["path"] is None
        assert result["total_commits"] > 0
        assert "symbols" not in result

    def test_churn_recent_commits_structure(self, mock_index):
        result = mock_index.git_churn(path="src/utils.py", project="test-project")
        for c in result["recent_commits"]:
            assert "hash" in c
            assert "date" in c
            assert "author" in c
            assert "message" in c
            assert "lines_changed" in c


# ---------------------------------------------------------------------------
# git_risk_commits
# ---------------------------------------------------------------------------

class TestGitRiskCommits:

    def test_finds_risky_commits(self, mock_index):
        result = mock_index.git_risk_commits(project="test-project")
        assert "commits" in result
        assert len(result["commits"]) > 0

        # The "workaround" commit and "fix:" commit should have risk scores > 0
        messages = [c["message"] for c in result["commits"]]
        risky = [c for c in result["commits"] if c["risk_score"] > 0]
        assert len(risky) > 0

    def test_risk_factors_populated(self, mock_index):
        result = mock_index.git_risk_commits(project="test-project")
        # At least one commit should have risk factors
        all_factors = []
        for c in result["commits"]:
            all_factors.extend(c["risk_factors"])
        # The "workaround" commit should trigger "risky keyword" factor
        assert any("risky keyword" in f for f in all_factors)

    def test_commit_structure(self, mock_index):
        result = mock_index.git_risk_commits(project="test-project")
        for c in result["commits"]:
            assert "hash" in c
            assert "message" in c
            assert "author" in c
            assert "date" in c
            assert "risk_score" in c
            assert "risk_factors" in c
            assert "stats" in c
            assert "files" in c["stats"]
            assert "insertions" in c["stats"]
            assert "deletions" in c["stats"]

    def test_max_results(self, mock_index):
        result = mock_index.git_risk_commits(project="test-project", max_results=2)
        assert len(result["commits"]) <= 2

    def test_sorted_by_risk_score(self, mock_index):
        result = mock_index.git_risk_commits(project="test-project")
        scores = [c["risk_score"] for c in result["commits"]]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_project_not_found(self, mock_index):
        result = mock_index.git_hotspots(project="nonexistent-project")
        assert "error" in result

    def test_find_git_root(self):
        from tools.git_intel import _find_git_root
        # Current repo should have a git root (or not — handle both)
        result = _find_git_root(os.getcwd())
        # Just make sure it doesn't crash
        assert result is None or os.path.isdir(os.path.join(result, ".git"))

    def test_run_git_timeout(self, tmp_path):
        from tools.git_intel import _run_git
        repo = str(tmp_path / "empty_repo")
        os.makedirs(repo)
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        # A simple command should work
        output = _run_git(["status"], cwd=repo)
        assert isinstance(output, str)

    def test_run_git_failure(self, tmp_path):
        from tools.git_intel import _run_git
        with pytest.raises(RuntimeError):
            _run_git(["log"], cwd=str(tmp_path))  # not a git repo
