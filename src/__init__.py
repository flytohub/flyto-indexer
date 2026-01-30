"""
Flyto Indexer - Code audit and smart indexing system.

讓 AI 精準定位、改了會影響什麼一目了然。

Usage:
    from flyto_indexer import IndexEngine

    engine = IndexEngine("my-project", "/path/to/project")

    # 掃描專案
    result = engine.scan()

    # 查詢影響範圍
    impact = engine.impact("src/utils.py:function:helper")

    # 取得上下文
    context = engine.context(query="儲值頁面")
"""

from .engine import IndexEngine
from .models import Symbol, Dependency, ProjectIndex, SymbolType, DependencyType

__version__ = "0.1.0"
__all__ = [
    "IndexEngine",
    "Symbol",
    "Dependency",
    "ProjectIndex",
    "SymbolType",
    "DependencyType",
]
