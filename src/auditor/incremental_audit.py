"""
Incremental audit -- only audit changed files

Flow:
1. Load existing PROJECT_MAP
2. Compute file hashes
3. Compare: find added, modified, and deleted files
4. Only audit new and modified files
5. Update PROJECT_MAP
"""

import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def file_hash(path: Path) -> str:
    """Compute file hash"""
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()[:16]


class IncrementalAuditor:
    """Incremental auditor"""

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

        # Load existing index
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
        """Scan all files, return {path: hash}"""
        files = {}
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if self._should_skip(rel_path):
                    continue
                try:
                    # Skip files that are too small
                    if file_path.stat().st_size < 50:
                        continue
                    files[rel_path] = file_hash(file_path)
                except Exception:
                    continue
        return files

    def find_changes(self, current_files: dict[str, str]) -> dict:
        """
        Find changes

        Returns:
            {
                "added": [path, ...],      # Newly added files
                "modified": [path, ...],   # Modified files
                "deleted": [path, ...],    # Deleted files
                "unchanged": [path, ...],  # Unchanged files
            }
        """
        old_manifest = self.manifest.get("files", {})

        added = []
        modified = []
        deleted = []
        unchanged = []

        # Check current files
        for path, hash_val in current_files.items():
            if path not in old_manifest:
                added.append(path)
            elif old_manifest[path] != hash_val:
                modified.append(path)
            else:
                unchanged.append(path)

        # Check for deleted files
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
        """Audit the specified list of files"""
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

                # Infer language
                ext = Path(rel_path).suffix
                lang_map = {
                    ".py": "python",
                    ".vue": "vue",
                    ".ts": "typescript",
                    ".tsx": "typescript",
                    ".js": "javascript"
                }
                language = lang_map.get(ext, "unknown")

                # Audit
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
        """Update PROJECT_MAP"""
        # Update files
        files = self.project_map.get("files", {})
        for path, audit in new_audits.items():
            files[path] = audit

        # Remove deleted files
        for path in deleted_files:
            if path in files:
                del files[path]

        # Rebuild index
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
        """Save PROJECT_MAP and manifest"""
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
        Run incremental audit

        Args:
            auditor: LLMAuditor instance
            force_full: Force full audit
            show_progress: Show progress

        Returns:
            {
                "added": int,
                "modified": int,
                "deleted": int,
                "unchanged": int,
                "audited": int,
            }
        """
        # Scan current files
        current_files = self.scan_files()
        logger.info(f"Found {len(current_files)} files")

        # Find changes
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

        # Files that need auditing
        files_to_audit = changes["added"] + changes["modified"]

        if files_to_audit:
            # Audit changed files
            new_audits = self.audit_files(files_to_audit, auditor, show_progress)

            # Update PROJECT_MAP
            self.update_project_map(new_audits, changes["deleted"])

            # Save
            self.save(current_files)

            logger.info(f"Audited {len(new_audits)} files")
        else:
            logger.info("No changes detected, skipping audit")

            # Only update deleted files
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
    Convenience function: run incremental audit

    Args:
        project_path: Project path
        index_dir: Index directory (default: project_path/.flyto-index)
        provider: LLM provider
        model: LLM model
        force_full: Force full audit

    Returns:
        Audit result statistics
    """
    from .llm_auditor import LLMAuditor

    if index_dir is None:
        index_dir = project_path / ".flyto-index"

    auditor = LLMAuditor(provider=provider, model=model)
    incremental = IncrementalAuditor(project_path, index_dir)

    return incremental.run(auditor, force_full=force_full)
