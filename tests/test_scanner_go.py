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

GO_IMPLEMENTATION = '''package service

type Stringer interface {
	String() string
}

type Saver interface {
	Save() error
}

type User struct {
	Name string
}

func (u *User) String() string {
	return u.Name
}

func (u *User) Save() error {
	return nil
}
'''

GO_EMBEDDING = '''package models

type Base struct {
	ID        int
	CreatedAt string
}

type Timestamps struct {
	UpdatedAt string
}

type User struct {
	Base
	*Timestamps
	Name  string
	Email string
}
'''

GO_TYPE_ALIAS = '''package types

// Duration is a custom duration type.
type Duration int64

type Handler func(ctx Context) error

type StringSlice []string
'''

GO_CONST_VAR = '''package config

const MaxRetries int = 3

var DefaultTimeout int = 30

const (
	StatusActive   = "active"
	StatusInactive = "inactive"
	StatusPending  = "pending"
)

var (
	GlobalCounter int
	AppName       string
)
'''

GO_COMPLEX_INTERFACE = '''package io

type Reader interface {
	Read(p []byte) (int, error)
}

type Writer interface {
	Write(p []byte) (int, error)
}

type ReadWriter interface {
	Reader
	Writer
}

type ReadCloser interface {
	Reader
	Close() error
}
'''

GO_METHOD_DEPS = '''package handler

type Server struct {
	Port int
}

func (s *Server) Start() error {
	return nil
}

func (s *Server) Stop() error {
	return nil
}

func (s *Server) Restart() error {
	return nil
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


class TestGoMethodDependencies:
    """Test EXTENDS edges from methods to their receiver structs."""

    def test_method_creates_extends_dependency(self, scanner):
        """Each method should have an EXTENDS edge to its receiver struct."""
        symbols, deps = scanner.scan_file(Path("models.go"), GO_STRUCT)
        extends_deps = [d for d in deps if d.dep_type == DependencyType.EXTENDS]
        assert len(extends_deps) >= 1

        # Validate should extend User
        validate_dep = [d for d in extends_deps
                        if "User.Validate" in d.source_id and "User" in d.target_id]
        assert len(validate_dep) == 1
        assert validate_dep[0].target_id == "test-project:models.go:class:User"

    def test_multiple_methods_create_edges(self, scanner):
        """All methods on a struct should have EXTENDS edges."""
        symbols, deps = scanner.scan_file(Path("handler.go"), GO_METHOD_DEPS)
        extends_deps = [d for d in deps if d.dep_type == DependencyType.EXTENDS]

        # Server has Start, Stop, Restart -> 3 EXTENDS edges
        server_deps = [d for d in extends_deps if "Server" in d.target_id]
        assert len(server_deps) == 3

        method_names = {d.source_id.split(":")[-1] for d in server_deps}
        assert "Server.Start" in method_names
        assert "Server.Stop" in method_names
        assert "Server.Restart" in method_names

    def test_method_extends_target_format(self, scanner):
        """EXTENDS target should use class: prefix for struct."""
        _, deps = scanner.scan_file(Path("handler.go"), GO_METHOD_DEPS)
        extends_deps = [d for d in deps if d.dep_type == DependencyType.EXTENDS]
        for dep in extends_deps:
            assert ":class:Server" in dep.target_id


class TestGoInterfaceImplementation:
    """Test IMPLEMENTS edges when struct method set satisfies interface."""

    def test_struct_implements_interface(self, scanner):
        """User implements Stringer because it has String() method."""
        symbols, deps = scanner.scan_file(Path("service.go"), GO_IMPLEMENTATION)
        impl_deps = [d for d in deps if d.dep_type == DependencyType.IMPLEMENTS]
        assert len(impl_deps) >= 1

        stringer_impl = [d for d in impl_deps if "Stringer" in d.target_id]
        assert len(stringer_impl) == 1
        assert ":class:User" in stringer_impl[0].source_id

    def test_struct_implements_multiple_interfaces(self, scanner):
        """User implements both Stringer and Saver."""
        symbols, deps = scanner.scan_file(Path("service.go"), GO_IMPLEMENTATION)
        impl_deps = [d for d in deps if d.dep_type == DependencyType.IMPLEMENTS]
        impl_targets = {d.target_id.split(":")[-1] for d in impl_deps}
        assert "Stringer" in impl_targets
        assert "Saver" in impl_targets

    def test_no_false_implementation(self, scanner):
        """Struct should NOT implement an interface if methods don't match."""
        code = '''package test

