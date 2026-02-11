"""Tests for Java scanner."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import SymbolType, DependencyType
from scanner.java import JavaScanner


@pytest.fixture
def scanner():
    return JavaScanner("test-project")


JAVA_CLASS = '''package com.example;

import java.util.List;
import java.util.ArrayList;

/**
 * Represents a user in the system.
 */
public class User {
    private String name;
    private int age;

    public User(String name, int age) {
        this.name = name;
        this.age = age;
    }

    public String getName() {
        return this.name;
    }

    public void setName(String name) {
        this.name = name;
    }
}
'''

JAVA_INTERFACE = '''package com.example;

public interface Repository<T> {
    T findById(String id);
    void save(T entity);
}
'''

JAVA_EXTENDS = '''package com.example;

import com.example.User;

public class Admin extends User implements Serializable {
    private String role;

    public String getRole() {
        return this.role;
    }
}
'''

JAVA_ENUM = '''package com.example;

public enum Status {
    ACTIVE,
    INACTIVE,
    PENDING
}
'''


class TestJavaScannerBasic:
    """Test basic Java scanner setup."""

    def test_supported_extensions(self, scanner):
        assert ".java" in scanner.supported_extensions

    def test_empty_file(self, scanner):
        symbols, deps = scanner.scan_file(Path("Empty.java"), "")
        assert symbols == []


class TestJavaScannerClasses:
    """Test class extraction."""

    def test_class_extracted(self, scanner):
        symbols, _ = scanner.scan_file(Path("User.java"), JAVA_CLASS)
        classes = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "User"
        assert classes[0].language == "java"

    def test_class_javadoc(self, scanner):
        symbols, _ = scanner.scan_file(Path("User.java"), JAVA_CLASS)
        classes = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        assert "user" in classes[0].summary.lower()


class TestJavaScannerMethods:
    """Test method extraction."""

    def test_methods_extracted(self, scanner):
        symbols, _ = scanner.scan_file(Path("User.java"), JAVA_CLASS)
        methods = [s for s in symbols if s.symbol_type == SymbolType.METHOD]
        method_names = [m.name for m in methods]
        assert "User.getName" in method_names
        assert "User.setName" in method_names

    def test_method_params(self, scanner):
        symbols, _ = scanner.scan_file(Path("User.java"), JAVA_CLASS)
        setName = [m for m in symbols if m.name == "User.setName"]
        assert len(setName) == 1
        assert "name" in setName[0].params

    def test_constructor_extracted(self, scanner):
        symbols, _ = scanner.scan_file(Path("User.java"), JAVA_CLASS)
        constructors = [s for s in symbols if "<init>" in s.name]
        assert len(constructors) == 1
        assert constructors[0].name == "User.<init>"


class TestJavaScannerInterfaces:
    """Test interface extraction."""

    def test_interface(self, scanner):
        symbols, _ = scanner.scan_file(Path("Repository.java"), JAVA_INTERFACE)
        ifaces = [s for s in symbols if s.symbol_type == SymbolType.INTERFACE]
        assert len(ifaces) == 1
        assert ifaces[0].name == "Repository"


class TestJavaScannerInheritance:
    """Test extends/implements extraction."""

    def test_extends_dependency(self, scanner):
        _, deps = scanner.scan_file(Path("Admin.java"), JAVA_EXTENDS)
        extends_deps = [d for d in deps if d.dep_type == DependencyType.EXTENDS]
        assert len(extends_deps) >= 1
        targets = [d.target_id for d in extends_deps]
        assert "User" in targets

    def test_implements_dependency(self, scanner):
        _, deps = scanner.scan_file(Path("Admin.java"), JAVA_EXTENDS)
        impl_deps = [d for d in deps if d.dep_type == DependencyType.IMPLEMENTS]
        assert len(impl_deps) >= 1
        targets = [d.target_id for d in impl_deps]
        assert "Serializable" in targets


class TestJavaScannerEnums:
    """Test enum extraction."""

    def test_enum_as_type(self, scanner):
        symbols, _ = scanner.scan_file(Path("Status.java"), JAVA_ENUM)
        types = [s for s in symbols if s.symbol_type == SymbolType.TYPE]
        assert len(types) == 1
        assert types[0].name == "Status"


class TestJavaScannerImports:
    """Test import extraction."""

    def test_imports(self, scanner):
        _, deps = scanner.scan_file(Path("User.java"), JAVA_CLASS)
        import_deps = [d for d in deps if d.dep_type == DependencyType.IMPORTS]
        targets = [d.target_id for d in import_deps]
        assert "java.util.List" in targets
        assert "java.util.ArrayList" in targets
