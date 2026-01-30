"""Basic tests for flyto-indexer."""

import tempfile
from pathlib import Path

import pytest

# 將 src 加入路徑
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models import Symbol, Dependency, SymbolType, DependencyType, ProjectIndex
from scanner.python import PythonScanner
from scanner.vue import VueScanner


class TestSymbol:
    """Test Symbol model."""

    def test_symbol_id(self):
        """Symbol ID 格式正確"""
        symbol = Symbol(
            project="flyto-cloud",
            path="src/pages/TopUp.vue",
            symbol_type=SymbolType.COMPONENT,
            name="TopUp",
        )
        assert symbol.id == "flyto-cloud:src/pages/TopUp.vue:component:TopUp"

    def test_symbol_hash(self):
        """Symbol hash 計算正確"""
        symbol = Symbol(
            project="test",
            path="test.py",
            symbol_type=SymbolType.FUNCTION,
            name="test_func",
            content="def test_func():\n    pass",
        )
        hash1 = symbol.compute_hash()
        assert len(hash1) == 16

        # 內容不同，hash 不同
        symbol2 = Symbol(
            project="test",
            path="test.py",
            symbol_type=SymbolType.FUNCTION,
            name="test_func",
            content="def test_func():\n    return 1",
        )
        hash2 = symbol2.compute_hash()
        assert hash1 != hash2


class TestProjectIndex:
    """Test ProjectIndex model."""

    def test_impact_chain(self):
        """影響鏈查詢正確"""
        index = ProjectIndex(project="test", root_path="/test")

        # 建立 symbols
        index.symbols["test:a.py:function:a"] = Symbol(
            project="test", path="a.py", symbol_type=SymbolType.FUNCTION, name="a"
        )
        index.symbols["test:b.py:function:b"] = Symbol(
            project="test", path="b.py", symbol_type=SymbolType.FUNCTION, name="b"
        )
        index.symbols["test:c.py:function:c"] = Symbol(
            project="test", path="c.py", symbol_type=SymbolType.FUNCTION, name="c"
        )

        # 建立依賴：c -> b -> a
        index.dependencies["dep1"] = Dependency(
            source_id="test:b.py:function:b",
            target_id="test:a.py:function:a",
            dep_type=DependencyType.CALLS,
        )
        index.dependencies["dep2"] = Dependency(
            source_id="test:c.py:function:c",
            target_id="test:b.py:function:b",
            dep_type=DependencyType.CALLS,
        )

        # 改了 a，影響 b 和 c
        chain = index.get_impact_chain("test:a.py:function:a", max_depth=3)
        assert len(chain["levels"]) >= 1
        assert "test:b.py:function:b" in chain["levels"][0]["symbols"]


class TestPythonScanner:
    """Test Python scanner."""

    def test_scan_function(self):
        """能正確掃描 Python function"""
        scanner = PythonScanner("test")
        content = '''
def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}"
'''
        symbols, deps = scanner.scan_file(Path("test.py"), content)

        assert len(symbols) == 1
        assert symbols[0].name == "hello"
        assert symbols[0].symbol_type == SymbolType.FUNCTION
        assert "name" in symbols[0].params

    def test_scan_class(self):
        """能正確掃描 Python class"""
        scanner = PythonScanner("test")
        content = '''
class MyClass:
    """A test class."""

    def method(self, x):
        return x * 2
'''
        symbols, deps = scanner.scan_file(Path("test.py"), content)

        # 應該有 class 和 method
        class_symbols = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        method_symbols = [s for s in symbols if s.symbol_type == SymbolType.METHOD]

        assert len(class_symbols) == 1
        assert class_symbols[0].name == "MyClass"
        assert len(method_symbols) == 1
        assert method_symbols[0].name == "MyClass.method"


class TestVueScanner:
    """Test Vue scanner."""

    def test_scan_component(self):
        """能正確掃描 Vue component"""
        scanner = VueScanner("test")
        content = '''<template>
  <div>{{ message }}</div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useStore } from '@/stores/main'

const message = ref('Hello')
const store = useStore()

function handleClick() {
  console.log('clicked')
}
</script>
'''
        symbols, deps = scanner.scan_file(Path("Hello.vue"), content)

        # 應該有 component
        comp_symbols = [s for s in symbols if s.symbol_type == SymbolType.COMPONENT]
        assert len(comp_symbols) == 1
        assert comp_symbols[0].name == "Hello"

        # 應該有 import dependencies
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        assert len(import_deps) >= 2  # vue 和 @/stores/main


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