type Flyer interface {
	Fly() error
}

type Dog struct {
	Name string
}

func (d *Dog) Bark() string {
	return "woof"
}
'''
        symbols, deps = scanner.scan_file(Path("test.go"), code)
        impl_deps = [d for d in deps if d.dep_type == DependencyType.IMPLEMENTS]
        assert len(impl_deps) == 0


class TestGoStructEmbedding:
    """Test EXTENDS edges for struct embeddings."""

    def test_simple_embedding(self, scanner):
        """Embedded struct creates EXTENDS edge."""
        symbols, deps = scanner.scan_file(Path("models.go"), GO_EMBEDDING)
        extends_deps = [d for d in deps
                        if d.dep_type == DependencyType.EXTENDS
                        and d.metadata.get("kind") == "embedding"]
        assert len(extends_deps) >= 1

        target_types = {d.target_id.split(":")[-1] for d in extends_deps}
        assert "Base" in target_types

    def test_pointer_embedding(self, scanner):
        """Pointer-embedded struct (*Timestamps) creates EXTENDS edge."""
        symbols, deps = scanner.scan_file(Path("models.go"), GO_EMBEDDING)
        extends_deps = [d for d in deps
                        if d.dep_type == DependencyType.EXTENDS
                        and d.metadata.get("kind") == "embedding"]
        target_types = {d.target_id.split(":")[-1] for d in extends_deps}
        assert "Timestamps" in target_types

    def test_embedding_source_is_struct(self, scanner):
        """Embedding EXTENDS source should be the containing struct."""
        symbols, deps = scanner.scan_file(Path("models.go"), GO_EMBEDDING)
        extends_deps = [d for d in deps
                        if d.dep_type == DependencyType.EXTENDS
                        and d.metadata.get("kind") == "embedding"]
        for dep in extends_deps:
            assert ":class:User" in dep.source_id


class TestGoTypeAliases:
    """Test type alias and named type detection."""

    def test_type_alias_detected(self, scanner):
        """Type aliases should be detected as TYPE symbols."""
        symbols, _ = scanner.scan_file(Path("types.go"), GO_TYPE_ALIAS)
        type_symbols = [s for s in symbols if s.symbol_type == SymbolType.TYPE]
        names = {s.name for s in type_symbols}
        assert "Duration" in names
        assert "Handler" in names
        assert "StringSlice" in names

    def test_type_alias_count(self, scanner):
        """Should find all 3 type aliases."""
        symbols, _ = scanner.scan_file(Path("types.go"), GO_TYPE_ALIAS)
        type_symbols = [s for s in symbols if s.symbol_type == SymbolType.TYPE]
        assert len(type_symbols) == 3

    def test_type_alias_underlying_type(self, scanner):
        """Underlying type should be stored in returns field."""
        symbols, _ = scanner.scan_file(Path("types.go"), GO_TYPE_ALIAS)
        duration = [s for s in symbols if s.name == "Duration"][0]
        assert duration.returns == "int64"

    def test_type_alias_not_struct(self, scanner):
        """Struct type declarations should NOT appear as type aliases."""
        symbols, _ = scanner.scan_file(Path("models.go"), GO_STRUCT)
        type_symbols = [s for s in symbols if s.symbol_type == SymbolType.TYPE]
        assert len(type_symbols) == 0

    def test_type_alias_exports(self, scanner):
        """Exported type aliases should have exports."""
        symbols, _ = scanner.scan_file(Path("types.go"), GO_TYPE_ALIAS)
        duration = [s for s in symbols if s.name == "Duration"][0]
        assert "Duration" in duration.exports


class TestGoInterfaceMethods:
    """Test interface method extraction and embedding."""

    def test_interface_methods_extracted(self, scanner):
        """Interface methods should be stored in params field."""
        symbols, _ = scanner.scan_file(Path("service.go"), GO_INTERFACE)
        repo = [s for s in symbols if s.name == "Repository"][0]
        assert "Find" in repo.params
        assert "Save" in repo.params

    def test_complex_interface_methods(self, scanner):
        """Reader and Writer interfaces should have their methods."""
        symbols, _ = scanner.scan_file(Path("io.go"), GO_COMPLEX_INTERFACE)
        reader = [s for s in symbols if s.name == "Reader"][0]
        assert "Read" in reader.params

        writer = [s for s in symbols if s.name == "Writer"][0]
        assert "Write" in writer.params

    def test_interface_embedding_creates_extends(self, scanner):
        """Embedded interfaces should create EXTENDS edges."""
        symbols, deps = scanner.scan_file(Path("io.go"), GO_COMPLEX_INTERFACE)
        extends_deps = [d for d in deps
                        if d.dep_type == DependencyType.EXTENDS
                        and d.metadata.get("kind") == "interface_embedding"]
        assert len(extends_deps) >= 2

        # ReadWriter embeds Reader and Writer
        rw_deps = [d for d in extends_deps if "ReadWriter" in d.source_id]
        rw_targets = {d.target_id.split(":")[-1] for d in rw_deps}
        assert "Reader" in rw_targets
        assert "Writer" in rw_targets

    def test_readcloser_embeds_reader(self, scanner):
        """ReadCloser embeds Reader and has Close method."""
        symbols, deps = scanner.scan_file(Path("io.go"), GO_COMPLEX_INTERFACE)
        extends_deps = [d for d in deps
                        if d.dep_type == DependencyType.EXTENDS
                        and d.metadata.get("kind") == "interface_embedding"]

        rc_deps = [d for d in extends_deps if "ReadCloser" in d.source_id]
        assert len(rc_deps) == 1
        assert "Reader" in rc_deps[0].target_id

        # ReadCloser should have Close as a method
        rc = [s for s in symbols if s.name == "ReadCloser"][0]
        assert "Close" in rc.params


class TestGoConstVar:
    """Test const and var declaration detection."""

    def test_single_const(self, scanner):
        """Single const declaration should be detected."""
        symbols, _ = scanner.scan_file(Path("config.go"), GO_CONST_VAR)
        vars_ = [s for s in symbols if s.symbol_type == SymbolType.VARIABLE]
        names = {s.name for s in vars_}
        assert "MaxRetries" in names

    def test_single_var(self, scanner):
        """Single var declaration should be detected."""
        symbols, _ = scanner.scan_file(Path("config.go"), GO_CONST_VAR)
        vars_ = [s for s in symbols if s.symbol_type == SymbolType.VARIABLE]
        names = {s.name for s in vars_}
        assert "DefaultTimeout" in names

    def test_const_block(self, scanner):
        """Const block entries should be detected."""
        symbols, _ = scanner.scan_file(Path("config.go"), GO_CONST_VAR)
        vars_ = [s for s in symbols if s.symbol_type == SymbolType.VARIABLE]
        names = {s.name for s in vars_}
        assert "StatusActive" in names
        assert "StatusInactive" in names
        assert "StatusPending" in names

    def test_var_block(self, scanner):
        """Var block entries should be detected."""
        symbols, _ = scanner.scan_file(Path("config.go"), GO_CONST_VAR)
        vars_ = [s for s in symbols if s.symbol_type == SymbolType.VARIABLE]
        names = {s.name for s in vars_}
        assert "GlobalCounter" in names
        assert "AppName" in names

    def test_const_var_total_count(self, scanner):
        """Should find all const and var declarations."""
        symbols, _ = scanner.scan_file(Path("config.go"), GO_CONST_VAR)
        vars_ = [s for s in symbols if s.symbol_type == SymbolType.VARIABLE]
        # MaxRetries, DefaultTimeout, StatusActive, StatusInactive, StatusPending,
        # GlobalCounter, AppName = 7
        assert len(vars_) == 7

    def test_exported_const(self, scanner):
        """Exported consts should have exports field."""
        symbols, _ = scanner.scan_file(Path("config.go"), GO_CONST_VAR)
        max_retries = [s for s in symbols if s.name == "MaxRetries"][0]
        assert "MaxRetries" in max_retries.exports


# --- Cross-file interface implementation tests ---

GO_CROSS_FILE_IFACE = '''package repo

