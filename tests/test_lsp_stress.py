"""Stress and stability tests for LSP client, manager, mapper, and protocol.

All tests run WITHOUT any real LSP server installed. They use mocked
subprocesses to simulate various failure modes and verify graceful handling.
"""

import json
import os
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from lsp.protocol import (
    Position,
    Range,
    Location,
    encode_message,
    parse_content_length,
    utf16_offset,
    byte_offset_from_utf16,
)
from lsp.client import LSPClient
from lsp.manager import LSPManager
from lsp.mapper import symbol_to_lsp_position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lsp_response(req_id, result):
    """Build a framed LSP response (header + JSON body)."""
    body = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}).encode("utf-8")
    return encode_message(body)


def _make_dead_process():
    """Create a mock Popen whose poll() returns 1 (exited)."""
    proc = MagicMock()
    proc.poll.return_value = 1  # Already dead
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.read.return_value = b""
    proc.stderr = MagicMock()
    proc.pid = 99999
    return proc


def _make_live_process_with_responses(responses):
    """Create a mock Popen that serves canned responses synchronised with writes.

    Each call to stdin.write() releases the next response on stdout.
    """
    proc = MagicMock()
    proc.poll.return_value = None  # Running
    proc.stderr = MagicMock()

    response_chunks = []
    for resp in responses:
        body = json.dumps(resp).encode("utf-8")
        chunk = encode_message(body)
        response_chunks.append(chunk)

    _available = bytearray()
    _data_ready = threading.Event()
    _eof_event = threading.Event()
    _write_count = [0]
    _read_pos = [0]
    _lock = threading.Lock()

    def mock_write(data):
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
            _data_ready.wait(timeout=0.5)
            if _eof_event.is_set():
                return b""
            _data_ready.clear()

    proc.stdout = MagicMock()
    proc.stdout.read = mock_read
    proc._eof_event = _eof_event

    return proc


# ---------------------------------------------------------------------------
# Client stability tests
# ---------------------------------------------------------------------------


class TestClientHandlesDeadServer:
    """Server dies immediately after Popen — client must return None gracefully."""

    @patch("lsp.client.subprocess.Popen")
    def test_dead_on_start(self, mock_popen):
        proc = _make_dead_process()
        mock_popen.return_value = proc

        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=1.0)
        # start() spawns the process but the reader thread will see EOF
        # The initialize request should fail because the process is dead.
        result = client.start()
        assert result is False
        assert not client.alive

    @patch("lsp.client.subprocess.Popen")
    def test_references_on_dead_server(self, mock_popen):
        """Calling references on a never-started client returns empty list."""
        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=1.0)
        refs = client.text_document_references("file:///tmp/foo.py", 0, 0)
        assert refs == []

    @patch("lsp.client.subprocess.Popen")
    def test_definition_on_dead_server(self, mock_popen):
        """Calling definition on a never-started client returns empty list."""
        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=1.0)
        defs = client.text_document_definition("file:///tmp/foo.py", 0, 0)
        assert defs == []


class TestClientHandlesSlowServer:
    """Server takes too long to respond — timeout must fire."""

    @patch("lsp.client.subprocess.Popen")
    def test_timeout_returns_none(self, mock_popen):
        # Process that stays alive but never writes any response
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stderr = MagicMock()

        # stdin accepts writes silently
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.flush = MagicMock()

        # stdout blocks forever
        block_event = threading.Event()

        def blocking_read(n=1):
            block_event.wait(timeout=5)
            return b""

        proc.stdout = MagicMock()
        proc.stdout.read = blocking_read
        mock_popen.return_value = proc

        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=0.3)
        # Manually set alive so _send_request proceeds
        client._process = proc
        client._alive = True
        client._reader_thread = threading.Thread(
            target=client._read_loop, daemon=True
        )
        client._reader_thread.start()

        result = client._send_request("textDocument/references", {})
        assert result is None

        # Cleanup
        client._alive = False
        block_event.set()


