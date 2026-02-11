"""Tests for core data models."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import (
    Symbol, Dependency, FileManifest, ProjectIndex,
    SymbolType, DependencyType,
)


class TestSymbolType:
    """Test SymbolType enum."""

    def test_all_types_exist(self):
        assert SymbolType.FILE == "file"
        assert SymbolType.CLASS == "class"
        assert SymbolType.FUNCTION == "function"
        assert SymbolType.METHOD == "method"
        assert SymbolType.COMPONENT == "component"
        assert SymbolType.COMPOSABLE == "composable"
        assert SymbolType.INTERFACE == "interface"
        assert SymbolType.TYPE == "type"

    def test_string_value(self):
        assert str(SymbolType.FUNCTION) == "SymbolType.FUNCTION"
        assert SymbolType.FUNCTION.value == "function"


class TestDependencyType:
    """Test DependencyType enum."""

    def test_all_types_exist(self):
        assert DependencyType.IMPORTS == "imports"
        assert DependencyType.CALLS == "calls"
        assert DependencyType.EXTENDS == "extends"
        assert DependencyType.IMPLEMENTS == "implements"
        assert DependencyType.USES == "uses"


class TestSymbol:
    """Test Symbol dataclass."""

    def test_symbol_id_format(self):
        sym = Symbol(
            project="proj",
            path="src/foo.py",
            symbol_type=SymbolType.FUNCTION,
            name="bar",
        )
        assert sym.id == "proj:src/foo.py:function:bar"

    def test_short_id(self):
        sym = Symbol(
            project="proj",
            path="src/foo.py",
            symbol_type=SymbolType.FUNCTION,
            name="bar",
        )
        assert sym.short_id == "src/foo.py:function:bar"

    def test_compute_hash(self):
        sym = Symbol(
            project="proj", path="a.py",
            symbol_type=SymbolType.FUNCTION, name="f",
            content="def f(): pass",
        )
        h = sym.compute_hash()
        assert len(h) == 16
        assert sym.content_hash == h

    def test_hash_different_content(self):
        sym1 = Symbol(
            project="proj", path="a.py",
            symbol_type=SymbolType.FUNCTION, name="f",
            content="def f(): pass",
        )
        sym2 = Symbol(
            project="proj", path="a.py",
            symbol_type=SymbolType.FUNCTION, name="f",
            content="def f(): return 1",
        )
        assert sym1.compute_hash() != sym2.compute_hash()

    def test_to_dict_with_content(self):
        sym = Symbol(
            project="proj", path="a.py",
            symbol_type=SymbolType.FUNCTION, name="f",
            content="def f(): pass",
            language="python",
        )
        d = sym.to_dict(include_content=True)
        assert d["project"] == "proj"
        assert d["path"] == "a.py"
        assert d["type"] == "function"
        assert d["name"] == "f"
        assert d["content"] == "def f(): pass"

    def test_to_dict_without_content(self):
        sym = Symbol(
            project="proj", path="a.py",
            symbol_type=SymbolType.FUNCTION, name="f",
            content="def f(): pass",
        )
        d = sym.to_dict(include_content=False)
        assert "content" not in d

    def test_to_dict_compact(self):
        sym = Symbol(
            project="proj", path="a.py",
            symbol_type=SymbolType.FUNCTION, name="f",
            content="def f(): pass",
            summary="A function",
            params=["x"],
            returns="int",
        )
        d = sym.to_dict(compact=True)
        assert d["summary"] == "A function"
        assert d["params"] == ["x"]
        assert d["returns"] == "int"

    def test_to_content_record(self):
        sym = Symbol(
            project="proj", path="a.py",
            symbol_type=SymbolType.FUNCTION, name="f",
            content="def f(): pass",
        )
        rec = sym.to_content_record()
        assert rec["id"] == sym.id
        assert rec["content"] == "def f(): pass"

    def test_default_values(self):
        sym = Symbol(
            project="p", path="a.py",
            symbol_type=SymbolType.FUNCTION, name="f",
        )
        assert sym.start_line == 0
        assert sym.end_line == 0
        assert sym.content == ""
        assert sym.exports == []
        assert sym.imports == []
        assert sym.params == []
        assert sym.returns == ""
        assert sym.reference_count == 0


class TestDependency:
    """Test Dependency dataclass."""

    def test_dependency_id(self):
        dep = Dependency(
            source_id="a:b:c:d",
            target_id="e:f:g:h",
            dep_type=DependencyType.IMPORTS,
        )
        assert dep.id == "a:b:c:d--imports-->e:f:g:h"

    def test_to_dict(self):
        dep = Dependency(
            source_id="src", target_id="tgt",
            dep_type=DependencyType.CALLS,
            source_line=42,
            metadata={"raw_call": True},
        )
        d = dep.to_dict()
        assert d["source"] == "src"
        assert d["target"] == "tgt"
        assert d["type"] == "calls"
        assert d["line"] == 42
        assert d["metadata"]["raw_call"] is True


class TestFileManifest:
    """Test FileManifest dataclass."""

    def test_to_dict(self):
        fm = FileManifest(
            path="src/foo.py",
            content_hash="abc123",
            line_count=100,
            symbols=["s1", "s2"],
            last_indexed="2026-01-01",
        )
        d = fm.to_dict()
        assert d["path"] == "src/foo.py"
        assert d["hash"] == "abc123"
        assert d["lines"] == 100
        assert d["symbols"] == ["s1", "s2"]


class TestProjectIndex:
    """Test ProjectIndex dataclass."""

    def test_get_affected_by(self):
        idx = ProjectIndex(project="p", root_path="/tmp")
        dep = Dependency(
            source_id="a", target_id="b",
            dep_type=DependencyType.CALLS,
        )
        idx.dependencies[dep.id] = dep
        affected = idx.get_affected_by("b")
        assert "a" in affected

    def test_get_depends_on(self):
        idx = ProjectIndex(project="p", root_path="/tmp")
        dep = Dependency(
            source_id="a", target_id="b",
            dep_type=DependencyType.IMPORTS,
        )
        idx.dependencies[dep.id] = dep
        depends = idx.get_depends_on("a")
        assert "b" in depends

    def test_get_impact_chain(self):
        idx = ProjectIndex(project="p", root_path="/tmp")
        dep1 = Dependency(source_id="b", target_id="a", dep_type=DependencyType.CALLS)
        dep2 = Dependency(source_id="c", target_id="b", dep_type=DependencyType.CALLS)
        idx.dependencies[dep1.id] = dep1
        idx.dependencies[dep2.id] = dep2

        chain = idx.get_impact_chain("a", max_depth=3)
        assert chain["symbol"] == "a"
        assert len(chain["levels"]) >= 1
        assert "b" in chain["levels"][0]["symbols"]

    def test_empty_impact_chain(self):
        idx = ProjectIndex(project="p", root_path="/tmp")
        chain = idx.get_impact_chain("orphan")
        assert chain["levels"] == []
