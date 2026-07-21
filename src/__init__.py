"""
Flyto2 Indexer - Code audit and smart indexing system.

Enables AI to precisely locate code and clearly see what is affected by changes.

Usage:
    from flyto_indexer import IndexEngine

    engine = IndexEngine("my-project", "/path/to/project")

    # Scan project
    result = engine.scan()

    # Query impact scope
    impact = engine.impact("src/utils.py:function:helper")

    # Get context
    context = engine.context(query="top-up page")
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib

from .engine import IndexEngine
from .models import Dependency, DependencyType, ProjectIndex, Symbol, SymbolType


def _resolve_version() -> str:
    """Use the checkout version in source mode and wheel metadata when installed."""
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

__version__ = _resolve_version()
__all__ = [
    "IndexEngine",
    "Symbol",
    "Dependency",
    "ProjectIndex",
    "SymbolType",
    "DependencyType",
]
