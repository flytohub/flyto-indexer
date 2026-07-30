"""
Incremental indexing - only update what changed.

Core logic:
1. Load the old manifest (hash table)
2. Scan current files and compute new hashes
3. Compare: same hash -> skip, different hash -> rebuild
4. Update the manifest
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from ..models import Dependency, FileManifest, Symbol
    from ..safe_io import atomic_write_json
except ImportError:
    from models import Dependency, FileManifest, Symbol
    from safe_io import atomic_write_json

MANIFEST_VERSION = 2
CONTENT_HASH_ALGORITHM = "sha256"


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
        "version": 2,
        "hash_algorithm": "sha256",
        "pipeline_fingerprint": "<sha256>",
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

    def __init__(self, store_path: Path, pipeline_fingerprint: str = ""):
        self.store_path = store_path
        self.pipeline_fingerprint = pipeline_fingerprint
        self.data = self._empty_data()

    def _empty_data(self) -> dict:
        return {
            "project": "",
            "version": MANIFEST_VERSION,
            "hash_algorithm": CONTENT_HASH_ALGORITHM,
            "pipeline_fingerprint": self.pipeline_fingerprint,
            "files": {},
        }

    def load(self) -> bool:
        """Load manifest"""
        if self.store_path.exists():
            try:
                self.data = json.loads(self.store_path.read_text())
                if not isinstance(self.data, dict):
                    self.data = self._empty_data()
                    return False
                if not isinstance(self.data.get("files"), dict):
                    self.data["files"] = {}
                return True
            except (json.JSONDecodeError, OSError):
                self.data = self._empty_data()
                return False
        return False

    def save(self):
        """Save manifest"""
        atomic_write_json(self.store_path, self.data)

    def replace(self, project: str, manifests: list[FileManifest]):
        """Atomically replace stale state after a full project rebuild."""
        self.data = {
            "project": project,
            "version": MANIFEST_VERSION,
            "hash_algorithm": CONTENT_HASH_ALGORITHM,
            "pipeline_fingerprint": self.pipeline_fingerprint,
            "files": {
                manifest.path: manifest.to_dict()
                for manifest in manifests
            },
        }
        self.save()

    def is_compatible(self) -> bool:
        """Whether cached file entries were produced by this exact pipeline."""
        return (
            self.data.get("version") == MANIFEST_VERSION
            and self.data.get("hash_algorithm") == CONTENT_HASH_ALGORITHM
            and self.data.get("pipeline_fingerprint") == self.pipeline_fingerprint
        )

    def mark_compatible(self) -> None:
        self.data["version"] = MANIFEST_VERSION
        self.data["hash_algorithm"] = CONTENT_HASH_ALGORITHM
        self.data["pipeline_fingerprint"] = self.pipeline_fingerprint

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

    def __init__(
        self,
        project_root: Path,
        index_dir: Path,
        pipeline_fingerprint: str = "",
    ):
        self.project_root = project_root
        self.index_dir = index_dir
        self.manifest_store = ManifestStore(
            index_dir / "manifest.json",
            pipeline_fingerprint=pipeline_fingerprint,
        )

    def detect_changes(self, current_files: dict[str, str]) -> ChangeSet:
        """
        Detect changes

        Args:
            current_files: {path: content_hash} hash table of current files

        Returns:
            ChangeSet of changes
        """
        self.manifest_store.load()
        compatible = self.manifest_store.is_compatible()

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
            if not compatible or old_hash != new_hash:
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
        self.manifest_store.mark_compatible()
        for manifest in new_manifests:
            self.manifest_store.update_file(manifest)

        # Delete
        for path in change_set.deleted:
            self.manifest_store.remove_file(path)

        # Save
        self.manifest_store.save()

    def replace_manifest(
        self,
        project: str,
        manifests: list[FileManifest],
    ):
        """Replace the complete manifest after a non-incremental scan."""
        self.manifest_store.replace(project, manifests)

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
    """Compute a collision-resistant content address for one source file."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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
        ".venv", "venv", ".pytest_cache", ".mypy_cache",
        ".vitepress/cache", ".next", ".open-next", ".nuxt", ".output",
    ]

    ignore_set = set(ignore_patterns)
    ext_set = set(extensions)
    result = {}

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories in-place so os.walk skips them entirely
        dirnames[:] = [
            d for d in dirnames
            if d not in ignore_set
        ]

        for fname in filenames:
            # Check extension (e.g. ".py", ".ts")
            _, ext = os.path.splitext(fname)
            if ext not in ext_set:
                continue

            file_path = Path(dirpath) / fname
            rel_path = file_path.relative_to(root)

            # Also check substring match for nested ignore patterns
            rel_str = str(rel_path)
            if any(p in rel_str for p in ignore_patterns):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                result[rel_str] = compute_file_hash(content)
            except Exception:
                # Skip files that cannot be read
                pass

    return result
