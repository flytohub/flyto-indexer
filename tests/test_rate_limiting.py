"""Tests for rate limiting in mcp_server.py — deque-based O(1) implementation."""

import os
import sys
import time
from collections import deque
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import mcp_server


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Reset rate limiting state before each test."""
    mcp_server._rate_limit_timestamps.clear()
    mcp_server._session_rate_limits.clear()
    yield
    mcp_server._rate_limit_timestamps.clear()
    mcp_server._session_rate_limits.clear()


class TestRateLimitDataStructures:

    def test_timestamps_is_deque(self):
        assert isinstance(mcp_server._rate_limit_timestamps, deque)

    def test_session_creates_deque(self):
        mcp_server._check_rate_limit(session_id="test-session")
        assert isinstance(mcp_server._session_rate_limits["test-session"], deque)


class TestGlobalRateLimit:

    def test_allows_within_limit(self):
        for _ in range(5):
            assert mcp_server._check_rate_limit() is True

    def test_blocks_at_limit(self):
        original_max = mcp_server._RATE_LIMIT_MAX
        try:
            mcp_server._RATE_LIMIT_MAX = 3
            assert mcp_server._check_rate_limit() is True
            assert mcp_server._check_rate_limit() is True
            assert mcp_server._check_rate_limit() is True
            assert mcp_server._check_rate_limit() is False
        finally:
            mcp_server._RATE_LIMIT_MAX = original_max

    def test_evicts_old_timestamps(self):
        original_max = mcp_server._RATE_LIMIT_MAX
        try:
            mcp_server._RATE_LIMIT_MAX = 2
            # Add old timestamps (before the window)
            old_time = time.monotonic() - mcp_server._RATE_LIMIT_WINDOW - 1
            mcp_server._rate_limit_timestamps.append(old_time)
            mcp_server._rate_limit_timestamps.append(old_time)
            # Should still allow — old timestamps get evicted
            assert mcp_server._check_rate_limit() is True
            # Old entries should have been removed
            assert len(mcp_server._rate_limit_timestamps) == 1
        finally:
            mcp_server._RATE_LIMIT_MAX = original_max


class TestSessionRateLimit:

    def test_allows_within_session_limit(self):
        for _ in range(5):
            assert mcp_server._check_rate_limit(session_id="sess1") is True

    def test_blocks_session_at_limit(self):
        original_max = mcp_server._RATE_LIMIT_SESSION_MAX
        try:
            mcp_server._RATE_LIMIT_SESSION_MAX = 2
            assert mcp_server._check_rate_limit(session_id="sess1") is True
            assert mcp_server._check_rate_limit(session_id="sess1") is True
            assert mcp_server._check_rate_limit(session_id="sess1") is False
            # Different session should still be allowed
            assert mcp_server._check_rate_limit(session_id="sess2") is True
        finally:
            mcp_server._RATE_LIMIT_SESSION_MAX = original_max

    def test_evicts_old_session_timestamps(self):
        original_max = mcp_server._RATE_LIMIT_SESSION_MAX
        try:
            mcp_server._RATE_LIMIT_SESSION_MAX = 1
            # Add old timestamp
            old_time = time.monotonic() - mcp_server._RATE_LIMIT_WINDOW - 1
            mcp_server._session_rate_limits["sess1"] = deque([old_time])
            # Should still allow after eviction
            assert mcp_server._check_rate_limit(session_id="sess1") is True
        finally:
            mcp_server._RATE_LIMIT_SESSION_MAX = original_max

    def test_session_bucket_eviction(self):
        """Oldest session bucket is evicted when exceeding 200 sessions."""
        # Create 201 sessions to trigger eviction
        for i in range(201):
            mcp_server._check_rate_limit(session_id=f"sess-{i}")
        assert len(mcp_server._session_rate_limits) <= 201


class TestNoSessionRateLimit:

    def test_empty_session_id_skips_session_check(self):
        original_max = mcp_server._RATE_LIMIT_SESSION_MAX
        try:
            mcp_server._RATE_LIMIT_SESSION_MAX = 1
            # With no session_id, session limit should not apply
            assert mcp_server._check_rate_limit(session_id="") is True
            assert mcp_server._check_rate_limit(session_id="") is True
            assert mcp_server._check_rate_limit(session_id="") is True
        finally:
            mcp_server._RATE_LIMIT_SESSION_MAX = original_max
