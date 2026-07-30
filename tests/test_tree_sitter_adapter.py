"""Optional Tree-sitter adapter remains precise and dependency-safe."""

from pathlib import Path

from src.engine import IndexEngine
from src.models import Symbol, SymbolType
from src.tree_sitter_adapter import OptionalTreeSitterAdapter


class _FakeNode:
    def __init__(
        self,
        node_type,
        *,
        start_byte=0,
        end_byte=0,
        children=(),
        name_node=None,
        has_error=False,
    ):
        self.type = node_type
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.children = list(children)
        self._name_node = name_node
        self.has_error = has_error

    def child_by_field_name(self, field):
        return self._name_node if field == "name" else None


class _FakeParser:
    def parse(self, source):
        name_start = source.index(b"run")
        name = _FakeNode(
            "identifier",
            start_byte=name_start,
            end_byte=name_start + 3,
        )
        definition = _FakeNode("function_definition", name_node=name)
        root = _FakeNode("module", children=[definition])
        return type("Tree", (), {"root_node": root})()


def test_disabled_adapter_does_not_import_or_slow_default_path():
    adapter = OptionalTreeSitterAdapter(enabled=False)

    result = adapter.inspect("app.py", "def run(): pass", [])

    assert result == {"status": "disabled"}
    assert adapter.summary()["mode"] == "disabled"
    assert adapter.summary()["validated_files"] == 0


def test_enabled_adapter_cross_checks_native_definitions(monkeypatch):
    adapter = OptionalTreeSitterAdapter(enabled=True)
    monkeypatch.setattr(adapter, "_load_parser", lambda _suffix: _FakeParser())
    symbol = Symbol(
        project="demo",
        path="app.py",
        symbol_type=SymbolType.FUNCTION,
        name="run",
    )

    result = adapter.inspect(
        Path("app.py"),
        "def run():\n    pass\n",
        [symbol],
    )

    assert result == {
        "status": "validated",
        "has_error": False,
        "native_definitions": 1,
        "tree_sitter_definitions": 1,
        "definition_mismatches": 0,
    }
    assert adapter.summary()["validated_files"] == 1


def test_engine_reports_disabled_fallback_without_runtime_dependency(tmp_path):
    (tmp_path / "app.py").write_text("def run():\n    pass\n")
    engine = IndexEngine("demo", tmp_path, index_dir=tmp_path / ".index")

    result = engine.scan(incremental=False)

    assert result["parser"]["schema"] == "tree-sitter-adapter.v1"
    assert result["parser"]["mode"] == "disabled"
    assert result["parser"]["fallback_files"] == 0
    assert len(result["parser"]["signature"]) == 64
