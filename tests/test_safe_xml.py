"""Tests for the safe XML parser (XXE / billion-laughs hardening).

The guard must reject DTD/entity-bearing XML *regardless of encoding*, since the
original byte-substring guard was bypassable by UTF-16/UTF-32 encoding the
DOCTYPE/ENTITY markers (interleaved NUL bytes defeat a raw b"<!doctype" scan).
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from safe_xml import safe_parse_xml, UnsafeXMLError  # noqa: E402


# Classic "billion laughs" internal-entity-expansion payload.
BILLION_LAUGHS = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE lolz [\n'
    '  <!ENTITY lol "lol">\n'
    '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
    '  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
    '  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">\n'
    '  <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">\n'
    '  <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">\n'
    '  <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">\n'
    '  <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">\n'
    '  <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">\n'
    ']>\n'
    '<lolz>&lol9;</lolz>\n'
)

# XXE external-entity (file read / SSRF) payload.
XXE_EXTERNAL = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE foo [\n'
    '  <!ENTITY xxe SYSTEM "file:///etc/passwd">\n'
    ']>\n'
    '<foo>&xxe;</foo>\n'
)


def _write(tmp_path, name, text, encoding):
    p = tmp_path / name
    # Use BOM-bearing UTF-16 codecs so expat auto-detects the encoding, which is
    # exactly the bypass vector the raw-byte guard missed.
    p.write_bytes(text.encode(encoding))
    return p


class TestBillionLaughsRejected:
    def test_utf8(self, tmp_path):
        p = _write(tmp_path, "pom.xml", BILLION_LAUGHS, "utf-8")
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p)

    def test_utf16_le_bom(self, tmp_path):
        # The encoding-bypass case: UTF-16-LE with BOM serializes "<!DOCTYPE" as
        # 3c 00 21 00 44 00 ... so b"<!doctype" is NOT a raw substring.
        p = _write(tmp_path, "coverage.xml", BILLION_LAUGHS, "utf-16")
        # Sanity: confirm the raw bytes really do dodge a naive substring scan.
        raw = p.read_bytes().lower()
        assert b"<!doctype" not in raw
        assert b"<!entity" not in raw
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p)

    def test_utf16_be_bom(self, tmp_path):
        p = _write(tmp_path, "coverage.xml", BILLION_LAUGHS, "utf-16-be")
        raw = p.read_bytes()
        # Prepend a UTF-16-BE BOM so expat can auto-detect.
        p.write_bytes(b"\xfe\xff" + raw)
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p)

    def test_not_expanded_is_fast(self, tmp_path):
        # Guard must reject *before* any expansion. If it expanded, this would
        # blow up memory/CPU; assert it returns quickly with a rejection.
        p = _write(tmp_path, "pom.xml", BILLION_LAUGHS, "utf-8")
        start = time.monotonic()
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p)
        assert time.monotonic() - start < 2.0


class TestXXERejected:
    def test_external_entity_utf8(self, tmp_path):
        p = _write(tmp_path, "pom.xml", XXE_EXTERNAL, "utf-8")
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p)

    def test_external_entity_utf16_le_bom(self, tmp_path):
        p = _write(tmp_path, "pom.xml", XXE_EXTERNAL, "utf-16")
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p)


class TestBenignXMLStillParses:
    def test_simple_pom(self, tmp_path):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project>\n'
            '  <dependencies>\n'
            '    <dependency>\n'
            '      <groupId>org.example</groupId>\n'
            '      <artifactId>thing</artifactId>\n'
            '      <version>1.2.3</version>\n'
            '    </dependency>\n'
            '  </dependencies>\n'
            '</project>\n'
        )
        p = _write(tmp_path, "pom.xml", xml, "utf-8")
        tree = safe_parse_xml(p)
        root = tree.getroot()
        assert root.findtext(".//artifactId") == "thing"
        assert root.findtext(".//version") == "1.2.3"

    def test_cobertura_coverage(self, tmp_path):
        xml = (
            '<?xml version="1.0"?>\n'
            '<coverage>\n'
            '  <packages><package><classes>\n'
            '    <class filename="src/a.py">\n'
            '      <lines>\n'
            '        <line number="1" hits="5"/>\n'
            '        <line number="2" hits="0"/>\n'
            '      </lines>\n'
            '    </class>\n'
            '  </classes></package></packages>\n'
            '</coverage>\n'
        )
        p = _write(tmp_path, "coverage.xml", xml, "utf-8")
        tree = safe_parse_xml(p)
        classes = list(tree.getroot().iter("class"))
        assert len(classes) == 1
        assert classes[0].get("filename") == "src/a.py"


class TestSizeCap:
    def test_oversize_rejected(self, tmp_path):
        p = tmp_path / "big.xml"
        p.write_bytes(b"<root>" + b"a" * 1024 + b"</root>")
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p, max_bytes=100)
