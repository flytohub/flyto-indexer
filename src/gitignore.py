"""Bounded local filtering for Git's standard repository exclusions."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

_BATCH_PATHS = 512
_BATCH_BYTES = 64 * 1024
_TIMEOUT_SECONDS = 5.0


class GitIgnoreFilter:
    """Exclude untracked ignored paths while retaining tracked ignored files.

    Git's default ``check-ignore`` behavior omits tracked paths. Missing Git,
    non-repositories, timeouts, malformed output, and command errors all fail
    open, leaving each caller's existing built-in exclusions authoritative.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self._included: dict[str, bool] = {}

    @staticmethod
    def _relative(path: str | os.PathLike[str]) -> str | None:
        value = os.fspath(path).replace(os.sep, "/")
        candidate = Path(value)
        if candidate.is_absolute() or not value or "\x00" in value:
            return None
        if any(part in ("", ".", "..") for part in candidate.parts):
            return None
        return "/".join(candidate.parts)

    @staticmethod
    def _batches(paths: list[str]) -> Iterable[list[str]]:
        batch: list[str] = []
        size = 0
        for path in paths:
            encoded_size = len(path.encode("utf-8", errors="surrogateescape")) + 1
            # A repository path cannot normally approach this boundary, but
            # callers may supply arbitrary strings.  Do not create an
            # over-limit subprocess request; leaving it unqueried fails open.
            if encoded_size > _BATCH_BYTES:
                continue
            if batch and (
                len(batch) >= _BATCH_PATHS or size + encoded_size > _BATCH_BYTES
            ):
                yield batch
                batch = []
                size = 0
            batch.append(path)
            size += encoded_size
        if batch:
            yield batch

    def filter(self, paths: Iterable[str | os.PathLike[str]]) -> list[str]:
        """Return normalized repository-relative paths visible to scanners."""
        normalized: list[str] = []
        seen: set[str] = set()
        for path in paths:
            relative = self._relative(path)
            if relative is not None and relative not in seen:
                normalized.append(relative)
                seen.add(relative)
        normalized.sort()

        unknown = [path for path in normalized if path not in self._included]
        for batch in self._batches(unknown):
            payload = b"\0".join(
                path.encode("utf-8", errors="surrogateescape") for path in batch
            ) + b"\0"
            try:
                completed = subprocess.run(
                    ["git", "-C", os.fspath(self.root), "check-ignore", "--stdin", "-z"],
                    input=payload,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=_TIMEOUT_SECONDS,
                    check=False,
                    shell=False,
                )
                if completed.returncode not in (0, 1):
                    raise OSError("git check-ignore failed")
                if not isinstance(completed.stdout, bytes):
                    raise OSError("git check-ignore returned non-byte output")
                if completed.stdout and not completed.stdout.endswith(b"\0"):
                    raise OSError("git check-ignore returned unterminated output")
                output = completed.stdout.split(b"\0")[:-1]
                decoded = [
                    item.decode("utf-8", errors="surrogateescape") for item in output
                ]
                ignored = set(decoded)
                if (
                    len(ignored) != len(decoded)
                    or not ignored.issubset(batch)
                    or (completed.returncode == 0) != bool(ignored)
                ):
                    raise OSError("git check-ignore returned an unexpected path")
            except (OSError, subprocess.SubprocessError):
                ignored = set()
            for path in batch:
                self._included[path] = path not in ignored

        return [path for path in normalized if self._included.get(path, True)]

    def includes_cached(self, path: str | os.PathLike[str]) -> bool:
        """Return a cached decision; invalid or unknown paths fail open."""
        relative = self._relative(path)
        return relative is None or self._included.get(relative, True)
