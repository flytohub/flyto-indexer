"""Tests for type_contracts module — deep type extraction and normalization."""

import pytest

from src.tools.type_contracts import (
    _compare_schemas,
    _extract_python_fields,
    _extract_ts_fields,
    _normalize_type,
    _split_generic_args,
)


# =============================================================================
# _split_generic_args
# =============================================================================


class TestSplitGenericArgs:
    def test_simple_split(self):
        assert _split_generic_args("str, int") == ["str", "int"]

    def test_nested_generic(self):
        result = _split_generic_args("Dict[str, int], List[str]")
        assert result == ["Dict[str, int]", "List[str]"]

    def test_deep_nesting(self):
        result = _split_generic_args("Dict[str, List[int]], Optional[Dict[str, str]]")
        assert result == ["Dict[str, List[int]]", "Optional[Dict[str, str]]"]

    def test_single_element(self):
        assert _split_generic_args("str") == ["str"]

    def test_pipe_delimiter(self):
        result = _split_generic_args("str | int | None", delimiter='|')
        assert result == ["str", "int", "None"]


# =============================================================================
# Python field extraction
# =============================================================================


class TestExtractPythonFields:
    def test_pydantic_model(self):
        code = '''
class UserResponse(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
'''
        result = _extract_python_fields(code, "UserResponse")
        assert result["model_type"] == "pydantic"
        assert "id" in result["fields"]
        assert result["fields"]["id"]["type"] == "int"
        assert result["fields"]["email"]["optional"] is True

    def test_dataclass(self):
        code = '''
@dataclass
class Point:
    x: float
    y: float
    label: str = ""
'''
        result = _extract_python_fields(code, "Point")
        assert result["model_type"] == "dataclass"
        assert len(result["fields"]) == 3
        assert result["fields"]["label"]["has_default"] is True

    def test_typeddict(self):
        code = '''
class Config(TypedDict):
    host: str
    port: int
'''
        result = _extract_python_fields(code, "Config")
        assert result["model_type"] == "typeddict"

    def test_annotated_unwrap(self):
        code = '''
class Item(BaseModel):
    price: Annotated[int, Field(ge=0)]
    name: Annotated[str, Field(max_length=100)]
'''
        result = _extract_python_fields(code, "Item")
        assert result["fields"]["price"]["type"] == "int"
        assert result["fields"]["name"]["type"] == "str"

    def test_field_alias(self):
        code = '''
class APIResponse(BaseModel):
    user_id: int = Field(alias="userId")
    full_name: str = Field(alias="fullName")
'''
        result = _extract_python_fields(code, "APIResponse")
        assert result["fields"]["user_id"]["alias"] == "userId"
        assert result["fields"]["full_name"]["alias"] == "fullName"

    def test_union_pipe_syntax(self):
        code = '''
class Mixed(BaseModel):
    value: str | int | None
'''
        result = _extract_python_fields(code, "Mixed")
        assert result["fields"]["value"]["optional"] is True

    def test_none_left_side(self):
        code = '''
class Opt(BaseModel):
    value: None | str
'''
        result = _extract_python_fields(code, "Opt")
        assert result["fields"]["value"]["optional"] is True

    def test_class_not_found(self):
        result = _extract_python_fields("class Foo: pass", "Bar")
        assert result.get("error") == "class not found"


# =============================================================================
# TypeScript field extraction
# =============================================================================


class TestExtractTsFields:
    def test_interface(self):
        code = '''
interface User {
    id: number;
    name: string;
    email?: string;
}
'''
        result = _extract_ts_fields(code, "User")
        assert result["model_type"] == "interface"
        assert result["fields"]["id"]["type"] == "number"
        assert result["fields"]["email"]["optional"] is True

    def test_type_alias(self):
        code = '''
type Config = {
    host: string;
    port: number;
};
'''
        result = _extract_ts_fields(code, "Config")
        assert result["model_type"] == "type"
        assert len(result["fields"]) == 2

    def test_readonly_field(self):
        code = '''
interface Immutable {
    readonly id: number;
    name: string;
}
'''
        result = _extract_ts_fields(code, "Immutable")
        assert "id" in result["fields"]
        assert result["fields"]["id"]["type"] == "number"

    def test_type_not_found(self):
        result = _extract_ts_fields("interface Foo { x: number; }", "Bar")
        assert result.get("error") == "type not found"


# =============================================================================
# Type normalization
# =============================================================================