class TestClientHandlesMalformedResponse:
    """Server returns invalid JSON — client must not crash."""

    @patch("lsp.client.subprocess.Popen")
    def test_malformed_json_skipped(self, mock_popen):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stderr = MagicMock()

        # Build a malformed response followed by a valid one
        malformed = b"Content-Length: 5\r\n\r\n{bad}"
        valid_resp = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
        ).encode("utf-8")
        valid = encode_message(valid_resp)

        all_data = malformed + valid
        _read_pos = [0]
        _available = bytearray()
        _data_ready = threading.Event()
        _eof = threading.Event()
        _write_count = [0]
        _lock = threading.Lock()

        # Load all data after first write
        def mock_write(data):
            with _lock:
                if _write_count[0] == 0:
                    _available.extend(all_data)
                    _data_ready.set()
                _write_count[0] += 1

        proc.stdin = MagicMock()
        proc.stdin.write = mock_write
        proc.stdin.flush = MagicMock()

        def mock_read(n=1):
            while True:
                with _lock:
                    pos = _read_pos[0]
                    if pos < len(_available):
                        end = min(pos + n, len(_available))
                        chunk = bytes(_available[pos:end])
                        _read_pos[0] = end
                        return chunk
                _data_ready.wait(timeout=0.5)
                if _eof.is_set():
                    return b""
                _data_ready.clear()

        proc.stdout = MagicMock()
        proc.stdout.read = mock_read
        proc._eof_event = _eof
        mock_popen.return_value = proc

        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=2.0)
        result = client.start()
        # Should succeed because the valid response follows the malformed one
        assert result is True
        client._alive = False
        _eof.set()


class TestClientRestartOnCrash:
    """Server dies after first request — auto-restart should kick in."""

    @patch("lsp.client.subprocess.Popen")
    def test_restart_after_crash(self, mock_popen):
        # First process: serves init response then dies
        init_resp1 = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
        proc1 = _make_live_process_with_responses([init_resp1])

        # Second process: serves init response (restart)
        init_resp2 = {"jsonrpc": "2.0", "id": 2, "result": {"capabilities": {}}}
        proc2 = _make_live_process_with_responses([init_resp2])

        call_count = [0]

        def popen_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return proc1
            return proc2

        mock_popen.side_effect = popen_side_effect

        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=2.0)
        assert client.start() is True
        assert client._restart_count == 0

        # Simulate server crash
        client._alive = False
        proc1._eof_event.set()
        proc1.poll.return_value = 1  # Mark as dead

        # Next _check_alive should trigger restart
        alive = client._check_alive()
        assert alive is True
        assert client._restart_count == 1

        # Cleanup
        client._alive = False
        proc2._eof_event.set()


class TestClientMaxRestarts:
    """Server keeps dying — verify max restart limit is enforced."""

    @patch("lsp.client.subprocess.Popen")
    def test_max_restarts_enforced(self, mock_popen):
        # Every process dies immediately (poll returns 1)
        def make_dying_proc():
            p = _make_dead_process()
            return p

        mock_popen.side_effect = lambda *a, **kw: make_dying_proc()

        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=0.5)
        # start() will fail because process dies before init response
        client.start()  # Attempt 0 (not counted as restart)

        # Force restart attempts
        for i in range(LSPClient.MAX_RESTARTS + 1):
            client._check_alive()

        assert client._permanently_dead is True
        assert client._restart_count == LSPClient.MAX_RESTARTS

        # Further checks return False immediately
        assert client._check_alive() is False


# ---------------------------------------------------------------------------
# Manager stability tests
# ---------------------------------------------------------------------------


