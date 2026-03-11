"""Tests for type_contracts module — cross-repo type contract checking."""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tools.type_contracts import (
    _extract_python_fields,
    _extract_ts_fields,
    _normalize_type,
    _compare_schemas,
    extract_type_schema,
    contract_drift,
)


# =============================================================================
# Test _extract_python_fields
# =============================================================================

class TestExtractPythonFields:
    """Test Python type extraction via AST."""

    def test_pydantic_model(self):
        content = '''
class LoginResponse(BaseModel):
    access_token: str
    user_id: int
    email: Optional[str] = None
    roles: list[str] = []
'''
        schema = _extract_python_fields(content, "LoginResponse")
        assert schema["model_type"] == "pydantic"
        assert schema["name"] == "LoginResponse"
        assert "access_token" in schema["fields"]
        assert schema["fields"]["access_token"]["type"] == "str"
        assert schema["fields"]["access_token"]["optional"] is False
        assert schema["fields"]["access_token"]["has_default"] is False
        assert schema["fields"]["email"]["optional"] is True
        assert schema["fields"]["email"]["has_default"] is True
        assert schema["fields"]["user_id"]["type"] == "int"
        assert schema["fields"]["roles"]["type"] == "list[str]"
        assert schema["fields"]["roles"]["has_default"] is True

    def test_dataclass(self):
        content = '''
@dataclass
class UserProfile:
    name: str
    age: int
    bio: Optional[str] = None
'''
        schema = _extract_python_fields(content, "UserProfile")
        assert schema["model_type"] == "dataclass"
        assert "name" in schema["fields"]
        assert schema["fields"]["name"]["type"] == "str"
        assert schema["fields"]["bio"]["optional"] is True

    def test_typeddict(self):
        content = '''
class Config(TypedDict):
    host: str
    port: int
    debug: bool
'''
        schema = _extract_python_fields(content, "Config")
        assert schema["model_type"] == "typeddict"
        assert len(schema["fields"]) == 3
        assert schema["fields"]["port"]["type"] == "int"

    def test_plain_class(self):
        content = '''
class Payload:
    data: dict
    count: int
'''
        schema = _extract_python_fields(content, "Payload")
        assert schema["model_type"] == "class"
        assert "data" in schema["fields"]

    def test_union_none_optional(self):
        content = '''
class Response(BaseModel):
    value: Union[str, None]
'''
        schema = _extract_python_fields(content, "Response")
        assert schema["fields"]["value"]["optional"] is True

    def test_pipe_none_optional(self):
        content = '''
class Response(BaseModel):
    value: str | None
'''
        schema = _extract_python_fields(content, "Response")
        assert schema["fields"]["value"]["optional"] is True

    def test_class_not_found(self):
        content = '''
class Foo(BaseModel):
    x: int
'''
        schema = _extract_python_fields(content, "Bar")
        assert schema.get("error") == "class not found"
        assert schema["fields"] == {}

    def test_syntax_error(self):
        content = "class Broken(def:"
        schema = _extract_python_fields(content, "Broken")
        assert "error" in schema


# =============================================================================
# Test _extract_ts_fields
# =============================================================================

class TestExtractTsFields:
    """Test TypeScript type extraction via regex."""

    def test_interface(self):
        content = '''
interface LoginResult {
    accessToken: string;
    userId: number;
    email?: string;
    roles: string[];
}
'''
        schema = _extract_ts_fields(content, "LoginResult")
        assert schema["model_type"] == "interface"
        assert schema["name"] == "LoginResult"
        assert "accessToken" in schema["fields"]
        assert schema["fields"]["accessToken"]["type"] == "string"
        assert schema["fields"]["accessToken"]["optional"] is False
        assert schema["fields"]["email"]["optional"] is True
        assert schema["fields"]["userId"]["type"] == "number"
        assert schema["fields"]["roles"]["type"] == "string[]"

    def test_type_alias(self):
        content = '''
type UserData = {
    name: string;
    age: number;
    active: boolean;
}
'''
        schema = _extract_ts_fields(content, "UserData")
        assert schema["model_type"] == "type"
        assert len(schema["fields"]) == 3
        assert schema["fields"]["active"]["type"] == "boolean"

    def test_readonly_fields(self):
        content = '''
interface Config {
    readonly host: string;
    readonly port: number;
}
'''
        schema = _extract_ts_fields(content, "Config")
        assert "host" in schema["fields"]
        assert "port" in schema["fields"]

    def test_interface_with_extends(self):
        content = '''
interface AdminUser extends BaseUser {
    adminLevel: number;
    permissions: string[];
}
'''
        schema = _extract_ts_fields(content, "AdminUser")
        assert schema["model_type"] == "interface"
        assert "adminLevel" in schema["fields"]

    def test_type_not_found(self):
        content = '''
interface Foo {
    x: string;
}
'''
        schema = _extract_ts_fields(content, "Bar")
        assert schema.get("error") == "type not found"
        assert schema["fields"] == {}

    def test_nested_braces(self):
        content = '''
interface Complex {
    meta: { key: string; value: number };
    name: string;
}
'''
        schema = _extract_ts_fields(content, "Complex")
        assert "name" in schema["fields"]