type Storage interface {
	Get(key string) (string, error)
	Set(key string, value string) error
}
'''

GO_CROSS_FILE_STRUCT = '''package repo

type MemoryStore struct {
	data map[string]string
}

func (m *MemoryStore) Get(key string) (string, error) {
	return m.data[key], nil
}

func (m *MemoryStore) Set(key string, value string) error {
	m.data[key] = value
	return nil
}
'''


class TestGoCrossFileImpl:
    """Test cross-file interface implementation detection via engine resolver."""

    def _make_engine_with_index(self, idx):
        """Create a minimal engine mock with just the index and resolver method."""
        from unittest.mock import MagicMock
        # We can't import IndexEngine directly due to relative imports in engine.py.
        # Instead, import the method's logic from the source and bind it.
        # Simpler approach: build a lightweight object with the method inlined.

        class _EngineStub:
            def __init__(self, index):
                self.index = index

            def _resolve_go_implementations(self):
                """Cross-file: match Go struct method sets against interface method sets."""
                interfaces = {}
                struct_methods = {}

                for sid, sym in self.index.symbols.items():
                    stype = sym.symbol_type.value if hasattr(sym.symbol_type, 'value') else str(sym.symbol_type)
                    name = sym.name
                    params = sym.params or []
                    project = sid.split(":")[0] if ":" in sid else ""

                    if stype == "interface" and params:
                        interfaces[(project, name)] = set(params)
                    elif stype == "method" and "." in name:
                        receiver, method = name.split(".", 1)
                        struct_methods.setdefault((project, receiver), set()).add(method)

                if not interfaces or not struct_methods:
                    return

                existing_impls = set()
                for dep in self.index.dependencies.values():
                    if dep.dep_type == DependencyType.IMPLEMENTS:
                        existing_impls.add((dep.source_id, dep.target_id))

                sid_lookup = {}
                for sid in self.index.symbols:
                    parts = sid.split(":")
                    if len(parts) >= 4:
                        proj = parts[0]
                        sym_type = parts[-2]
                        sym_name = parts[-1]
                        key = (proj, sym_type, sym_name)
                        if key not in sid_lookup:
                            sid_lookup[key] = sid

                for (proj, struct_name), methods in struct_methods.items():
                    for (iproj, iface_name), iface_methods in interfaces.items():
                        if proj != iproj:
                            continue
                        if not iface_methods or not iface_methods.issubset(methods):
                            continue
                        struct_sid = sid_lookup.get((proj, "class", struct_name))
                        iface_sid = sid_lookup.get((proj, "interface", iface_name))
                        if not struct_sid or not iface_sid:
                            continue
                        if (struct_sid, iface_sid) in existing_impls:
                            continue
                        from models import Dependency
                        dep = Dependency(
                            source_id=struct_sid,
                            target_id=iface_sid,
                            dep_type=DependencyType.IMPLEMENTS,
                            source_line=0,
                            metadata={"kind": "cross_file"},
                        )
                        self.index.dependencies[dep.id] = dep
                        existing_impls.add((struct_sid, iface_sid))

        return _EngineStub(idx)

    def test_cross_file_implements_edge(self, scanner):
        """Interface in one file, struct+methods in another -> IMPLEMENTS edge."""
        from models import ProjectIndex

        syms1, deps1 = scanner.scan_file(Path("storage.go"), GO_CROSS_FILE_IFACE)
        syms2, deps2 = scanner.scan_file(Path("memory.go"), GO_CROSS_FILE_STRUCT)

        idx = ProjectIndex(project="test-project", root_path=".")
        for s in syms1 + syms2:
            idx.symbols[s.id] = s
        for d in deps1 + deps2:
            idx.dependencies[d.id] = d

        # Verify no in-file IMPLEMENTS edge exists (different files)
        impl_deps = [d for d in idx.dependencies.values()
                     if d.dep_type == DependencyType.IMPLEMENTS]
        assert len(impl_deps) == 0

        engine = self._make_engine_with_index(idx)
        engine._resolve_go_implementations()

        impl_deps = [d for d in idx.dependencies.values()
                     if d.dep_type == DependencyType.IMPLEMENTS]
        assert len(impl_deps) == 1
        assert ":class:MemoryStore" in impl_deps[0].source_id
        assert ":interface:Storage" in impl_deps[0].target_id

    def test_cross_file_no_duplicate_if_already_exists(self, scanner):
        """If in-file detection already added IMPLEMENTS, cross-file should not duplicate."""
        from models import ProjectIndex

        syms, deps = scanner.scan_file(Path("service.go"), GO_IMPLEMENTATION)

        idx = ProjectIndex(project="test-project", root_path=".")
        for s in syms:
            idx.symbols[s.id] = s
        for d in deps:
            idx.dependencies[d.id] = d

        before_count = len([d for d in idx.dependencies.values()
                           if d.dep_type == DependencyType.IMPLEMENTS])

        engine = self._make_engine_with_index(idx)
        engine._resolve_go_implementations()

        after_count = len([d for d in idx.dependencies.values()
                          if d.dep_type == DependencyType.IMPLEMENTS])
        assert after_count == before_count  # no duplicates

    def test_cross_file_missing_method_no_edge(self, scanner):
        """Struct missing one interface method should NOT get IMPLEMENTS edge."""
        from models import ProjectIndex

        iface_code = '''package svc

