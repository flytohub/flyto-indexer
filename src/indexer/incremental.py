"""
Incremental indexing - only update what changed.

核心邏輯：
1. 讀取舊的 manifest（hash 表）
2. 掃描當前檔案，計算新 hash
3. 比對：hash 一樣 → 跳過，hash 不同 → 重建
4. 更新 manifest
"""

import json
import hashlib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

try:
    from ..models import FileManifest, Symbol, Dependency
except ImportError:
    from models import FileManifest, Symbol, Dependency


@dataclass
class ChangeSet:
    """變更集合"""
    added: list[str]      # 新增的檔案
    modified: list[str]   # 修改的檔案
    deleted: list[str]    # 刪除的檔案

    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.deleted)

    def all_changed(self) -> list[str]:
        return self.added + self.modified

    def summary(self) -> str:
        return f"+{len(self.added)} ~{len(self.modified)} -{len(self.deleted)}"


class ManifestStore:
    """
    Manifest 存儲（指紋表）

    存儲格式：
    {
        "project": "flyto-cloud",
        "version": 1,
        "files": {
            "src/pages/TopUp.vue": {
                "hash": "abc123...",
                "lines": 150,
                "symbols": ["flyto-cloud:src/pages/TopUp.vue:component:TopUp", ...],
                "indexed_at": "2024-01-15T10:30:00"
            }
        }
    }
    """

    def __init__(self, store_path: Path):
        self.store_path = store_path
        self.data = {"project": "", "version": 1, "files": {}}

    def load(self) -> bool:
        """載入 manifest"""
        if self.store_path.exists():
            try:
                self.data = json.loads(self.store_path.read_text())
                return True
            except json.JSONDecodeError:
                return False
        return False

    def save(self):
        """保存 manifest"""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(json.dumps(self.data, indent=2))

    def get_file_hash(self, path: str) -> Optional[str]:
        """取得檔案的舊 hash"""
        if path in self.data["files"]:
            return self.data["files"][path].get("hash")
        return None

    def update_file(self, manifest: FileManifest):
        """更新檔案 manifest"""
        self.data["files"][manifest.path] = manifest.to_dict()

    def remove_file(self, path: str):
        """移除檔案"""
        if path in self.data["files"]:
            del self.data["files"][path]

    def get_all_paths(self) -> set[str]:
        """取得所有已索引的檔案路徑"""
        return set(self.data["files"].keys())

    def set_project(self, project: str):
        self.data["project"] = project


class IncrementalIndexer:
    """
    增量索引器

    只更新變化的檔案，大幅減少重建時間。
    """

    def __init__(self, project_root: Path, index_dir: Path):
        self.project_root = project_root
        self.index_dir = index_dir
        self.manifest_store = ManifestStore(index_dir / "manifest.json")

    def detect_changes(self, current_files: dict[str, str]) -> ChangeSet:
        """
        偵測變更

        Args:
            current_files: {path: content_hash} 當前檔案的 hash 表

        Returns:
            ChangeSet 變更集合
        """
        self.manifest_store.load()

        old_paths = self.manifest_store.get_all_paths()
        new_paths = set(current_files.keys())

        added = []
        modified = []
        deleted = []

        # 新增的檔案
        for path in new_paths - old_paths:
            added.append(path)

        # 刪除的檔案
        for path in old_paths - new_paths:
            deleted.append(path)

        # 修改的檔案（hash 不同）
        for path in new_paths & old_paths:
            old_hash = self.manifest_store.get_file_hash(path)
            new_hash = current_files[path]
            if old_hash != new_hash:
                modified.append(path)

        return ChangeSet(added=added, modified=modified, deleted=deleted)

    def apply_changes(
        self,
        change_set: ChangeSet,
        new_manifests: list[FileManifest],
        new_symbols: list[Symbol],
        new_dependencies: list[Dependency]
    ):
        """
        應用變更到 manifest

        這只更新 manifest，向量庫更新在別處處理。
        """
        # 更新/新增
        for manifest in new_manifests:
            self.manifest_store.update_file(manifest)

        # 刪除
        for path in change_set.deleted:
            self.manifest_store.remove_file(path)

        # 保存
        self.manifest_store.save()

    def get_symbols_to_update(
        self,
        change_set: ChangeSet,
        all_symbols: dict[str, Symbol]
    ) -> tuple[list[str], list[str]]:
        """
        取得需要更新的 symbols

        Returns:
            (to_upsert, to_delete) symbol IDs
        """
        to_upsert = []
        to_delete = []

        # 變更/新增的檔案 → 其 symbols 需要 upsert
        for path in change_set.all_changed():
            for symbol in all_symbols.values():
                if symbol.path == path:
                    to_upsert.append(symbol.id)

        # 刪除的檔案 → 其 symbols 需要刪除
        # 從舊 manifest 取得
        self.manifest_store.load()
        for path in change_set.deleted:
            file_data = self.manifest_store.data["files"].get(path, {})
            symbol_ids = file_data.get("symbols", [])
            to_delete.extend(symbol_ids)

        return to_upsert, to_delete


def compute_file_hash(content: str) -> str:
    """計算檔案 hash"""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def scan_directory_hashes(
    root: Path,
    extensions: list[str],
    ignore_patterns: list[str] = None
) -> dict[str, str]:
    """
    掃描目錄，取得所有檔案的 hash

    Args:
        root: 專案根目錄
        extensions: 要掃描的副檔名
        ignore_patterns: 要忽略的路徑模式

    Returns:
        {relative_path: content_hash}
    """
    ignore_patterns = ignore_patterns or [
        "node_modules", "__pycache__", ".git", "dist", "build",
        ".venv", "venv", ".pytest_cache", ".mypy_cache"
    ]

    result = {}

    for ext in extensions:
        for file_path in root.rglob(f"*{ext}"):
            # 檢查是否需要忽略
            rel_path = file_path.relative_to(root)
            should_ignore = any(
                pattern in str(rel_path)
                for pattern in ignore_patterns
            )
            if should_ignore:
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                result[str(rel_path)] = compute_file_hash(content)
            except Exception:
                # 無法讀取的檔案跳過
                pass

    return result
