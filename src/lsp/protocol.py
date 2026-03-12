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


def utf16_offset(line: str, byte_col: int) -> int:
    """Convert a Python str index (byte_col) to a UTF-16 code-unit offset.

    LSP uses UTF-16 offsets for character positions. This converts a Python
    string index (which counts Unicode code points) to the equivalent UTF-16
    code-unit count.

    Characters outside the BMP (e.g., emoji) use 2 UTF-16 code units (surrogate pair).
    """
    if byte_col <= 0:
        return 0
    # Clamp to string length
    clamped = min(byte_col, len(line))
    prefix = line[:clamped]
    # Encode to UTF-16-LE and divide by 2 to get code units
    return len(prefix.encode("utf-16-le")) // 2


def byte_offset_from_utf16(line: str, utf16_col: int) -> int:
    """Convert a UTF-16 code-unit offset to a Python str index.

    This is the inverse of utf16_offset(). Given a UTF-16 column from an LSP
    server, return the corresponding Python string index.
    """
    if utf16_col <= 0:
        return 0
    count = 0
    for i, ch in enumerate(line):
        # Each character takes 1 or 2 UTF-16 code units
        code_units = 2 if ord(ch) > 0xFFFF else 1
        count += code_units
        if count >= utf16_col:
            return i + 1
    # utf16_col beyond end of line
    return len(line)
