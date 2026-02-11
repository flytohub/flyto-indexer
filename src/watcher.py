"""
File Change Watcher — polling-based change detection.

Design: Polling, not daemon.
- Cannot use watchdog (zero dependency constraint)
- MCP server is stateless stdin/stdout, not suitable for background threads
- Approach: compare os.stat().st_mtime vs index timestamp
"""

import os
from dataclasses import dataclass
from typing import Optional


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

    def detect_changes(self, project: Optional[str] = None) -> list[FileChange]:
        """
        Detect changed files since last index.

        Returns list of FileChange objects, max 200 files checked per project.
        """
        changes: list[FileChange] = []
        index_mtime = self._get_index_mtime()
        if index_mtime == 0:
            return changes

        symbols = self._index.get("symbols", {})
        project_roots = self._index.get("project_roots", {})

        # Group files by project
        project_files: dict[str, set[str]] = {}
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
                if checked >= 200:
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

        return changes

    def _get_index_mtime(self) -> float:
        """Get the index file modification time."""
        # Try to find index file from INDEX_DIR
        try:
            try:
                from .mcp_server import INDEX_DIR
            except ImportError:
                from mcp_server import INDEX_DIR
            for name in ("index.json.gz", "index.json"):
                p = INDEX_DIR / name
                if p.exists():
                    return p.stat().st_mtime
        except (ImportError, Exception):
            pass
        return 0.0

    def get_summary(self, changes: list[FileChange]) -> dict:
        """Summarize changes into counts."""
        by_type: dict[str, int] = {}
        by_project: dict[str, int] = {}

        for c in changes:
            by_type[c.change_type] = by_type.get(c.change_type, 0) + 1
            by_project[c.project] = by_project.get(c.project, 0) + 1

        return {
            "total": len(changes),
            "by_type": by_type,
            "by_project": by_project,
        }
