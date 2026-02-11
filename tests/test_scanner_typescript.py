"""Tests for TypeScript/JavaScript scanner."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import SymbolType, DependencyType
from scanner.typescript import TypeScriptScanner


@pytest.fixture
def scanner():
    return TypeScriptScanner("test-project")


class TestTypeScriptScannerBasic:
    """Test basic scanner setup."""

    def test_supported_extensions(self, scanner):
        assert ".ts" in scanner.supported_extensions
        assert ".tsx" in scanner.supported_extensions
        assert ".js" in scanner.supported_extensions
        assert ".jsx" in scanner.supported_extensions

    def test_empty_file(self, scanner):
        symbols, deps = scanner.scan_file(Path("test.ts"), "")
        assert symbols == []
        assert deps == []


class TestTypeScriptScannerFunctions:
    """Test function extraction."""

    def test_named_function(self, scanner):
        code = "function greet(name: string): string {\n  return name;\n}\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) == 1
        assert funcs[0].name == "greet"
        assert funcs[0].language == "typescript"

    def test_exported_function(self, scanner):
        code = "export function fetchData(url: string) {\n  return url;\n}\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) == 1
        assert funcs[0].name == "fetchData"
        # The scanner detects exports via the 'export' keyword in content
        assert "export" in funcs[0].content

    def test_async_function(self, scanner):
        code = "export async function loadUser(id: number) {\n  return id;\n}\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) == 1
        assert funcs[0].name == "loadUser"

    def test_arrow_function_export(self, scanner):
        code = "export const add = (a: number, b: number) => {\n  return a + b;\n}\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) == 1
        assert funcs[0].name == "add"


class TestTypeScriptScannerComposables:
    """Test composable detection."""

    def test_composable_detection(self, scanner):
        code = "export function useAuth() {\n  return {};\n}\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        composables = [s for s in symbols if s.symbol_type == SymbolType.COMPOSABLE]
        assert len(composables) == 1
        assert composables[0].name == "useAuth"

    def test_arrow_composable(self, scanner):
        code = "export const useToast = () => {\n  return {};\n}\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        composables = [s for s in symbols if s.symbol_type == SymbolType.COMPOSABLE]
        assert len(composables) == 1
        assert composables[0].name == "useToast"


class TestTypeScriptScannerClasses:
    """Test class extraction."""

    def test_simple_class(self, scanner):
        code = "class UserService {\n  name: string;\n}\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        classes = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "UserService"

    def test_class_extends(self, scanner):
        code = "class Admin extends User {\n  role: string;\n}\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        classes = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert len(classes) == 1
        assert "User" in classes[0].imports


class TestTypeScriptScannerInterfaces:
    """Test interface extraction."""

    def test_interface(self, scanner):
        code = "export interface UserConfig {\n  name: string;\n  age: number;\n}\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        ifaces = [s for s in symbols if s.symbol_type == SymbolType.INTERFACE]
        assert len(ifaces) == 1
        assert ifaces[0].name == "UserConfig"


class TestTypeScriptScannerTypes:
    """Test type alias extraction."""

    def test_type_alias(self, scanner):
        code = "export type Status = 'active' | 'inactive';\n"
        symbols, _ = scanner.scan_file(Path("test.ts"), code)
        types = [s for s in symbols if s.symbol_type == SymbolType.TYPE]
        assert len(types) == 1
        assert types[0].name == "Status"


class TestTypeScriptScannerImports:
    """Test import extraction."""

    def test_named_import(self, scanner):
        code = "import { ref, computed } from 'vue';\nfunction foo() {\n  return ref(1);\n}\n"
        _, deps = scanner.scan_file(Path("test.ts"), code)
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        assert len(import_deps) >= 1
        modules = [d.target_id for d in import_deps]
        assert "vue" in modules

    def test_default_import(self, scanner):
        code = "import axios from 'axios';\nfunction foo() {\n  return axios;\n}\n"
        _, deps = scanner.scan_file(Path("test.ts"), code)
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        modules = [d.target_id for d in import_deps]
        assert "axios" in modules

    def test_call_extraction(self, scanner):
        code = "function foo() {\n  fetchData();\n  user.login();\n}\n"
        _, deps = scanner.scan_file(Path("test.ts"), code)
        call_deps = [d for d in deps if d.dep_type == DependencyType.CALLS]
        call_names = [d.target_id for d in call_deps]
        assert "fetchData" in call_names
