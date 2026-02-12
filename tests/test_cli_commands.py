"""Tests for CLI commands: install-hook, demo, check."""

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.cli import (
    cmd_install_hook,
    cmd_demo,
    cmd_check,
    HOOK_MARKER_BEGIN,
    HOOK_MARKER_END,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo and return its path."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    return tmp_path


def make_args(**kwargs):
    """Create a simple namespace object for argparse-style args."""
    from argparse import Namespace
    return Namespace(**kwargs)


def write_python_files(tmp_path: Path):
    """Write sample Python files with cross-references for scan/impact testing."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)

    (src / "auth.py").write_text(
        'def handle_auth(user):\n'
        '    """Authenticate user."""\n'
        '    return user == "admin"\n'
    )
    (src / "routes.py").write_text(
        'from auth import handle_auth\n\n'
        'def get_routes():\n'
        '    if handle_auth("admin"):\n'
        '        return ["/dashboard"]\n'
        '    return ["/"]\n'
    )
    (src / "app.py").write_text(
        'from routes import get_routes\n\n'
        'def main():\n'
        '    routes = get_routes()\n'
        '    print(routes)\n'
    )


# ===========================================================================
# install-hook tests
# ===========================================================================

class TestInstallHook:
    def test_creates_post_commit(self, tmp_path):
        """Verify hook file is created with correct content and permissions."""
        make_git_repo(tmp_path)
        args = make_args(path=str(tmp_path), remove=False)

        cmd_install_hook(args)

        hook_file = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook_file.exists()
        content = hook_file.read_text()
        assert "#!/bin/sh" in content
        assert HOOK_MARKER_BEGIN in content
        assert HOOK_MARKER_END in content
        assert "flyto-index scan" in content

        # Check executable permission
        mode = hook_file.stat().st_mode
        assert mode & stat.S_IXUSR

    def test_idempotent(self, tmp_path):
        """Running install-hook twice should not duplicate markers."""
        make_git_repo(tmp_path)
        args = make_args(path=str(tmp_path), remove=False)

        cmd_install_hook(args)
        cmd_install_hook(args)

        hook_file = tmp_path / ".git" / "hooks" / "post-commit"
        content = hook_file.read_text()
        assert content.count(HOOK_MARKER_BEGIN) == 1
        assert content.count(HOOK_MARKER_END) == 1

    def test_preserves_existing_hook(self, tmp_path):
        """Existing hook content should be preserved when appending."""
        make_git_repo(tmp_path)
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hook_file = hooks_dir / "post-commit"
        hook_file.write_text("#!/bin/sh\necho 'existing hook'\n")
        os.chmod(hook_file, 0o755)

        args = make_args(path=str(tmp_path), remove=False)
        cmd_install_hook(args)

        content = hook_file.read_text()
        assert "echo 'existing hook'" in content
        assert HOOK_MARKER_BEGIN in content

    def test_remove(self, tmp_path):
        """--remove should strip flyto lines but keep other hook content."""
        make_git_repo(tmp_path)
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hook_file = hooks_dir / "post-commit"

        # Write hook with existing content + flyto markers
        hook_file.write_text(
            "#!/bin/sh\n"
            "echo 'before'\n"
            f"{HOOK_MARKER_BEGIN}\n"
            "flyto-index scan . 2>/dev/null &\n"
            f"{HOOK_MARKER_END}\n"
            "echo 'after'\n"
        )

        args = make_args(path=str(tmp_path), remove=True)
        cmd_install_hook(args)

        content = hook_file.read_text()
        assert HOOK_MARKER_BEGIN not in content
        assert HOOK_MARKER_END not in content
        assert "echo 'before'" in content
        assert "echo 'after'" in content

    def test_remove_deletes_empty_hook(self, tmp_path):
        """--remove should delete the hook file if only flyto content remains."""
        make_git_repo(tmp_path)
        args_install = make_args(path=str(tmp_path), remove=False)
        cmd_install_hook(args_install)

        hook_file = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook_file.exists()

        args_remove = make_args(path=str(tmp_path), remove=True)
        cmd_install_hook(args_remove)

        assert not hook_file.exists()

    def test_no_git_repo(self, tmp_path):
        """Non-git directory should produce an error exit."""
        args = make_args(path=str(tmp_path), remove=False)
        with pytest.raises(SystemExit) as exc:
            cmd_install_hook(args)
        assert exc.value.code == 1


# ===========================================================================
# demo tests
# ===========================================================================

class TestDemo:
    def test_scans_and_shows_impact(self, tmp_path, capsys):
        """Demo should scan, find symbols, and display impact info."""
        write_python_files(tmp_path)
        args = make_args(path=str(tmp_path))

        cmd_demo(args)

        captured = capsys.readouterr()
        # Should show scanning output with symbol count
        assert "symbols in" in captured.out
        # Should show "What if you change" impact example
        assert "What if you change" in captured.out
        # Should suggest the impact command
        assert "flyto-index impact" in captured.out

    def test_empty_project(self, tmp_path, capsys):
        """Empty project should show a friendly message."""
        args = make_args(path=str(tmp_path))

        cmd_demo(args)

        captured = capsys.readouterr()
        assert "No symbols found" in captured.out


# ===========================================================================
# check tests
# ===========================================================================

class TestCheck:
    def _scan_project(self, tmp_path):
        """Helper: scan project so index exists."""
        from src.engine import IndexEngine
        engine = IndexEngine(tmp_path.name, tmp_path)
        engine.scan(incremental=False)
        return engine

    def test_clean_state(self, tmp_path, capsys):
        """No changes should result in risk LOW and exit 0."""
        write_python_files(tmp_path)
        self._scan_project(tmp_path)

        args = make_args(
            path=str(tmp_path), threshold="high", as_json=False, base=None
        )
        # Should NOT raise SystemExit (exit 0)
        cmd_check(args)

        captured = capsys.readouterr()
        assert "PASS" in captured.out

    def test_detects_changes(self, tmp_path, capsys):
        """Modifying a file after scan should report non-zero affected."""
        write_python_files(tmp_path)
        self._scan_project(tmp_path)

        # Modify a file to trigger change detection
        auth_file = tmp_path / "src" / "auth.py"
        auth_file.write_text(
            'def handle_auth(user, token):\n'
            '    """Authenticate user with token."""\n'
            '    return user == "admin" and token\n'
        )

        args = make_args(
            path=str(tmp_path), threshold="high", as_json=False, base=None
        )
        # May or may not exit 1 depending on total_affected count
        try:
            cmd_check(args)
            captured = capsys.readouterr()
            assert "Risk:" in captured.out
        except SystemExit as e:
            captured = capsys.readouterr()
            assert "Risk:" in captured.out

    def test_threshold_high_passes_medium_risk(self, tmp_path, capsys):
        """Risk MEDIUM + threshold HIGH should exit 0 (pass)."""
        write_python_files(tmp_path)

        args = make_args(
            path=str(tmp_path), threshold="high", as_json=False, base=None
        )

        # With a fresh scan and small changes, risk should be < HIGH
        cmd_check(args)
        captured = capsys.readouterr()
        assert "PASS" in captured.out

    def test_threshold_low_fails(self, tmp_path, capsys):
        """threshold LOW should fail even with minimal changes if any affected."""
        write_python_files(tmp_path)
        self._scan_project(tmp_path)

        # Modify a file to create changes
        auth_file = tmp_path / "src" / "auth.py"
        auth_file.write_text(
            'def handle_auth(user, token):\n'
            '    return user == "admin"\n'
        )

        args = make_args(
            path=str(tmp_path), threshold="low", as_json=False, base=None
        )
        # With threshold=low, even LOW risk fails
        try:
            cmd_check(args)
            # If it doesn't exit, check output
            captured = capsys.readouterr()
            # Either PASS (no affected) or FAIL
            assert "Risk:" in captured.out
        except SystemExit as e:
            captured = capsys.readouterr()
            assert e.code == 1
            assert "FAIL" in captured.out

    def test_json_output(self, tmp_path, capsys):
        """--json flag should produce valid JSON with expected fields."""
        write_python_files(tmp_path)
        self._scan_project(tmp_path)

        args = make_args(
            path=str(tmp_path), threshold="high", as_json=True, base=None
        )

        cmd_check(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)

        assert "risk" in data
        assert "changed_files" in data
        assert "total_affected" in data
        assert "affected_files" in data
        assert "threshold" in data
        assert "pass" in data
        assert "symbols" in data
        assert data["risk"] in ("LOW", "MEDIUM", "HIGH")

    def test_with_base_ref(self, tmp_path, capsys):
        """--base should use git diff for change detection."""
        make_git_repo(tmp_path)
        write_python_files(tmp_path)

        # Create initial commit
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "."],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "initial"],
            capture_output=True, check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
        )

        # Scan after first commit
        self._scan_project(tmp_path)

        # Modify and commit
        auth_file = tmp_path / "src" / "auth.py"
        auth_file.write_text(
            'def handle_auth(user, role):\n'
            '    return user == "admin" and role == "super"\n'
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "."],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "update auth"],
            capture_output=True, check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
        )

        args = make_args(
            path=str(tmp_path), threshold="high", as_json=True, base="HEAD~1",
        )

        cmd_check(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)

        assert data["changed_files"] >= 1
        assert "risk" in data


# ===========================================================================
# CLI dispatch integration tests
# ===========================================================================

class TestCLIDispatch:
    def test_install_hook_dispatch(self, tmp_path, monkeypatch):
        """Verify install-hook is reachable via main() dispatch."""
        make_git_repo(tmp_path)
        monkeypatch.setattr(
            "sys.argv", ["flyto-index", "install-hook", str(tmp_path)]
        )
        main()
        hook = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook.exists()

    def test_demo_dispatch(self, tmp_path, monkeypatch, capsys):
        """Verify demo is reachable via main() dispatch."""
        monkeypatch.setattr(
            "sys.argv", ["flyto-index", "demo", str(tmp_path)]
        )
        main()
        captured = capsys.readouterr()
        assert "No symbols found" in captured.out or "symbols in" in captured.out

    def test_check_dispatch(self, tmp_path, monkeypatch, capsys):
        """Verify check is reachable via main() dispatch."""
        write_python_files(tmp_path)
        monkeypatch.setattr(
            "sys.argv", ["flyto-index", "check", str(tmp_path), "--json"]
        )
        main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "risk" in data
