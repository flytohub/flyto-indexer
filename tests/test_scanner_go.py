"""Tests for Go scanner."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import SymbolType, DependencyType
from scanner.go import GoScanner


@pytest.fixture
def scanner():
    return GoScanner("test-project")


GO_SIMPLE = '''package main

import "fmt"

func main() {
	fmt.Println("Hello")
}
'''

GO_STRUCT = '''package models

// User represents a user in the system.
type User struct {
	Name  string
	Email string
	Age   int
}

// Validate checks if the user is valid.
func (u *User) Validate() bool {
	return u.Name != "" && u.Email != ""
}
'''

GO_INTERFACE = '''package service

type Repository interface {
	Find(id string) (interface{}, error)
	Save(entity interface{}) error
}
'''

GO_MULTI_IMPORT = '''package handler

import (
	"fmt"
	"net/http"

	mux "github.com/gorilla/mux"
)

func HandleIndex(w http.ResponseWriter, r *http.Request) {
	fmt.Fprintln(w, "OK")
}
'''


class TestGoScannerBasic:
    """Test basic Go scanner setup."""

    def test_supported_extensions(self, scanner):
        assert ".go" in scanner.supported_extensions

    def test_empty_file(self, scanner):
        symbols, deps = scanner.scan_file(Path("main.go"), "")
        assert symbols == []


class TestGoScannerFunctions:
    """Test function extraction."""

    def test_simple_function(self, scanner):
        symbols, _ = scanner.scan_file(Path("main.go"), GO_SIMPLE)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) >= 1
        func_names = [f.name for f in funcs]
        assert "main" in func_names

    def test_exported_function(self, scanner):
        symbols, _ = scanner.scan_file(Path("handler.go"), GO_MULTI_IMPORT)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        exported = [f for f in funcs if f.name == "HandleIndex"]
        assert len(exported) == 1
        assert "HandleIndex" in exported[0].exports


class TestGoScannerStructs:
    """Test struct extraction."""

    def test_struct_as_class(self, scanner):
        symbols, _ = scanner.scan_file(Path("models.go"), GO_STRUCT)
        classes = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "User"
        assert classes[0].language == "go"

    def test_struct_doc_comment(self, scanner):
        symbols, _ = scanner.scan_file(Path("models.go"), GO_STRUCT)
        classes = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert "user" in classes[0].summary.lower() or "User" in classes[0].summary


class TestGoScannerMethods:
    """Test method extraction."""

    def test_method_with_receiver(self, scanner):
        symbols, _ = scanner.scan_file(Path("models.go"), GO_STRUCT)
        methods = [s for s in symbols if s.symbol_type == SymbolType.METHOD]
        assert len(methods) == 1
        assert methods[0].name == "User.Validate"


class TestGoScannerInterfaces:
    """Test interface extraction."""

    def test_interface(self, scanner):
        symbols, _ = scanner.scan_file(Path("service.go"), GO_INTERFACE)
        ifaces = [s for s in symbols if s.symbol_type == SymbolType.INTERFACE]
        assert len(ifaces) == 1
        assert ifaces[0].name == "Repository"


class TestGoScannerImports:
    """Test import extraction."""

    def test_single_import(self, scanner):
        _, deps = scanner.scan_file(Path("main.go"), GO_SIMPLE)
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        modules = [d.target_id for d in import_deps]
        assert "fmt" in modules

    def test_import_block(self, scanner):
        _, deps = scanner.scan_file(Path("handler.go"), GO_MULTI_IMPORT)
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        modules = [d.target_id for d in import_deps]
        assert "fmt" in modules
        assert "net/http" in modules
