"""
Project Profile — backward-compatibility shim.

All logic has moved to the ``profile`` subpackage.  This module
re-exports the two public entry points so existing call sites
(``from .project_profile import build_project_profile``) keep working.
"""

from .profile import build_project_profile, format_profile  # noqa: F401

__all__ = ["build_project_profile", "format_profile"]
