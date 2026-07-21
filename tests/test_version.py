from pathlib import Path
import subprocess
import sys
import tomllib

from src import __version__
from src.cli import cmd_tools


def _project_version() -> str:
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_package_version_matches_pyproject():
    assert __version__ == _project_version()


def test_cli_version_matches_pyproject():
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "--version"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == f"flyto-index {_project_version()}"


def test_tools_reports_package_version():
    class Args:
        compact = False

    assert cmd_tools(Args())["version"] == _project_version()
