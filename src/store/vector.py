"""
Vector store for code symbols.

將 symbols 存入 Qdrant，支援語義搜尋。

Collection 結構：
- collection: flyto_code_index
- vector: 768 維 (nomic-embed-text / OpenAI)
- payload:
  - symbol_id: str (唯一 ID)
  - project: str
  - path: str
  - type: str (function/class/component/...)
  - name: str
  - summary: str
  - start_line: int
  - end_line: int
  - content_hash: str
  - indexed_at: str (ISO timestamp)
"""

import os
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# 預設配置
DEFAULT_COLLECTION = "flyto_code_index"
DEFAULT_VECTOR_DIM = 768


class VectorStore:
    """
    向量存儲

    管理 Qdrant 連線和 CRUD 操作
    """

    def __init__(
        self,
        collection_name: str = DEFAULT_COLLECTION,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.collection_name = collection_name
        self.url = url or os.getenv("QDRANT_URL")
        self.api_key = api_key or os.getenv("QDRANT_API_KEY")
        self._client = None

    @property
    def client(self):
        """取得 Qdrant client（lazy init）"""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self):
        """建立 Qdrant client"""
        if not self.url:
            raise ValueError("QDRANT_URL not set")

        try:
            from qdrant_client import QdrantClient

            # 判斷是否為雲端 Qdrant
            if "cloud.qdrant.io" in self.url or self.api_key:
                client = QdrantClient(
                    url=self.url,
                    api_key=self.api_key,
                    timeout=60,
                )
            else:
                # 本地 Qdrant
                client = QdrantClient(url=self.url, timeout=60)

            logger.info(f"Connected to Qdrant: {self.url}")
            return client

        except Exception as e:
            logger.error(f"Failed to connect to Qdrant: {e}")
            raise

    def init_collection(self, vector_dim: int = DEFAULT_VECTOR_DIM) -> dict:
        """
        初始化 collection

        如果已存在則跳過
        """
        try:
            from qdrant_client.models import Distance, VectorParams

            collections = self.client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)

            if exists:
                logger.info(f"Collection {self.collection_name} already exists")
                return {"ok": True, "created": False}

            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=vector_dim,
                    distance=Distance.COSINE,
                ),
            )

            # 建立 payload 索引
            self._create_indexes()

            logger.info(f"Created collection: {self.collection_name}")
            return {"ok": True, "created": True}

        except Exception as e:
            logger.error(f"Failed to init collection: {e}")
            return {"ok": False, "error": str(e)}

    def _create_indexes(self):
        """建立 payload 索引"""
        try:
            from qdrant_client.models import PayloadSchemaType

            indexes = [
                ("project", PayloadSchemaType.KEYWORD),
                ("path", PayloadSchemaType.KEYWORD),
                ("type", PayloadSchemaType.KEYWORD),
                ("name", PayloadSchemaType.KEYWORD),
            ]

            for field, schema_type in indexes:
                try:
                    self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name=field,
                        field_schema=schema_type,
                    )
                except Exception:
                    pass  # 索引可能已存在

        except Exception as e:
            logger.warning(f"Failed to create indexes: {e}")

    def upsert_symbol(
        self,
        symbol_id: str,
        vector: list[float],
        payload: dict,
    ) -> dict:
        """
        插入或更新單個 symbol

        Args:
            symbol_id: Symbol ID（會轉成 hash int）
            vector: 嵌入向量
            payload: 元數據

        Returns:
            {"ok": bool, "point_id": int, "error": str or None}
        """
        try:
            from qdrant_client.models import PointStruct

            # 將 symbol_id 轉成 int（Qdrant 要求）
            point_id = self._symbol_id_to_int(symbol_id)

            # 添加時間戳
            payload["indexed_at"] = datetime.now().isoformat()
            payload["symbol_id"] = symbol_id

            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload,
                    )
                ],
            )

            return {"ok": True, "point_id": point_id, "error": None}

        except Exception as e:
            logger.error(f"Failed to upsert symbol: {e}")
            return {"ok": False, "point_id": 0, "error": str(e)}

    def upsert_batch(
        self,
        items: list[tuple[str, list[float], dict]],
        batch_size: int = 100,
        show_progress: bool = False,
    ) -> dict:
        """
        批量插入 symbols

        Args:
            items: [(symbol_id, vector, payload), ...]
            batch_size: 每批大小
            show_progress: 是否顯示進度

        Returns:
            {"ok": bool, "upserted": int, "failed": int, "error": str or None}
        """
        try:
            from qdrant_client.models import PointStruct

            points = []
            for symbol_id, vector, payload in items:
                if vector is None:
                    continue

                point_id = self._symbol_id_to_int(symbol_id)
                payload["indexed_at"] = datetime.now().isoformat()
                payload["symbol_id"] = symbol_id

                points.append(PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                ))

            # 分批上傳
            total_upserted = 0
            batches = [points[i:i + batch_size] for i in range(0, len(points), batch_size)]

            iterator = batches
            if show_progress:
                try:
                    from tqdm import tqdm
                    iterator = tqdm(batches, desc="Uploading to Qdrant")
                except ImportError:
                    pass

            for batch in iterator:
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=batch,
                )
                total_upserted += len(batch)

            failed = len(items) - total_upserted
            return {
                "ok": failed == 0,
                "upserted": total_upserted,
                "failed": failed,
                "error": None if failed == 0 else f"{failed} items failed",
            }

        except Exception as e:
            logger.error(f"Failed to batch upsert: {e}")
            return {"ok": False, "upserted": 0, "failed": len(items), "error": str(e)}

    def search(
        self,
        query_vector: list[float],
        limit: int = 10,
        filter_project: Optional[str] = None,
        filter_type: Optional[str] = None,
        filter_path: Optional[str] = None,
        score_threshold: float = 0.5,
    ) -> dict:
        """
        語義搜尋

        Args:
            query_vector: 查詢向量
            limit: 最大結果數
            filter_project: 過濾專案
            filter_type: 過濾類型
            filter_path: 過濾路徑（前綴匹配）
            score_threshold: 最低分數閾值

        Returns:
            {
                "ok": bool,
                "results": [{"id": str, "score": float, "payload": dict}, ...],
                "error": str or None
            }
        """
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            # 建立過濾條件
            conditions = []
            if filter_project:
                conditions.append(
                    FieldCondition(key="project", match=MatchValue(value=filter_project))
                )
            if filter_type:
                conditions.append(
                    FieldCondition(key="type", match=MatchValue(value=filter_type))
                )

            search_filter = Filter(must=conditions) if conditions else None

            # 執行搜尋
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=search_filter,
                limit=limit,
                score_threshold=score_threshold,
            ).points

            # 格式化結果
            formatted = []
            for point in results:
                # 如果有路徑過濾，在這裡做前綴匹配
                if filter_path:
                    point_path = point.payload.get("path", "")
                    if not point_path.startswith(filter_path):
                        continue

                formatted.append({
                    "id": point.payload.get("symbol_id", str(point.id)),
                    "score": point.score,
                    "payload": point.payload,
                })

            return {"ok": True, "results": formatted, "error": None}

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return {"ok": False, "results": [], "error": str(e)}

    def delete_by_project(self, project: str) -> dict:
        """刪除專案的所有 symbols"""
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            self.client.delete(
                collection_name=self.collection_name,
                points_selector=Filter(
                    must=[FieldCondition(key="project", match=MatchValue(value=project))]
                ),
            )

            return {"ok": True, "error": None}

        except Exception as e:
            logger.error(f"Delete failed: {e}")
            return {"ok": False, "error": str(e)}

    def delete_by_path(self, project: str, path: str) -> dict:
        """刪除特定檔案的 symbols"""
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            self.client.delete(
                collection_name=self.collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(key="project", match=MatchValue(value=project)),
                        FieldCondition(key="path", match=MatchValue(value=path)),
                    ]
                ),
            )

            return {"ok": True, "error": None}

        except Exception as e:
            logger.error(f"Delete failed: {e}")
            return {"ok": False, "error": str(e)}

    def get_stats(self) -> dict:
        """取得 collection 統計"""
        try:
            info = self.client.get_collection(self.collection_name)

            return {
                "ok": True,
                "stats": {
                    "points_count": info.points_count,
                    "vector_dimension": info.config.params.vectors.size,
                    "distance": str(info.config.params.vectors.distance),
                },
                "error": None,
            }

        except Exception as e:
            logger.error(f"Get stats failed: {e}")
            return {"ok": False, "stats": {}, "error": str(e)}

    def _symbol_id_to_int(self, symbol_id: str) -> int:
        """將 symbol ID 轉成 int（用於 Qdrant point ID）"""
        import hashlib
        # 取 hash 的前 16 位轉 int
        hash_hex = hashlib.sha256(symbol_id.encode()).hexdigest()[:16]
        return int(hash_hex, 16)
