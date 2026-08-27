"""Git standard excludes refine repository-wide source discovery."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from src.analyzer.security import SecurityScanner
from src.analyzer.taint import TaintAnalyzer
from src.gitignore import GitIgnoreFilter
from src.indexer.incremental import scan_directory_hashes


PY_FLOW = """\
def run_code():
    code = request.form.get("code")
    eval(code)
"""
JS_FLOW = "app.get('/x', (req, res) => { db.query('SELECT ' + req.query.name); });\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _finding_paths(root: Path) -> set[str]:
    return {flow.file_path for flow in TaintAnalyzer(root).analyze()}


def test_untracked_ignored_paths_are_removed_from_every_scanner(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _write(tmp_path, ".gitignore", "private-cache/\nignored.py\nignored.ts\n")
    _write(tmp_path, "private-cache/hidden.py", PY_FLOW)
    _write(tmp_path, "private-cache/hidden.ts", JS_FLOW)
    _write(tmp_path, "ignored.py", PY_FLOW)
    _write(tmp_path, "ignored.ts", JS_FLOW)
    _write(tmp_path, "private-cache-authored/visible.py", PY_FLOW)
    _write(tmp_path, "private-cache-authored/visible.ts", JS_FLOW)

    hidden = {
        "private-cache/hidden.py",
        "private-cache/hidden.ts",
        "ignored.py",
        "ignored.ts",
    }
    visible = {
        "private-cache-authored/visible.py",
        "private-cache-authored/visible.ts",
    }
    hashes = set(scan_directory_hashes(tmp_path, [".py", ".ts"]))
    security = set(SecurityScanner(tmp_path).scan_directory())
    taint = _finding_paths(tmp_path)

    assert hashes.isdisjoint(hidden)
    assert security.isdisjoint(hidden)
    assert taint.isdisjoint(hidden)
    assert visible <= hashes
    assert visible <= security
    assert visible <= taint


def test_tracked_ignored_files_remain_visible_everywhere(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _write(tmp_path, ".gitignore", "tracked-later/\n")
    _write(tmp_path, "tracked-later/kept.py", PY_FLOW)
    _write(tmp_path, "tracked-later/kept.ts", JS_FLOW)
    _git(tmp_path, "add", "--force", "tracked-later/kept.py", "tracked-later/kept.ts")

    expected = {"tracked-later/kept.py", "tracked-later/kept.ts"}
    assert expected <= set(scan_directory_hashes(tmp_path, [".py", ".ts"]))
    assert expected <= set(SecurityScanner(tmp_path).scan_directory())
    assert expected <= _finding_paths(tmp_path)


def test_nul_protocol_preserves_spaces_and_newlines(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    ignored_space = "ignored dir/space name.py"
    ignored_newline = "ignored dir/line\nbreak.py"
    visible_space = "source dir/space name.py"
    _write(tmp_path, ".gitignore", "ignored dir/\n")
    for relative in (ignored_space, ignored_newline, visible_space):
        _write(tmp_path, relative, PY_FLOW)

    assert GitIgnoreFilter(tmp_path).filter(
        [ignored_newline, visible_space, ignored_space]
    ) == [visible_space]


def test_nul_protocol_round_trips_surrogateescaped_path_bytes(tmp_path: Path) -> None:
    raw = b"generated-\xff.py"
    relative = raw.decode("utf-8", errors="surrogateescape")

    def check_run(*args, **kwargs):
        assert kwargs["input"] == raw + b"\0"
        return subprocess.CompletedProcess(args[0], 0, stdout=raw + b"\0")

    with patch("src.gitignore.subprocess.run", side_effect=check_run):
        assert GitIgnoreFilter(tmp_path).filter([relative]) == []


def test_malformed_git_output_fails_open(tmp_path: Path) -> None:
    malformed = (
        subprocess.CompletedProcess([], 0, stdout=b"other.py\0"),
        subprocess.CompletedProcess([], 0, stdout=b"source.py"),
        subprocess.CompletedProcess([], 1, stdout=b"source.py\0"),
        subprocess.CompletedProcess([], 0, stdout=b"source.py\0source.py\0"),
        subprocess.CompletedProcess([], 1, stdout=""),
    )
    for completed in malformed:
        with patch("src.gitignore.subprocess.run", return_value=completed):
            assert GitIgnoreFilter(tmp_path).filter(["source.py"]) == ["source.py"]


def test_timeout_and_bad_return_code_fail_open(tmp_path: Path) -> None:
    failures = (
        subprocess.TimeoutExpired(["git"], 5),
        subprocess.CompletedProcess([], 2, stdout=b""),
    )
    for failure in failures:
        context = (
            patch("src.gitignore.subprocess.run", side_effect=failure)
            if isinstance(failure, BaseException)
            else patch("src.gitignore.subprocess.run", return_value=failure)
        )
        with context:
            assert GitIgnoreFilter(tmp_path).filter(["source.py"]) == ["source.py"]


def test_batches_are_bounded_by_count_and_encoded_bytes(tmp_path: Path) -> None:
    count_paths = [f"src/file-{number}.py" for number in range(513)]
    byte_paths = ["a" * 32_767, "b" * 32_767, "c.py"]
    calls: list[bytes] = []

    def check_run(*args, **kwargs):
        payload = kwargs["input"]
        calls.append(payload)
        assert payload.endswith(b"\0")
        assert payload.count(b"\0") <= 512
        assert len(payload) <= 64 * 1024
        return subprocess.CompletedProcess(args[0], 1, stdout=b"")

    with patch("src.gitignore.subprocess.run", side_effect=check_run):
        assert GitIgnoreFilter(tmp_path).filter(count_paths + byte_paths)
    assert len(calls) >= 3


def test_oversized_candidate_is_included_without_a_git_request(tmp_path: Path) -> None:
    oversized = "x" * (64 * 1024)
    with patch("src.gitignore.subprocess.run") as run:
        assert GitIgnoreFilter(tmp_path).filter([oversized]) == [oversized]
    run.assert_not_called()


def test_git_failure_fails_open_without_weakening_built_in_exclusions(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "src/app.py", PY_FLOW)
    _write(tmp_path, "src/app.ts", JS_FLOW)
    _write(tmp_path, "build/generated.py", PY_FLOW)

    with patch("src.gitignore.subprocess.run", side_effect=OSError("git unavailable")):
        assert set(scan_directory_hashes(tmp_path, [".py", ".ts"])) == {
            "src/app.py",
            "src/app.ts",
        }
        assert {"src/app.py", "src/app.ts"} <= set(
            SecurityScanner(tmp_path).scan_directory()
        )
        assert {"src/app.py", "src/app.ts"} <= _finding_paths(tmp_path)


def test_taint_filters_return_python_regex_and_cached_caller_phases(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "--quiet")
    _write(tmp_path, ".gitignore", "ignored/\n")
    _write(tmp_path, "ignored/return_source.py", PY_FLOW)
    _write(tmp_path, "ignored/regex.ts", JS_FLOW)
    _write(tmp_path, "ignored/caller.py", PY_FLOW)
    analyzer = TaintAnalyzer(tmp_path)

    analyzer.analyze()

    assert "ignored/return_source.py" not in analyzer._ast_cache
    assert "ignored/regex.ts" not in {flow.file_path for flow in analyzer.findings}
    assert not analyzer._gitignore.includes_cached("ignored/caller.py")
    analyzer._check_caller("ignored/caller.py", "run_code", [])
    assert "ignored/caller.py" not in analyzer._ast_cache
    assert analyzer._find_param_index("run_code", "code", "ignored/caller.py") is None
