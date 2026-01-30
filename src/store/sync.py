"""
Sync manager - synchronize local index to Qdrant.

負責：
1. 增量同步（只同步變化的 symbols）
2. 全量同步（重建整個向量索引）
3. 檢查同步狀態
"""

import logging
from typing import Optional
from pathlib import Path
from datetime import datetime

from .embedding import EmbeddingProvider, create_symbol_text
from .vector import VectorStore

logger = logging.getLogger(__name__)


class SyncManager:
    """
    同步管理器

    將本地索引同步到 Qdrant 向量庫
    """

    def __init__(
        self,
        project_name: str,
        vector_store: Optional[VectorStore] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ):
        self.project_name = project_name
        self.vector_store = vector_store or VectorStore()
        self.embedding = embedding_provider or EmbeddingProvider()

    def sync_symbols(
        self,
        symbols: list[dict],
        incremental: bool = True,
        changed_paths: Optional[list[str]] = None,
        show_progress: bool = True,
    ) -> dict:
        """
        同步 symbols 到向量庫

        Args:
            symbols: Symbol 列表（dict 格式）
            incremental: 是否增量同步
            changed_paths: 變更的檔案路徑（增量用）
            show_progress: 是否顯示進度

        Returns:
            {
                "ok": bool,
                "synced": int,
                "skipped": int,
                "failed": int,
                "error": str or None
            }
        """
        # 檢查 provider
        if not self.embedding.is_available:
            return {
                "ok": False,
                "synced": 0,
                "skipped": 0,
                "failed": 0,
                "error": "No embedding provider available",
            }

        # 初始化 collection
        init_result = self.vector_store.init_collection(
            vector_dim=self.embedding.dimension
        )
        if not init_result["ok"]:
            return {
                "ok": False,
                "synced": 0,
                "skipped": 0,
                "failed": 0,
                "error": f"Failed to init collection: {init_result.get('error')}",
            }

        # 過濾要同步的 symbols
        if incremental and changed_paths:
            symbols_to_sync = [
                s for s in symbols
                if s.get("path") in changed_paths
            ]
            skipped = len(symbols) - len(symbols_to_sync)

            # 刪除變更檔案的舊 symbols
            for path in changed_paths:
                self.vector_store.delete_by_path(self.project_name, path)
        else:
            symbols_to_sync = symbols
            skipped = 0

            # 全量同步：先刪除專案的所有 symbols
            self.vector_store.delete_by_project(self.project_name)

        if not symbols_to_sync:
            return {
                "ok": True,
                "synced": 0,
                "skipped": skipped,
                "failed": 0,
                "error": None,
            }

        # 生成嵌入
        logger.info(f"Generating embeddings for {len(symbols_to_sync)} symbols...")
        texts = [create_symbol_text(s) for s in symbols_to_sync]
        embed_result = self.embedding.embed_batch(texts, show_progress=show_progress)

        if not embed_result["ok"]:
            logger.warning(f"Some embeddings failed: {embed_result['error']}")

        # 準備上傳資料
        items = []
        for i, symbol in enumerate(symbols_to_sync):
            embedding = embed_result["embeddings"][i]
            if embedding is None:
                continue

            symbol_id = symbol.get("id") or f"{self.project_name}:{symbol['path']}:{symbol['type']}:{symbol['name']}"

            payload = {
                "project": self.project_name,
                "path": symbol.get("path", ""),
                "type": symbol.get("type", ""),
                "name": symbol.get("name", ""),
                "summary": symbol.get("summary", ""),
                "start_line": symbol.get("start_line", 0),
                "end_line": symbol.get("end_line", 0),
                "content_hash": symbol.get("content_hash", ""),
            }

            items.append((symbol_id, embedding, payload))

        # 批量上傳
        logger.info(f"Uploading {len(items)} symbols to Qdrant...")
        upload_result = self.vector_store.upsert_batch(
            items,
            show_progress=show_progress,
        )

        return {
            "ok": upload_result["ok"],
            "synced": upload_result["upserted"],
            "skipped": skipped,
            "failed": len(symbols_to_sync) - upload_result["upserted"],
            "error": upload_result.get("error"),
        }

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_type: Optional[str] = None,
        filter_path: Optional[str] = None,
        score_threshold: float = 0.5,
    ) -> dict:
        """
        語義搜尋 symbols

        Args:
            query: 自然語言查詢
            limit: 最大結果數
            filter_type: 過濾類型（function/class/component...）
            filter_path: 過濾路徑前綴
            score_threshold: 最低分數

        Returns:
            {
                "ok": bool,
                "results": [
                    {
                        "id": str,
                        "score": float,
                        "path": str,
                        "type": str,
                        "name": str,
                        "summary": str,
                        "line": int,
                    },
                    ...
                ],
                "error": str or None
            }
        """
        # 生成查詢嵌入
        embed_result = self.embedding.embed(query)
        if not embed_result["ok"]:
            return {
                "ok": False,
                "results": [],
                "error": f"Failed to embed query: {embed_result['error']}",
            }

        # 搜尋
        search_result = self.vector_store.search(
            query_vector=embed_result["embedding"],
            limit=limit,
            filter_project=self.project_name,
            filter_type=filter_type,
            filter_path=filter_path,
            score_threshold=score_threshold,
        )

        if not search_result["ok"]:
            return search_result

        # 格式化結果
        results = []
        for item in search_result["results"]:
            payload = item["payload"]
            results.append({
                "id": item["id"],
                "score": round(item["score"], 4),
                "path": payload.get("path", ""),
                "type": payload.get("type", ""),
                "name": payload.get("name", ""),
                "summary": payload.get("summary", ""),
                "line": payload.get("start_line", 0),
            })

        return {"ok": True, "results": results, "error": None}

    def get_stats(self) -> dict:
        """取得同步狀態"""
        return self.vector_store.get_stats()


def sync_index_to_qdrant(
    index_file: Path,
    project_name: Optional[str] = None,
    incremental: bool = True,
    show_progress: bool = True,
) -> dict:
    """
    將本地索引檔同步到 Qdrant

    Args:
        index_file: 索引檔路徑（.flyto-index/index.json）
        project_name: 專案名稱（預設從索引取）
        incremental: 是否增量同步
        show_progress: 是否顯示進度

    Returns:
        同步結果
    """
    import json

    if not index_file.exists():
        return {"ok": False, "error": f"Index file not found: {index_file}"}

    try:
        data = json.loads(index_file.read_text())
    except Exception as e:
        return {"ok": False, "error": f"Failed to load index: {e}"}

    project = project_name or data.get("project", "unknown")

    # 取得 symbols
    symbols = list(data.get("symbols", {}).values())
    if not symbols:
        return {"ok": True, "synced": 0, "skipped": 0, "failed": 0, "error": None}

    # 建立 sync manager
    sync_manager = SyncManager(project)

    # 執行同步
    return sync_manager.sync_symbols(
        symbols,
        incremental=incremental,
        show_progress=show_progress,
    )
