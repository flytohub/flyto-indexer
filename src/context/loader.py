"""
Progressive Context Loading (由淺入深上下文載入)

L0: 專案大綱（目錄樹 + 每個檔案一句話）→ 幾百~一千 tokens
L1: 檔案摘要（exports/imports/主要功能）→ 命中檔的詳細資訊
L2: 片段原文（只取需要的 chunk）→ 真正要看的程式碼

使用流程：
1. AI 先讀 L0，決定要看哪些檔案
2. 讀 L1（只讀候選檔案）
3. 讀 L2（只取必要片段）
"""

import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

try:
    from ..models import ProjectIndex, Symbol, SymbolType
except ImportError:
    from models import ProjectIndex, Symbol, SymbolType


@dataclass
class L0Context:
    """L0 專案大綱"""
    project: str
    tree: str              # 目錄樹（文字格式）
    file_map: dict         # {path: one_line_summary}
    entry_points: list     # 入口點
    routes: dict           # 路由表
    api_endpoints: list    # API 列表

    def to_text(self, max_files: int = 100) -> str:
        """轉成文字（給 AI 看）"""
        lines = [
            f"# Project: {self.project}",
            "",
            "## Directory Structure",
            "```",
            self.tree,
            "```",
            "",
            "## File Map",
        ]

        # 按目錄分組顯示
        sorted_files = sorted(self.file_map.items())[:max_files]
        current_dir = ""
        for path, summary in sorted_files:
            dir_name = str(Path(path).parent)
            if dir_name != current_dir:
                current_dir = dir_name
                lines.append(f"\n### {dir_name}/")
            file_name = Path(path).name
            lines.append(f"- `{file_name}`: {summary}")

        if self.entry_points:
            lines.extend([
                "",
                "## Entry Points",
                *[f"- {e}" for e in self.entry_points],
            ])

        if self.routes:
            lines.extend([
                "",
                "## Routes",
                *[f"- `{path}` → {comp}" for path, comp in list(self.routes.items())[:20]],
            ])

        if self.api_endpoints:
            lines.extend([
                "",
                "## API Endpoints",
                *[f"- `{e['method']} {e['path']}`: {e.get('summary', '')}" for e in self.api_endpoints[:20]],
            ])

        return "\n".join(lines)

    def token_estimate(self) -> int:
        """估算 token 數"""
        text = self.to_text()
        return len(text) // 4  # 粗估


@dataclass
class L1Context:
    """L1 檔案摘要"""
    path: str
    language: str
    summary: str
    imports: list[str]
    exports: list[str]
    symbols: list[dict]    # [{name, type, summary, line}]
    dependencies: list[str]  # 依賴的其他檔案

    def to_text(self) -> str:
        lines = [
            f"# File: {self.path}",
            f"Language: {self.language}",
            "",
            f"## Summary",
            self.summary,
            "",
        ]

        if self.imports:
            lines.extend([
                "## Imports",
                *[f"- {i}" for i in self.imports[:20]],
                "",
            ])

        if self.exports:
            lines.extend([
                "## Exports",
                *[f"- {e}" for e in self.exports],
                "",
            ])

        if self.symbols:
            lines.extend([
                "## Symbols",
            ])
            for s in self.symbols:
                line_info = f" (L{s['line']})" if s.get('line') else ""
                summary = f": {s['summary']}" if s.get('summary') else ""
                lines.append(f"- `{s['type']}` **{s['name']}**{line_info}{summary}")
            lines.append("")

        if self.dependencies:
            lines.extend([
                "## Dependencies",
                *[f"- {d}" for d in self.dependencies],
            ])

        return "\n".join(lines)


@dataclass
class L2Context:
    """L2 片段原文"""
    symbol_id: str
    path: str
    name: str
    symbol_type: str
    start_line: int
    end_line: int
    content: str

    def to_text(self) -> str:
        return f"""# {self.symbol_id}
File: {self.path}
Lines: {self.start_line}-{self.end_line}

```
{self.content}
```"""


