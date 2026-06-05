"""Dependency-free safe XML parsing for ingested, untrusted repository files.

The indexer parses XML (pom.xml, coverage.xml) from repositories it does not
control. Stdlib xml.etree is vulnerable to entity-expansion ("billion laughs")
and external-entity (XXE) attacks via DTDs. This module keeps the package
dependency-free (defusedxml would be the alternative) by:

  1. capping the input size, and
  2. rejecting any DTD / entity declaration up front — neither pom.xml nor
     coverage.xml legitimately uses a DOCTYPE or <!ENTITY>, so a hard reject is
     both safe and correct for these formats.

External and internal entity attacks both require a DTD/DOCTYPE or an <!ENTITY>
declaration, so rejecting those closes the class outright.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

# 25 MiB is far above any real pom.xml / coverage.xml while still bounding the
# memory a malicious file can force us to read and parse.
DEFAULT_MAX_XML_BYTES = 25 * 1024 * 1024


class UnsafeXMLError(ValueError):
    """Raised when an XML input exceeds the size cap or declares a DTD/entity."""


def safe_parse_xml(path, max_bytes: int = DEFAULT_MAX_XML_BYTES) -> ET.ElementTree:
    """Parse an XML file, refusing oversized inputs and DTD/entity declarations.

    Returns an ElementTree (same surface as ET.parse) so callers can keep using
    .getroot()/.iter(). Raises UnsafeXMLError on a rejected input and the usual
    ET.ParseError on malformed XML.
    """
    data = Path(path).read_bytes()
    if len(data) > max_bytes:
        raise UnsafeXMLError(
            f"XML input {path} is {len(data)} bytes, over the {max_bytes}-byte limit"
        )
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise UnsafeXMLError(
            f"XML input {path} declares a DTD/ENTITY, which is rejected "
            "(XXE / entity-expansion guard)"
        )
    return ET.ElementTree(ET.fromstring(data))
