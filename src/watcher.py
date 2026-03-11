"""
File Change Watcher — polling-based change detection.

Design: Polling, not daemon.
- Cannot use watchdog (zero dependency constraint)
- MCP server is stateless stdin/stdout, not suitable for background threads
- Approach: compare os.stat().st_mtime vs index timestamp

Two modes:
- detect_changes(): check indexed files for modifications/deletions (fast)
- detect_new_files(): scan project roots for unindexed files (slower, optional)
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SUPPORTED_EXTENSIONS = {
    ".py", ".vue", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
}

_IGNORE_DIRS = {
    "node_modules", "__pycache__", ".git", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    "target", ".next", ".nuxt", "coverage",
}


@dataclass
class FileChange:
    path: str
    project: str
    change_type: str  # "modified" | "added" | "deleted"
    mtime: float = 0.0


class FileWatcher:
    """Detect file changes by comparing mtimes against index timestamp."""

    def __init__(self, index: dict):
        self._index = index

    def detect_changes(self, project: Optional[str] = None) -> list:
        """
        Detect changed files since last index.

        Returns list of FileChange objects, max 500 files checked per project.
        """
        changes = []
        index_mtime = self._get_index_mtime()
        if index_mtime == 0:
            return changes

        symbols = self._index.get("symbols", {})
        project_roots = self._index.get("project_roots", {})

        # Group files by project
        project_files = {}  # dict[str, set[str]]
        for sym_id, sym in symbols.items():
            proj = sym_id.split(":")[0] if ":" in sym_id else ""
            if project and proj != project:
                continue
            path = sym.get("path", "")
            if proj and path:
                if proj not in project_files:
                    project_files[proj] = set()
                project_files[proj].add(path)

        for proj, paths in project_files.items():
            root = project_roots.get(proj, "")
            if not root or not os.path.isdir(root):
                continue

            checked = 0
            for rel_path in sorted(paths):
                if checked >= 500:
                    break

                full_path = os.path.join(root, rel_path)
                checked += 1

                if not os.path.exists(full_path):
                    changes.append(FileChange(
                        path=rel_path,
                        project=proj,
                        change_type="deleted",
                    ))
                    continue

                try:
                    file_mtime = os.path.getmtime(full_path)
                    if file_mtime > index_mtime:
                        changes.append(FileChange(
                            path=rel_path,
                            project=proj,
                            change_type="modified",
                            mtime=file_mtime,
                        ))
                except OSError:
                    pass

            # Also detect new files (not in index) — quick scan of top-level dirs
            self._detect_new_files(root, proj, paths, index_mtime, changes)

        return changes

    def _detect_new_files(
        self,
        root: str,
        project: str,
        indexed_paths: set,
        index_mtime: float,
        changes: list,
        max_new: int = 50,
    ):
        """Scan project root for new files not in the index."""
        found = 0
        root_path = Path(root)

        for dirpath, dirnames, filenames in os.walk(root_path):
            # Prune ignored directories
            dirnames[:] = [
                d for d in dirnames
                if d not in _IGNORE_DIRS and not d.startswith(".")
            ]

            rel_dir = Path(dirpath).relative_to(root_path)

            for fname in filenames:
                if found >= max_new:
                    return

                ext = os.path.splitext(fname)[1]
                if ext not in _SUPPORTED_EXTENSIONS:
                    continue

                rel_path = str(rel_dir / fname) if str(rel_dir) != "." else fname
                if rel_path in indexed_paths:
                    continue

                full_path = os.path.join(dirpath, fname)
                try:
                    file_mtime = os.path.getmtime(full_path)
                    if file_mtime > index_mtime:
                        changes.append(FileChange(
                            path=rel_path,
                            project=project,
                            change_type="added",
                            mtime=file_mtime,
                        ))
                        found += 1
                except OSError:
                    pass

    def _get_index_mtime(self) -> float:
        """Get the index file modification time."""
        try:
            try:
                from .index_store import INDEX_DIR
            except ImportError:
                from index_store import INDEX_DIR
            for name in ("index.json.gz", "index.json"):
                p = INDEX_DIR / name
                if p.exists():
                    return p.stat().st_mtime
        except (ImportError, Exception):
            pass
        return 0.0

    def get_summary(self, changes: list) -> dict:
        """Summarize changes into counts."""
        by_type = {}  # dict[str, int]
        by_project = {}  # dict[str, int]

        for c in changes:
            by_type[c.change_type] = by_type.get(c.change_type, 0) + 1
            by_project[c.project] = by_project.get(c.project, 0) + 1

        return {
            "total": len(changes),
            "by_type": by_type,
            "by_project": by_project,
        }
