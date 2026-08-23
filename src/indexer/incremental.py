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

    def _orphaned_index_paths(self, current_files: dict[str, str]) -> set[str]:
        """Vanished files index.json still describes but the manifest has lost.

        Eviction candidates come from the manifest alone, so a path the manifest
        dropped - a truncated write, an index carried across a tool upgrade -
        can never reach ChangeSet.deleted. Its symbols, BM25 docs and content
        rows then survive every incremental scan and only --full-scan clears
        them, which is how a stale index keeps describing a deleted tree while
        `verify --strict` measures the phantom.

        The manifest has no opinion on these paths, so the filesystem decides:
        only files that are genuinely gone are evicted.
        """
        try:
            data = json.loads((self.index_dir / "index.json").read_text())
        except (json.JSONDecodeError, OSError):
            return set()

        files = data.get("files")
        if not isinstance(files, dict):
            return set()

        return {
            path for path in files
            if path not in current_files
            and not (self.project_root / path).exists()
        }

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
        old_paths |= self._orphaned_index_paths(current_files)
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


# The one list every caller starts from. It used to be duplicated in
# engine.scan(), and the copies drifted: the engine's lacked .mypy_cache and
# both lacked the dist siblings, so a scan driven through the engine indexed
# 193 build bundles that a direct call correctly skipped. Callers with extra
# needs spread this and add to it rather than restating it.
DEFAULT_IGNORE_PATTERNS = [
    "node_modules", "__pycache__", ".git", "dist", "build",
    # Vite/Rollup emit these siblings of dist/. They were previously excluded
    # only as an accident of substring matching ("dist" occurs inside
    # "dist-next"), so they need naming now that patterns match whole
    # components. Indexing a bundle is worse than useless: minified vendor
    # code produces symbols nobody wrote and trips the taint rules.
    "dist-ce", "dist-next", "dist-ssr",
    # Framework production builds commonly copy hashed, minified Rollup/Vite
    # chunks here for a Python static-file server. These are generated bundles,
    # not authored source, and already sit outside the security scanner's source
    # boundary for the same reason.
    "static/assets",
    ".venv", "venv", ".pytest_cache", ".mypy_cache",
    ".vitepress/cache", ".next", ".open-next", ".nuxt", ".output",
    # Agent scratch checkouts are full copies of the project; indexing them
    # duplicates every symbol and makes impact analysis point at ghost files.
    ".claude/worktrees", ".codex/worktrees",
]


# PEP 405 puts a regular `pyvenv.cfg` at the root of every Python virtual
# environment. Names cannot carry that fact: a strict scan of flyto-core
# walked `.venv-sec/`, pulling 3,952 site-packages files into the symbol and
# documentation denominators, and no name list can keep up with the next
# spelling an operator invents. The marker identifies an environment by
# structure, so any name is pruned while a source directory that merely
# spells venv/build/dist inside a longer name keeps every file it owns.
VIRTUALENV_MARKER = "pyvenv.cfg"


def is_virtualenv_root(directory) -> bool:
    """True when *directory* itself is the root of a virtual environment.

    Exactly one entry is probed - the marker directly inside *directory* -
    with no recursion and no link resolution. A symlinked directory, or a
    marker that is itself a symlink, reports False rather than being
    followed, so this never stats a path outside the project tree.

    `src/profile/filesystem.py` and `src/doc_scanner.py` carry byte-identical
    copies: both are deliberately import-light (doc_scanner takes no
    intra-project import at all), and this predicate has no configuration to
    drift - it is the marker filename and two link checks.
    """
    path = os.fspath(directory)
    if os.path.islink(path):
        return False
    marker = os.path.join(path, VIRTUALENV_MARKER)
    return not os.path.islink(marker) and os.path.isfile(marker)


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
    ignore_patterns = ignore_patterns or list(DEFAULT_IGNORE_PATTERNS)

    # Patterns match whole path COMPONENTS, never substrings. A raw
    # `pattern in str(rel_path)` test silently dropped every path that merely
    # contained one — "build" hid src/profile/builder.py, and the same went for
    # any path spelling dist or venv inside a longer name. Those files then had
    # no symbols, so search, impact and dead-code analysis were blind to them
    # with nothing to indicate anything was missing.
    ignore_names = set()
    ignore_sequences = []
    for pattern in ignore_patterns:
        parts = tuple(p for p in Path(pattern).parts if p not in ("", "."))
        if not parts:
            continue
        if len(parts) == 1:
            ignore_names.add(parts[0])
        else:
            ignore_sequences.append(parts)

    def is_ignored(parts: tuple[str, ...]) -> bool:
        if ignore_names.intersection(parts):
            return True
        for seq in ignore_sequences:
            span = len(seq)
            if any(
                parts[i:i + span] == seq
                for i in range(len(parts) - span + 1)
            ):
                return True
        return False

    ext_set = set(extensions)
    result = {}

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).parts
        # Prune ignored directories in-place so os.walk skips them entirely.
        # Multi-component patterns are pruned here too: `.claude/worktrees`
        # holds whole repository copies, and matching it only per-file meant
        # walking every one of them just to discard the result.
        # Marked virtual environments are pruned by structure, next to the
        # name-based patterns rather than inside them: a pattern list only
        # ever knows the spellings someone already met.
        dirnames[:] = [
            d for d in dirnames
            if not is_ignored(rel_dir + (d,))
            and not is_virtualenv_root(os.path.join(dirpath, d))
        ]

        for fname in filenames:
            # Check extension (e.g. ".py", ".ts")
            _, ext = os.path.splitext(fname)
            if ext not in ext_set:
                continue

            file_path = Path(dirpath) / fname
            rel_path = file_path.relative_to(root)
            rel_str = str(rel_path)

            if is_ignored(rel_path.parts):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                result[rel_str] = compute_file_hash(content)
            except Exception:
                # Skip files that cannot be read
                pass

    return result
