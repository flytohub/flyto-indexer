"""
AI 工作流程 - 大向 → 中向 → 細項 → 影響分析

用戶說「我要做商城功能」時的流程：

1. 大向（L0）：從 PROJECT_MAP 找相關模組
   → 找到：ProductList.vue, Cart.vue, Order.vue, useCart.ts

2. 中向（L1）：AI 挑選要看的檔案
   → 選擇：Cart.vue（購物車核心）

3. 細項（L2）：查看具體函數
   → addToCart(), removeItem(), checkout()

4. 影響分析：要修改 addToCart() 時
   → 反查：ProductCard.vue, QuickBuy.vue 都有呼叫
   → 反推給 AI：「修改 addToCart 會影響這些地方，確定要改嗎？」
"""

import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class SearchResult:
    """搜尋結果"""
    level: str  # "l0", "l1", "l2"
    query: str
    matches: list[dict]
    suggestion: str  # AI 建議下一步


class AIWorkflow:
    """
    AI 輔助工作流程

    實現大向 → 中向 → 細項的導航
    """

    def __init__(
        self,
        project_map_path: Path,
        index_path: Path,
    ):
        """
        Args:
            project_map_path: PROJECT_MAP.json 路徑
            index_path: .flyto-index/index.json 路徑
        """
        self.project_map = self._load_json(project_map_path)
        self.index = self._load_json(index_path)

    def _load_json(self, path: Path) -> dict:
        if path.exists():
            return json.loads(path.read_text())
        return {}

    def search_l0(self, query: str) -> SearchResult:
        """
        大向搜尋（L0）

        從 PROJECT_MAP 的 keywords/categories 找相關模組

        Example:
            query = "商城功能"
            → 找到 category=product, keywords=["商城", "商品", "cart"]
            → 返回相關檔案列表
        """
        matches = []
        query_lower = query.lower()
        query_words = query_lower.split()

        # 搜尋 keyword_index
        keyword_index = self.project_map.get("keyword_index", {})
        for keyword, paths in keyword_index.items():
            if any(w in keyword or keyword in w for w in query_words):
                for path in paths:
                    file_info = self.project_map.get("files", {}).get(path, {})
                    matches.append({
                        "path": path,
                        "purpose": file_info.get("purpose", ""),
                        "category": file_info.get("category", ""),
                        "match_keyword": keyword,
                        "relevance": "keyword",
                    })

        # 搜尋 categories
        categories = self.project_map.get("categories", {})
        for category, paths in categories.items():
            if any(w in category or category in w for w in query_words):
                for path in paths:
                    if not any(m["path"] == path for m in matches):
                        file_info = self.project_map.get("files", {}).get(path, {})
                        matches.append({
                            "path": path,
                            "purpose": file_info.get("purpose", ""),
                            "category": category,
                            "match_keyword": category,
                            "relevance": "category",
                        })

        # 去重並排序
        seen = set()
        unique_matches = []
        for m in matches:
            if m["path"] not in seen:
                seen.add(m["path"])
                unique_matches.append(m)

        suggestion = ""
        if unique_matches:
            suggestion = f"找到 {len(unique_matches)} 個相關檔案。建議先看："
            for m in unique_matches[:3]:
                suggestion += f"\n  - {m['path']}: {m['purpose']}"
            suggestion += "\n\n使用 search_l1(path) 查看檔案詳情"
        else:
            suggestion = "沒有找到相關檔案。試試其他關鍵字？"

        return SearchResult(
            level="l0",
            query=query,
            matches=unique_matches,
            suggestion=suggestion,
        )

    def search_l1(self, path: str) -> SearchResult:
        """
        中向搜尋（L1）

        查看特定檔案的 symbols 列表

        Example:
            path = "src/pages/Cart.vue"
            → 返回該檔案的所有函數/組件
        """
        matches = []

        # 從 index 找該檔案的 symbols
        symbols = self.index.get("symbols", {})
        for symbol_id, symbol in symbols.items():
            if symbol.get("path") == path:
                matches.append({
                    "id": symbol_id,
                    "name": symbol.get("name", ""),
                    "type": symbol.get("type", ""),
                    "line": symbol.get("start_line", 0),
                    "summary": symbol.get("summary", ""),
                })

        # 取得檔案的審計資訊
        file_info = self.project_map.get("files", {}).get(path, {})

        suggestion = ""
        if matches:
            suggestion = f"檔案 {path} 包含 {len(matches)} 個 symbols:\n"
            suggestion += f"用途：{file_info.get('purpose', 'N/A')}\n"
            suggestion += f"分類：{file_info.get('category', 'N/A')}\n"
            suggestion += f"APIs：{', '.join(file_info.get('apis', []))}\n"
            suggestion += f"\n主要 symbols："
            for m in matches[:5]:
                suggestion += f"\n  - [{m['type']}] {m['name']} (L{m['line']})"
            suggestion += "\n\n使用 search_l2(symbol_id) 查看詳情"
        else:
            suggestion = f"檔案 {path} 沒有找到 symbols"

        return SearchResult(
            level="l1",
            query=path,
            matches=matches,
            suggestion=suggestion,
        )

    def search_l2(self, symbol_id: str) -> SearchResult:
        """
        細項搜尋（L2）

        查看特定 symbol 的詳細內容

        Example:
            symbol_id = "flyto-cloud:src/pages/Cart.vue:function:addToCart"
            → 返回該函數的完整內容
        """
        symbol = self.index.get("symbols", {}).get(symbol_id, {})

        if not symbol:
            # 嘗試模糊匹配
            for sid, s in self.index.get("symbols", {}).items():
                if symbol_id in sid or sid.endswith(symbol_id):
                    symbol = s
                    symbol_id = sid
                    break

        if not symbol:
            return SearchResult(
                level="l2",
                query=symbol_id,
                matches=[],
                suggestion=f"找不到 symbol: {symbol_id}",
            )

        matches = [{
            "id": symbol_id,
            "path": symbol.get("path", ""),
            "name": symbol.get("name", ""),
            "type": symbol.get("type", ""),
            "start_line": symbol.get("start_line", 0),
            "end_line": symbol.get("end_line", 0),
            "content": symbol.get("content", ""),
            "summary": symbol.get("summary", ""),
        }]

        suggestion = f"[{symbol.get('type')}] {symbol.get('name')}\n"
        suggestion += f"位置：{symbol.get('path')}:{symbol.get('start_line')}-{symbol.get('end_line')}\n"
        suggestion += f"\n使用 impact_analysis(symbol_id) 查看影響範圍"

        return SearchResult(
            level="l2",
            query=symbol_id,
            matches=matches,
            suggestion=suggestion,
        )

    def impact_analysis(self, symbol_id: str, max_depth: int = 3) -> dict:
        """
        影響分析

        修改這個 symbol 會影響哪些地方

        Returns:
            {
                "symbol": symbol_id,
                "affected": [
                    {"id": str, "path": str, "name": str, "reason": str},
                    ...
                ],
                "warning": str,  # 警告訊息
                "suggestion": str,  # AI 建議
            }
        """
        # 找出所有依賴這個 symbol 的地方
        dependencies = self.index.get("dependencies", {})
        affected = []

        # 反向查詢：誰依賴這個 symbol
        for dep_id, dep in dependencies.items():
            if dep.get("target") == symbol_id or symbol_id in dep.get("target", ""):
                source_id = dep.get("source", "")
                source_symbol = self.index.get("symbols", {}).get(source_id, {})

                affected.append({
                    "id": source_id,
                    "path": source_symbol.get("path", ""),
                    "name": source_symbol.get("name", ""),
                    "type": dep.get("type", ""),
                    "line": dep.get("line", 0),
                    "reason": f"透過 {dep.get('type', 'unknown')} 依賴",
                })

        # 遞迴查詢（第二層影響）
        if max_depth > 1:
            second_level = []
            for a in affected:
                for dep_id, dep in dependencies.items():
                    if dep.get("target") == a["id"]:
                        source_id = dep.get("source", "")
                        if source_id not in [x["id"] for x in affected + second_level]:
                            source_symbol = self.index.get("symbols", {}).get(source_id, {})
                            second_level.append({
                                "id": source_id,
                                "path": source_symbol.get("path", ""),
                                "name": source_symbol.get("name", ""),
                                "type": dep.get("type", ""),
                                "reason": f"間接依賴（透過 {a['name']}）",
                            })
            affected.extend(second_level)

        # 生成警告和建議
        warning = ""
        suggestion = ""

        if len(affected) == 0:
            suggestion = "這個 symbol 沒有被其他地方引用，可以安全修改。"
        elif len(affected) <= 3:
            warning = f"修改會影響 {len(affected)} 個地方"
            suggestion = "影響範圍較小，建議逐一檢查這些調用處。"
        else:
            warning = f"⚠️ 修改會影響 {len(affected)} 個地方！"
            suggestion = "影響範圍較大，建議：\n"
            suggestion += "1. 考慮是否需要向下相容\n"
            suggestion += "2. 先修改測試，確保行為正確\n"
            suggestion += "3. 逐一更新所有調用處"

        return {
            "symbol": symbol_id,
            "affected": affected,
            "affected_count": len(affected),
            "warning": warning,
            "suggestion": suggestion,
        }

    def plan_modification(self, query: str) -> dict:
        """
        規劃修改

        用戶說「我要做商城功能」時，AI 規劃完整流程

        Returns:
            {
                "query": str,
                "related_files": list,
                "suggested_changes": list,
                "impact_summary": str,
                "next_steps": list,
            }
        """
        # Step 1: L0 搜尋相關檔案
        l0_result = self.search_l0(query)

        # Step 2: 收集這些檔案的 symbols
        all_symbols = []
        for match in l0_result.matches[:5]:  # 最多 5 個檔案
            l1_result = self.search_l1(match["path"])
            for symbol in l1_result.matches:
                symbol["file_purpose"] = match["purpose"]
                all_symbols.append(symbol)

        # Step 3: 分析潛在影響
        total_affected = 0
        for symbol in all_symbols[:10]:  # 最多分析 10 個 symbols
            impact = self.impact_analysis(symbol["id"], max_depth=1)
            total_affected += impact["affected_count"]

        return {
            "query": query,
            "related_files": [
                {"path": m["path"], "purpose": m["purpose"]}
                for m in l0_result.matches[:5]
            ],
            "related_symbols": [
                {"name": s["name"], "type": s["type"], "file": s.get("file_purpose", "")}
                for s in all_symbols[:10]
            ],
            "impact_summary": f"潛在影響範圍：{total_affected} 個調用處",
            "next_steps": [
                "1. 確認需求範圍",
                "2. 選擇要修改的具體檔案",
                "3. 使用 search_l2() 查看具體函數",
                "4. 使用 impact_analysis() 確認影響",
                "5. 開始修改，記得更新測試",
            ],
        }
