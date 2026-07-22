"""Resolve the Flyto2 Indexer package version in source and installed modes."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib


def resolve_version() -> str:
    """Return the checkout version first, then installed package metadata."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project_version = data.get("project", {}).get("version")
        if isinstance(project_version, str) and project_version:
            return project_version
    except (OSError, tomllib.TOMLDecodeError):
        pass

    try:
        return version("flyto-indexer")
    except PackageNotFoundError:
        return "0+unknown"


__version__ = resolve_version()