class ContextLoader:
    """
    上下文載入器

    實現由淺入深的載入策略
    """

    def __init__(self, index: ProjectIndex):
        self.index = index

    def load_l0(self) -> L0Context:
        """
        載入 L0 大綱

        這是最輕量的，先讓 AI 定位要看哪些檔案
        """
        # 生成目錄樹
        tree = self._generate_tree()

        # 生成 file map（每個檔案一句話）
        file_map = {}
        for path, manifest in self.index.files.items():
            # 找到這個檔案的主要 symbol
            main_symbol = self._find_main_symbol(path)
            if main_symbol:
                file_map[path] = main_symbol.summary or f"{main_symbol.symbol_type.value}: {main_symbol.name}"
            else:
                file_map[path] = self._infer_file_purpose(path)

        return L0Context(
            project=self.index.project,
            tree=tree,
            file_map=file_map,
            entry_points=self.index.entry_points,
            routes=self.index.routes,
            api_endpoints=self.index.api_endpoints,
        )

    def load_l1(self, path: str) -> Optional[L1Context]:
        """
        載入 L1 檔案摘要

        只在確定要看這個檔案時才載入
        """
        if path not in self.index.files:
            return None

        manifest = self.index.files[path]

        # 收集這個檔案的 symbols
        symbols = []
        imports = []
        exports = []
        main_summary = ""

        for symbol_id in manifest.symbols:
            if symbol_id in self.index.symbols:
                symbol = self.index.symbols[symbol_id]
                symbols.append({
                    "name": symbol.name,
                    "type": symbol.symbol_type.value,
                    "summary": symbol.summary,
                    "line": symbol.start_line,
                })
                imports.extend(symbol.imports)
                exports.extend(symbol.exports)

                # 主要 symbol 的摘要作為檔案摘要
                if symbol.symbol_type in (SymbolType.COMPONENT, SymbolType.CLASS):
                    main_summary = symbol.summary

        # 收集依賴
        dependencies = []
        for dep in self.index.dependencies.values():
            if dep.source_id.startswith(f"{self.index.project}:{path}:"):
                dependencies.append(dep.target_id)

        # 推斷語言
        ext = Path(path).suffix
        lang_map = {".py": "python", ".vue": "vue", ".ts": "typescript", ".js": "javascript"}
        language = lang_map.get(ext, ext[1:])

        return L1Context(
            path=path,
            language=language,
            summary=main_summary or self._infer_file_purpose(path),
            imports=list(set(imports)),
            exports=list(set(exports)),
            symbols=symbols,
            dependencies=list(set(dependencies)),
        )

    def load_l2(self, symbol_id: str) -> Optional[L2Context]:
        """
        載入 L2 片段原文

        只在真正需要看程式碼時才載入
        """
        if symbol_id not in self.index.symbols:
            return None

        symbol = self.index.symbols[symbol_id]

        return L2Context(
            symbol_id=symbol_id,
            path=symbol.path,
            name=symbol.name,
            symbol_type=symbol.symbol_type.value,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            content=symbol.content,
        )

    def load_l2_by_query(self, query: str, top_k: int = 5) -> list[L2Context]:
        """
        根據查詢載入相關的 L2 片段

        這會用向量檢索（需要外部向量庫）
        """
        # TODO: 整合向量檢索
        # 目前用簡單的關鍵字匹配
        results = []
        query_lower = query.lower()

        for symbol_id, symbol in self.index.symbols.items():
            if symbol.symbol_type == SymbolType.FILE:
                continue

            score = 0
            # 名稱匹配
            if query_lower in symbol.name.lower():
                score += 10
            # 摘要匹配
            if symbol.summary and query_lower in symbol.summary.lower():
                score += 5
            # 內容匹配
            if query_lower in symbol.content.lower():
                score += 1

            if score > 0:
                results.append((score, symbol_id))

        # 排序取 top_k
        results.sort(reverse=True)
        return [self.load_l2(sid) for _, sid in results[:top_k]]

    def _generate_tree(self, max_depth: int = 3) -> str:
        """生成目錄樹"""
        paths = sorted(self.index.files.keys())
        if not paths:
            return "(empty)"

        # 簡化：只顯示到指定深度
        tree_lines = []
        seen_dirs = set()

        for path in paths:
            parts = Path(path).parts[:max_depth]
            for i, part in enumerate(parts):
                dir_path = "/".join(parts[:i + 1])
                if dir_path not in seen_dirs:
                    seen_dirs.add(dir_path)
                    indent = "  " * i
                    tree_lines.append(f"{indent}{part}/")

        return "\n".join(tree_lines[:50])  # 限制行數

    def _find_main_symbol(self, path: str) -> Optional[Symbol]:
        """找到檔案的主要 symbol（component/class）"""
        for symbol in self.index.symbols.values():
            if symbol.path == path:
                if symbol.symbol_type in (SymbolType.COMPONENT, SymbolType.CLASS):
                    return symbol
        return None

    def _infer_file_purpose(self, path: str) -> str:
        """從路徑推斷檔案用途"""
        path_lower = path.lower()

        if "test" in path_lower:
            return "Test file"
        if "composable" in path_lower or path_lower.startswith("use"):
            return "Vue composable"
        if "store" in path_lower:
            return "State store"
        if "api" in path_lower or "service" in path_lower:
            return "API service"
        if "component" in path_lower:
            return "UI component"
        if "page" in path_lower or "view" in path_lower:
            return "Page view"
        if "util" in path_lower or "helper" in path_lower:
            return "Utility functions"
        if "config" in path_lower:
            return "Configuration"
        if "router" in path_lower:
            return "Router definition"
        if "model" in path_lower:
            return "Data model"

        return Path(path).stem
