"""
Incremental indexing - only update what changed.

Core logic:
1. Load the old manifest (hash table)
2. Scan current files and compute new hashes
3. Compare: same hash -> skip, different hash -> rebuild
4. Update the manifest
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
    """Change set"""
    added: list[str]      # Newly added files
    modified: list[str]   # Modified files
    deleted: list[str]    # Deleted files

    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.deleted)

    def all_changed(self) -> list[str]:
        return self.added + self.modified

    def summary(self) -> str:
        return f"+{len(self.added)} ~{len(self.modified)} -{len(self.deleted)}"


class ManifestStore:
    """
    Manifest store (fingerprint table)

    Storage format:
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
        """Load manifest"""
        if self.store_path.exists():
            try:
                self.data = json.loads(self.store_path.read_text())
                return True
            except json.JSONDecodeError:
                return False
        return False

    def save(self):
        """Save manifest"""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(json.dumps(self.data, indent=2))

    def get_file_hash(self, path: str) -> Optional[str]:
        """Get the old hash for a file"""
        if path in self.data["files"]:
            return self.data["files"][path].get("hash")
        return None

    def update_file(self, manifest: FileManifest):
        """Update file manifest"""
        self.data["files"][manifest.path] = manifest.to_dict()

    def remove_file(self, path: str):
        """Remove file"""
        if path in self.data["files"]:
            del self.data["files"][path]

    def get_all_paths(self) -> set[str]:
        """Get all indexed file paths"""
        return set(self.data["files"].keys())

    def set_project(self, project: str):
        self.data["project"] = project


class IncrementalIndexer:
    """
    Incremental indexer

    Only updates changed files, significantly reducing rebuild time.
    """

    def __init__(self, project_root: Path, index_dir: Path):
        self.project_root = project_root
        self.index_dir = index_dir
        self.manifest_store = ManifestStore(index_dir / "manifest.json")

    def detect_changes(self, current_files: dict[str, str]) -> ChangeSet:
        """
        Detect changes

        Args:
            current_files: {path: content_hash} hash table of current files

        Returns:
            ChangeSet of changes
        """
        self.manifest_store.load()

        old_paths = self.manifest_store.get_all_paths()
        new_paths = set(current_files.keys())

        added = []
        modified = []
        deleted = []

        # Added files
        for path in new_paths - old_paths:
            added.append(path)

        # Deleted files
        for path in old_paths - new_paths:
            deleted.append(path)

        # Modified files (hash differs)
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
        Apply changes to the manifest

        This only updates the manifest; vector store updates are handled elsewhere.
        """
        # Update/add
        for manifest in new_manifests:
            self.manifest_store.update_file(manifest)

        # Delete
        for path in change_set.deleted:
            self.manifest_store.remove_file(path)

        # Save
        self.manifest_store.save()

    def get_symbols_to_update(
        self,
        change_set: ChangeSet,
        all_symbols: dict[str, Symbol]
    ) -> tuple[list[str], list[str]]:
        """
        Get symbols that need updating

        Returns:
            (to_upsert, to_delete) symbol IDs
        """
        to_upsert = []
        to_delete = []

        # Changed/added files -> their symbols need upsert
        for path in change_set.all_changed():
            for symbol in all_symbols.values():
                if symbol.path == path:
                    to_upsert.append(symbol.id)

        # Deleted files -> their symbols need deletion
        # Retrieved from old manifest
        self.manifest_store.load()
        for path in change_set.deleted:
            file_data = self.manifest_store.data["files"].get(path, {})
            symbol_ids = file_data.get("symbols", [])
            to_delete.extend(symbol_ids)

        return to_upsert, to_delete


def compute_file_hash(content: str) -> str:
    """Compute file hash"""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def scan_directory_hashes(
    root: Path,
    extensions: list[str],
    ignore_patterns: list[str] = None
) -> dict[str, str]:
    """
    Scan a directory and get hashes for all files

    Args:
        root: Project root directory
        extensions: File extensions to scan
        ignore_patterns: Path patterns to ignore

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
            # Check if this path should be ignored
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
                # Skip files that cannot be read
                pass

    return result
