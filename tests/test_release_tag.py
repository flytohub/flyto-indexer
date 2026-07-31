from __future__ import annotations

from pathlib import Path

from scripts.check_release_tag import release_errors


def _release_files(tmp_path: Path, version: str = "2.18.0") -> tuple[Path, Path]:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f'[project]\nname = "example"\nversion = "{version}"\n', encoding="utf-8")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(f"# Changelog\n\n## [{version}] - 2026-07-31\n", encoding="utf-8")
    return pyproject, changelog


def test_release_tag_accepts_synchronized_release(tmp_path: Path) -> None:
    pyproject, changelog = _release_files(tmp_path)

    assert release_errors("v2.18.0", pyproject=pyproject, changelog=changelog) == []


def test_release_tag_rejects_version_and_changelog_drift(tmp_path: Path) -> None:
    pyproject, changelog = _release_files(tmp_path)
    changelog.write_text("# Changelog\n\n## Unreleased\n", encoding="utf-8")

    errors = release_errors("v2.17.0", pyproject=pyproject, changelog=changelog)

    assert len(errors) == 2
    assert "does not match package version" in errors[0]
    assert "no dated release heading" in errors[1]
