"""
Safe XML parsing — hardened against XXE and entity-expansion ("billion laughs")
denial-of-service.

Background
---------
Python's stdlib ``xml.etree.ElementTree`` does NOT mitigate internal entity
expansion. A few-KB file with nested entities expands to gigabytes in memory.
External/parameter entities additionally enable XXE (file/SSRF reads). The
``defusedxml`` package exists precisely to close these holes — but flyto-indexer
is intentionally **zero external dependencies, pure Python stdlib only** (see
CLAUDE.md). This module therefore implements an equivalent guard using only the
stdlib expat parser.

Threat model
------------
Both XML sinks (``_parse_pom_xml`` in dependency_scanner, ``_parse_coverage_xml``
in coverage_intel) parse *untrusted repository content* reachable via the MCP
tool dispatch. A crafted ``pom.xml`` / ``coverage.xml`` — including one encoded
as UTF-16 or UTF-32 with a BOM — must not be able to trigger entity expansion or
external-entity resolution.

Defenses (all must hold, defense-in-depth)
------------------------------------------
1. **Input size cap.** Reject files larger than ``max_bytes`` before parsing.
2. **Encoding-aware DOCTYPE/ENTITY rejection.** A raw-byte substring scan for
   ``<!DOCTYPE``/``<!ENTITY`` is bypassable when the document is UTF-16/UTF-32
   encoded (the markers serialize with interleaved NUL bytes). We therefore
   *decode* the bytes to text using the BOM / XML-declaration encoding first,
   then scan the decoded text case-insensitively.
3. **Expat handlers that forbid DTDs and entities.** Even if a payload somehow
   evades the text scan, the expat parser is configured to *raise* the moment a
   DTD (``<!DOCTYPE``) is declared, an entity is declared, or an unknown /
   external entity is referenced. This is the structural guarantee — it does not
   depend on getting the encoding detection exactly right.

This module returns a standard ``xml.etree.ElementTree.ElementTree`` so callers
can use ``.getroot()`` / ``.iter()`` exactly as with ``ET.parse``.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Union
from xml.parsers import expat

# 25 MiB input cap. This is an *input-size* cap only; the real anti-expansion
# defense is the DTD/entity rejection below. A large legitimate coverage.xml
# from a big monorepo stays well under this.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024


class UnsafeXMLError(ValueError):
    """Raised when XML content is rejected by the safety guard.

    Covers: input over the size cap, a declared DTD or entity, or a reference to
    an undefined / external entity. Treated by callers like a parse error.
    """


def _detect_encoding(data: bytes) -> str:
    """Best-effort encoding detection for the DOCTYPE/ENTITY text scan.

    BOM takes precedence; otherwise we read the ``encoding="..."`` attribute of
    the XML declaration (interpreting the leading bytes as ASCII-compatible).
    Defaults to UTF-8. This is only used to *decode for scanning* — the expat
    handlers below are the authoritative guard regardless of what we guess here.
    """
    # Byte-order marks. Order matters: UTF-32 BOMs start with the UTF-16 LE BOM
    # bytes, so check the 4-byte forms first.
    if data.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be"
    if data.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le"
    if data.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if data.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    # No BOM: sniff the XML declaration. Handle UTF-16 declarations too, where
    # ASCII bytes are interleaved with NULs.
    head = data[:200]
    if head[:1] == b"<" and head[1:2] == b"\x00":
        # UTF-16-LE without BOM (e.g. "<\x00?\x00x\x00m\x00l\x00")
        return "utf-16-le"
    if head[:1] == b"\x00" and head[1:2] == b"<":
        return "utf-16-be"

    # ASCII-compatible: try to read the declared encoding.
    try:
        ascii_head = head.decode("ascii", errors="ignore")
    except Exception:  # pragma: no cover - decode with errors='ignore' won't raise
        ascii_head = ""

    m = re.search(r"""encoding\s*=\s*["']([\w.\-]+)["']""", ascii_head, re.IGNORECASE)
    if m:
        return m.group(1)
    return "utf-8"


def _reject_doctype_entities(data: bytes) -> None:
    """Decode the document and reject any DOCTYPE/ENTITY markers.

    Encoding-aware so UTF-16/UTF-32 payloads cannot smuggle the markers past a
    naive raw-byte substring scan.
    """
    encoding = _detect_encoding(data)
    try:
        text = data.decode(encoding, errors="replace")
    except (LookupError, ValueError):
        # Unknown codec name in the declaration — fall back to latin-1, which
        # maps every byte 1:1 so ASCII markers still surface.
        text = data.decode("latin-1", errors="replace")

    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise UnsafeXMLError("XML declares a DTD or entity; rejected (XXE/DoS guard)")


def _build_tree_with_safe_expat(data: bytes) -> ET.ElementTree:
    """Parse ``data`` with a raw expat parser that forbids DTDs/entities.

    The C-accelerated ``ET.XMLParser`` does not expose its underlying expat
    object across CPython versions, so we drive expat directly and feed events
    into an ``ET.TreeBuilder``. This is the structural guarantee, independent of
    encoding detection:

      * ``StartDoctypeDeclHandler`` raises -> any ``<!DOCTYPE`` aborts parsing.
      * ``EntityDeclHandler`` raises -> any ``<!ENTITY`` declaration aborts.
      * ``UnparsedEntityDeclHandler`` raises -> unparsed/NDATA entities abort.
      * ``ExternalEntityRefHandler`` raises -> external entity refs abort.
      * ``DefaultHandlerExpand`` is NOT installed, so an undefined entity
        reference raises the normal expat ``UndefinedEntityError`` rather than
        being silently expanded/skipped.

    Expat auto-detects the on-the-wire encoding (BOM / XML declaration), so the
    UTF-16/UTF-32 bypass that defeats a raw-byte DOCTYPE scan is handled here.
    """
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate()

    def _forbid_dtd(name, sysid, pubid, has_internal_subset):
        raise UnsafeXMLError("DTD declaration is forbidden (XXE/DoS guard)")

    def _forbid_entity(*args, **kwargs):
        raise UnsafeXMLError("entity declaration is forbidden (XXE/DoS guard)")

    def _forbid_external(context, base, sysid, pubid):
        raise UnsafeXMLError("external entity reference is forbidden (XXE guard)")

    parser.StartDoctypeDeclHandler = _forbid_dtd
    parser.EntityDeclHandler = _forbid_entity
    parser.UnparsedEntityDeclHandler = _forbid_entity
    parser.ExternalEntityRefHandler = _forbid_external

    # Feed structural events into the TreeBuilder.
    parser.StartElementHandler = lambda tag, attrs: builder.start(tag, attrs)
    parser.EndElementHandler = lambda tag: builder.end(tag)
    parser.CharacterDataHandler = lambda text: builder.data(text)

    try:
        parser.Parse(data, True)
    except UnsafeXMLError:
        raise
    except expat.ExpatError as e:
        # Normalize expat errors (malformed XML, undefined-entity references) to
        # the standard ElementTree ParseError that callers already handle.
        err = ET.ParseError(str(e))
        err.code = getattr(e, "code", None)
        raise err from e

    return ET.ElementTree(builder.close())


def safe_parse_xml(
    path: Union[str, Path],
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> ET.ElementTree:
    """Safely parse an XML file from disk, hardened against XXE / billion-laughs.

    Args:
        path: Path to the XML file (untrusted repository content).
        max_bytes: Reject inputs larger than this many bytes.

    Returns:
        An ``xml.etree.ElementTree.ElementTree`` (use ``.getroot()`` / ``.iter()``).

    Raises:
        UnsafeXMLError: input too large, or a DTD/entity was declared/referenced.
        xml.etree.ElementTree.ParseError: malformed XML.
        OSError: file could not be read.
    """
    data = Path(path).read_bytes()
    if len(data) > max_bytes:
        raise UnsafeXMLError(
            f"XML input exceeds size cap ({len(data)} > {max_bytes} bytes)"
        )

    # Layer 2: encoding-aware textual rejection of DOCTYPE/ENTITY.
    _reject_doctype_entities(data)

    # Layer 3: raw expat with DTD/entity handlers that forbid expansion,
    # regardless of the document's declared encoding.
    return _build_tree_with_safe_expat(data)
