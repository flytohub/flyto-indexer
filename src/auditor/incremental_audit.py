"""
增量審計 - 只審計有變動的檔案

流程：
1. 載入現有 PROJECT_MAP
2. 計算所有檔案的 hash
3. 比對：找出新增、修改、刪除的檔案
4. 只審計新增和修改的檔案
5. 更新 PROJECT_MAP
"""

import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def file_hash(path: Path) -> str:
    """計算檔案 hash"""
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()[:16]


class IncrementalAuditor:
    """增量審計器"""

    def __init__(
        self,
        project_root: Path,
        index_dir: Path,
        extensions: list[str] = None,
        ignore_patterns: list[str] = None,
    ):
        self.project_root = project_root
        self.index_dir = index_dir
        self.extensions = extensions or [".py", ".vue", ".ts", ".tsx", ".js"]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".pytest_cache", ".flyto-index",
            ".nuxt", ".output", "coverage", "test", "tests",
            "__init__.py", "conftest.py"
        ]

        # 載入現有索引
        self.project_map_path = index_dir / "PROJECT_MAP.json"
        self.manifest_path = index_dir / "manifest.json"
        self.project_map = self._load_json(self.project_map_path)
        self.manifest = self._load_json(self.manifest_path)

    def _load_json(self, path: Path) -> dict:
        if path.exists():
            return json.loads(path.read_text())
        return {}

    def _save_json(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _should_skip(self, path: str) -> bool:
        for pattern in self.ignore_patterns:
            if pattern in path:
                return True
        return False

    def scan_files(self) -> dict[str, str]:
        """掃描所有檔案，返回 {path: hash}"""
        files = {}
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if self._should_skip(rel_path):
                    continue
                try:
                    # 跳過太小的檔案
                    if file_path.stat().st_size < 50:
                        continue
                    files[rel_path] = file_hash(file_path)
                except Exception:
                    continue
        return files

    def find_changes(self, current_files: dict[str, str]) -> dict:
        """
        找出變動

        Returns:
            {
                "added": [path, ...],      # 新增的檔案
                "modified": [path, ...],   # 修改的檔案
                "deleted": [path, ...],    # 刪除的檔案
                "unchanged": [path, ...],  # 沒變的檔案
            }
        """
        old_manifest = self.manifest.get("files", {})

        added = []
        modified = []
        deleted = []
        unchanged = []

        # 檢查當前檔案
        for path, hash_val in current_files.items():
            if path not in old_manifest:
                added.append(path)
            elif old_manifest[path] != hash_val:
                modified.append(path)
            else:
                unchanged.append(path)

        # 檢查刪除的檔案
        for path in old_manifest:
            if path not in current_files:
                deleted.append(path)

        return {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "unchanged": unchanged,
        }

    def audit_files(
        self,
        files_to_audit: list[str],
        auditor,  # LLMAuditor instance
        show_progress: bool = True
    ) -> dict[str, dict]:
        """審計指定的檔案列表"""
        results = {}

        if show_progress:
            try:
                from tqdm import tqdm
                iterator = tqdm(files_to_audit, desc="Auditing")
            except ImportError:
                iterator = files_to_audit
        else:
            iterator = files_to_audit

        for rel_path in iterator:
            full_path = self.project_root / rel_path
            if not full_path.exists():
                continue

            try:
                content = full_path.read_text(encoding="utf-8")

                # 推斷語言
                ext = Path(rel_path).suffix
                lang_map = {
                    ".py": "python",
                    ".vue": "vue",
                    ".ts": "typescript",
                    ".tsx": "typescript",
                    ".js": "javascript"
                }
                language = lang_map.get(ext, "unknown")

                # 審計
                audit = auditor.audit_file(rel_path, content, language)
                if not audit.get("error"):
                    results[rel_path] = audit

            except Exception as e:
                logger.error(f"Error auditing {rel_path}: {e}")
                continue

        return results

    def update_project_map(
        self,
        new_audits: dict[str, dict],
        deleted_files: list[str]
    ):
        """更新 PROJECT_MAP"""
        # 更新 files
        files = self.project_map.get("files", {})
        for path, audit in new_audits.items():
            files[path] = audit

        # 刪除已刪除的檔案
        for path in deleted_files:
            if path in files:
                del files[path]

        # 重建索引
        categories = {}
        api_map = {}
        keyword_index = {}

        for path, audit in files.items():
            # categories
            category = audit.get("category", "unknown")
            if category not in categories:
                categories[category] = []
            categories[category].append(path)

            # api_map
            for api in audit.get("apis", []):
                if api:
                    if api not in api_map:
                        api_map[api] = []
                    api_map[api].append(path)

            # keyword_index
            for keyword in audit.get("keywords", []):
                if keyword:
                    kw_lower = keyword.lower()
                    if kw_lower not in keyword_index:
                        keyword_index[kw_lower] = []
                    keyword_index[kw_lower].append(path)

        self.project_map = {
            "audited_at": datetime.now().isoformat(),
            "total_files": len(files),
            "files": files,
            "categories": categories,
            "api_map": api_map,
            "keyword_index": keyword_index,
        }

    def save(self, current_files: dict[str, str]):
        """保存 PROJECT_MAP 和 manifest"""
        self._save_json(self.project_map_path, self.project_map)
        self._save_json(self.manifest_path, {
            "updated_at": datetime.now().isoformat(),
            "files": current_files,
        })

    def run(
        self,
        auditor,
        force_full: bool = False,
        show_progress: bool = True
    ) -> dict:
        """
        執行增量審計

        Args:
            auditor: LLMAuditor instance
            force_full: 強制全量審計
            show_progress: 顯示進度

        Returns:
            {
                "added": int,
                "modified": int,
                "deleted": int,
                "unchanged": int,
                "audited": int,
            }
        """
        # 掃描當前檔案
        current_files = self.scan_files()
        logger.info(f"Found {len(current_files)} files")

        # 找出變動
        if force_full:
            changes = {
                "added": list(current_files.keys()),
                "modified": [],
                "deleted": [],
                "unchanged": [],
            }
        else:
            changes = self.find_changes(current_files)

        logger.info(
            f"Changes: +{len(changes['added'])} "
            f"~{len(changes['modified'])} "
            f"-{len(changes['deleted'])} "
            f"={len(changes['unchanged'])}"
        )

        # 需要審計的檔案
        files_to_audit = changes["added"] + changes["modified"]

        if files_to_audit:
            # 審計變動的檔案
            new_audits = self.audit_files(files_to_audit, auditor, show_progress)

            # 更新 PROJECT_MAP
            self.update_project_map(new_audits, changes["deleted"])

            # 保存
            self.save(current_files)

            logger.info(f"Audited {len(new_audits)} files")
        else:
            logger.info("No changes detected, skipping audit")

            # 只更新刪除的檔案
            if changes["deleted"]:
                self.update_project_map({}, changes["deleted"])
                self.save(current_files)

        return {
            "added": len(changes["added"]),
            "modified": len(changes["modified"]),
            "deleted": len(changes["deleted"]),
            "unchanged": len(changes["unchanged"]),
            "audited": len(files_to_audit),
        }


def incremental_audit(
    project_path: Path,
    index_dir: Optional[Path] = None,
    provider: str = "openai",
    model: str = None,
    force_full: bool = False,
) -> dict:
    """
    便捷函數：執行增量審計

    Args:
        project_path: 專案路徑
        index_dir: 索引目錄（預設 project_path/.flyto-index）
        provider: LLM provider
        model: LLM model
        force_full: 強制全量審計

    Returns:
        審計結果統計
    """
    from .llm_auditor import LLMAuditor

    if index_dir is None:
        index_dir = project_path / ".flyto-index"

    auditor = LLMAuditor(provider=provider, model=model)
    incremental = IncrementalAuditor(project_path, index_dir)

    return incremental.run(auditor, force_full=force_full)
