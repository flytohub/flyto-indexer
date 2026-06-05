"""Security-hardening tests: HTTP body/DoS bounds + XXE-safe XML parsing."""

import io
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api_server import (
    APIHandler,
    MAX_BODY_BYTES,
    MAX_SEARCH_RESULTS,
    _BadRequest,
    _PayloadTooLarge,
    _clamp_max_results,
)
from safe_xml import safe_parse_xml, UnsafeXMLError


def _handler_with_body(content_length, body: bytes) -> APIHandler:
    """Build an APIHandler without a real socket to exercise _read_json."""
    h = APIHandler.__new__(APIHandler)
    h.headers = {"Content-Length": str(content_length)}
    h.rfile = io.BytesIO(body)
    return h


class TestReadJsonBounds:
    def test_oversized_body_is_rejected_before_read(self):
        # Declares a huge body but supplies none — must reject on the header,
        # never attempting an unbounded allocation/read.
        h = _handler_with_body(MAX_BODY_BYTES + 1, b"")
        with pytest.raises(_PayloadTooLarge):
            h._read_json()

    def test_invalid_content_length_is_bad_request(self):
        h = APIHandler.__new__(APIHandler)
        h.headers = {"Content-Length": "not-a-number"}
        h.rfile = io.BytesIO(b"")
        with pytest.raises(_BadRequest):
            h._read_json()

    def test_malformed_json_is_bad_request(self):
        body = b"{not valid json"
        h = _handler_with_body(len(body), body)
        with pytest.raises(_BadRequest):
            h._read_json()

    def test_valid_body_parses(self):
        body = b'{"query": "hi", "max_results": 5}'
        h = _handler_with_body(len(body), body)
        assert h._read_json() == {"query": "hi", "max_results": 5}

    def test_empty_body_is_empty_dict(self):
        h = _handler_with_body(0, b"")
        assert h._read_json() == {}


class TestClampMaxResults:
    @pytest.mark.parametrize("value,expected", [
        (5, 5),
        (0, 1),
        (-3, 1),
        (10_000, MAX_SEARCH_RESULTS),
        ("7", 7),
        ("garbage", 10),
        (None, 10),
    ])
    def test_clamp(self, value, expected):
        assert _clamp_max_results(value) == expected


class TestSafeXML:
    def test_parses_valid_coverage_xml(self, tmp_path):
        p = tmp_path / "coverage.xml"
        p.write_text(
            '<coverage><class filename="a.py">'
            '<line number="1" hits="1"/></class></coverage>'
        )
        tree = safe_parse_xml(p)
        assert tree.getroot().tag == "coverage"

    def test_rejects_doctype_xxe(self, tmp_path):
        p = tmp_path / "evil.xml"
        p.write_text(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
            "<coverage>&xxe;</coverage>"
        )
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p)

    def test_rejects_entity_billion_laughs(self, tmp_path):
        p = tmp_path / "lol.xml"
        p.write_text(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;">]>\n'
            "<root>&lol2;</root>"
        )
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p)

    def test_rejects_oversized(self, tmp_path):
        p = tmp_path / "big.xml"
        p.write_text("<root/>")
        with pytest.raises(UnsafeXMLError):
            safe_parse_xml(p, max_bytes=3)
