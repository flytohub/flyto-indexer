"""
Embedding generation for code indexing.

支援兩種模式：
1. Ollama (本地，免費) - 預設
2. OpenAI (雲端，收費) - 備選

使用 flyto-pro 相同的嵌入策略，確保相容性。
"""

import os
import logging
import hashlib
from typing import Optional
from pathlib import Path
import json

logger = logging.getLogger(__name__)

# 配置
DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_DIMENSION = 768
CACHE_DIR = Path.home() / ".flyto-indexer" / "embedding_cache"


class EmbeddingCache:
    """
    嵌入快取

    避免重複計算相同內容的嵌入
    """

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: dict[str, list[float]] = {}

    def _get_key(self, text: str) -> str:
        """生成快取 key"""
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    def get(self, text: str) -> Optional[list[float]]:
        """從快取取得嵌入"""
        key = self._get_key(text)

        # 先查記憶體快取
        if key in self._memory_cache:
            return self._memory_cache[key]

        # 再查磁碟快取
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                self._memory_cache[key] = data["embedding"]
                return data["embedding"]
            except Exception:
                pass

        return None

    def set(self, text: str, embedding: list[float]):
        """存入快取"""
        key = self._get_key(text)
        self._memory_cache[key] = embedding

        # 同時存磁碟
        cache_file = self.cache_dir / f"{key}.json"
        try:
            cache_file.write_text(json.dumps({
                "text_hash": key,
                "embedding": embedding,
                "dimension": len(embedding),
            }))
        except Exception as e:
            logger.debug(f"Failed to write cache: {e}")

    def clear(self):
        """清除快取"""
        self._memory_cache.clear()
        for f in self.cache_dir.glob("*.json"):
            try:
                f.unlink()
            except Exception:
                pass


class EmbeddingProvider:
    """
    嵌入生成器

    自動選擇可用的 provider（Ollama -> OpenAI）
    """

    def __init__(self, use_cache: bool = True):
        self.cache = EmbeddingCache() if use_cache else None
        self._provider = None
        self._detect_provider()

    def _detect_provider(self):
        """偵測可用的 provider"""
        # 檢查 Ollama
        if not os.getenv("SKIP_OLLAMA", "").lower() in ("1", "true", "yes"):
            if self._check_ollama():
                self._provider = "ollama"
                logger.info("Using Ollama for embeddings")
                return

        # 檢查 OpenAI
        if os.getenv("OPENAI_API_KEY"):
            self._provider = "openai"
            logger.info("Using OpenAI for embeddings")
            return

        logger.warning("No embedding provider available")
        self._provider = None

    def _check_ollama(self) -> bool:
        """檢查 Ollama 是否可用"""
        try:
            import requests
            response = requests.get("http://localhost:11434/api/version", timeout=2)
            return response.status_code == 200
        except Exception:
            return False

    @property
    def is_available(self) -> bool:
        return self._provider is not None

    @property
    def dimension(self) -> int:
        """取得嵌入維度"""
        if self._provider == "ollama":
            return 768  # nomic-embed-text
        elif self._provider == "openai":
            return 768  # text-embedding-3-small with dimensions=768
        return 0

    def embed(self, text: str) -> dict:
        """
        生成單個文本的嵌入

        Returns:
            {
                "ok": bool,
                "embedding": list[float] or None,
                "dimension": int,
                "provider": str,
                "cached": bool,
                "error": str or None
            }
        """
        # 檢查快取
        if self.cache:
            cached = self.cache.get(text)
            if cached:
                return {
                    "ok": True,
                    "embedding": cached,
                    "dimension": len(cached),
                    "provider": self._provider,
                    "cached": True,
                    "error": None,
                }

        # 生成嵌入
        if not self._provider:
            return {
                "ok": False,
                "embedding": None,
                "dimension": 0,
                "provider": None,
                "cached": False,
                "error": "No embedding provider available",
            }

        try:
            if self._provider == "ollama":
                embedding = self._embed_ollama(text)
            else:
                embedding = self._embed_openai(text)

            if embedding:
                if self.cache:
                    self.cache.set(text, embedding)

                return {
                    "ok": True,
                    "embedding": embedding,
                    "dimension": len(embedding),
                    "provider": self._provider,
                    "cached": False,
                    "error": None,
                }
            else:
                return {
                    "ok": False,
                    "embedding": None,
                    "dimension": 0,
                    "provider": self._provider,
                    "cached": False,
                    "error": "Failed to generate embedding",
                }

        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return {
                "ok": False,
                "embedding": None,
                "dimension": 0,
                "provider": self._provider,
                "cached": False,
                "error": str(e),
            }

    def embed_batch(self, texts: list[str], show_progress: bool = False) -> dict:
        """
        批量生成嵌入

        Returns:
            {
                "ok": bool,
                "embeddings": list[list[float] or None],
                "success_count": int,
                "failed_indices": list[int],
                "error": str or None
            }
        """
        embeddings = []
        failed_indices = []

        iterator = enumerate(texts)
        if show_progress:
            try:
                from tqdm import tqdm
                iterator = tqdm(list(iterator), desc="Embedding")
            except ImportError:
                pass

        for i, text in iterator:
            result = self.embed(text)
            if result["ok"]:
                embeddings.append(result["embedding"])
            else:
                embeddings.append(None)
                failed_indices.append(i)

        return {
            "ok": len(failed_indices) == 0,
            "embeddings": embeddings,
            "success_count": len(texts) - len(failed_indices),
            "failed_indices": failed_indices,
            "error": f"Failed {len(failed_indices)} embeddings" if failed_indices else None,
        }

    def _embed_ollama(self, text: str) -> Optional[list[float]]:
        """使用 Ollama 生成嵌入"""
        import requests

        try:
            response = requests.post(
                "http://localhost:11434/api/embeddings",
                json={"model": DEFAULT_MODEL, "prompt": text},
                timeout=30,
            )
            if response.status_code == 200:
                return response.json().get("embedding")
        except Exception as e:
            logger.error(f"Ollama embedding failed: {e}")
        return None

    def _embed_openai(self, text: str) -> Optional[list[float]]:
        """使用 OpenAI 生成嵌入"""
        try:
            from openai import OpenAI

            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=text,
                dimensions=768,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"OpenAI embedding failed: {e}")
        return None


def create_symbol_text(symbol: dict) -> str:
    """
    將 symbol 轉換成適合嵌入的文本

    格式：
    [type] name
    path: xxx
    summary: xxx
    content (truncated)
    """
    parts = []

    # 類型和名稱
    parts.append(f"[{symbol.get('type', 'unknown')}] {symbol.get('name', '')}")

    # 路徑
    if symbol.get('path'):
        parts.append(f"path: {symbol['path']}")

    # 摘要
    if symbol.get('summary'):
        parts.append(f"summary: {symbol['summary']}")

    # 內容（截斷到合理長度）
    content = symbol.get('content', '')
    if content:
        # 最多 500 字符
        if len(content) > 500:
            content = content[:500] + "..."
        parts.append(f"code:\n{content}")

    return "\n".join(parts)