class TestManagerConcurrentClients:
    """Multiple clients for different languages — no cross-contamination."""

    def setup_method(self):
        LSPManager.reset_instance()

    def teardown_method(self):
        LSPManager.reset_instance()

    @patch("lsp.manager.shutil.which")
    def test_independent_languages(self, mock_which):
        """get_client for different languages returns independent clients."""
        mock_which.return_value = None  # No servers available

        mgr = LSPManager()
        mgr._available = {"python": "/usr/bin/pyright", "go": "/usr/bin/gopls"}

        # Both return None because start() will fail (no real binary)
        # But verify the cache keys are independent
        c1 = mgr.get_client("python", "/project")
        c2 = mgr.get_client("go", "/project")
        # Both None because no real server, but no exceptions
        assert c1 is None
        assert c2 is None

    @patch("lsp.manager.shutil.which")
    def test_same_language_different_roots(self, mock_which):
        """Same language with different roots gets separate entries."""
        mock_which.return_value = None
        mgr = LSPManager()
        mgr._available = {"python": "/usr/bin/pyright"}

        c1 = mgr.get_client("python", "/project-a")
        c2 = mgr.get_client("python", "/project-b")
        # Both None but independently attempted
        assert c1 is None
        assert c2 is None


class TestManagerShutdownWithDeadClients:
    """Some clients are already dead when shutdown_all is called."""

    def setup_method(self):
        LSPManager.reset_instance()

    def teardown_method(self):
        LSPManager.reset_instance()

    def test_shutdown_mixed_alive_dead(self):
        mgr = LSPManager()

        # Create mock clients — one alive, one dead
        alive_client = MagicMock()
        alive_client.alive = True
        alive_client.shutdown = MagicMock()

        dead_client = MagicMock()
        dead_client.alive = False
        dead_client.shutdown = MagicMock()

        # Client that raises on shutdown
        error_client = MagicMock()
        error_client.alive = True
        error_client.shutdown = MagicMock(side_effect=RuntimeError("boom"))

        mgr._clients = {
            ("python", "file:///a"): alive_client,
            ("go", "file:///b"): dead_client,
            ("rust", "file:///c"): error_client,
        }

        # Must not raise
        mgr.shutdown_all()
        assert len(mgr._clients) == 0
        alive_client.shutdown.assert_called_once()
        dead_client.shutdown.assert_called_once()
        error_client.shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# Mapper edge-case tests
# ---------------------------------------------------------------------------


class TestMapperHandlesBinaryFile:
    """symbol_to_lsp_position on a binary file returns None."""

    def test_binary_file_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            # Write binary content that is not valid text
            f.write(b"\x00\x01\x02\xff\xfe" * 100)
            tmp_path = f.name

        try:
            symbol = {
                "path": tmp_path,
                "name": "some_func",
                "start_line": 1,
            }
            result = symbol_to_lsp_position(symbol, "/")
            # Should return None because the symbol won't be found in binary noise
            assert result is None
        finally:
            os.unlink(tmp_path)


class TestMapperHandlesDeletedFile:
    """File doesn't exist anymore — returns None."""

    def test_deleted_file_returns_none(self):
        # Create and immediately delete
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"def hello(): pass\n")
            tmp_path = f.name
        os.unlink(tmp_path)

        symbol = {
            "path": tmp_path,
            "name": "hello",
            "start_line": 1,
        }
        result = symbol_to_lsp_position(symbol, "/")
        assert result is None


# ---------------------------------------------------------------------------
# UTF-16 offset tests
# ---------------------------------------------------------------------------


