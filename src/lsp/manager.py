"""LSP manager — multi-language server pool with lazy startup."""

import logging
import os
import shutil
from typing import Dict, Optional, Tuple

from .client import LSPClient
from .protocol import path_to_uri

logger = logging.getLogger("flyto-indexer.lsp.manager")

# Language server configurations: language -> (commands list, extensions, language_id)
_LSP_SERVERS: Dict[str, dict] = {
    "python": {
        "commands": [
            ["pyright-langserver", "--stdio"],
            ["pylsp"],
        ],
        "extensions": {".py", ".pyi"},
        "language_id": "python",
    },
    "typescript": {
        "commands": [
            ["typescript-language-server", "--stdio"],
        ],
        "extensions": {".ts", ".tsx", ".js", ".jsx"},
        "language_id": "typescript",
    },
    "go": {
        "commands": [
            ["gopls", "serve"],
        ],
        "extensions": {".go"},
        "language_id": "go",
    },
    "rust": {
        "commands": [
            ["rust-analyzer"],
        ],
        "extensions": {".rs"},
        "language_id": "rust",
    },
}

# Extension -> language lookup (built from _LSP_SERVERS)
_EXT_TO_LANG: Dict[str, str] = {}
for _lang, _cfg in _LSP_SERVERS.items():
    for _ext in _cfg["extensions"]:
        _EXT_TO_LANG[_ext] = _lang


class LSPManager:
    """Singleton manager for multiple LSP server instances.

    Lazily starts language servers on demand and caches them by (language, root).
    Controlled via environment variables:
      - FLYTO_LSP_ENABLED: "1" (default) or "0" to disable
      - FLYTO_LSP_TIMEOUT: seconds (default "10")
    """

    _instance: Optional["LSPManager"] = None

    def __init__(self):
        self._clients: Dict[Tuple[str, str], LSPClient] = {}
        self._available: Optional[Dict[str, str]] = None
        self._enabled = os.environ.get("FLYTO_LSP_ENABLED", "1") != "0"
        try:
            self._timeout = float(os.environ.get("FLYTO_LSP_TIMEOUT", "10"))
        except (ValueError, TypeError):
            self._timeout = 10.0

    @classmethod
    def get_instance(cls) -> "LSPManager":
        """Return the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton (used in tests and cache invalidation)."""
        if cls._instance is not None:
            cls._instance.shutdown_all()
        cls._instance = None

    def detect_available(self) -> Dict[str, str]:
        """Detect which LSP servers are available on PATH.

        Returns a dict of language -> resolved command path, cached after first call.
        """
        if self._available is not None:
            return self._available

        self._available = {}
        for lang, cfg in _LSP_SERVERS.items():
            for cmd_list in cfg["commands"]:
                exe = cmd_list[0]
                resolved = shutil.which(exe)
                if resolved:
                    self._available[lang] = resolved
                    break  # Use first available command for this language
        return self._available

    def get_client(self, language: str, project_root: str) -> Optional[LSPClient]:
        """Get or create an LSP client for the given language and project root.

        Returns None if:
          - LSP is disabled
          - Language is not supported
          - No LSP server binary found
          - Server failed to start
        """
        if not self._enabled:
            return None

        available = self.detect_available()
        if language not in available:
            return None

        root_uri = path_to_uri(project_root)
        key = (language, root_uri)

        # Return cached client if still alive
        if key in self._clients:
            client = self._clients[key]
            if client.alive:
                return client
            # Dead client, remove and retry
            del self._clients[key]

        # Find the command list for this language
        cfg = _LSP_SERVERS[language]
        cmd_list = None
        for candidate in cfg["commands"]:
            if shutil.which(candidate[0]):
                cmd_list = candidate
                break
        if not cmd_list:
            return None

        client = LSPClient(cmd_list, root_uri, timeout=self._timeout)
        if not client.start():
            return None

        self._clients[key] = client
        return client

    def shutdown_all(self):
        """Gracefully shut down all active LSP clients."""
        for client in self._clients.values():
            try:
                client.shutdown()
            except Exception:
                pass
        self._clients.clear()

    def language_for_path(self, path: str) -> Optional[str]:
        """Determine the language from file extension.

        Returns None if the extension is not recognized.
        """
        # Extract extension
        dot_idx = path.rfind(".")
        if dot_idx < 0:
            return None
        ext = path[dot_idx:]
        return _EXT_TO_LANG.get(ext)
