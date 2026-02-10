"""
Tests for session.py — workspace session tracking for search boost.

Covers:
- Session LRU behavior (add_file, add_query, add_edit)
- Capacity limits (50 files, 20 queries, 20 edits)
- Boost path computation
- Expiry logic
- Serialization (to_dict)
- SessionStore CRUD, eviction, and expired session cleanup
- Edge cases (empty IDs, unicode, concurrent-like access)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import patch

from session import Session, SessionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(**kwargs) -> Session:
    """Create a Session with sensible defaults, allowing overrides."""
    defaults = {"session_id": "test-session", "workspace_root": "/tmp/project"}
    defaults.update(kwargs)
    return Session(**defaults)


# ===========================================================================
# Session.add_file()
# ===========================================================================


class TestSessionAddFile:
    """Test Session.add_file() LRU behavior."""

    def test_add_file_basic(self):
        s = make_session()
        s.add_file("a.py")
        assert s.open_files == ["a.py"]

    def test_add_file_ordering(self):
        """Most recently added file should be at index 0."""
        s = make_session()
        s.add_file("a.py")
        s.add_file("b.py")
        s.add_file("c.py")
        assert s.open_files == ["c.py", "b.py", "a.py"]

    def test_add_file_duplicate_moves_to_front(self):
        """Adding a file that already exists should move it to front (LRU)."""
        s = make_session()
        s.add_file("a.py")
        s.add_file("b.py")
        s.add_file("c.py")
        # Now add 'a.py' again
        s.add_file("a.py")
        assert s.open_files[0] == "a.py"
        assert s.open_files == ["a.py", "c.py", "b.py"]

    def test_add_file_duplicate_does_not_increase_length(self):
        s = make_session()
        s.add_file("a.py")
        s.add_file("b.py")
        s.add_file("a.py")
        assert len(s.open_files) == 2

    def test_add_file_max_limit(self):
        """open_files should never exceed MAX_OPEN_FILES (50)."""
        s = make_session()
        for i in range(60):
            s.add_file(f"file_{i}.py")
        assert len(s.open_files) == Session.MAX_OPEN_FILES
        # Most recent should be first
        assert s.open_files[0] == "file_59.py"
        # Oldest beyond limit should be gone
        assert "file_0.py" not in s.open_files

    def test_add_file_at_exact_limit(self):
        """Adding exactly MAX_OPEN_FILES files should be fine."""
        s = make_session()
        for i in range(50):
            s.add_file(f"file_{i}.py")
        assert len(s.open_files) == 50
        assert s.open_files[0] == "file_49.py"

    def test_add_file_updates_last_active(self):
        s = make_session()
        old_active = s.last_active
        time.sleep(0.01)
        s.add_file("a.py")
        assert s.last_active > old_active


# ===========================================================================
# Session.add_query()
# ===========================================================================


class TestSessionAddQuery:
    """Test Session.add_query() LRU behavior."""

    def test_add_query_basic(self):
        s = make_session()
        s.add_query("useAuth")
        assert s.recent_queries == ["useAuth"]

    def test_add_query_ordering(self):
        s = make_session()
        s.add_query("first")
        s.add_query("second")
        s.add_query("third")
        assert s.recent_queries == ["third", "second", "first"]

    def test_add_query_duplicate_moves_to_front(self):
        s = make_session()
        s.add_query("alpha")
        s.add_query("beta")
        s.add_query("gamma")
        s.add_query("alpha")
        assert s.recent_queries[0] == "alpha"
        assert s.recent_queries == ["alpha", "gamma", "beta"]

    def test_add_query_duplicate_does_not_increase_length(self):
        s = make_session()
        s.add_query("x")
        s.add_query("y")
        s.add_query("x")
        assert len(s.recent_queries) == 2

    def test_add_query_max_limit(self):
        """recent_queries should never exceed MAX_RECENT_QUERIES (20)."""
        s = make_session()
        for i in range(30):
            s.add_query(f"query_{i}")
        assert len(s.recent_queries) == Session.MAX_RECENT_QUERIES
        assert s.recent_queries[0] == "query_29"
        assert "query_0" not in s.recent_queries

    def test_add_query_at_exact_limit(self):
        s = make_session()
        for i in range(20):
            s.add_query(f"q_{i}")
        assert len(s.recent_queries) == 20
        assert s.recent_queries[0] == "q_19"

    def test_add_query_updates_last_active(self):
        s = make_session()
        old_active = s.last_active
        time.sleep(0.01)
        s.add_query("test")
        assert s.last_active > old_active


# ===========================================================================
# Session.add_edit()
# ===========================================================================


class TestSessionAddEdit:
    """Test Session.add_edit() LRU behavior."""

    def test_add_edit_basic(self):
        s = make_session()
        s.add_edit("src/main.py")
        assert s.recent_edits == ["src/main.py"]

    def test_add_edit_ordering(self):
        s = make_session()
        s.add_edit("a.py")
        s.add_edit("b.py")
        s.add_edit("c.py")
        assert s.recent_edits == ["c.py", "b.py", "a.py"]

    def test_add_edit_duplicate_moves_to_front(self):
        s = make_session()
        s.add_edit("a.py")
        s.add_edit("b.py")
        s.add_edit("c.py")
        s.add_edit("a.py")
        assert s.recent_edits[0] == "a.py"
        assert s.recent_edits == ["a.py", "c.py", "b.py"]

    def test_add_edit_duplicate_does_not_increase_length(self):
        s = make_session()
        s.add_edit("x")
        s.add_edit("y")
        s.add_edit("x")
        assert len(s.recent_edits) == 2

    def test_add_edit_max_limit(self):
        """recent_edits should never exceed MAX_RECENT_EDITS (20)."""
        s = make_session()
        for i in range(30):
            s.add_edit(f"edit_{i}.py")
        assert len(s.recent_edits) == Session.MAX_RECENT_EDITS
        assert s.recent_edits[0] == "edit_29.py"
        assert "edit_0.py" not in s.recent_edits

    def test_add_edit_at_exact_limit(self):
        s = make_session()
        for i in range(20):
            s.add_edit(f"e_{i}")
        assert len(s.recent_edits) == 20
        assert s.recent_edits[0] == "e_19"

    def test_add_edit_updates_last_active(self):
        s = make_session()
        old_active = s.last_active
        time.sleep(0.01)
        s.add_edit("test.py")
        assert s.last_active > old_active


# ===========================================================================
# Session.get_boost_paths()
# ===========================================================================


class TestSessionBoostPaths:
    """Test Session.get_boost_paths()."""

    def test_empty_session(self):
        s = make_session()
        assert s.get_boost_paths() == set()

    def test_only_open_files(self):
        s = make_session()
        s.add_file("a.py")
        s.add_file("b.py")
        assert s.get_boost_paths() == {"a.py", "b.py"}

    def test_only_recent_edits(self):
        s = make_session()
        s.add_edit("c.py")
        s.add_edit("d.py")
        assert s.get_boost_paths() == {"c.py", "d.py"}

    def test_union_of_files_and_edits(self):
        """get_boost_paths returns the union of open_files and recent_edits."""
        s = make_session()
        s.add_file("a.py")
        s.add_file("b.py")
        s.add_edit("b.py")
        s.add_edit("c.py")
        assert s.get_boost_paths() == {"a.py", "b.py", "c.py"}

    def test_queries_not_included(self):
        """recent_queries should NOT be part of boost paths."""
        s = make_session()
        s.add_file("a.py")
        s.add_query("some query")
        s.add_edit("b.py")
        boost = s.get_boost_paths()
        assert "some query" not in boost
        assert boost == {"a.py", "b.py"}

    def test_returns_set_type(self):
        s = make_session()
        s.add_file("a.py")
        result = s.get_boost_paths()
        assert isinstance(result, set)

    def test_deduplication(self):
        """Same path in both open_files and recent_edits appears once in the set."""
        s = make_session()
        s.add_file("shared.py")
        s.add_edit("shared.py")
        boost = s.get_boost_paths()
        assert len(boost) == 1
        assert "shared.py" in boost


# ===========================================================================
# Session.is_expired()
# ===========================================================================


class TestSessionExpiry:
    """Test Session.is_expired()."""

    def test_fresh_session_not_expired(self):
        s = make_session()
        assert s.is_expired() is False

    def test_expired_session_default_ttl(self):
        """Session inactive for >24h should be expired."""
        s = make_session()
        s.last_active = time.time() - 86401  # 24h + 1 second
        assert s.is_expired() is True

    def test_not_expired_just_under_ttl(self):
        s = make_session()
        s.last_active = time.time() - 86399  # 24h - 1 second
        assert s.is_expired() is False

    def test_expired_custom_ttl(self):
        s = make_session()
        s.last_active = time.time() - 61
        assert s.is_expired(ttl=60) is True

    def test_not_expired_custom_ttl(self):
        s = make_session()
        s.last_active = time.time() - 30
        assert s.is_expired(ttl=60) is False

    def test_expired_zero_ttl(self):
        """With TTL=0, any session older than now is expired."""
        s = make_session()
        s.last_active = time.time() - 0.001
        assert s.is_expired(ttl=0) is True

    def test_activity_resets_expiry(self):
        """Adding a file should reset last_active and prevent expiry."""
        s = make_session()
        s.last_active = time.time() - 86401  # Would be expired
        assert s.is_expired() is True
        s.add_file("x.py")  # This resets last_active
        assert s.is_expired() is False


# ===========================================================================
# Session.to_dict()
# ===========================================================================


class TestSessionSerialization:
    """Test Session.to_dict()."""

    def test_to_dict_keys(self):
        s = make_session()
        d = s.to_dict()
        expected_keys = {
            "session_id",
            "workspace_root",
            "open_files",
            "open_files_count",
            "recent_queries",
            "recent_queries_count",
            "recent_edits",
            "recent_edits_count",
            "created_at",
            "last_active",
            "boost_paths_count",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values_empty_session(self):
        s = make_session(session_id="abc", workspace_root="/home")
        d = s.to_dict()
        assert d["session_id"] == "abc"
        assert d["workspace_root"] == "/home"
        assert d["open_files"] == []
        assert d["open_files_count"] == 0
        assert d["recent_queries"] == []
        assert d["recent_queries_count"] == 0
        assert d["recent_edits"] == []
        assert d["recent_edits_count"] == 0
        assert d["boost_paths_count"] == 0

    def test_to_dict_truncates_open_files_to_10(self):
        """to_dict() only includes first 10 open_files for display."""
        s = make_session()
        for i in range(25):
            s.add_file(f"file_{i}.py")
        d = s.to_dict()
        assert len(d["open_files"]) == 10
        assert d["open_files_count"] == 25

    def test_to_dict_truncates_queries_to_5(self):
        """to_dict() only includes first 5 recent_queries for display."""
        s = make_session()
        for i in range(15):
            s.add_query(f"query_{i}")
        d = s.to_dict()
        assert len(d["recent_queries"]) == 5
        assert d["recent_queries_count"] == 15

    def test_to_dict_truncates_edits_to_5(self):
        """to_dict() only includes first 5 recent_edits for display."""
        s = make_session()
        for i in range(15):
            s.add_edit(f"edit_{i}")
        d = s.to_dict()
        assert len(d["recent_edits"]) == 5
        assert d["recent_edits_count"] == 15

    def test_to_dict_boost_paths_count(self):
        s = make_session()
        s.add_file("a.py")
        s.add_file("b.py")
        s.add_edit("b.py")
        s.add_edit("c.py")
        d = s.to_dict()
        # Union of {a.py, b.py} and {b.py, c.py} = {a.py, b.py, c.py}
        assert d["boost_paths_count"] == 3

    def test_to_dict_timestamps_are_floats(self):
        s = make_session()
        d = s.to_dict()
        assert isinstance(d["created_at"], float)
        assert isinstance(d["last_active"], float)

    def test_to_dict_fewer_than_truncation_limit(self):
        """When fewer items than the truncation limit, all are included."""
        s = make_session()
        s.add_file("only.py")
        s.add_query("only query")
        s.add_edit("only edit")
        d = s.to_dict()
        assert d["open_files"] == ["only.py"]
        assert d["recent_queries"] == ["only query"]
        assert d["recent_edits"] == ["only edit"]


# ===========================================================================
# Session.last_active updates
# ===========================================================================


class TestSessionLastActive:
    """Test that last_active updates on each add_* call."""

    def test_add_file_updates_last_active(self):
        s = make_session()
        before = s.last_active
        time.sleep(0.01)
        s.add_file("x.py")
        assert s.last_active > before

    def test_add_query_updates_last_active(self):
        s = make_session()
        before = s.last_active
        time.sleep(0.01)
        s.add_query("search")
        assert s.last_active > before

    def test_add_edit_updates_last_active(self):
        s = make_session()
        before = s.last_active
        time.sleep(0.01)
        s.add_edit("y.py")
        assert s.last_active > before

    def test_sequential_updates_increase_monotonically(self):
        s = make_session()
        timestamps = []
        time.sleep(0.01)
        s.add_file("a.py")
        timestamps.append(s.last_active)
        time.sleep(0.01)
        s.add_query("q")
        timestamps.append(s.last_active)
        time.sleep(0.01)
        s.add_edit("e.py")
        timestamps.append(s.last_active)
        assert timestamps == sorted(timestamps)
        assert len(set(timestamps)) == 3  # All distinct


# ===========================================================================
# SessionStore basic operations
# ===========================================================================


class TestSessionStoreBasic:
    """Test SessionStore basic operations."""

    def test_get_or_create_new_session(self):
        store = SessionStore()
        session = store.get_or_create("s1", "/workspace")
        assert session.session_id == "s1"
        assert session.workspace_root == "/workspace"
        assert isinstance(session, Session)

    def test_get_or_create_returns_existing(self):
        store = SessionStore()
        s1 = store.get_or_create("s1", "/workspace")
        s1.add_file("a.py")
        s2 = store.get_or_create("s1", "/workspace")
        assert s1 is s2
        assert s2.open_files == ["a.py"]

    def test_get_or_create_default_workspace_root(self):
        store = SessionStore()
        session = store.get_or_create("s1")
        assert session.workspace_root == ""

    def test_get_returns_existing(self):
        store = SessionStore()
        created = store.get_or_create("s1", "/ws")
        fetched = store.get("s1")
        assert fetched is created

    def test_get_returns_none_for_unknown(self):
        store = SessionStore()
        assert store.get("nonexistent") is None

    def test_multiple_sessions(self):
        store = SessionStore()
        s1 = store.get_or_create("s1")
        s2 = store.get_or_create("s2")
        s3 = store.get_or_create("s3")
        assert s1.session_id == "s1"
        assert s2.session_id == "s2"
        assert s3.session_id == "s3"
        assert store.get("s1") is s1
        assert store.get("s2") is s2
        assert store.get("s3") is s3

    def test_sessions_are_independent(self):
        store = SessionStore()
        s1 = store.get_or_create("s1")
        s2 = store.get_or_create("s2")
        s1.add_file("file_in_s1.py")
        assert s2.open_files == []
        assert s1.open_files == ["file_in_s1.py"]


# ===========================================================================
# SessionStore eviction
# ===========================================================================


class TestSessionStoreEviction:
    """Test SessionStore MAX_SESSIONS and eviction."""

    def test_max_sessions_limit(self):
        """Store should not exceed MAX_SESSIONS."""
        store = SessionStore()
        for i in range(110):
            store.get_or_create(f"session_{i}")
        assert len(store._sessions) <= SessionStore.MAX_SESSIONS

    def test_evict_oldest_removes_least_recently_active(self):
        store = SessionStore()
        # Create sessions with staggered last_active times
        s_old = store.get_or_create("old")
        s_old.last_active = 1000.0

        s_mid = store.get_or_create("mid")
        s_mid.last_active = 2000.0

        s_new = store.get_or_create("new")
        s_new.last_active = 3000.0

        store._evict_oldest()
        assert store.get("old") is None  # Evicted (or expired, either way gone)
        assert "old" not in store._sessions
        assert "mid" in store._sessions
        assert "new" in store._sessions

    def test_evict_oldest_on_empty_store(self):
        """_evict_oldest should not crash on empty store."""
        store = SessionStore()
        store._evict_oldest()  # Should not raise

    def test_new_session_evicts_oldest_at_capacity(self):
        """When at MAX_SESSIONS, creating a new session evicts the oldest."""
        store = SessionStore()
        # Fill to capacity
        for i in range(SessionStore.MAX_SESSIONS):
            s = store.get_or_create(f"s_{i}")
            s.last_active = float(i)  # s_0 is oldest

        # One more should evict s_0
        store.get_or_create("new_session")
        assert "s_0" not in store._sessions
        assert "new_session" in store._sessions
        assert len(store._sessions) <= SessionStore.MAX_SESSIONS

    def test_eviction_targets_correct_session(self):
        """Eviction should target the session with the oldest last_active."""
        store = SessionStore()
        for i in range(SessionStore.MAX_SESSIONS):
            s = store.get_or_create(f"s_{i}")
            s.last_active = float(i + 100)

        # Make s_50 the oldest
        store._sessions["s_50"].last_active = 1.0

        store.get_or_create("trigger_eviction")
        assert "s_50" not in store._sessions


# ===========================================================================
# SessionStore expired session handling
# ===========================================================================


class TestSessionStoreExpiry:
    """Test expired session handling in store."""

    def test_get_or_create_recreates_expired_session(self):
        store = SessionStore()
        s1 = store.get_or_create("s1", "/old")
        s1.last_active = time.time() - 86401  # Expired

        s2 = store.get_or_create("s1", "/new")
        assert s2 is not s1
        assert s2.workspace_root == "/new"
        assert s2.open_files == []  # Fresh session

    def test_get_returns_none_for_expired(self):
        store = SessionStore()
        s = store.get_or_create("s1")
        s.last_active = time.time() - 86401
        assert store.get("s1") is None

    def test_get_cleans_up_expired_session(self):
        """get() should remove expired sessions from internal dict."""
        store = SessionStore()
        store.get_or_create("s1")
        store._sessions["s1"].last_active = time.time() - 86401
        store.get("s1")
        assert "s1" not in store._sessions

    def test_get_or_create_expired_then_readd(self):
        """Expired session replaced by get_or_create should be fully fresh."""
        store = SessionStore()
        s1 = store.get_or_create("s1")
        s1.add_file("old_file.py")
        s1.add_query("old_query")
        s1.add_edit("old_edit.py")
        s1.last_active = time.time() - 86401

        s2 = store.get_or_create("s1")
        assert s2.open_files == []
        assert s2.recent_queries == []
        assert s2.recent_edits == []
        assert s2.is_expired() is False


# ===========================================================================
# Concurrent-like access
# ===========================================================================


class TestConcurrentAccess:
    """Test concurrent-like access patterns."""

    def test_create_many_sessions_rapidly(self):
        store = SessionStore()
        sessions = []
        for i in range(200):
            s = store.get_or_create(f"rapid_{i}")
            s.add_file(f"file_{i}.py")
            sessions.append(s)

        # Store should be at capacity
        assert len(store._sessions) <= SessionStore.MAX_SESSIONS

    def test_interleave_operations_across_sessions(self):
        store = SessionStore()
        s1 = store.get_or_create("s1")
        s2 = store.get_or_create("s2")

        # Interleave operations
        s1.add_file("a.py")
        s2.add_file("b.py")
        s1.add_query("q1")
        s2.add_edit("e2.py")
        s1.add_edit("e1.py")
        s2.add_query("q2")

        assert s1.open_files == ["a.py"]
        assert s2.open_files == ["b.py"]
        assert s1.recent_queries == ["q1"]
        assert s2.recent_queries == ["q2"]
        assert s1.recent_edits == ["e1.py"]
        assert s2.recent_edits == ["e2.py"]

    def test_rapid_duplicate_adds(self):
        """Rapidly adding the same file should not cause issues."""
        s = make_session()
        for _ in range(1000):
            s.add_file("same_file.py")
        assert len(s.open_files) == 1
        assert s.open_files == ["same_file.py"]

    def test_interleave_get_and_create(self):
        store = SessionStore()
        for i in range(50):
            store.get_or_create(f"s_{i}")
            # Interleave gets of previous sessions
            if i > 0:
                fetched = store.get(f"s_{i - 1}")
                # May or may not exist due to eviction, but should not crash
                if fetched:
                    fetched.add_file(f"accessed_from_{i}.py")


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Test edge cases."""

    def test_empty_session_id(self):
        """Empty string as session_id should work."""
        store = SessionStore()
        s = store.get_or_create("")
        assert s.session_id == ""
        assert store.get("") is s

    def test_very_long_session_id(self):
        long_id = "x" * 10000
        store = SessionStore()
        s = store.get_or_create(long_id)
        assert s.session_id == long_id
        assert store.get(long_id) is s

    def test_unicode_session_id(self):
        store = SessionStore()
        s = store.get_or_create("session-with-unicode")
        assert s.session_id == "session-with-unicode"

    def test_unicode_paths(self):
        s = make_session()
        s.add_file("src/components/Header.vue")
        s.add_edit("docs/README.md")
        assert "src/components/Header.vue" in s.open_files
        assert "docs/README.md" in s.recent_edits
        boost = s.get_boost_paths()
        assert "src/components/Header.vue" in boost

    def test_unicode_queries(self):
        s = make_session()
        s.add_query("search query")
        assert s.recent_queries == ["search query"]

    def test_empty_string_file(self):
        s = make_session()
        s.add_file("")
        assert s.open_files == [""]

    def test_empty_string_query(self):
        s = make_session()
        s.add_query("")
        assert s.recent_queries == [""]

    def test_empty_string_edit(self):
        s = make_session()
        s.add_edit("")
        assert s.recent_edits == [""]

    def test_session_class_constants(self):
        """Verify class-level constants are correct."""
        assert Session.MAX_OPEN_FILES == 50
        assert Session.MAX_RECENT_QUERIES == 20
        assert Session.MAX_RECENT_EDITS == 20

    def test_session_store_max_sessions_constant(self):
        assert SessionStore.MAX_SESSIONS == 100

    def test_paths_with_spaces(self):
        s = make_session()
        s.add_file("/path/with spaces/file.py")
        assert s.open_files == ["/path/with spaces/file.py"]

    def test_paths_with_special_characters(self):
        s = make_session()
        special = "/path/to/file (copy).py"
        s.add_file(special)
        assert special in s.open_files
        assert special in s.get_boost_paths()

    def test_session_created_at_is_preserved(self):
        """created_at should not change after operations."""
        s = make_session()
        original_created = s.created_at
        time.sleep(0.01)
        s.add_file("a.py")
        s.add_query("q")
        s.add_edit("e.py")
        assert s.created_at == original_created

    def test_to_dict_returns_new_dict(self):
        """to_dict should return a new dict each time (not a reference)."""
        s = make_session()
        d1 = s.to_dict()
        d2 = s.to_dict()
        assert d1 == d2
        assert d1 is not d2

    def test_get_boost_paths_returns_new_set(self):
        """get_boost_paths should return a new set each time."""
        s = make_session()
        s.add_file("a.py")
        set1 = s.get_boost_paths()
        set2 = s.get_boost_paths()
        assert set1 == set2
        assert set1 is not set2
