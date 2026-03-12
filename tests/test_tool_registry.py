"""Tests for tool_registry module — unified schema definitions and dispatch."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tool_registry import (
    MCP_TOOLS,
    INDEXER_TOOL_NAMES,
    get_vscode_tool_schemas,
    execute_tool,
    _VSCODE_TOOL_NAMES,
    _mcp_to_openai,
)


# ===========================================================================
# MCP_TOOLS (canonical source)
# ===========================================================================

class TestMCPTools:
    """Test the canonical MCP tool definitions."""

    def test_is_a_list(self):
        assert isinstance(MCP_TOOLS, list)
        assert len(MCP_TOOLS) > 0

    def test_each_tool_has_required_fields(self):
        for tool in MCP_TOOLS:
            assert "name" in tool, f"Missing 'name' in tool: {tool}"
            assert "description" in tool, f"Missing 'description' in {tool['name']}"
            assert "inputSchema" in tool, f"Missing 'inputSchema' in {tool['name']}"
            assert isinstance(tool["name"], str)
            assert len(tool["name"]) > 0

    def test_each_tool_has_mcp_fields(self):
        for tool in MCP_TOOLS:
            assert "title" in tool, f"Missing 'title' in {tool['name']}"
            assert "annotations" in tool, f"Missing 'annotations' in {tool['name']}"

    def test_input_schema_structure(self):
        for tool in MCP_TOOLS:
            schema = tool["inputSchema"]
            assert schema["type"] == "object", f"Invalid schema type in {tool['name']}"
            assert "properties" in schema, f"Missing 'properties' in {tool['name']}"

    def test_no_duplicate_names(self):
        names = [t["name"] for t in MCP_TOOLS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"

    def test_contains_core_tools(self):
        names = {t["name"] for t in MCP_TOOLS}
        assert "search_code" in names
        assert "find_references" in names
        assert "impact_analysis" in names
        assert "find_dead_code" in names
        assert "code_health_score" in names
        assert "analyze_task" in names
        assert "validate_changes" in names


# ===========================================================================
# INDEXER_TOOL_NAMES (auto-derived)
# ===========================================================================

class TestIndexerToolNames:
    """Test the auto-derived canonical tool name set."""

    def test_is_a_set(self):
        assert isinstance(INDEXER_TOOL_NAMES, set)

    def test_derived_from_mcp_tools(self):
        """INDEXER_TOOL_NAMES must match exactly the names in MCP_TOOLS."""
        mcp_names = {t["name"] for t in MCP_TOOLS}
        assert INDEXER_TOOL_NAMES == mcp_names

    def test_contains_core_tools(self):
        assert "search_code" in INDEXER_TOOL_NAMES
        assert "get_symbol_content" in INDEXER_TOOL_NAMES
        assert "find_references" in INDEXER_TOOL_NAMES
        assert "impact_analysis" in INDEXER_TOOL_NAMES
        assert "find_dead_code" in INDEXER_TOOL_NAMES

    def test_all_names_are_strings(self):
        for name in INDEXER_TOOL_NAMES:
            assert isinstance(name, str)
            assert len(name) > 0


# ===========================================================================
# VSCode schemas (generated from MCP_TOOLS)
# ===========================================================================

class TestGetVscodeToolSchemas:
    """Test OpenAI function-calling format schemas generated from MCP_TOOLS."""

    def test_returns_list(self):
        schemas = get_vscode_tool_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) > 0

    def test_each_schema_has_required_fields(self):
        schemas = get_vscode_tool_schemas()
        for schema in schemas:
            assert schema["type"] == "function"
            assert "function" in schema
            func = schema["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_search_code_schema(self):
        schemas = get_vscode_tool_schemas()
        search = next(s for s in schemas if s["function"]["name"] == "search_code")
        params = search["function"]["parameters"]
        assert "query" in params["properties"]
        assert "query" in params.get("required", [])

    def test_schema_names_are_subset_of_mcp(self):
        """All VSCode tool names must exist in MCP_TOOLS."""
        schemas = get_vscode_tool_schemas()
        schema_names = {s["function"]["name"] for s in schemas}
        mcp_names = {t["name"] for t in MCP_TOOLS}
        for name in schema_names:
            assert name in mcp_names, f"VSCode tool '{name}' not found in MCP_TOOLS"

    def test_vscode_tool_names_match_schemas(self):
        """_VSCODE_TOOL_NAMES should match what get_vscode_tool_schemas returns."""
        schemas = get_vscode_tool_schemas()
        schema_names = {s["function"]["name"] for s in schemas}
        assert schema_names == _VSCODE_TOOL_NAMES

    def test_descriptions_are_nonempty(self):
        schemas = get_vscode_tool_schemas()
        for schema in schemas:
            desc = schema["function"]["description"]
            assert len(desc) > 10

    def test_description_overrides_applied(self):
        """search_code should have chain hints in VSCode description."""
        schemas = get_vscode_tool_schemas()
        search = next(s for s in schemas if s["function"]["name"] == "search_code")
        assert "Chain with:" in search["function"]["description"]

    def test_param_overrides_applied(self):
        """dependency_graph should use simplified params in VSCode."""
        schemas = get_vscode_tool_schemas()
        dep_graph = next(s for s in schemas if s["function"]["name"] == "dependency_graph")
        params = dep_graph["function"]["parameters"]
        # VSCode override uses 'path', MCP uses 'file_path'
        assert "path" in params["properties"]


# ===========================================================================
# _mcp_to_openai converter
# ===========================================================================

class TestMCPToOpenAI:

    def test_basic_conversion(self):
        mcp_tool = {
            "name": "test_tool",
            "title": "Test",
            "annotations": {},
            "description": "A test tool",
            "inputSchema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        }
        result = _mcp_to_openai(mcp_tool)
        assert result["type"] == "function"
        assert result["function"]["name"] == "test_tool"
        assert result["function"]["description"] == "A test tool"
        assert result["function"]["parameters"]["required"] == ["q"]

    def test_with_description_override(self):
        mcp_tool = {
            "name": "test_tool",
            "description": "Original desc",
            "inputSchema": {"type": "object", "properties": {}},
        }
        result = _mcp_to_openai(mcp_tool, desc_override="Custom desc")
        assert result["function"]["description"] == "Custom desc"

    def test_with_param_override(self):
        mcp_tool = {
            "name": "test_tool",
            "description": "Desc",
            "inputSchema": {
                "type": "object",
                "properties": {"a": {"type": "string"}},
            },
        }
        override = {"type": "object", "properties": {"b": {"type": "integer"}}}
        result = _mcp_to_openai(mcp_tool, param_override=override)
        assert "b" in result["function"]["parameters"]["properties"]
        assert "a" not in result["function"]["parameters"]["properties"]


# ===========================================================================
# Schema Consistency
# ===========================================================================

class TestSchemaConsistency:
    """Ensure MCP and dispatch are in sync."""

    def test_all_mcp_tools_in_dispatch(self):
        """Every MCP tool should have a dispatch handler."""
        # We can't easily test dispatch without importing all tools,
        # but we can verify the dispatch dict keys exist
        from tool_registry import execute_tool
        for tool in MCP_TOOLS:
            name = tool["name"]
            # execute_tool should not raise KeyError for known tools
            # (it will fail because index isn't loaded, but shouldn't raise KeyError)
            try:
                execute_tool(name, {})
            except KeyError:
                pytest.fail(f"Tool '{name}' missing from dispatch table")
            except Exception:
                pass  # Other errors are expected (no index loaded)

    def test_vscode_tools_subset_of_mcp(self):
        """All VSCode tool names must be valid MCP tool names."""
        mcp_names = {t["name"] for t in MCP_TOOLS}
        for name in _VSCODE_TOOL_NAMES:
            assert name in mcp_names, f"VSCode tool '{name}' not in MCP_TOOLS"


# ===========================================================================
# execute_tool dispatch
# ===========================================================================

class TestExecuteTool:
    """Test execute_tool dispatch."""

    def test_unknown_tool_raises_keyerror(self):
        with pytest.raises(KeyError, match="Unknown tool"):
            execute_tool("nonexistent_tool", {})

    def test_unknown_tool_message(self):
        try:
            execute_tool("fake_tool_xyz", {})
        except KeyError as e:
            assert "fake_tool_xyz" in str(e)