# =============================================================================
# Test _normalize_type
# =============================================================================

class TestNormalizeType:
    """Test cross-language type normalization."""

    def test_python_primitives(self):
        assert _normalize_type("str", "python") == "string"
        assert _normalize_type("int", "python") == "number"
        assert _normalize_type("float", "python") == "number"
        assert _normalize_type("bool", "python") == "boolean"
        assert _normalize_type("None", "python") == "null"
        assert _normalize_type("dict", "python") == "object"
        assert _normalize_type("list", "python") == "array"
        assert _normalize_type("Any", "python") == "any"

    def test_ts_primitives(self):
        assert _normalize_type("string", "typescript") == "string"
        assert _normalize_type("number", "typescript") == "number"
        assert _normalize_type("boolean", "typescript") == "boolean"
        assert _normalize_type("null", "typescript") == "null"
        assert _normalize_type("undefined", "typescript") == "null"
        assert _normalize_type("void", "typescript") == "null"
        assert _normalize_type("any", "typescript") == "any"

    def test_python_optional(self):
        assert _normalize_type("Optional[str]", "python") == "string | null"
        assert _normalize_type("Optional[int]", "python") == "number | null"

    def test_python_list(self):
        assert _normalize_type("list[str]", "python") == "string[]"
        assert _normalize_type("List[int]", "python") == "number[]"

    def test_python_dict(self):
        assert _normalize_type("dict[str, int]", "python") == "Record<string, number>"

    def test_ts_array(self):
        assert _normalize_type("string[]", "typescript") == "string[]"
        assert _normalize_type("Array<number>", "typescript") == "number[]"

    def test_python_union(self):
        result = _normalize_type("Union[str, None]", "python")
        assert "string" in result
        assert "null" in result

    def test_python_pipe_union(self):
        result = _normalize_type("str | None", "python")
        assert "string" in result
        assert "null" in result

    def test_unknown_type_passthrough(self):
        assert _normalize_type("CustomType", "python") == "CustomType"
        assert _normalize_type("CustomType", "typescript") == "CustomType"


# =============================================================================
# Test _compare_schemas
# =============================================================================

class TestCompareSchemas:
    """Test schema comparison logic."""

    def test_identical_schemas(self):
        producer = {
            "model_type": "pydantic",
            "fields": {
                "name": {"type": "str", "optional": False},
                "age": {"type": "int", "optional": False},
            },
        }
        consumer = {
            "model_type": "interface",
            "fields": {
                "name": {"type": "string", "optional": False},
                "age": {"type": "number", "optional": False},
            },
        }
        mismatches = _compare_schemas(producer, consumer)
        assert len(mismatches) == 0

    def test_missing_in_consumer(self):
        producer = {
            "model_type": "pydantic",
            "fields": {
                "name": {"type": "str", "optional": False},
                "extra": {"type": "str", "optional": False},
            },
        }
        consumer = {
            "model_type": "interface",
            "fields": {
                "name": {"type": "string", "optional": False},
            },
        }
        mismatches = _compare_schemas(producer, consumer)
        info_mismatches = [m for m in mismatches if m["severity"] == "info"]
        assert len(info_mismatches) == 1
        assert info_mismatches[0]["field"] == "extra"
        assert info_mismatches[0]["issue"] == "missing_in_consumer"

    def test_missing_in_producer(self):
        producer = {
            "model_type": "pydantic",
            "fields": {
                "name": {"type": "str", "optional": False},
            },
        }
        consumer = {
            "model_type": "interface",
            "fields": {
                "name": {"type": "string", "optional": False},
                "missing_field": {"type": "string", "optional": False},
            },
        }
        mismatches = _compare_schemas(producer, consumer)
        errors = [m for m in mismatches if m["severity"] == "error"]
        assert len(errors) == 1
        assert errors[0]["field"] == "missing_field"
        assert errors[0]["issue"] == "missing_in_producer"

    def test_type_mismatch(self):
        producer = {
            "model_type": "pydantic",
            "fields": {
                "count": {"type": "str", "optional": False},
            },
        }
        consumer = {
            "model_type": "interface",
            "fields": {
                "count": {"type": "number", "optional": False},
            },
        }
        mismatches = _compare_schemas(producer, consumer)
        errors = [m for m in mismatches if m["issue"] == "type_mismatch"]
        assert len(errors) == 1
        assert errors[0]["severity"] == "error"

    def test_optionality_mismatch(self):
        producer = {
            "model_type": "pydantic",
            "fields": {
                "email": {"type": "Optional[str]", "optional": True},
            },
        }
        consumer = {
            "model_type": "interface",
            "fields": {
                "email": {"type": "string", "optional": False},
            },
        }
        mismatches = _compare_schemas(producer, consumer)
        warnings = [m for m in mismatches if m["severity"] == "warning"]
        assert len(warnings) == 1
        assert warnings[0]["issue"] == "optionality_mismatch"

    def test_empty_schemas(self):
        mismatches = _compare_schemas(
            {"model_type": "class", "fields": {}},
            {"model_type": "interface", "fields": {}},
        )
        assert len(mismatches) == 0


