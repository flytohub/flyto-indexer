"""Security tests: gate untrusted test execution (conftest RCE) + git ref injection."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.validation import _run_pytest, _validate_test_path, _test_execution_allowed
import diff_impact


class TestValidateTestPath:
    def test_rejects_leading_dash(self, tmp_path):
        ok, msg = _validate_test_path("-x", str(tmp_path))
        assert ok is False
        assert "start with '-'" in msg

    def test_rejects_escape(self, tmp_path):
        ok, msg = _validate_test_path("../../etc/passwd", str(tmp_path))
        assert ok is False
        assert "escapes" in msg

    def test_accepts_inside(self, tmp_path):
        (tmp_path / "tests").mkdir()
        ok, rel = _validate_test_path("tests", str(tmp_path))
        assert ok is True
        assert rel == "tests"


class TestPytestOptIn:
    def test_disabled_by_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLYTO_ALLOW_TEST_EXECUTION", raising=False)
        assert _test_execution_allowed() is False
        result = _run_pytest(str(tmp_path))
        assert result["status"] == "skipped"
        assert "test execution disabled" in result["output"]
        assert "conftest.py" in result["output"]

    def test_bad_test_path_rejected_even_when_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLYTO_ALLOW_TEST_EXECUTION", "1")
        result = _run_pytest(str(tmp_path), test_path="../../etc")
        assert result["status"] == "error"
        assert "escapes" in result["output"]

    def test_leading_dash_rejected_when_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLYTO_ALLOW_TEST_EXECUTION", "1")
        result = _run_pytest(str(tmp_path), test_path="-x")
        assert result["status"] == "error"
        assert "start with '-'" in result["output"]


class TestGitRefInjection:
    def test_rejects_option_like_base(self, tmp_path):
        with pytest.raises(ValueError, match="must not start with '-'"):
            diff_impact._run_git_diff(str(tmp_path), "committed", "--output=/tmp/x")
