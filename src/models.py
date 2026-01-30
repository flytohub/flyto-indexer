"""
Core data models for Flyto Indexer.

Symbol ID 格式：project:path:type:name
例如：flyto-cloud:src/pages/TopUp.vue:component:TopUp
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import hashlib


class SymbolType(str, Enum):
    """Symbol 類型（年級）"""
    FILE = "file"           # 整個檔案
    CLASS = "class"         # 類
    FUNCTION = "function"   # 函數
    METHOD = "method"       # 類方法
    COMPONENT = "component" # Vue/React 組件
    COMPOSABLE = "composable"  # Vue composable
    STORE = "store"         # Pinia/Vuex store
    ROUTE = "route"         # 路由定義
    API = "api"             # API endpoint
    VARIABLE = "variable"   # 常數/變數
    TYPE = "type"           # TypeScript 類型定義
    INTERFACE = "interface" # 介面定義


class DependencyType(str, Enum):
    """依賴類型"""
    IMPORTS = "imports"       # A imports B
    CALLS = "calls"           # A calls B
    EXTENDS = "extends"       # A extends B
    IMPLEMENTS = "implements" # A implements B
    USES = "uses"             # A uses B (composable/store)
    ROUTES_TO = "routes_to"   # route points to component
    API_CALLS = "api_calls"   # frontend calls backend API


@dataclass
class Symbol:
    """
    Symbol（學號）- 代碼中的唯一單元

    ID 格式：project:path:type:name
    像學號一樣：學校_年級_班級_座號
    - project = 學校
    - path = 班級（檔案路徑）
    - type = 年級（symbol 類型）
    - name = 座號（symbol 名稱）
    """
    project: str          # 專案名稱
    path: str             # 相對路徑
    symbol_type: SymbolType  # symbol 類型
    name: str             # symbol 名稱

    # 位置信息
    start_line: int = 0
    end_line: int = 0

    # 內容（用於計算 hash 和生成 embedding）
    content: str = ""
    content_hash: str = ""

    # 摘要（L1 用）
    summary: str = ""

    # 元數據
    language: str = ""
    exports: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    returns: str = ""

    @property
    def id(self) -> str:
        """生成唯一 Symbol ID"""
        return f"{self.project}:{self.path}:{self.symbol_type.value}:{self.name}"

    @property
    def short_id(self) -> str:
        """短 ID（不含 project）"""
        return f"{self.path}:{self.symbol_type.value}:{self.name}"

    def compute_hash(self) -> str:
        """計算內容 hash"""
        self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]
        return self.content_hash

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project": self.project,
            "path": self.path,
            "type": self.symbol_type.value,
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content": self.content,
            "content_hash": self.content_hash,
            "summary": self.summary,
            "language": self.language,
            "exports": self.exports,
            "imports": self.imports,
        }


@dataclass
class Dependency:
    """
    依賴關係（因果關係圖的邊）

    source -> target (type)
    例如：TopUp.vue -calls-> useWallet.topUp()
    """
    source_id: str        # 來源 Symbol ID
    target_id: str        # 目標 Symbol ID
    dep_type: DependencyType

    # 來源位置（哪一行引用的）
    source_line: int = 0

    # 額外信息
    metadata: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.source_id}--{self.dep_type.value}-->{self.target_id}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source_id,
            "target": self.target_id,
            "type": self.dep_type.value,
            "line": self.source_line,
            "metadata": self.metadata,
        }


@dataclass
class FileManifest:
    """
    檔案指紋（用於判斷變更）
    """
    path: str
    content_hash: str
    line_count: int
    symbols: list[str] = field(default_factory=list)  # Symbol IDs
    last_indexed: str = ""  # ISO timestamp

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "hash": self.content_hash,
            "lines": self.line_count,
            "symbols": self.symbols,
            "indexed_at": self.last_indexed,
        }


@dataclass
class ProjectIndex:
    """
    專案索引（L0 大綱）
    """
    project: str
    root_path: str

    # 目錄結構
    tree: dict = field(default_factory=dict)

    # 檔案清單（path -> FileManifest）
    files: dict[str, FileManifest] = field(default_factory=dict)

    # 所有 symbols（id -> Symbol）
    symbols: dict[str, Symbol] = field(default_factory=dict)

    # 依賴關係（id -> Dependency）
    dependencies: dict[str, Dependency] = field(default_factory=dict)

    # 入口點
    entry_points: list[str] = field(default_factory=list)

    # 路由表（path -> component）
    routes: dict[str, str] = field(default_factory=dict)

    # API endpoints
    api_endpoints: list[dict] = field(default_factory=list)

    def get_affected_by(self, symbol_id: str) -> list[str]:
        """
        反向查詢：改了這個 symbol，會影響哪些其他 symbols

        這就是「改了座號，反查會影響哪個班級/年級」
        """
        affected = []
        for dep in self.dependencies.values():
            if dep.target_id == symbol_id:
                affected.append(dep.source_id)
        return affected

    def get_depends_on(self, symbol_id: str) -> list[str]:
        """
        正向查詢：這個 symbol 依賴哪些其他 symbols
        """
        depends = []
        for dep in self.dependencies.values():
            if dep.source_id == symbol_id:
                depends.append(dep.target_id)
        return depends

    def get_impact_chain(self, symbol_id: str, max_depth: int = 3) -> dict:
        """
        取得完整影響鏈（遞迴）

        改了 useWallet.topUp()
          → L1: TopUp.vue, WalletPage.vue（直接調用者）
          → L2: /wallet route（引用 TopUp.vue）
          → L3: App.vue（包含 router-view）
        """
        result = {"symbol": symbol_id, "levels": []}
        visited = {symbol_id}
        current_level = [symbol_id]

        for depth in range(max_depth):
            next_level = []
            for sid in current_level:
                affected = self.get_affected_by(sid)
                for a in affected:
                    if a not in visited:
                        visited.add(a)
                        next_level.append(a)

            if next_level:
                result["levels"].append({
                    "depth": depth + 1,
                    "symbols": next_level,
                })
            current_level = next_level

            if not current_level:
                break

        return result
