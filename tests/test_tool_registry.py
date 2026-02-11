"""Tests for tool_registry module."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tool_registry import INDEXER_TOOL_NAMES, get_vscode_tool_schemas, execute_tool


class TestIndexerToolNames:
    """Test the canonical tool name set."""

    def test_contains_core_tools(self):
        assert "search_code" in INDEXER_TOOL_NAMES
        assert "get_symbol_content" in INDEXER_TOOL_NAMES
        assert "find_references" in INDEXER_TOOL_NAMES
        assert "impact_analysis" in INDEXER_TOOL_NAMES
        assert "find_dead_code" in INDEXER_TOOL_NAMES

    def test_contains_search_tools(self):
        assert "fulltext_search" in INDEXER_TOOL_NAMES
        assert "check_index_status" in INDEXER_TOOL_NAMES
        assert "find_todos" in INDEXER_TOOL_NAMES

    def test_contains_navigation_tools(self):
        assert "dependency_graph" in INDEXER_TOOL_NAMES
        assert "get_file_context" in INDEXER_TOOL_NAMES
        assert "find_test_file" in INDEXER_TOOL_NAMES

    def test_is_a_set(self):
        assert isinstance(INDEXER_TOOL_NAMES, set)

    def test_all_names_are_strings(self):
        for name in INDEXER_TOOL_NAMES:
            assert isinstance(name, str)
            assert len(name) > 0


class TestGetVscodeToolSchemas:
    """Test OpenAI function-calling format schemas."""

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

    def test_schema_names_match_tool_names(self):
        schemas = get_vscode_tool_schemas()
        schema_names = {s["function"]["name"] for s in schemas}
        # All schema names should be in the canonical set
        for name in schema_names:
            assert name in INDEXER_TOOL_NAMES

    def test_descriptions_are_nonempty(self):
        schemas = get_vscode_tool_schemas()
        for schema in schemas:
            desc = schema["function"]["description"]
            assert len(desc) > 10


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
