"""
profile — project profile subpackage.

Public API:
    build_project_profile(project_path, compact=False) -> dict
    format_profile(profile) -> str
"""

from .builder import build_project_profile
from .formatter import format_profile

__all__ = ["build_project_profile", "format_profile"]
