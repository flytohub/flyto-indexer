"""
tool_registry — tool definitions, schemas, and dispatch subpackage.

Public API (all re-exported for backward compat):
    MCP_TOOLS, SMART_TOOLS, SMART_TOOL_NAMES, INDEXER_TOOL_NAMES
    get_vscode_tool_schemas, _mcp_to_openai, _VSCODE_TOOL_NAMES
    has_tool, execute_tool, _TOOL_NAMES
"""

from .mcp_tools import MCP_TOOLS
from .smart_tools import SMART_TOOLS, SMART_TOOL_NAMES
from .vscode_tools import (
    get_vscode_tool_schemas,
    _mcp_to_openai,
    _VSCODE_TOOL_NAMES,
)
from .dispatch import has_tool, execute_tool, _TOOL_NAMES

# Derived constant
INDEXER_TOOL_NAMES = {tool["name"] for tool in MCP_TOOLS} | SMART_TOOL_NAMES

__all__ = [
    "MCP_TOOLS", "SMART_TOOLS", "SMART_TOOL_NAMES", "INDEXER_TOOL_NAMES",
    "get_vscode_tool_schemas", "_mcp_to_openai", "_VSCODE_TOOL_NAMES",
    "has_tool", "execute_tool", "_TOOL_NAMES",
]
