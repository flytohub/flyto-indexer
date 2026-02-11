"""Tests for Vue SFC scanner."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import SymbolType, DependencyType
from scanner.vue import VueScanner


@pytest.fixture
def scanner():
    return VueScanner("test-project")


SIMPLE_SFC = """<template>
  <div>{{ message }}</div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const message = ref('Hello')
</script>
"""

SFC_WITH_FUNCTION = """<template>
  <button @click="handleClick">Click</button>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

function handleClick() {
  router.push('/')
}
</script>
"""

SFC_WITH_PROPS = """<template>
  <div>{{ title }}</div>
</template>

<script setup lang="ts">
defineProps<{
  title: string
  count?: number
}>()

defineEmits<{
  (e: 'update', value: string): void
}>()
</script>
"""


class TestVueScannerBasic:
    """Test basic Vue scanner setup."""

    def test_supported_extensions(self, scanner):
        assert ".vue" in scanner.supported_extensions

    def test_can_scan_vue_file(self, scanner):
        assert scanner.can_scan(Path("App.vue")) is True
        assert scanner.can_scan(Path("app.ts")) is False


class TestVueScannerComponent:
    """Test component symbol extraction."""

    def test_component_extracted(self, scanner):
        symbols, _ = scanner.scan_file(Path("HelloWorld.vue"), SIMPLE_SFC)
        components = [s for s in symbols if s.symbol_type == SymbolType.COMPONENT]
        assert len(components) == 1
        assert components[0].name == "HelloWorld"
        assert components[0].language == "vue"

    def test_component_exports_name(self, scanner):
        symbols, _ = scanner.scan_file(Path("MyComp.vue"), SIMPLE_SFC)
        comp = [s for s in symbols if s.symbol_type == SymbolType.COMPONENT][0]
        assert "MyComp" in comp.exports


class TestVueScannerImports:
    """Test import extraction from script block."""

    def test_imports_from_script(self, scanner):
        _, deps = scanner.scan_file(Path("App.vue"), SIMPLE_SFC)
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        modules = [d.target_id for d in import_deps]
        assert "vue" in modules

    def test_composable_usage_detected(self, scanner):
        _, deps = scanner.scan_file(Path("App.vue"), SFC_WITH_FUNCTION)
        use_deps = [d for d in deps if d.dep_type == DependencyType.USES]
        assert len(use_deps) >= 1
        target_names = [d.target_id for d in use_deps]
        assert any("useRouter" in t for t in target_names)


class TestVueScannerFunctions:
    """Test function extraction from script block."""

    def test_function_in_script(self, scanner):
        symbols, _ = scanner.scan_file(Path("App.vue"), SFC_WITH_FUNCTION)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) >= 1
        func_names = [f.name for f in funcs]
        assert "handleClick" in func_names


class TestVueScannerPropsEmits:
    """Test defineProps and defineEmits extraction."""

    def test_props_extracted(self, scanner):
        symbols, _ = scanner.scan_file(Path("MyComp.vue"), SFC_WITH_PROPS)
        comp = [s for s in symbols if s.symbol_type == SymbolType.COMPONENT][0]
        assert "title" in comp.metadata.get("props", [])

    def test_emits_extracted(self, scanner):
        symbols, _ = scanner.scan_file(Path("MyComp.vue"), SFC_WITH_PROPS)
        comp = [s for s in symbols if s.symbol_type == SymbolType.COMPONENT][0]
        assert "update" in comp.metadata.get("emits", [])


class TestVueScannerSummary:
    """Test component summary generation."""

    def test_summary_includes_component_name(self, scanner):
        symbols, _ = scanner.scan_file(Path("LoginForm.vue"), SIMPLE_SFC)
        comp = [s for s in symbols if s.symbol_type == SymbolType.COMPONENT][0]
        assert "LoginForm" in comp.summary

    def test_summary_with_router(self, scanner):
        sfc = """<template>
  <router-link to="/">Home</router-link>
</template>
<script setup lang="ts">
</script>
"""
        symbols, deps = scanner.scan_file(Path("Nav.vue"), sfc)
        comp = [s for s in symbols if s.symbol_type == SymbolType.COMPONENT][0]
        assert "router" in comp.summary.lower()