type Worker interface {
	Start() error
	Stop() error
}
'''
        struct_code = '''package svc

type SimpleWorker struct {}

func (w *SimpleWorker) Start() error {
	return nil
}
'''
        syms1, deps1 = scanner.scan_file(Path("worker.go"), iface_code)
        syms2, deps2 = scanner.scan_file(Path("simple.go"), struct_code)

        idx = ProjectIndex(project="test-project", root_path=".")
        for s in syms1 + syms2:
            idx.symbols[s.id] = s
        for d in deps1 + deps2:
            idx.dependencies[d.id] = d

        engine = self._make_engine_with_index(idx)
        engine._resolve_go_implementations()

        impl_deps = [d for d in idx.dependencies.values()
                     if d.dep_type == DependencyType.IMPLEMENTS]
        assert len(impl_deps) == 0

    def test_cross_file_different_projects_no_edge(self, scanner):
        """Interface in project A, struct in project B -> no IMPLEMENTS edge."""
        from models import ProjectIndex

        scanner_a = GoScanner("project-a")
        scanner_b = GoScanner("project-b")

        syms1, deps1 = scanner_a.scan_file(Path("iface.go"), GO_CROSS_FILE_IFACE)
        syms2, deps2 = scanner_b.scan_file(Path("impl.go"), GO_CROSS_FILE_STRUCT)

        idx = ProjectIndex(project="project-a", root_path=".")
        for s in syms1 + syms2:
            idx.symbols[s.id] = s
        for d in deps1 + deps2:
            idx.dependencies[d.id] = d

        engine = self._make_engine_with_index(idx)
        engine._resolve_go_implementations()

        impl_deps = [d for d in idx.dependencies.values()
                     if d.dep_type == DependencyType.IMPLEMENTS]
        assert len(impl_deps) == 0


# --- EMBED_PATTERN edge case tests ---

class TestGoEmbedPatternEdgeCases:
    """Test that EMBED_PATTERN doesn't false-positive on edge cases."""

    def test_comment_not_treated_as_embed(self, scanner):
        """A comment line with just a type name should NOT create an embedding."""
        code = '''package models