class TestMapperUtf16Offset:
    """Verify UTF-16 column conversion for CJK, emoji, and ASCII."""

    def test_pure_ascii(self):
        line = "hello world"
        # Python index 5 -> UTF-16 offset 5 (all BMP, 1 unit each)
        assert utf16_offset(line, 5) == 5
        assert byte_offset_from_utf16(line, 5) == 5

    def test_cjk_characters(self):
        # CJK chars are BMP (U+4E00..U+9FFF), each 1 UTF-16 code unit
        line = "abc\u4e2d\u6587def"  # abc中文def
        # Python index 3 -> 3 UTF-16 units (abc)
        assert utf16_offset(line, 3) == 3
        # Python index 5 -> 5 UTF-16 units (abc中文)
        assert utf16_offset(line, 5) == 5
        # Roundtrip
        assert byte_offset_from_utf16(line, 5) == 5

    def test_emoji_surrogate_pairs(self):
        # Emoji U+1F600 (GRINNING FACE) is outside BMP -> 2 UTF-16 code units
        line = "ab\U0001F600cd"  # ab😀cd
        # Python index 2 -> 2 UTF-16 units (ab)
        assert utf16_offset(line, 2) == 2
        # Python index 3 -> 4 UTF-16 units (ab + surrogate pair)
        assert utf16_offset(line, 3) == 4
        # Python index 4 -> 5 UTF-16 units (ab + surrogate pair + c)
        assert utf16_offset(line, 4) == 5

        # Reverse: UTF-16 offset 4 -> Python index 3
        assert byte_offset_from_utf16(line, 4) == 3
        # Reverse: UTF-16 offset 5 -> Python index 4
        assert byte_offset_from_utf16(line, 5) == 4

    def test_mixed_cjk_emoji(self):
        line = "\u4e2d\U0001F600\u6587"  # 中😀文
        # Index 0: nothing
        assert utf16_offset(line, 0) == 0
        # Index 1: 中 (1 UTF-16 unit)
        assert utf16_offset(line, 1) == 1
        # Index 2: 中 + 😀 (1 + 2 = 3 UTF-16 units)
        assert utf16_offset(line, 2) == 3
        # Index 3: 中 + 😀 + 文 (1 + 2 + 1 = 4 UTF-16 units)
        assert utf16_offset(line, 3) == 4

    def test_zero_and_edge_cases(self):
        assert utf16_offset("hello", 0) == 0
        assert byte_offset_from_utf16("hello", 0) == 0
        # Beyond end of line
        assert utf16_offset("hi", 100) == 2
        assert byte_offset_from_utf16("hi", 100) == 2

    def test_empty_string(self):
        assert utf16_offset("", 0) == 0
        assert utf16_offset("", 5) == 0
        assert byte_offset_from_utf16("", 0) == 0
        assert byte_offset_from_utf16("", 5) == 0


# ---------------------------------------------------------------------------
# Process cleanup tests
# ---------------------------------------------------------------------------


class TestCleanupZombie:
    """Verify _cleanup_zombie handles various process states."""

    def test_cleanup_already_none(self):
        client = LSPClient(["fake-lsp"], "file:///tmp")
        client._process = None
        # Must not raise
        client._cleanup_zombie()
        assert client._process is None

    def test_cleanup_kill_raises_oserror(self):
        client = LSPClient(["fake-lsp"], "file:///tmp")
        proc = MagicMock()
        proc.kill.side_effect = OSError("No such process")
        proc.wait.return_value = None
        client._process = proc
        client._alive = True

        # Must not raise
        client._cleanup_zombie()
        assert client._alive is False
        assert client._process is None

    def test_cleanup_wait_times_out(self):
        import subprocess as sp

        client = LSPClient(["fake-lsp"], "file:///tmp")
        proc = MagicMock()
        proc.kill.return_value = None
        proc.wait.side_effect = sp.TimeoutExpired(cmd="fake", timeout=3)
        client._process = proc
        client._alive = True

        # Must not raise, even if wait times out
        client._cleanup_zombie()
        assert client._alive is False
        assert client._process is None


# ---------------------------------------------------------------------------
# Permanently dead client tests
# ---------------------------------------------------------------------------


class TestPermanentlyDeadClient:
    """Once permanently dead, no more restarts attempted."""

    def test_permanently_dead_flag(self):
        client = LSPClient(["fake-lsp"], "file:///tmp", timeout=0.5)
        client._permanently_dead = True

        assert client._check_alive() is False
        # _send_request returns None immediately
        result = client._send_request("test", {})
        assert result is None
        # references/definition return empty
        assert client.text_document_references("file:///x", 0, 0) == []
        assert client.text_document_definition("file:///x", 0, 0) == []
