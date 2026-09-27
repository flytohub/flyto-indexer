"""Bounded source admission and AST snapshot support for taint analysis."""

from __future__ import annotations

import ast
import os
import stat
from pathlib import Path

from .taint_common import (
    MAX_TAINT_FILE_BYTES, MAX_TAINT_SOURCE_BYTES, MAX_TAINT_SOURCE_FILES,
    SKIP_DIR_PATTERNS, _in_hidden_dir,
)


class TaintSourceMixin:
    def _filesystem_paths(self, pattern: str) -> list[Path]:
        """Return sorted built-in candidates refined by standard Git excludes."""
        candidates = sorted(self.project_root.rglob(pattern))
        relative = [
            str(path.relative_to(self.project_root)).replace("\\", "/")
            for path in candidates
        ]
        return [self.project_root / path for path in self._gitignore.filter(relative)]

    def _read_taint_source(self, rel: str) -> str | None:
        """Read an admitted regular file once, checking identity and byte budgets."""
        if rel in self._content_cache:
            return self._content_cache[rel]
        if rel in self._rejected_sources:
            return None
        self._rejected_sources.add(rel)
        candidate = Path(rel)
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or "\\" in rel
            or _in_hidden_dir(rel)
            or SKIP_DIR_PATTERNS.search(rel)
            or not self._gitignore.includes_cached(rel)
        ):
            return None
        if (
            len(self._content_cache) >= self._max_taint_source_files
            or self._source_bytes >= self._max_taint_source_bytes
        ):
            self._truncation.add("source_snapshot_cap")
            return None
        source = self.project_root / rel
        try:
            root = self.project_root.resolve()
            if not source.resolve().is_relative_to(root) or any(
                parent.is_symlink()
                for parent in [source, *source.parents]
                if parent != self.project_root and parent.is_relative_to(self.project_root)
            ):
                self._truncation.add("source_symlink")
                return None
            before = source.stat()
            if not stat.S_ISREG(before.st_mode):
                self._truncation.add("source_not_regular")
                return None
            limit = min(self._max_taint_file_bytes, self._max_taint_source_bytes - self._source_bytes)
            if before.st_size > limit:
                self._truncation.add("source_byte_cap")
                return None
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(source, flags)
            with os.fdopen(fd, "rb") as handle:
                opened = os.fstat(handle.fileno())
                identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
                    self._truncation.add("source_changed")
                    return None
                data = handle.read(limit + 1)
                after = os.fstat(handle.fileno())
                if (
                    len(data) > limit
                    or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != identity
                ):
                    self._truncation.add("source_changed_or_oversized")
                    return None
            content = data.decode("utf-8")
        except (OSError, ValueError, UnicodeError):
            self._truncation.add("source_read_error")
            return None
        self._source_bytes += len(data)
        self._content_cache[rel] = content
        return content

    def _python_tree(self, rel: str) -> ast.Module | None:
        """All passes share the same source bytes and parsed module identity."""
        if rel in self._ast_cache:
            return self._ast_cache[rel]
        if rel in self._parse_failures:
            return None
        content = self._read_taint_source(rel)
        if content is None:
            return None
        try:
            tree = ast.parse(content, filename=rel)
        except (SyntaxError, ValueError, RecursionError):
            self._parse_failures.add(rel)
            self._truncation.add("python_parse_error")
            return None
        self._ast_cache[rel] = tree
        return tree

    def _callee_verifier(self):
        """Type-aware callee verification, created once per scan."""
        if self._verifier is None:
            try:
                from .taint_lsp import CalleeVerifier
            except ImportError:  # pragma: no cover - flat-layout fallback
                from analyzer.taint_lsp import CalleeVerifier  # type: ignore
            self._verifier = CalleeVerifier(self.project_root)
        return self._verifier

