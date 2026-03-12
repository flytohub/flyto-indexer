"""
Safe I/O utilities — atomic writes and structured logging.

Atomic writes prevent index corruption on crash/power loss.
Structured logging replaces bare `except Exception` swallowing.
"""

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("flyto-indexer")

# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------

_LOG_CONFIGURED = False


def configure_logging(level: int = logging.INFO):
    """Configure structured logging to stderr (MCP stdout is reserved)."""
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "[flyto-indexer] %(levelname)s %(name)s:%(funcName)s — %(message)s"
    ))
    root = logging.getLogger("flyto-indexer")
    root.setLevel(level)
    root.addHandler(handler)
    _LOG_CONFIGURED = True


# ---------------------------------------------------------------------------
# Atomic file writes
# ---------------------------------------------------------------------------

def atomic_write_text(path: Path, content: str, encoding: str = "utf-8"):
    """Write text to a file atomically via temp file + rename.

    On POSIX, os.rename() is atomic within the same filesystem.
    Writing to a temp file first ensures the target is never partially written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data, indent: int = 2, ensure_ascii: bool = False):
    """Write JSON to a file atomically."""
    content = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    atomic_write_text(path, content)


def atomic_write_lines(path: Path, lines_iter, encoding: str = "utf-8"):
    """Write lines to a file atomically (for JSONL and similar formats).

    Args:
        lines_iter: Iterable of strings (each should end with newline).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            for line in lines_iter:
                f.write(line)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