# =============================================================================
# Test extract_type_schema (with mocked index)
# =============================================================================

class TestExtractTypeSchema:
    """Test extract_type_schema tool with mocked index."""

    @patch("tools.type_contracts.load_index")
    @patch("tools.type_contracts.get_symbol_content_text")
    def test_extract_pydantic(self, mock_content, mock_index):
        mock_index.return_value = {
            "symbols": {
                "myapp:src/models.py:class:LoginResponse": {
                    "name": "LoginResponse",
                    "path": "src/models.py",
                    "type": "class",
                },
            },
        }
        mock_content.return_value = '''class LoginResponse(BaseModel):
    access_token: str
    user_id: int
    email: Optional[str] = None
'''
        result = extract_type_schema("LoginResponse")
        assert result["language"] == "python"
        assert result["model_type"] == "pydantic"
        assert "access_token" in result["fields"]
        assert result["fields"]["access_token"]["type"] == "str"
        assert result["fields"]["email"]["optional"] is True

    @patch("tools.type_contracts.load_index")
    @patch("tools.type_contracts.get_symbol_content_text")
    def test_extract_ts_interface(self, mock_content, mock_index):
        mock_index.return_value = {
            "symbols": {
                "myapp:src/types.ts:interface:LoginResult": {
                    "name": "LoginResult",
                    "path": "src/types.ts",
                    "type": "interface",
                },
            },
        }
        mock_content.return_value = '''interface LoginResult {
    accessToken: string;
    userId: number;
    email?: string;
}
'''
        result = extract_type_schema("LoginResult")
        assert result["language"] == "typescript"
        assert result["model_type"] == "interface"
        assert "accessToken" in result["fields"]
        assert result["fields"]["email"]["optional"] is True

    @patch("tools.type_contracts.load_index")
    def test_symbol_not_found(self, mock_index):
        mock_index.return_value = {"symbols": {}}
        result = extract_type_schema("NonExistent")
        assert "error" in result


# =============================================================================
# Test contract_drift (with mocked index)
# =============================================================================

class TestContractDrift:
    """Test contract_drift tool with mocked index."""

    @patch("tools.type_contracts.load_index")
    @patch("tools.type_contracts.get_symbol_content_text")
    def test_drift_detected(self, mock_content, mock_index):
        mock_index.return_value = {
            "symbols": {
                "project-a:src/models.py:class:UserProfile": {
                    "name": "UserProfile",
                    "path": "src/models.py",
                    "type": "class",
                },
                "project-b:src/types.ts:interface:UserProfile": {
                    "name": "UserProfile",
                    "path": "src/types.ts",
                    "type": "interface",
                },
            },
        }

        def content_side_effect(sid, sym):
            if "project-a" in sid:
                return '''class UserProfile(BaseModel):
    name: str
    email: str
    age: int
'''
            else:
                return '''interface UserProfile {
    name: string;
    email: string;
}
'''

        mock_content.side_effect = content_side_effect

        result = contract_drift()
        assert result["types_checked"] >= 1
        assert result["drifts_found"] >= 1
        # age is missing in consumer
        drift = result["drifts"][0]
        assert drift["source"]["project"] == "project-a"
        assert drift["consumer"]["project"] == "project-b"
        field_issues = {m["field"]: m["issue"] for m in drift["mismatches"]}
        assert "age" in field_issues

    @patch("tools.type_contracts.load_index")
    @patch("tools.type_contracts.get_symbol_content_text")
    def test_no_drift(self, mock_content, mock_index):
        mock_index.return_value = {
            "symbols": {
                "project-a:src/models.py:class:Config": {
                    "name": "Config",
                    "path": "src/models.py",
                    "type": "class",
                },
                "project-b:src/types.ts:interface:Config": {
                    "name": "Config",
                    "path": "src/types.ts",
                    "type": "interface",
                },
            },
        }

        def content_side_effect(sid, sym):
            if "project-a" in sid:
                return '''class Config(BaseModel):
    host: str
    port: int
'''
            else:
                return '''interface Config {
    host: string;
    port: number;
}
'''

        mock_content.side_effect = content_side_effect

        result = contract_drift()
        assert result["drifts_found"] == 0
