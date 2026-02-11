"""Tests for Rust scanner."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import SymbolType, DependencyType
from scanner.rust import RustScanner


@pytest.fixture
def scanner():
    return RustScanner("test-project")


RUST_STRUCT = '''use std::fmt;

/// A point in 2D space.
pub struct Point {
    pub x: f64,
    pub y: f64,
}

impl Point {
    pub fn new(x: f64, y: f64) -> Self {
        Point { x, y }
    }

    pub fn distance(&self, other: &Point) -> f64 {
        ((self.x - other.x).powi(2) + (self.y - other.y).powi(2)).sqrt()
    }
}
'''

RUST_TRAIT = '''/// Defines a shape.
pub trait Shape {
    fn area(&self) -> f64;
    fn perimeter(&self) -> f64;
}
'''

RUST_FUNCTION = '''pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

fn private_helper(x: &str) -> String {
    x.to_uppercase()
}
'''

RUST_ENUM = '''pub enum Color {
    Red,
    Green,
    Blue,
}
'''

RUST_USE = '''use std::collections::{HashMap, HashSet};
use crate::models::User;
use super::utils;
'''


class TestRustScannerBasic:
    """Test basic Rust scanner setup."""

    def test_supported_extensions(self, scanner):
        assert ".rs" in scanner.supported_extensions

    def test_empty_file(self, scanner):
        symbols, deps = scanner.scan_file(Path("lib.rs"), "")
        assert symbols == []


class TestRustScannerStructs:
    """Test struct extraction."""

    def test_struct(self, scanner):
        symbols, _ = scanner.scan_file(Path("point.rs"), RUST_STRUCT)
        structs = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert len(structs) == 1
        assert structs[0].name == "Point"
        assert structs[0].language == "rust"

    def test_struct_doc_comment(self, scanner):
        symbols, _ = scanner.scan_file(Path("point.rs"), RUST_STRUCT)
        structs = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert "point" in structs[0].summary.lower() or "2D" in structs[0].summary


class TestRustScannerMethods:
    """Test impl block method extraction."""

    def test_impl_methods(self, scanner):
        symbols, _ = scanner.scan_file(Path("point.rs"), RUST_STRUCT)
        methods = [s for s in symbols if s.symbol_type == SymbolType.METHOD]
        method_names = [m.name for m in methods]
        assert "Point.new" in method_names
        assert "Point.distance" in method_names

    def test_method_params_exclude_self(self, scanner):
        symbols, _ = scanner.scan_file(Path("point.rs"), RUST_STRUCT)
        distance = [m for m in symbols if m.name == "Point.distance"]
        assert len(distance) == 1
        assert "self" not in distance[0].params
        assert "other" in distance[0].params


class TestRustScannerTraits:
    """Test trait extraction."""

    def test_trait_as_interface(self, scanner):
        symbols, _ = scanner.scan_file(Path("shape.rs"), RUST_TRAIT)
        traits = [s for s in symbols if s.symbol_type == SymbolType.INTERFACE]
        assert len(traits) == 1
        assert traits[0].name == "Shape"


class TestRustScannerFunctions:
    """Test top-level function extraction."""

    def test_pub_function(self, scanner):
        symbols, _ = scanner.scan_file(Path("math.rs"), RUST_FUNCTION)
        funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) >= 1
        pub_funcs = [f for f in funcs if "add" in f.exports]
        assert len(pub_funcs) == 1

    def test_function_params(self, scanner):
        symbols, _ = scanner.scan_file(Path("math.rs"), RUST_FUNCTION)
        add_fn = [f for f in symbols if f.name == "add" and f.symbol_type == SymbolType.FUNCTION]
        assert len(add_fn) == 1
        assert "a" in add_fn[0].params
        assert "b" in add_fn[0].params


class TestRustScannerEnums:
    """Test enum extraction."""

    def test_enum_as_type(self, scanner):
        symbols, _ = scanner.scan_file(Path("color.rs"), RUST_ENUM)
        types = [s for s in symbols if s.symbol_type == SymbolType.TYPE]
        assert len(types) == 1
        assert types[0].name == "Color"


class TestRustScannerUseStatements:
    """Test use statement extraction."""

    def test_use_imports(self, scanner):
        _, deps = scanner.scan_file(Path("lib.rs"), RUST_USE)
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        assert len(import_deps) >= 2
        # Check multi-import
        names_flat = []
        for d in import_deps:
            names_flat.extend(d.metadata.get("names", []))
        assert "HashMap" in names_flat or "HashSet" in names_flat
