#!/usr/bin/env python3
"""
Sync all flyto projects to Qdrant.

同步所有專案到向量庫，讓 AI 可以跨專案搜尋。
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# 載入 .env
def load_dotenv():
    # 嘗試從 flyto-pro 載入
    env_files = [
        Path(__file__).parent.parent / ".env",
        Path("/Library/其他專案/flytohub/flyto-pro/.env"),
    ]
    for env_file in env_files:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
            break

load_dotenv()

# 設定路徑
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))
os.chdir(src_path)

from models import Symbol
from scanner.python import PythonScanner
from scanner.vue import VueScanner
from scanner.base import ScanResult
from indexer.incremental import scan_directory_hashes
from store.sync import SyncManager
from store.vector import VectorStore

# 要同步的專案
FLYTOHUB_ROOT = Path("/Library/其他專案/flytohub")
PROJECTS = [
    ("flyto-core", ["python"]),
    ("flyto-pro", ["python"]),
    ("flyto-cloud", ["python", "vue"]),
    ("flyto-cloud-dev", ["vue"]),
    ("flyto-i18n", []),  # 主要是 JSON，跳過
    ("flyto-landing-page", ["vue"]),
    ("flyto-modules-pro", ["python"]),
    ("flyto-indexer", ["python"]),
]


def scan_project(project_name: str, languages: list[str]) -> list[dict]:
    """掃描專案，返回 symbols"""
    project_path = FLYTOHUB_ROOT / project_name
    if not project_path.exists():
        print(f"  [SKIP] {project_name} not found")
        return []

    # 建立掃描器
    scanners = []
    extensions = []
    if "python" in languages:
        scanners.append(PythonScanner(project_name))
        extensions.extend([".py"])
    if "vue" in languages:
        scanners.append(VueScanner(project_name))
        extensions.extend([".vue"])

    if not scanners:
        print(f"  [SKIP] {project_name} no supported languages")
        return []

    # 掃描檔案
    current_hashes = scan_directory_hashes(
        project_path,
        extensions,
        ignore_patterns=[
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".pytest_cache", ".flyto-index",
            ".nuxt", ".output", "coverage"
        ]
    )

    result = ScanResult()
    for rel_path in current_hashes.keys():
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

    return [s.to_dict() for s in result.symbols]


def main():
    print(f"\n{'='*60}")
    print(f"Flyto Indexer - Sync All Projects")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # 檢查環境
    qdrant_url = os.getenv("QDRANT_URL")
    if not qdrant_url:
        print("ERROR: QDRANT_URL not set")
        return
    print(f"Qdrant: {qdrant_url[:50]}...")

    # 初始化 vector store
    vector_store = VectorStore(collection_name="flyto_code_index")
    init_result = vector_store.init_collection(vector_dim=768)
    print(f"Collection init: {init_result}")

    # 統計
    total_symbols = 0
    total_synced = 0
    results = []

    # 逐個專案同步
    for project_name, languages in PROJECTS:
        print(f"\n[{project_name}]")

        # 掃描
        symbols = scan_project(project_name, languages)
        if not symbols:
            results.append((project_name, 0, 0, "skipped"))
            continue

        print(f"  Scanned: {len(symbols)} symbols")
        total_symbols += len(symbols)

        # 同步
        sync_manager = SyncManager(
            project_name,
            vector_store=vector_store,
        )

        sync_result = sync_manager.sync_symbols(
            symbols,
            incremental=False,  # 全量同步
            show_progress=True,
        )

        synced = sync_result.get("synced", 0)
        failed = sync_result.get("failed", 0)
        total_synced += synced

        status = "ok" if sync_result["ok"] else f"error: {sync_result.get('error')}"
        results.append((project_name, len(symbols), synced, status))
        print(f"  Synced: {synced}, Failed: {failed}")

    # 最終統計
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"{'Project':<25} {'Scanned':>10} {'Synced':>10} {'Status':<20}")
    print(f"{'-'*60}")
    for project, scanned, synced, status in results:
        print(f"{project:<25} {scanned:>10} {synced:>10} {status:<20}")
    print(f"{'-'*60}")
    print(f"{'TOTAL':<25} {total_symbols:>10} {total_synced:>10}")

    # 向量庫統計
    stats = vector_store.get_stats()
    if stats["ok"]:
        print(f"\nQdrant Collection:")
        print(f"  Points: {stats['stats'].get('points_count', 0)}")
        print(f"  Dimension: {stats['stats'].get('vector_dimension', 0)}")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
