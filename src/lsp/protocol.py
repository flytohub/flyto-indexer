"""Minimal LSP protocol types — zero third-party dependencies."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import sys

if sys.platform == "win32":
    _URI_PREFIX = "file:///"
else:
    _URI_PREFIX = "file://"


@dataclass
class Position:
    """Zero-based line and character offset."""
    line: int
    character: int


@dataclass
class Range:
    """A range in a text document."""
    start: Position
    end: Position


@dataclass
class Location:
    """A location in a text document (uri + range)."""
    uri: str
    range: Range


def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to a filesystem path.

    >>> uri_to_path("file:///home/user/code/foo.py")
    '/home/user/code/foo.py'
    """
    if uri.startswith("file:///"):
        # On Windows: file:///C:/... -> C:/...
        # On Unix: file:///home/... -> /home/...
        if sys.platform == "win32":
            return uri[len("file:///"):]
        return uri[len("file://"):]
    if uri.startswith("file://"):
        return uri[len("file://"):]
    return uri


def path_to_uri(path: str) -> str:
    """Convert a filesystem path to a file:// URI.

    >>> path_to_uri("/home/user/code/foo.py")
    'file:///home/user/code/foo.py'
    """
    p = str(Path(path).resolve())
    if sys.platform == "win32":
        p = p.replace("\\", "/")
        return "file:///" + p
    return "file://" + p


def parse_content_length(header_bytes: bytes) -> Optional[int]:
    """Parse Content-Length from LSP header bytes.

    Headers are terminated by \\r\\n\\r\\n. Returns None if not found.
    """
    text = header_bytes.decode("ascii", errors="replace")
    for line in text.split("\r\n"):
        if line.lower().startswith("content-length:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                return None
    return None


def encode_message(body: bytes) -> bytes:
    """Encode a JSON-RPC body with Content-Length header."""
    header = f"Content-Length: {len(body)}\r\n\r\n"
    return header.encode("ascii") + body
