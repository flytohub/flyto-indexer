"""Type-aware callee verification for the cross-function taint pass.

The cross-function pass finds callers by name: it takes a dangerous function
`execute`, asks the index who calls something called `execute`, and treats
every hit as a caller. `db.execute`, `cursor.execute`, `logger.execute` and a
local helper called `execute` all collapse to the same string, so the pass
attributes flows to functions that were never called.

This module puts the language server's semantic model in front of that guess.
At a call site it resolves what the call actually binds to and compares it with
the dangerous function's own definition:

    verdict True   the call really does reach that definition — keep the flow
    verdict False  it binds somewhere else — a name collision, drop it
    verdict None   nothing to compare with (no server, unsupported language,
                   budget spent, server had no answer) — keep the name-based
                   result

`None` is the important case. No language server is installed on most machines,
so verification must be an upgrade over the regex floor, never a precondition
for it. Every branch that cannot answer returns `None` and the engine behaves
exactly as it did before.
"""
from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from typing import Callable, Optional, Tuple

logger = logging.getLogger("flyto-indexer.analyzer.taint_lsp")

#: Call sites verified per scan. Each one is a language-server round trip
#: (cached per position), so this bounds a scan's worst case rather than the
#: common case.
MAX_LSP_CHECKS = 500


def _call_position(call: ast.Call) -> tuple[int, int] | None:
    """0-based (line, column) of the called name inside a call expression.

    For `db.execute(x)` the interesting position is the `execute` token, not
    the start of the expression — prepareCallHierarchy resolves whatever the
    cursor sits on.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        end_line = getattr(func, "end_lineno", None)
        end_col = getattr(func, "end_col_offset", None)
        if end_line is None or end_col is None:
            return None
        col = end_col - len(func.attr)
        if col < 0:
            return None
        return end_line - 1, col
    if isinstance(func, ast.Name):
        return func.lineno - 1, func.col_offset
    return None


class CalleeVerifier:
    """Answers "does this call site really reach that definition?"."""

    def __init__(self, project_root: Path, *, max_checks: int = MAX_LSP_CHECKS):
        self.project_root = Path(project_root)
        self.max_checks = max_checks
        self.checks = 0
        self.verified = 0
        self.rejected = 0
        self.unknown = 0
        #: Set to `lsp.call_graph.resolve_definition` once a server is found;
        #: None means every verdict is "unknown" and the name-based result
        #: stands.
        self._resolve: Optional[
            Callable[[Path, Path, int, int], Optional[Tuple[str, int, str]]]
        ] = None
        self._probed = False

    # ── availability ────────────────────────────────────────────────────────

    def _probe(self) -> None:
        """Load the LSP resolver once, if the environment has one to offer."""
        self._probed = True
        if os.environ.get("FLYTO_TAINT_LSP", "1") == "0":
            return
        try:
            try:
                from ..lsp.call_graph import resolve_definition
                from ..lsp.manager import LSPManager
            except ImportError:  # pragma: no cover - flat-layout fallback
                from lsp.call_graph import resolve_definition  # type: ignore
                from lsp.manager import LSPManager  # type: ignore
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("taint LSP verification unavailable: %s", exc)
            return

        try:
            manager = LSPManager.get_instance()
            if not manager._enabled:
                return
            # No installed server means every verification would return None
            # at a per-call-site cost. Skip the whole path instead.
            if not manager.detect_available():
                return
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("LSP manager probe failed: %s", exc)
            return

        self._resolve = resolve_definition

    @property
    def available(self) -> bool:
        if not self._probed:
            self._probe()
        return self._resolve is not None

    # ── verification ────────────────────────────────────────────────────────

    def verify_call(
        self,
        caller_file: str,
        call: ast.Call,
        target_file: str,
        target_name: str,
    ) -> bool | None:
        """True / False / None — see the module docstring."""
        if not self.available or not target_file:
            return None
        if self.checks >= self.max_checks:
            self.unknown += 1
            return None

        position = _call_position(call)
        if position is None:
            self.unknown += 1
            return None

        source_path = self.project_root / caller_file
        if not source_path.is_file():
            self.unknown += 1
            return None

        resolve = self._resolve
        if resolve is None:  # pragma: no cover - guarded by `available`
            return None

        self.checks += 1
        try:
            resolved = resolve(
                self.project_root, source_path, position[0], position[1],
            )
        except Exception as exc:  # pragma: no cover - server-dependent
            logger.debug("callee resolution failed for %s: %s", caller_file, exc)
            resolved = None

        if not resolved:
            self.unknown += 1
            return None

        resolved_path, _resolved_line, resolved_name = resolved
        if resolved_name and target_name and resolved_name != target_name:
            self.rejected += 1
            return False

        try:
            same_file = Path(resolved_path).resolve() == (
                self.project_root / target_file
            ).resolve()
        except OSError:  # pragma: no cover - defensive
            self.unknown += 1
            return None

        if same_file:
            self.verified += 1
            return True
        self.rejected += 1
        return False

    # ── reporting ───────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """What the verifier actually did, for the scan's coverage block."""
        if not self.available:
            return {
                "mode": "name_only",
                "reason": (
                    "no language server available; cross-function callees were "
                    "matched by name, which cannot separate same-named symbols"
                ),
                "checks": 0,
                "verified": 0,
                "rejected": 0,
                "unknown": 0,
            }
        return {
            "mode": "lsp_verified",
            "checks": self.checks,
            "verified": self.verified,
            "rejected": self.rejected,
            "unknown": self.unknown,
            "budget_exhausted": self.checks >= self.max_checks,
        }