class TestNormalizeType:
    # Python types
    def test_py_primitives(self):
        assert _normalize_type("str", "python") == "string"
        assert _normalize_type("int", "python") == "number"
        assert _normalize_type("bool", "python") == "boolean"
        assert _normalize_type("None", "python") == "null"

    def test_py_optional(self):
        assert _normalize_type("Optional[str]", "python") == "string | null"

    def test_py_union(self):
        result = _normalize_type("Union[str, int, None]", "python")
        assert "string" in result
        assert "number" in result
        assert "null" in result

    def test_py_list(self):
        assert _normalize_type("list[str]", "python") == "string[]"
        assert _normalize_type("List[int]", "python") == "number[]"

    def test_py_dict(self):
        assert _normalize_type("dict[str, int]", "python") == "Record<string, number>"
        assert _normalize_type("Dict[str, List[int]]", "python") == "Record<string, number[]>"

    def test_py_set(self):
        assert _normalize_type("set[str]", "python") == "string[]"

    def test_py_tuple_homogeneous(self):
        assert _normalize_type("tuple[int, ...]", "python") == "number[]"

    def test_py_annotated(self):
        assert _normalize_type("Annotated[int, Field(ge=0)]", "python") == "number"

    def test_py_literal(self):
        result = _normalize_type("Literal['a', 'b']", "python")
        assert "'a'" in result
        assert "'b'" in result

    def test_py_pipe_union(self):
        result = _normalize_type("str | int | None", "python")
        assert "string" in result
        assert "number" in result

    def test_py_callable(self):
        assert _normalize_type("Callable[[str], int]", "python") == "function"

    # TypeScript types
    def test_ts_primitives(self):
        assert _normalize_type("string", "typescript") == "string"
        assert _normalize_type("number", "typescript") == "number"
        assert _normalize_type("undefined", "typescript") == "null"

    def test_ts_array(self):
        assert _normalize_type("string[]", "typescript") == "string[]"
        assert _normalize_type("Array<number>", "typescript") == "number[]"

    def test_ts_record(self):
        assert _normalize_type("Record<string, number>", "typescript") == "Record<string, number>"

    def test_ts_union(self):
        result = _normalize_type("string | number | null", "typescript")
        assert "string" in result
        assert "number" in result
        assert "null" in result

    def test_py_nested_dict_in_union(self):
        """Bracket-depth-aware splitting: Union[Dict[str, int], None]."""
        result = _normalize_type("Union[Dict[str, int], None]", "python")
        assert "Record<string, number>" in result
        assert "null" in result


# =============================================================================
# Schema comparison
# =============================================================================


class TestCompareSchemas:
    def test_matching_schemas(self):
        producer = {"model_type": "pydantic", "fields": {
            "id": {"type": "int", "optional": False},
            "name": {"type": "str", "optional": False},
        }}
        consumer = {"model_type": "interface", "fields": {
            "id": {"type": "number", "optional": False},
            "name": {"type": "string", "optional": False},
        }}
        mismatches = _compare_schemas(producer, consumer)
        assert len(mismatches) == 0

    def test_type_mismatch(self):
        producer = {"model_type": "pydantic", "fields": {
            "count": {"type": "int", "optional": False},
        }}
        consumer = {"model_type": "interface", "fields": {
            "count": {"type": "string", "optional": False},
        }}
        mismatches = _compare_schemas(producer, consumer)
        assert len(mismatches) == 1
        assert mismatches[0]["issue"] == "type_mismatch"

    def test_missing_in_consumer(self):
        producer = {"model_type": "pydantic", "fields": {
            "id": {"type": "int", "optional": False},
            "extra": {"type": "str", "optional": False},
        }}
        consumer = {"model_type": "interface", "fields": {
            "id": {"type": "number", "optional": False},
        }}
        mismatches = _compare_schemas(producer, consumer)
        assert any(m["issue"] == "missing_in_consumer" for m in mismatches)

    def test_field_alias_match(self):
        """Consumer uses alias name — should not flag as missing."""
        producer = {"model_type": "pydantic", "fields": {
            "user_id": {"type": "int", "optional": False, "alias": "userId"},
        }}
        consumer = {"model_type": "interface", "fields": {
            "userId": {"type": "number", "optional": False},
        }}
        mismatches = _compare_schemas(producer, consumer)
        type_mismatches = [m for m in mismatches if m["severity"] == "error"]
        assert len(type_mismatches) == 0

    def test_optionality_mismatch(self):
        producer = {"model_type": "pydantic", "fields": {
            "value": {"type": "str", "optional": True},
        }}
        consumer = {"model_type": "interface", "fields": {
            "value": {"type": "string", "optional": False},
        }}
        mismatches = _compare_schemas(producer, consumer)
        assert any(m["issue"] == "optionality_mismatch" for m in mismatches)


# =============================================================================
# Reverse index improvements (tested via engine)
# =============================================================================


class TestReverseIndexImprovements:
    def test_same_file_resolution(self):
        """Functions in the same file should resolve to each other."""
        from src.engine import IndexEngine
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = Path(tmpdir) / "proj"
            proj_dir.mkdir()
            (proj_dir / "helpers.py").write_text(
                'def helper():\n    return 42\n\ndef main():\n    return helper()\n'
            )
            idx_dir = proj_dir / ".flyto-index"
            engine = IndexEngine("test", proj_dir, index_dir=idx_dir)
            engine.scan()

            helper_refs = 0
            for sid, sym in engine.index.symbols.items():
                if sym.name == "helper":
                    helper_refs = sym.reference_count
            assert helper_refs >= 1, "Same-file call should resolve"

    def test_extends_dependency(self):
        """Class inheritance should create extends dependencies."""
        from src.engine import IndexEngine
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = Path(tmpdir) / "proj"
            proj_dir.mkdir()
            (proj_dir / "models.py").write_text(
                'class Base:\n    pass\n\nclass Child(Base):\n    pass\n'
            )
            idx_dir = proj_dir / ".flyto-index"
            engine = IndexEngine("test", proj_dir, index_dir=idx_dir)
            engine.scan()

            extends_deps = [
                d for d in engine.index.dependencies.values()
                if d.dep_type.value == "extends"
            ]
            assert len(extends_deps) >= 1
            assert any(d.target_id == "Base" for d in extends_deps)

            for sid, sym in engine.index.symbols.items():
                if sym.name == "Base":
                    assert sym.reference_count >= 1
