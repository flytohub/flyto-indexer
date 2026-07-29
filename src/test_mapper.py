"""
Test File Mapper — source ↔ test file bidirectional mapping.

Two-layer strategy:
1. Naming convention (primary): src/foo.py → tests/test_foo.py, Foo.vue → Foo.test.ts
2. Import analysis (fallback): test file imports source → establish link
"""

import re
import threading
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

    __test__ = False

    def __init__(self, index: dict, project: str | None = None):
        self._index = index
        self._project = project
        self._source_to_test: dict[str, str] = {}
        self._test_to_source: dict[str, str] = {}
        self._source_to_test_by_project: dict[tuple[str, str], str] = {}
        self._test_to_source_by_project: dict[tuple[str, str], str] = {}
        self._built = False
        self._build_lock = threading.Lock()

    def build(self) -> None:
        """Build the mapping once with bounded, project-aware lookups."""
        if self._built:
            return

        with self._build_lock:
            if self._built:
                return

            symbols = self._index.get("symbols", {})
            dependencies = self._index.get("dependencies", {})

            # Collect file paths once. A scoped mapper ignores unrelated projects,
            # which keeps task planning proportional to the selected project.
            project_files: dict[str, set[str]] = {}
            for sym_id, sym in symbols.items():
                proj = self._project_from_symbol_id(sym_id)
                if self._project and proj != self._project:
                    continue
                path = sym.get("path", "")
                if proj and path:
                    project_files.setdefault(proj, set()).add(path)

            # Layer 1: naming convention. Index test basenames once instead of
            # comparing every source with every test file.
            for proj in sorted(project_files):
                paths = project_files[proj]
                source_paths = sorted(p for p in paths if not self._is_test_file(p))
                test_paths_by_basename: dict[str, list[str]] = {}
                for test_path in sorted(p for p in paths if self._is_test_file(p)):
                    basename = test_path.rsplit("/", 1)[-1]
                    test_paths_by_basename.setdefault(basename, []).append(test_path)

                for source in source_paths:
                    matches = self._find_test_by_convention(
                        source, test_paths_by_basename
                    )
                    if matches:
                        self._record_pair(proj, source, matches[0])

            # Layer 2: import analysis (for unmapped test files).
            self._build_by_import_analysis(project_files, dependencies)
            self._built = True

    @staticmethod
    def _project_from_symbol_id(symbol_id: str) -> str:
        return symbol_id.split(":", 1)[0] if ":" in symbol_id else ""

    @staticmethod
    def _path_from_symbol_id(symbol_id: str) -> str:
        parts = symbol_id.split(":")
        return parts[1] if len(parts) >= 2 else ""

    @staticmethod
    def _path_stem(path: str) -> str:
        return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    def _record_pair(self, project: str, source: str, test: str) -> None:
        """Record a deterministic project-qualified pair plus legacy lookup."""
        project_source = (project, source)
        project_test = (project, test)
        if project_source in self._source_to_test_by_project:
            return
        self._source_to_test_by_project[project_source] = test
        self._test_to_source_by_project[project_test] = source
        self._source_to_test.setdefault(source, test)
        self._test_to_source.setdefault(test, source)

    def _build_by_import_analysis(
        self, project_files: dict[str, set[str]], dependencies: dict
    ) -> None:
        """Layer 2: link unmapped test files to source via import analysis."""
        sources_by_project_and_stem: dict[str, dict[str, list[str]]] = {}
        for project, paths in project_files.items():
            by_stem: dict[str, list[str]] = {}
            for path in sorted(paths):
                if not self._is_test_file(path):
                    by_stem.setdefault(self._path_stem(path), []).append(path)
            sources_by_project_and_stem[project] = by_stem

        for _dep_id, dep in dependencies.items():
            if dep.get("type") != "imports":
                continue
            source_id = dep.get("source", "")
            source_project = self._project_from_symbol_id(source_id)
            if self._project and source_project != self._project:
                continue
            source_path = self._path_from_symbol_id(source_id)

            if not source_path or not self._is_test_file(source_path):
                continue
            if (source_project, source_path) in self._test_to_source_by_project:
                continue

            # This test file imports something — find the target source file
            target = dep.get("target", "")
            resolved = dep.get("metadata", {}).get("resolved_target", "")

            target_path = ""
            if resolved and ":" in resolved:
                resolved_project = self._project_from_symbol_id(resolved)
                if not source_project or resolved_project == source_project:
                    target_path = self._path_from_symbol_id(resolved)
            elif target:
                target_base = self._path_stem(target)
                candidates = sources_by_project_and_stem.get(
                    source_project, {}
                ).get(target_base, [])
                if candidates:
                    target_path = candidates[0]

            if target_path and not self._is_test_file(target_path):
                self._record_pair(source_project, target_path, source_path)

    def find_test(self, path: str, project: str | None = None) -> Optional[str]:
        """Find test file for a source file."""
        self.build()
        scoped_project = project or self._project
        if scoped_project:
            return self._source_to_test_by_project.get((scoped_project, path))
        return self._source_to_test.get(path)

    def find_source(self, path: str, project: str | None = None) -> Optional[str]:
        """Find source file for a test file."""
        self.build()
        scoped_project = project or self._project
        if scoped_project:
            return self._test_to_source_by_project.get((scoped_project, path))
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
        return any(part.lower() in _TEST_DIRS for part in parts)

    def _find_test_by_convention(
        self,
        source: str,
        test_paths: list[str] | dict[str, list[str]],
    ) -> list[str]:
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

        if isinstance(test_paths, dict):
            for expected_name in expected_names:
                matches.extend(test_paths.get(expected_name, []))
        else:
            for tp in test_paths:
                test_basename = tp.rsplit("/", 1)[-1]
                if test_basename in expected_names:
                    matches.append(tp)

        # Sort: prefer same directory depth, then shorter path
        matches.sort(key=lambda p: (abs(p.count("/") - source.count("/")), len(p)))
        return matches
