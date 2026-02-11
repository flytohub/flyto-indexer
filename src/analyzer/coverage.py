"""
Test coverage analysis - find code without corresponding tests

Strategy:
1. Scan the src directory to find all modules
2. Scan test/tests directories to find all tests
3. Compare: which modules have no tests?
4. Analyze: which public functions are not referenced by tests?
"""

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class UncoveredModule:
    """Module without tests"""
    path: str
    functions: list[str]  # Public functions
    classes: list[str]    # Public classes
    importance: str       # high/medium/low


@dataclass
class CoverageReport:
    """Test coverage report"""
    total_modules: int = 0
    total_tests: int = 0
    covered_modules: int = 0
    uncovered_modules: list[UncoveredModule] = field(default_factory=list)
    partial_modules: list[tuple[str, list[str], list[str]]] = field(default_factory=list)  # (path, tested, untested)

    @property
    def coverage_rate(self) -> float:
        if self.total_modules == 0:
            return 0
        return self.covered_modules / self.total_modules * 100


class CoverageAnalyzer:
    """Test coverage analyzer"""

    def __init__(
        self,
        project_root: Path,
        src_dirs: list[str] = None,
        test_dirs: list[str] = None,
        ignore_patterns: list[str] = None,
    ):
        self.project_root = project_root
        self.src_dirs = src_dirs or ["src", "lib", "app", "core", "modules"]
        self.test_dirs = test_dirs or ["test", "tests", "__tests__", "spec"]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".nuxt", ".output",
        ]

        # Index
        self.modules: dict[str, dict] = {}  # path -> {functions, classes}
        self.tests: dict[str, set[str]] = defaultdict(set)  # test_path -> imported modules
        self.test_coverage: dict[str, set[str]] = defaultdict(set)  # module -> tested functions

    def _should_skip(self, path: str) -> bool:
        return any(pattern in path for pattern in self.ignore_patterns)

    def _is_test_file(self, path: str) -> bool:
        """Determine if a file is a test file"""
        name = Path(path).stem.lower()
        return (
            name.startswith("test_") or
            name.endswith("_test") or
            name.endswith(".test") or
            name.endswith(".spec") or
            "test" in Path(path).parts or
            "tests" in Path(path).parts or
            "__tests__" in Path(path).parts
        )

    def _get_module_name(self, path: str) -> str:
        """Get module name (for matching)"""
        p = Path(path)
        # Remove extension and test prefix
        name = p.stem.lower()
        if name.startswith("test_"):
            name = name[5:]
        if name.endswith("_test"):
            name = name[:-5]
        return name

    def scan_modules(self):
        """Scan all modules"""
        for ext in [".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go"]:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))

                if self._should_skip(rel_path):
                    continue

                if self._is_test_file(rel_path):
                    continue

                # Only look at files under src directories (or root level)
                parts = Path(rel_path).parts
                is_src = any(d in parts for d in self.src_dirs) or len(parts) <= 2

                if not is_src:
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8")
                except Exception:
                    continue

                # Extract public functions and classes
                if ext == ".py":
                    exports = self._extract_python_exports(content)
                elif ext == ".java":
                    exports = self._extract_java_exports(content)
                elif ext == ".go":
                    exports = self._extract_go_exports(content)
                else:
                    exports = self._extract_ts_exports(content)

                if exports["functions"] or exports["classes"]:
                    self.modules[rel_path] = exports

    def _extract_python_exports(self, content: str) -> dict:
        """Extract Python public exports"""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return {"functions": [], "classes": []}

        functions = []
        classes = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    functions.append(node.name)
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                classes.append(node.name)

        return {"functions": functions, "classes": classes}

    def _extract_ts_exports(self, content: str) -> dict:
        """Extract TypeScript public exports"""
        functions = []
        classes = []

        # export function name
        for match in re.finditer(r'export\s+(?:async\s+)?function\s+(\w+)', content):
            functions.append(match.group(1))

        # export const name = () =>
        for match in re.finditer(r'export\s+const\s+(\w+)\s*=', content):
            functions.append(match.group(1))

        # export class name
        for match in re.finditer(r'export\s+class\s+(\w+)', content):
            classes.append(match.group(1))

        # export default
        for match in re.finditer(r'export\s+default\s+(?:function|class)?\s*(\w+)?', content):
            name = match.group(1)
            if name:
                functions.append(name)

        return {"functions": functions, "classes": classes}

    def _extract_java_exports(self, content: str) -> dict:
        """Extract Java public exports"""
        functions = []
        classes = []

        # public class Name
        for match in re.finditer(r'public\s+(?:abstract\s+)?class\s+(\w+)', content):
            classes.append(match.group(1))

        # public interface Name
        for match in re.finditer(r'public\s+interface\s+(\w+)', content):
            classes.append(match.group(1))

        # public method (not constructor)
        for match in re.finditer(r'public\s+(?:static\s+)?(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\([^)]*\)', content):
            name = match.group(1)
            # Skip constructors (uppercase first letter and not common method names)
            if not (name[0].isupper() and name not in ["ToString", "GetHashCode", "Equals"]):
                functions.append(name)

        return {"functions": functions, "classes": classes}

    def _extract_go_exports(self, content: str) -> dict:
        """Extract Go public exports (uppercase first letter)"""
        functions = []
        classes = []  # Go uses structs as classes

        # func Name (exported if uppercase)
        for match in re.finditer(r'func\s+(?:\([^)]+\)\s+)?([A-Z]\w*)\s*\(', content):
            functions.append(match.group(1))

        # type Name struct (exported if uppercase)
        for match in re.finditer(r'type\s+([A-Z]\w*)\s+struct', content):
            classes.append(match.group(1))

        # type Name interface (exported if uppercase)
        for match in re.finditer(r'type\s+([A-Z]\w*)\s+interface', content):
            classes.append(match.group(1))

        return {"functions": functions, "classes": classes}

    def scan_tests(self):
        """Scan all test files"""
        for ext in [".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go"]:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))

                if self._should_skip(rel_path):
                    continue

                if not self._is_test_file(rel_path):
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8")
                except Exception:
                    continue

                # Find which modules the test references
                if ext == ".py":
                    imports = self._extract_python_imports(content)
                else:
                    imports = self._extract_ts_imports(content)

                self.tests[rel_path] = imports

                # Find which functions the test calls
                for module_path, exports in self.modules.items():
                    for func in exports["functions"]:
                        if re.search(rf'\b{func}\s*\(', content):
                            self.test_coverage[module_path].add(func)
                    for cls in exports["classes"]:
                        if re.search(rf'\b{cls}\s*\(', content):
                            self.test_coverage[module_path].add(cls)

    def _extract_python_imports(self, content: str) -> set[str]:
        """Extract Python imports"""
        imports = set()

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return imports

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])

        return imports

    def _extract_ts_imports(self, content: str) -> set[str]:
        """Extract TypeScript imports"""
        imports = set()

        patterns = [
            r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]',
            r'require\s*\([\'"]([^\'"]+)[\'"]\)',
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, content):
                module = match.group(1)
                # Get module name
                name = Path(module).stem if module.startswith(".") else module.split("/")[0]
                imports.add(name.lower())

        return imports

    def _assess_importance(self, path: str, exports: dict) -> str:
        """Assess module importance"""
        parts = Path(path).parts
        name = Path(path).stem.lower()

        # High importance
        if any(kw in name for kw in ["auth", "pay", "order", "user", "api", "core"]):
            return "high"
        if any(kw in parts for kw in ["api", "core", "services", "auth"]):
            return "high"

        # Medium importance
        if len(exports["functions"]) > 5 or len(exports["classes"]) > 2:
            return "medium"
        if any(kw in parts for kw in ["utils", "helpers", "lib"]):
            return "medium"

        return "low"

    def analyze(self) -> CoverageReport:
        """Run the analysis"""
        self.scan_modules()
        self.scan_tests()

        report = CoverageReport()
        report.total_modules = len(self.modules)
        report.total_tests = len(self.tests)

        # Analyze coverage
        for path, exports in self.modules.items():
            module_name = self._get_module_name(path)
            all_exports = set(exports["functions"] + exports["classes"])
            tested = self.test_coverage.get(path, set())

            # Check if there is a corresponding test
            has_test = False
            for test_path in self.tests:
                test_name = self._get_module_name(test_path)
                if module_name in test_name or test_name in module_name:
                    has_test = True
                    break

            if has_test or tested:
                report.covered_modules += 1
                untested = all_exports - tested
                if untested:
                    report.partial_modules.append((path, list(tested), list(untested)))
            else:
                importance = self._assess_importance(path, exports)
                report.uncovered_modules.append(UncoveredModule(
                    path=path,
                    functions=exports["functions"],
                    classes=exports["classes"],
                    importance=importance,
                ))

        # Sort: high importance first
        importance_order = {"high": 0, "medium": 1, "low": 2}
        report.uncovered_modules.sort(key=lambda x: importance_order.get(x.importance, 2))

        return report

    def print_report(self, report: CoverageReport):
        """Print the report"""
        print(f"\n{'='*70}")
        print("Test Coverage Analysis")
        print(f"{'='*70}")
        print(f"\nModules: {report.total_modules}")
        print(f"Test files: {report.total_tests}")
        print(f"Covered: {report.covered_modules} ({report.coverage_rate:.1f}%)")
        print(f"Uncovered: {len(report.uncovered_modules)}")

        if report.uncovered_modules:
            print(f"\n{'='*70}")
            print("UNCOVERED MODULES (need tests)")
            print(f"{'='*70}")

            # Group by importance
            for importance in ["high", "medium", "low"]:
                modules = [m for m in report.uncovered_modules if m.importance == importance]
                if not modules:
                    continue

                icon = {"high": "!", "medium": "?", "low": "-"}[importance]
                print(f"\n[{importance.upper()}] {len(modules)} modules")

                for m in modules[:10]:
                    print(f"  {icon} {m.path}")
                    if m.functions:
                        print(f"    Functions: {', '.join(m.functions[:5])}")
                        if len(m.functions) > 5:
                            print(f"    ... and {len(m.functions) - 5} more")
                    if m.classes:
                        print(f"    Classes: {', '.join(m.classes)}")

                if len(modules) > 10:
                    print(f"  ... and {len(modules) - 10} more")

        if report.partial_modules:
            print(f"\n{'='*70}")
            print("PARTIALLY COVERED (some functions untested)")
            print(f"{'='*70}")

            for path, tested, untested in report.partial_modules[:10]:
                print(f"\n  {path}")
                print(f"    Tested: {', '.join(tested[:3])}")
                print(f"    Untested: {', '.join(untested[:3])}")


def analyze_coverage(project_path: Path) -> CoverageReport:
    """Convenience function"""
    analyzer = CoverageAnalyzer(project_path)
    return analyzer.analyze()
