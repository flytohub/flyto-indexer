"""
AI Workflow - High-level -> Mid-level -> Detail -> Impact Analysis

Flow when a user says "I want to build an e-commerce feature":

1. High-level (L0): Find related modules from PROJECT_MAP
   -> Found: ProductList.vue, Cart.vue, Order.vue, useCart.ts

2. Mid-level (L1): AI selects files to inspect
   -> Selected: Cart.vue (shopping cart core)

3. Detail (L2): View specific functions
   -> addToCart(), removeItem(), checkout()

4. Impact Analysis: When modifying addToCart()
   -> Reverse lookup: ProductCard.vue, QuickBuy.vue both call it
   -> Report to AI: "Modifying addToCart will affect these locations. Proceed?"
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SearchResult:
    """Search result"""
    level: str  # "l0", "l1", "l2"
    query: str
    matches: list[dict]
    suggestion: str  # AI-suggested next step


class AIWorkflow:
    """
    AI-assisted workflow

    Implements high-level -> mid-level -> detail navigation
    """

    def __init__(
        self,
        project_map_path: Path,
        index_path: Path,
    ):
        """
        Args:
            project_map_path: Path to PROJECT_MAP.json
            index_path: Path to .flyto-index/index.json
        """
        self.project_map = self._load_json(project_map_path)
        self.index = self._load_json(index_path)

    def _load_json(self, path: Path) -> dict:
        if path.exists():
            return json.loads(path.read_text())
        return {}

    def search_l0(self, query: str) -> SearchResult:
        """
        High-level search (L0)

        Find related modules from PROJECT_MAP keywords/categories

        Example:
            query = "e-commerce"
            -> Found category=product, keywords=["shop", "product", "cart"]
            -> Returns list of related files
        """
        matches = []
        query_lower = query.lower()
        query_words = query_lower.split()

        # Search keyword_index
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

        # Search categories
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

        # Deduplicate and sort
        seen = set()
        unique_matches = []
        for m in matches:
            if m["path"] not in seen:
                seen.add(m["path"])
                unique_matches.append(m)

        suggestion = ""
        if unique_matches:
            suggestion = f"Found {len(unique_matches)} related files. Recommended to start with:"
            for m in unique_matches[:3]:
                suggestion += f"\n  - {m['path']}: {m['purpose']}"
            suggestion += "\n\nUse search_l1(path) to view file details"
        else:
            suggestion = "No related files found. Try other keywords?"

        return SearchResult(
            level="l0",
            query=query,
            matches=unique_matches,
            suggestion=suggestion,
        )

    def search_l1(self, path: str) -> SearchResult:
        """
        Mid-level search (L1)

        View the symbol list for a specific file

        Example:
            path = "src/pages/Cart.vue"
            -> Returns all functions/components in that file
        """
        matches = []

        # Find symbols for this file from the index
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

        # Get file audit info
        file_info = self.project_map.get("files", {}).get(path, {})

        suggestion = ""
        if matches:
            suggestion = f"File {path} contains {len(matches)} symbols:\n"
            suggestion += f"Purpose: {file_info.get('purpose', 'N/A')}\n"
            suggestion += f"Category: {file_info.get('category', 'N/A')}\n"
            suggestion += f"APIs: {', '.join(file_info.get('apis', []))}\n"
            suggestion += "\nMain symbols:"
            for m in matches[:5]:
                suggestion += f"\n  - [{m['type']}] {m['name']} (L{m['line']})"
            suggestion += "\n\nUse search_l2(symbol_id) to view details"
        else:
            suggestion = f"File {path}: no symbols found"

        return SearchResult(
            level="l1",
            query=path,
            matches=matches,
            suggestion=suggestion,
        )

    def search_l2(self, symbol_id: str) -> SearchResult:
        """
        Detail search (L2)

        View the detailed content of a specific symbol

        Example:
            symbol_id = "flyto-cloud:src/pages/Cart.vue:function:addToCart"
            -> Returns the full content of that function
        """
        symbol = self.index.get("symbols", {}).get(symbol_id, {})

        if not symbol:
            # Try fuzzy matching
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
                suggestion=f"Symbol not found: {symbol_id}",
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
        suggestion += f"Location: {symbol.get('path')}:{symbol.get('start_line')}-{symbol.get('end_line')}\n"
        suggestion += "\nUse impact_analysis(symbol_id) to view the blast radius"

        return SearchResult(
            level="l2",
            query=symbol_id,
            matches=matches,
            suggestion=suggestion,
        )

    def impact_analysis(self, symbol_id: str, max_depth: int = 3) -> dict:
        """
        Impact analysis

        Determine what will be affected by modifying this symbol

        Returns:
            {
                "symbol": symbol_id,
                "affected": [
                    {"id": str, "path": str, "name": str, "reason": str},
                    ...
                ],
                "warning": str,  # Warning message
                "suggestion": str,  # AI suggestion
            }
        """
        # Find all locations that depend on this symbol
        dependencies = self.index.get("dependencies", {})
        affected = []

        # Reverse lookup: who depends on this symbol
        for _dep_id, dep in dependencies.items():
            if dep.get("target") == symbol_id or symbol_id in dep.get("target", ""):
                source_id = dep.get("source", "")
                source_symbol = self.index.get("symbols", {}).get(source_id, {})

                affected.append({
                    "id": source_id,
                    "path": source_symbol.get("path", ""),
                    "name": source_symbol.get("name", ""),
                    "type": dep.get("type", ""),
                    "line": dep.get("line", 0),
                    "reason": f"Depends via {dep.get('type', 'unknown')}",
                })

        # Recursive lookup (second-level impact)
        if max_depth > 1:
            second_level = []
            for a in affected:
                for _dep_id, dep in dependencies.items():
                    if dep.get("target") == a["id"]:
                        source_id = dep.get("source", "")
                        if source_id not in [x["id"] for x in affected + second_level]:
                            source_symbol = self.index.get("symbols", {}).get(source_id, {})
                            second_level.append({
                                "id": source_id,
                                "path": source_symbol.get("path", ""),
                                "name": source_symbol.get("name", ""),
                                "type": dep.get("type", ""),
                                "reason": f"Indirect dependency (via {a['name']})",
                            })
            affected.extend(second_level)

        # Generate warnings and suggestions
        warning = ""
        suggestion = ""

        if len(affected) == 0:
            suggestion = "This symbol is not referenced anywhere else. Safe to modify."
        elif len(affected) <= 3:
            warning = f"Modification will affect {len(affected)} locations"
            suggestion = "Small blast radius. Recommend reviewing each call site individually."
        else:
            warning = f"Warning: Modification will affect {len(affected)} locations!"
            suggestion = "Large blast radius. Recommendations:\n"
            suggestion += "1. Consider whether backward compatibility is needed\n"
            suggestion += "2. Update tests first to ensure correct behavior\n"
            suggestion += "3. Update all call sites one by one"

        return {
            "symbol": symbol_id,
            "affected": affected,
            "affected_count": len(affected),
            "warning": warning,
            "suggestion": suggestion,
        }

    def plan_modification(self, query: str) -> dict:
        """
        Plan a modification

        When a user says "I want to build an e-commerce feature", AI plans the full workflow

        Returns:
            {
                "query": str,
                "related_files": list,
                "suggested_changes": list,
                "impact_summary": str,
                "next_steps": list,
            }
        """
        # Step 1: L0 search for related files
        l0_result = self.search_l0(query)

        # Step 2: Collect symbols from these files
        all_symbols = []
        for match in l0_result.matches[:5]:  # Up to 5 files
            l1_result = self.search_l1(match["path"])
            for symbol in l1_result.matches:
                symbol["file_purpose"] = match["purpose"]
                all_symbols.append(symbol)

        # Step 3: Analyze potential impact
        total_affected = 0
        for symbol in all_symbols[:10]:  # Analyze up to 10 symbols
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
            "impact_summary": f"Potential blast radius: {total_affected} call sites",
            "next_steps": [
                "1. Confirm the scope of requirements",
                "2. Select specific files to modify",
                "3. Use search_l2() to inspect specific functions",
                "4. Use impact_analysis() to confirm the blast radius",
                "5. Begin modifications and remember to update tests",
            ],
        }