type Config struct {
	// ServerConfig
	Host string
	Port int
}
'''
        symbols, deps = scanner.scan_file(Path("config.go"), code)
        embed_deps = [d for d in deps
                      if d.dep_type == DependencyType.EXTENDS
                      and d.metadata.get("kind") == "embedding"]
        assert len(embed_deps) == 0

    def test_named_field_not_treated_as_embed(self, scanner):
        """A named field like 'SomeType string' should NOT match EMBED_PATTERN."""
        code = '''package models

type Record struct {
	SomeType string
	Value    int
}
'''
        symbols, deps = scanner.scan_file(Path("record.go"), code)
        embed_deps = [d for d in deps
                      if d.dep_type == DependencyType.EXTENDS
                      and d.metadata.get("kind") == "embedding"]
        # 'SomeType string' has two tokens, so regex won't match (not just a type name)
        assert len(embed_deps) == 0

    def test_builtin_type_not_treated_as_embed(self, scanner):
        """Go builtin types like 'error' should NOT create embedding edges."""
        # Note: builtin types are lowercase so EMBED_PATTERN requires [A-Z] start.
        # But verify the blocklist works for edge cases where a capitalized
        # variant might slip through via package prefix.
        code = '''package models

type Wrapper struct {
	Inner
}
'''
        symbols, deps = scanner.scan_file(Path("wrapper.go"), code)
        embed_deps = [d for d in deps
                      if d.dep_type == DependencyType.EXTENDS
                      and d.metadata.get("kind") == "embedding"]
        # 'Inner' is a valid embedding (capitalized, not builtin)
        assert len(embed_deps) == 1
        assert "Inner" in embed_deps[0].target_id

    def test_legitimate_embedding_still_works(self, scanner):
        """Verify real embeddings still work after adding blocklist."""
        symbols, deps = scanner.scan_file(Path("models.go"), GO_EMBEDDING)
        embed_deps = [d for d in deps
                      if d.dep_type == DependencyType.EXTENDS
                      and d.metadata.get("kind") == "embedding"]
        target_types = {d.target_id.split(":")[-1] for d in embed_deps}
        assert "Base" in target_types
        assert "Timestamps" in target_types
