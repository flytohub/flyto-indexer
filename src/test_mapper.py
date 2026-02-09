"""
Test File Mapper — source ↔ test file bidirectional mapping.

Two-layer strategy:
1. Naming convention (primary): src/foo.py → tests/test_foo.py, Foo.vue → Foo.test.ts
2. Import analysis (fallback): test file imports source → establish link
"""

import os
import re
from pathlib import PurePosixPath
from typing import Optional


# Test file patterns (basename matching)
_TEST_FILE_PATTERNS = [
    re.compile(r'^test_.*\.py$'),           # test_foo.py
    re.compile(r'^.*_test\.py$'),           # foo_test.py
    re.compile(r'^.*\.test\.[jt]sx?$'),     # foo.test.ts, foo.test.js
    re.compile(r'^.*\.spec\.[jt]sx?$'),     # foo.spec.ts, foo.spec.js
    re.compile(r'^.*Test\.[jt]sx?$'),       # FooTest.js
    re.compile(r'^.*\.test\.vue$'),         # Foo.test.vue
]

# Test directory names
_TEST_DIRS = {'tests', 'test', '__tests__', '__test__', 'spec', 'specs'}


class TestMapper:
    """Bidirectional source ↔ test file mapper."""

    def __init__(self, index: dict):
        self._index = index
        self._source_to_test: dict[str, str] = {}
        self._test_to_source: dict[str, str] = {}
        self._built = False

    def build(self) -> None:
        """Build the mapping (lazy, one-time)."""
        if self._built:
            return
        self._built = True

        symbols = self._index.get("symbols", {})
        dependencies = self._index.get("dependencies", {})

        # Collect all file paths grouped by project
        project_files: dict[str, set[str]] = {}
        for sym_id, sym in symbols.items():
            proj = sym_id.split(":")[0] if ":" in sym_id else ""
            path = sym.get("path", "")
            if proj and path:
                if proj not in project_files:
                    project_files[proj] = set()
                project_files[proj].add(path)

        # Separate test files from source files
        all_test_paths: set[str] = set()
        for paths in project_files.values():
            for p in paths:
                if self._is_test_file(p):
                    all_test_paths.add(p)

        # Layer 1: Naming convention
        for proj, paths in project_files.items():
            source_paths = [p for p in paths if not self._is_test_file(p)]
            test_paths = [p for p in paths if self._is_test_file(p)]

            for source in source_paths:
                matches = self._find_test_by_convention(source, test_paths)
                if matches:
                    best = matches[0]
                    self._source_to_test[source] = best
                    self._test_to_source[best] = source

        # Layer 2: Import analysis (for unmapped test files)
        for dep_id, dep in dependencies.items():
            if dep.get("type") != "imports":
                continue
            source_id = dep.get("source", "")
            source_path = ""
            if ":" in source_id:
                parts = source_id.split(":")
                if len(parts) >= 2:
                    source_path = parts[1]

            if not source_path or not self._is_test_file(source_path):
                continue
            if source_path in self._test_to_source:
                continue

            # This test file imports something — find the target source file
            target = dep.get("target", "")
            resolved = dep.get("metadata", {}).get("resolved_target", "")

            target_path = ""
            if resolved and ":" in resolved:
                rparts = resolved.split(":")
                if len(rparts) >= 2:
                    target_path = rparts[1]
            elif target:
                # Try to find a source file matching this target name
                target_base = target.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                for proj_paths in project_files.values():
                    for p in proj_paths:
                        if not self._is_test_file(p):
                            p_base = p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                            if p_base == target_base:
                                target_path = p
                                break
                    if target_path:
                        break

            if target_path and not self._is_test_file(target_path):
                if target_path not in self._source_to_test:
                    self._source_to_test[target_path] = source_path
                    self._test_to_source[source_path] = target_path

    def find_test(self, path: str) -> Optional[str]:
        """Find test file for a source file."""
        self.build()
        return self._source_to_test.get(path)

    def find_source(self, path: str) -> Optional[str]:
        """Find source file for a test file."""
        self.build()
        return self._test_to_source.get(path)

    @staticmethod
    def _is_test_file(path: str) -> bool:
        """Check if a path is a test file."""
        basename = path.rsplit("/", 1)[-1]
        # Check filename patterns
        for pattern in _TEST_FILE_PATTERNS:
            if pattern.match(basename):
                return True
        # Check directory
        parts = path.replace("\\", "/").split("/")
        for part in parts:
            if part.lower() in _TEST_DIRS:
                return True
        return False

    def _find_test_by_convention(self, source: str, test_paths: list[str]) -> list[str]:
        """Find test files matching a source file by naming convention."""
        matches = []
        src_basename = source.rsplit("/", 1)[-1]
        src_stem = src_basename.rsplit(".", 1)[0]

        # Generate expected test file basenames
        expected_names = set()

        # Python: foo.py → test_foo.py, foo_test.py
        if src_basename.endswith(".py"):
            expected_names.add(f"test_{src_stem}.py")
            expected_names.add(f"{src_stem}_test.py")

        # JS/TS/Vue: Foo.vue → Foo.test.ts, Foo.test.js, Foo.spec.ts, Foo.spec.js
        for ext in (".vue", ".ts", ".tsx", ".js", ".jsx"):
            if src_basename.endswith(ext):
                for test_ext in (".test.ts", ".test.js", ".test.tsx", ".test.jsx",
                                 ".spec.ts", ".spec.js", ".spec.tsx", ".spec.jsx"):
                    expected_names.add(f"{src_stem}{test_ext}")
                break

        for tp in test_paths:
            test_basename = tp.rsplit("/", 1)[-1]
            if test_basename in expected_names:
                matches.append(tp)

        # Sort: prefer same directory depth, then shorter path
        matches.sort(key=lambda p: (abs(p.count("/") - source.count("/")), len(p)))
        return matches
