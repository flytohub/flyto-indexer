"""
Main indexing engine - orchestrates the entire flow.

使用流程：
1. engine.scan() - 掃描專案，建立索引
2. engine.impact(symbol_id) - 查詢影響範圍
3. engine.context(query) - 取得相關上下文（L0→L1→L2）
"""

import json
from pathlib import Path
from typing import Optional
from datetime import datetime

from .models import ProjectIndex, Symbol, Dependency, FileManifest, SymbolType
from .scanner import PythonScanner, VueScanner, TypeScriptScanner, ScanResult
from .indexer import IncrementalIndexer, scan_directory_hashes, compute_file_hash
from .context.loader import ContextLoader, L0Context, L1Context, L2Context


class IndexEngine:
    """
    索引引擎

    主要功能：
    1. scan() - 掃描專案，增量更新索引
    2. impact() - 查詢影響範圍
    3. context() - 取得上下文（由淺入深）
    """

    def __init__(
        self,
        project_name: str,
        project_root: Path,
        index_dir: Optional[Path] = None
    ):
        self.project_name = project_name
        self.project_root = Path(project_root)
        self.index_dir = index_dir or (self.project_root / ".flyto-index")

        # 初始化組件
        self.scanners = [
            PythonScanner(project_name),
            VueScanner(project_name),
            TypeScriptScanner(project_name),
        ]
        self.incremental = IncrementalIndexer(self.project_root, self.index_dir)

        # 載入或初始化索引
        self.index = self._load_or_create_index()

    def scan(self, incremental: bool = True) -> dict:
        """
        掃描專案，建立/更新索引

        Args:
            incremental: 是否增量更新（只更新變化的檔案）

        Returns:
            掃描結果摘要
        """
        # 收集所有支援的副檔名
        extensions = []
        for scanner in self.scanners:
            extensions.extend(scanner.supported_extensions)

        # 掃描目錄取得所有檔案 hash
        current_hashes = scan_directory_hashes(
            self.project_root,
            extensions,
            ignore_patterns=[
                "node_modules", "__pycache__", ".git", "dist", "build",
                ".venv", "venv", ".pytest_cache", ".flyto-index"
            ]
        )

        # 偵測變更
        if incremental:
            changes = self.incremental.detect_changes(current_hashes)
            files_to_scan = changes.all_changed()
        else:
            # 全量重建
            changes = None
            files_to_scan = list(current_hashes.keys())
            self.index = self._create_empty_index()

        # 掃描檔案
        result = ScanResult()
        for rel_path in files_to_scan:
            file_path = self.project_root / rel_path
            if not file_path.exists():
                continue

            # 找到對應的 scanner
            scanner = self._get_scanner(file_path)
            if not scanner:
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                symbols, deps = scanner.scan_file(Path(rel_path), content)
                manifest = scanner.create_file_manifest(Path(rel_path), content, symbols)
                result.add_file_result(symbols, deps, manifest)
            except Exception as e:
                result.add_error(rel_path, str(e))

        # 更新索引
        self._update_index(result, changes)

        # 保存索引
        self._save_index()

        # 應用增量變更到 manifest
        if incremental and changes:
            self.incremental.apply_changes(
                changes,
                result.manifests,
                result.symbols,
                result.dependencies
            )

        return {
            "project": self.project_name,
            "files_scanned": len(files_to_scan),
            "symbols_found": len(result.symbols),
            "dependencies_found": len(result.dependencies),
            "errors": len(result.errors),
            "changes": changes.summary() if changes else "full rebuild",
        }

    def impact(self, symbol_id: str, max_depth: int = 3) -> dict:
        """
        查詢影響範圍

        改了這個 symbol，會影響哪些其他 symbols

        Args:
            symbol_id: Symbol ID（完整或短格式）
            max_depth: 最大追溯深度

        Returns:
            影響鏈結構
        """
        # 嘗試補全 symbol_id
        full_id = self._resolve_symbol_id(symbol_id)
        if not full_id:
            return {"error": f"Symbol not found: {symbol_id}"}

        # 取得影響鏈
        chain = self.index.get_impact_chain(full_id, max_depth)

        # 加上 symbol 資訊
        result = {
            "symbol": full_id,
            "symbol_info": self.index.symbols[full_id].to_dict() if full_id in self.index.symbols else None,
            "impact_chain": [],
        }

        for level in chain["levels"]:
            level_info = {
                "depth": level["depth"],
                "affected": [],
            }
            for sid in level["symbols"]:
                if sid in self.index.symbols:
                    s = self.index.symbols[sid]
                    level_info["affected"].append({
                        "id": sid,
                        "path": s.path,
                        "type": s.symbol_type.value,
                        "name": s.name,
                    })
            result["impact_chain"].append(level_info)

        return result

    def context(
        self,
        query: Optional[str] = None,
        paths: Optional[list[str]] = None,
        symbols: Optional[list[str]] = None,
        level: str = "auto"
    ) -> dict:
        """
        取得上下文（由淺入深）

        Args:
            query: 自然語言查詢（會用 L0 定位，再取 L1/L2）
            paths: 指定檔案路徑（直接取 L1）
            symbols: 指定 symbol IDs（直接取 L2）
            level: "l0", "l1", "l2", 或 "auto"

        Returns:
            上下文內容
        """
        loader = ContextLoader(self.index)

        # L0 永遠先載入（作為定位用）
        l0 = loader.load_l0()

        if level == "l0" or (level == "auto" and not query and not paths and not symbols):
            return {
                "level": "l0",
                "content": l0.to_text(),
                "token_estimate": l0.token_estimate(),
            }

        # 如果有指定 symbols，直接取 L2
        if symbols:
            l2_list = []
            for sid in symbols:
                full_id = self._resolve_symbol_id(sid)
                if full_id:
                    l2 = loader.load_l2(full_id)
                    if l2:
                        l2_list.append(l2)
            return {
                "level": "l2",
                "content": "\n\n---\n\n".join(l2.to_text() for l2 in l2_list),
                "symbols": [l2.symbol_id for l2 in l2_list],
            }

        # 如果有指定 paths，取 L1
        if paths:
            l1_list = []
            for path in paths:
                l1 = loader.load_l1(path)
                if l1:
                    l1_list.append(l1)
            return {
                "level": "l1",
                "content": "\n\n---\n\n".join(l1.to_text() for l1 in l1_list),
                "files": [l1.path for l1 in l1_list],
            }

        # 如果有 query，用 L0 定位後取 L2
        if query:
            l2_list = loader.load_l2_by_query(query, top_k=5)
            return {
                "level": "l2",
                "query": query,
                "content": "\n\n---\n\n".join(l2.to_text() for l2 in l2_list if l2),
                "symbols": [l2.symbol_id for l2 in l2_list if l2],
                "l0_summary": f"Project has {len(self.index.files)} files, {len(self.index.symbols)} symbols",
            }

        return {"error": "No query, paths, or symbols provided"}

    def outline(self) -> str:
        """
        生成專案大綱（L0）文字版

        這是給 AI 看的，用來快速定位
        """
        loader = ContextLoader(self.index)
        l0 = loader.load_l0()
        return l0.to_text()

    def _get_scanner(self, file_path: Path):
        """取得對應的 scanner"""
        for scanner in self.scanners:
            if scanner.can_scan(file_path):
                return scanner
        return None

    def _resolve_symbol_id(self, symbol_id: str) -> Optional[str]:
        """解析 symbol ID（支援短格式）"""
        # 完整格式
        if symbol_id in self.index.symbols:
            return symbol_id

        # 短格式：補上 project
        full_id = f"{self.project_name}:{symbol_id}"
        if full_id in self.index.symbols:
            return full_id

        # 模糊匹配
        for sid in self.index.symbols:
            if sid.endswith(symbol_id):
                return sid

        return None

    def _load_or_create_index(self) -> ProjectIndex:
        """載入或建立索引"""
        index_file = self.index_dir / "index.json"
        if index_file.exists():
            try:
                data = json.loads(index_file.read_text())
                return self._deserialize_index(data)
            except Exception:
                pass
        return self._create_empty_index()

    def _create_empty_index(self) -> ProjectIndex:
        """建立空索引"""
        return ProjectIndex(
            project=self.project_name,
            root_path=str(self.project_root),
        )

    def _update_index(self, result: ScanResult, changes=None):
        """更新索引"""
        # 如果是增量更新，先刪除變化檔案的舊資料
        if changes:
            for path in changes.all_changed() + changes.deleted:
                # 刪除舊 symbols
                to_remove = [
                    sid for sid in self.index.symbols
                    if self.index.symbols[sid].path == path
                ]
                for sid in to_remove:
                    del self.index.symbols[sid]

                # 刪除舊 dependencies
                to_remove = [
                    did for did, dep in self.index.dependencies.items()
                    if dep.source_id.startswith(f"{self.project_name}:{path}:")
                ]
                for did in to_remove:
                    del self.index.dependencies[did]

                # 刪除舊 manifest
                if path in self.index.files:
                    del self.index.files[path]

        # 新增新資料
        for symbol in result.symbols:
            self.index.symbols[symbol.id] = symbol

        for dep in result.dependencies:
            self.index.dependencies[dep.id] = dep

        for manifest in result.manifests:
            self.index.files[manifest.path] = manifest

    def _save_index(self):
        """保存索引"""
        self.index_dir.mkdir(parents=True, exist_ok=True)
        index_file = self.index_dir / "index.json"

        data = {
            "project": self.index.project,
            "root_path": self.index.root_path,
            "indexed_at": datetime.now().isoformat(),
            "files": {k: v.to_dict() for k, v in self.index.files.items()},
            "symbols": {k: v.to_dict() for k, v in self.index.symbols.items()},
            "dependencies": {k: v.to_dict() for k, v in self.index.dependencies.items()},
            "entry_points": self.index.entry_points,
            "routes": self.index.routes,
            "api_endpoints": self.index.api_endpoints,
        }

        index_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _deserialize_index(self, data: dict) -> ProjectIndex:
        """從 JSON 還原索引"""
        index = ProjectIndex(
            project=data["project"],
            root_path=data["root_path"],
            entry_points=data.get("entry_points", []),
            routes=data.get("routes", {}),
            api_endpoints=data.get("api_endpoints", []),
        )

        # 還原 files
        for path, fdata in data.get("files", {}).items():
            index.files[path] = FileManifest(
                path=fdata["path"],
                content_hash=fdata["hash"],
                line_count=fdata["lines"],
                symbols=fdata.get("symbols", []),
                last_indexed=fdata.get("indexed_at", ""),
            )

        # 還原 symbols
        for sid, sdata in data.get("symbols", {}).items():
            index.symbols[sid] = Symbol(
                project=sdata["project"],
                path=sdata["path"],
                symbol_type=SymbolType(sdata["type"]),
                name=sdata["name"],
                start_line=sdata.get("start_line", 0),
                end_line=sdata.get("end_line", 0),
                content=sdata.get("content", ""),
                content_hash=sdata.get("content_hash", ""),
                summary=sdata.get("summary", ""),
                language=sdata.get("language", ""),
                exports=sdata.get("exports", []),
                imports=sdata.get("imports", []),
            )

        # 還原 dependencies
        for did, ddata in data.get("dependencies", {}).items():
            from .models import DependencyType
            index.dependencies[did] = Dependency(
                source_id=ddata["source"],
                target_id=ddata["target"],
                dep_type=DependencyType(ddata["type"]),
                source_line=ddata.get("line", 0),
                metadata=ddata.get("metadata", {}),
            )

        return index
