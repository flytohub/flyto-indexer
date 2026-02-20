"""
Flyto Indexer - Code audit and smart indexing system.

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

from .engine import IndexEngine
from .models import Dependency, DependencyType, ProjectIndex, Symbol, SymbolType

__version__ = "1.2.1"
__all__ = [
    "IndexEngine",
    "Symbol",
    "Dependency",
    "ProjectIndex",
    "SymbolType",
    "DependencyType",
]
