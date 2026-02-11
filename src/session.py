"""
Workspace Session — stateful session tracking for search boost.

Sessions are in-memory only. MCP server restart clears all sessions.
This is acceptable — sessions are boost, not required.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

# Session ID validation: alphanumeric, hyphens, underscores only, max 64 chars
_SESSION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


def validate_session_id(session_id: str) -> str:
    """
    Validate session ID format.

    Args:
        session_id: The session ID to validate

    Returns:
        The validated session ID

    Raises:
        ValueError: If the session ID format is invalid
    """
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session_id: must be 1-64 characters, alphanumeric/hyphens/underscores only. "
            f"Got: {repr(session_id)[:80]}"
        )
    return session_id


@dataclass
class Session:
    session_id: str
    workspace_root: str
    open_files: list[str] = field(default_factory=list)
    recent_queries: list[str] = field(default_factory=list)
    recent_edits: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    MAX_OPEN_FILES = 50
    MAX_RECENT_QUERIES = 20
    MAX_RECENT_EDITS = 20

    def add_file(self, path: str) -> None:
        """Track a file open event."""
        # Remove if already in list (move to front)
        if path in self.open_files:
            self.open_files.remove(path)
        self.open_files.insert(0, path)
        if len(self.open_files) > self.MAX_OPEN_FILES:
            self.open_files = self.open_files[:self.MAX_OPEN_FILES]
        self.last_active = time.time()

    def add_query(self, query: str) -> None:
        """Track a search query."""
        if query in self.recent_queries:
            self.recent_queries.remove(query)
        self.recent_queries.insert(0, query)
        if len(self.recent_queries) > self.MAX_RECENT_QUERIES:
            self.recent_queries = self.recent_queries[:self.MAX_RECENT_QUERIES]
        self.last_active = time.time()

    def add_edit(self, target: str) -> None:
        """Track an edit target (file or symbol)."""
        if target in self.recent_edits:
            self.recent_edits.remove(target)
        self.recent_edits.insert(0, target)
        if len(self.recent_edits) > self.MAX_RECENT_EDITS:
            self.recent_edits = self.recent_edits[:self.MAX_RECENT_EDITS]
        self.last_active = time.time()

    def get_boost_paths(self) -> set[str]:
        """Get set of paths that should receive search boost."""
        paths = set(self.open_files)
        paths.update(self.recent_edits)
        return paths

    def is_expired(self, ttl: int = 86400) -> bool:
        """Check if session has expired (default 24h TTL)."""
        return (time.time() - self.last_active) > ttl

    def to_dict(self) -> dict:
        """Serialize session state."""
        return {
            "session_id": self.session_id,
            "workspace_root": self.workspace_root,
            "open_files": self.open_files[:10],  # Only show recent 10
            "open_files_count": len(self.open_files),
            "recent_queries": self.recent_queries[:5],
            "recent_queries_count": len(self.recent_queries),
            "recent_edits": self.recent_edits[:5],
            "recent_edits_count": len(self.recent_edits),
            "created_at": self.created_at,
            "last_active": self.last_active,
            "boost_paths_count": len(self.get_boost_paths()),
        }


class SessionStore:
    """In-memory session store with LRU eviction."""

    MAX_SESSIONS = 100

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str, workspace_root: str = "") -> Session:
        """Get existing session or create new one."""
        validate_session_id(session_id)
        if session_id in self._sessions:
            session = self._sessions[session_id]
            if not session.is_expired():
                return session
            # Expired — remove and recreate
            del self._sessions[session_id]

        # Evict oldest if at capacity
        if len(self._sessions) >= self.MAX_SESSIONS:
            self._evict_oldest()

        session = Session(session_id=session_id, workspace_root=workspace_root)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        """Get session by ID, or None if not found/expired."""
        validate_session_id(session_id)
        session = self._sessions.get(session_id)
        if session and not session.is_expired():
            return session
        if session:
            del self._sessions[session_id]
        return None

    def _evict_oldest(self) -> None:
        """Remove the least recently active session."""
        if not self._sessions:
            return
        oldest_id = min(self._sessions, key=lambda k: self._sessions[k].last_active)
        del self._sessions[oldest_id]
