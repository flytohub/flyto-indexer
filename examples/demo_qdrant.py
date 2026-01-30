#!/usr/bin/env python3
"""
Demo script for Qdrant integration.

使用方式：
    # 確保設定環境變數
    export QDRANT_URL="https://xxx.cloud.qdrant.io:6333"
    export QDRANT_API_KEY="your-api-key"

    # 或使用 .env 檔案
    python demo_qdrant.py /path/to/project
"""

import sys
import os
from pathlib import Path

# 載入 .env（如果有）
def load_dotenv():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

load_dotenv()

# 設定路徑
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))
os.chdir(src_path)

# Import
from models import ProjectIndex, Symbol, SymbolType
from scanner.python import PythonScanner
from scanner.vue import VueScanner
from scanner.base import ScanResult
from indexer.incremental import scan_directory_hashes
from store.embedding import EmbeddingProvider, create_symbol_text
from store.vector import VectorStore
from store.sync import SyncManager


def main():
    if len(sys.argv) < 2:
        print("Usage: python demo_qdrant.py <project_path>")
        print("\nEnvironment variables required:")
        print("  QDRANT_URL - Qdrant cloud URL")
        print("  QDRANT_API_KEY - Qdrant API key")
        print("\nOptional:")
        print("  OPENAI_API_KEY - OpenAI API key (if Ollama not available)")
        return

    project_path = Path(sys.argv[1]).resolve()
    if not project_path.exists():
        print(f"Error: Path does not exist: {project_path}")
        return

    project_name = project_path.name

    print(f"\n{'='*60}")
    print(f"Flyto Indexer - Qdrant Demo")
    print(f"Project: {project_name}")
    print(f"Path: {project_path}")
    print(f"{'='*60}\n")

    # 1. 檢查環境
    print("[1/5] Checking environment...")
    qdrant_url = os.getenv("QDRANT_URL")
    if not qdrant_url:
        print("  ERROR: QDRANT_URL not set")
        return
    print(f"  Qdrant URL: {qdrant_url[:50]}...")

    # 2. 檢查嵌入 provider
    print("\n[2/5] Checking embedding provider...")
    embedding = EmbeddingProvider(use_cache=True)
    if not embedding.is_available:
        print("  ERROR: No embedding provider available")
        print("  - Ollama: not running or SKIP_OLLAMA=1")
        print("  - OpenAI: OPENAI_API_KEY not set")
        return
    print(f"  Provider: {embedding._provider}")
    print(f"  Dimension: {embedding.dimension}")

    # 3. 掃描專案
    print("\n[3/5] Scanning project...")
    scanners = [PythonScanner(project_name), VueScanner(project_name)]
    extensions = [ext for s in scanners for ext in s.supported_extensions]

    current_hashes = scan_directory_hashes(
        project_path,
        extensions,
        ignore_patterns=["node_modules", "__pycache__", ".git", "dist", "build", ".venv"]
    )

    result = ScanResult()
    for rel_path in list(current_hashes.keys())[:50]:  # 限制 50 個檔案 demo
        file_path = project_path / rel_path
        if not file_path.exists():
            continue

        scanner = None
        for s in scanners:
            if s.can_scan(file_path):
                scanner = s
                break
        if not scanner:
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
            symbols, deps = scanner.scan_file(Path(rel_path), content)
            manifest = scanner.create_file_manifest(Path(rel_path), content, symbols)
            result.add_file_result(symbols, deps, manifest)
        except Exception as e:
            result.add_error(rel_path, str(e))

    print(f"  Files scanned: {len(result.manifests)}")
    print(f"  Symbols found: {len(result.symbols)}")

    if not result.symbols:
        print("  No symbols to sync")
        return

    # 4. 同步到 Qdrant
    print("\n[4/5] Syncing to Qdrant...")
    sync_manager = SyncManager(project_name)

    # 轉換 symbols 為 dict 格式
    symbols_dict = [s.to_dict() for s in result.symbols]

    sync_result = sync_manager.sync_symbols(
        symbols_dict,
        incremental=False,  # demo 用全量
        show_progress=True,
    )

    print(f"  Synced: {sync_result.get('synced', 0)}")
    print(f"  Failed: {sync_result.get('failed', 0)}")
    if sync_result.get("error"):
        print(f"  Error: {sync_result['error']}")

    # 5. 測試搜尋
    print("\n[5/5] Testing search...")

    # 取第一個 symbol 的名稱作為搜尋
    first_symbol = result.symbols[0]
    query = f"function {first_symbol.name}"

    print(f"  Query: '{query}'")
    search_result = sync_manager.search(query, limit=5)

    if search_result["ok"]:
        print(f"  Results: {len(search_result['results'])}")
        for r in search_result["results"][:3]:
            print(f"    - [{r['type']}] {r['name']} (score: {r['score']})")
            print(f"      path: {r['path']}")
    else:
        print(f"  Search failed: {search_result['error']}")

    # 顯示統計
    print("\n" + "="*60)
    stats = sync_manager.get_stats()
    if stats["ok"]:
        print(f"Collection stats:")
        print(f"  Points: {stats['stats'].get('points_count', 0)}")
        print(f"  Dimension: {stats['stats'].get('vector_dimension', 0)}")
    print("="*60)


if __name__ == "__main__":
    main()
