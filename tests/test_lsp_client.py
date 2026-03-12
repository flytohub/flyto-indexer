"""Tests for LSP client, protocol framing, and manager."""

import json
import sys
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from lsp.protocol import (
    Position,
    Range,
    Location,
    uri_to_path,
    path_to_uri,
    parse_content_length,
    encode_message,
)
from lsp.client import LSPClient
from lsp.manager import LSPManager


# ---------------------------------------------------------------------------
# Protocol tests
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_position_fields(self):
        p = Position(line=10, character=5)
        assert p.line == 10
        assert p.character == 5

    def test_range_fields(self):
        r = Range(
            start=Position(line=1, character=0),
            end=Position(line=1, character=10),
        )
        assert r.start.line == 1
        assert r.end.character == 10

    def test_location_fields(self):
        loc = Location(
            uri="file:///tmp/foo.py",
            range=Range(
                start=Position(0, 0),
                end=Position(0, 5),
            ),
        )
        assert loc.uri == "file:///tmp/foo.py"

    def test_parse_content_length_valid(self):
        header = b"Content-Length: 42\r\n\r\n"
        assert parse_content_length(header) == 42

    def test_parse_content_length_with_extra_headers(self):
        header = b"Content-Type: utf-8\r\nContent-Length: 100\r\n\r\n"
        assert parse_content_length(header) == 100

    def test_parse_content_length_missing(self):
        header = b"Content-Type: utf-8\r\n\r\n"
        assert parse_content_length(header) is None

    def test_parse_content_length_invalid_number(self):
        header = b"Content-Length: abc\r\n\r\n"
        assert parse_content_length(header) is None

    def test_encode_message(self):
        body = b'{"jsonrpc":"2.0"}'
        encoded = encode_message(body)
        assert encoded.startswith(b"Content-Length: 17\r\n\r\n")
        assert encoded.endswith(body)

    def test_encode_decode_roundtrip(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "test"}).encode()
        encoded = encode_message(body)
        # Parse header
        header_end = encoded.index(b"\r\n\r\n") + 4
        header = encoded[:header_end]
        length = parse_content_length(header)
        assert length == len(body)
        decoded_body = encoded[header_end:]
        assert decoded_body == body


# ---------------------------------------------------------------------------
# Client tests with mock subprocess
# ---------------------------------------------------------------------------


def _make_mock_process(responses=None):
    """Create a mock Popen that simulates an LSP server.

    Responses are only made available after a corresponding stdin.write() call,
    preventing the reader thread from consuming response data before the client
    has registered its event handler.
    """
    proc = MagicMock()
    proc.poll.return_value = None  # Process is running
    proc.stderr = MagicMock()

    if responses is None:
        responses = []

    # Build per-response byte chunks
    response_chunks = []
    for resp in responses:
        body = json.dumps(resp).encode("utf-8")
        chunk = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        response_chunks.append(chunk)

    # State shared between stdin.write (which "triggers" the next response)
    # and stdout.read (which the reader thread calls).
    _available = bytearray()
    _data_ready = threading.Event()
    _eof_event = threading.Event()
    _write_count = [0]
    _read_pos = [0]
    _lock = threading.Lock()

    def mock_write(data):
        """Called when the client sends a message; release the next response."""
        with _lock:
            idx = _write_count[0]
            _write_count[0] += 1
            if idx < len(response_chunks):
                _available.extend(response_chunks[idx])
                _data_ready.set()

    def mock_flush():
        pass

    proc.stdin = MagicMock()
    proc.stdin.write = mock_write
    proc.stdin.flush = mock_flush

    def mock_read(n=1):
        while True:
            with _lock:
                pos = _read_pos[0]
                if pos < len(_available):
                    end = min(pos + n, len(_available))
                    chunk = bytes(_available[pos:end])
                    _read_pos[0] = end
                    return chunk
            # No data yet — wait for it or for test teardown
            _data_ready.wait(timeout=0.5)
            if _eof_event.is_set():
                return b""
            _data_ready.clear()

    proc.stdout = MagicMock()
    proc.stdout.read = mock_read
    proc._eof_event = _eof_event

    return proc


class TestLSPClient:
    def test_start_failure_no_binary(self):
        """start() returns False when binary not found."""
        client = LSPClient(["nonexistent-lsp-server"], "file:///tmp")
        assert client.start() is False
        assert not client.alive

    @patch("lsp.client.subprocess.Popen")
    def test_start_success(self, mock_popen):
        """start() returns True with a properly responding server."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"capabilities": {}},
        }
        proc = _make_mock_process([init_response])
        # Keep poll() returning None so the reader thread stays alive
        poll_calls = [0]

        def poll_side_effect():
            poll_calls[0] += 1
            return None

        proc.poll.side_effect = poll_side_effect
        mock_popen.return_value = proc

        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=3.0)
        result = client.start()
        assert result is True
        # Clean up
        client._alive = False
        proc._eof_event.set()

    @patch("lsp.client.subprocess.Popen")
    def test_shutdown_sends_messages(self, mock_popen):
        """shutdown() sends shutdown request and exit notification."""
        init_response = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
        shutdown_response = {"jsonrpc": "2.0", "id": 2, "result": None}
        proc = _make_mock_process([init_response, shutdown_response])
        mock_popen.return_value = proc

        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=3.0)
        client.start()
        client.shutdown()
        proc._eof_event.set()
        assert not client.alive

    def test_references_when_not_alive(self):
        """text_document_references returns empty when not connected."""
        client = LSPClient(["fake-lsp"], "file:///tmp")
        result = client.text_document_references("file:///tmp/foo.py", 0, 0)
        assert result == []

    def test_definition_when_not_alive(self):
        """text_document_definition returns empty when not connected."""
        client = LSPClient(["fake-lsp"], "file:///tmp")
        result = client.text_document_definition("file:///tmp/foo.py", 0, 0)
        assert result == []

    def test_did_open_when_not_alive(self):
        """did_open is a no-op when not connected."""
        client = LSPClient(["fake-lsp"], "file:///tmp")
        # Should not raise
        client.did_open("file:///tmp/foo.py", "python", "x = 1")

    def test_alive_property_false_by_default(self):
        client = LSPClient(["fake-lsp"], "file:///tmp")
        assert not client.alive


# ---------------------------------------------------------------------------
# Manager tests
# ---------------------------------------------------------------------------


class TestLSPManager:
    def setup_method(self):
        LSPManager.reset_instance()

    def teardown_method(self):
        LSPManager.reset_instance()

    @patch("lsp.manager.shutil.which")
    def test_detect_available_finds_pyright(self, mock_which):
        def which_side_effect(exe):
            if exe == "pyright-langserver":
                return "/usr/bin/pyright-langserver"
            return None
        mock_which.side_effect = which_side_effect

        mgr = LSPManager()
        available = mgr.detect_available()
        assert "python" in available
        assert available["python"] == "/usr/bin/pyright-langserver"

    @patch("lsp.manager.shutil.which")
    def test_detect_available_finds_pylsp_fallback(self, mock_which):
        def which_side_effect(exe):
            if exe == "pylsp":
                return "/usr/bin/pylsp"
            return None
        mock_which.side_effect = which_side_effect

        mgr = LSPManager()
        available = mgr.detect_available()
        assert "python" in available
        assert available["python"] == "/usr/bin/pylsp"

    @patch("lsp.manager.shutil.which")
    def test_detect_available_none(self, mock_which):
        mock_which.return_value = None
        mgr = LSPManager()
        available = mgr.detect_available()
        assert available == {}

    @patch("lsp.manager.shutil.which")
    def test_detect_available_multiple_languages(self, mock_which):
        def which_side_effect(exe):
            mapping = {
                "pyright-langserver": "/usr/bin/pyright-langserver",
                "gopls": "/usr/bin/gopls",
                "rust-analyzer": "/usr/bin/rust-analyzer",
            }
            return mapping.get(exe)
        mock_which.side_effect = which_side_effect

        mgr = LSPManager()
        available = mgr.detect_available()
        assert "python" in available
        assert "go" in available
        assert "rust" in available
        assert "typescript" not in available

    def test_language_for_path(self):
        mgr = LSPManager()
        assert mgr.language_for_path("foo.py") == "python"
        assert mgr.language_for_path("bar.ts") == "typescript"
        assert mgr.language_for_path("baz.tsx") == "typescript"
        assert mgr.language_for_path("main.go") == "go"
        assert mgr.language_for_path("lib.rs") == "rust"
        assert mgr.language_for_path("README.md") is None
        assert mgr.language_for_path("noext") is None

    @patch("lsp.manager.shutil.which")
    def test_get_client_disabled(self, mock_which):
        mock_which.return_value = "/usr/bin/pyright-langserver"
        mgr = LSPManager()
        mgr._enabled = False
        assert mgr.get_client("python", "/tmp") is None

    @patch("lsp.manager.shutil.which")
    def test_get_client_no_server(self, mock_which):
        mock_which.return_value = None
        mgr = LSPManager()
        assert mgr.get_client("python", "/tmp") is None

    def test_singleton(self):
        m1 = LSPManager.get_instance()
        m2 = LSPManager.get_instance()
        assert m1 is m2

    def test_reset_instance(self):
        m1 = LSPManager.get_instance()
        LSPManager.reset_instance()
        m2 = LSPManager.get_instance()
        assert m1 is not m2

    def test_detect_available_cached(self):
        mgr = LSPManager()
        mgr._available = {"python": "/usr/bin/pyright-langserver"}
        # Should return cached value without calling shutil.which
        with patch("lsp.manager.shutil.which") as mock_which:
            result = mgr.detect_available()
            mock_which.assert_not_called()
        assert result == {"python": "/usr/bin/pyright-langserver"}

    @patch.dict("os.environ", {"FLYTO_LSP_ENABLED": "0"})
    def test_disabled_via_env(self):
        mgr = LSPManager()
        assert not mgr._enabled

    @patch.dict("os.environ", {"FLYTO_LSP_TIMEOUT": "5"})
    def test_timeout_from_env(self):
        mgr = LSPManager()
        assert mgr._timeout == 5.0
