"""
Dead code detector -- find unreferenced files and functions

Features:
1. Scan all import/require statements
2. Build reference graph (who references whom)
3. Find files never referenced (orphan files)
4. Find exported functions never called (orphan functions)

Supported languages: Python, TypeScript/JavaScript, Vue
"""

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DeadCodeReport:
    """Dead code analysis report"""
    total_files: int = 0
    orphan_files: list[str] = field(default_factory=list)
    low_reference_files: list[tuple[str, int]] = field(default_factory=list)  # (path, ref_count)
    orphan_exports: list[tuple[str, str]] = field(default_factory=list)  # (path, export_name)
    circular_deps: list[tuple[str, str]] = field(default_factory=list)  # (file_a, file_b)


class DeadCodeDetector:
    """Dead code detector"""

    def __init__(
        self,
        project_root: Path,
        extensions: list[str] = None,
        ignore_patterns: list[str] = None,
        entry_points: list[str] = None,
    ):
        self.project_root = project_root
        self.extensions = extensions or [".py", ".ts", ".tsx", ".js", ".jsx", ".vue"]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".pytest_cache", ".nuxt", ".output",
            "test", "tests", "spec", "__tests__",
        ]
        # Entry points (these files are not considered orphans)
        self.entry_points = entry_points or [
            "main", "index", "app", "__main__", "cli", "server",
            "setup", "conftest", "manage", "wsgi", "asgi",
            "run", "start", "config", "settings",
        ]
        # Special directories (these are not considered orphans)
        self.special_dirs = [
            "scripts", "examples", "cli", "commands", "bin",
            "migrations", "fixtures", "seeds", "functions",
        ]

        # Reference graph
        self.imports: dict[str, set[str]] = defaultdict(set)  # file -> set of imported files
        self.imported_by: dict[str, set[str]] = defaultdict(set)  # file -> set of files that import it
        self.exports: dict[str, set[str]] = defaultdict(set)  # file -> set of exported names
        self.used_exports: dict[str, set[str]] = defaultdict(set)  # file -> set of used export names

    def _should_skip(self, path: str) -> bool:
        return any(pattern in path for pattern in self.ignore_patterns)

    def _is_entry_point(self, path: str) -> bool:
        """Check if file is an entry point (should not be flagged as orphan)"""
        p = Path(path)
        stem = p.stem.lower()

        # __init__.py is not an orphan
        if stem == "__init__":
            return True

        # Entry point names
        if any(ep in stem for ep in self.entry_points):
            return True

        # Files under special directories are not orphans
        parts = p.parts
        for special in self.special_dirs:
            if special in parts:
                return True

        # API routes are not orphans (frameworks auto-load)
        if "api" in parts or "routes" in parts or "routers" in parts:
            return True

        # Pages/views are not orphans (frameworks auto-load)
        if "pages" in parts or "views" in parts:
            return True

        # Components are not orphans (may be lazy loaded)
        if "components" in parts:
            return True

        # Composables/hooks are not orphans (may be used dynamically)
        return bool("composables" in parts or "hooks" in parts)

    def scan_directory(self) -> list[str]:
        """Scan directory, return all files"""
        files = []
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if not self._should_skip(rel_path):
                    files.append(rel_path)
        return files

    def analyze_file(self, rel_path: str):
        """Analyze a single file's imports/exports"""
        full_path = self.project_root / rel_path
        if not full_path.exists():
            return

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:
            return

        ext = Path(rel_path).suffix

        if ext == ".py":
            self._analyze_python(rel_path, content)
        elif ext in [".ts", ".tsx", ".js", ".jsx"]:
            self._analyze_typescript(rel_path, content)
        elif ext == ".vue":
            self._analyze_vue(rel_path, content)

    def _analyze_python(self, rel_path: str, content: str):
        """Analyze Python file"""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return

        dir_path = str(Path(rel_path).parent)

        for node in ast.walk(tree):
            # import xxx
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    self._add_import(rel_path, module, dir_path)

            # from xxx import yyy
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split(".")[0]
                    self._add_import(rel_path, module, dir_path)

            # def xxx / class xxx (exports)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                    self.exports[rel_path].add(node.name)

    def _analyze_typescript(self, rel_path: str, content: str):
        """Analyze TypeScript/JavaScript file"""
        dir_path = str(Path(rel_path).parent)

        # import patterns
        import_patterns = [
            r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]',  # import x from 'y'
            r'import\s*\([\'"]([^\'"]+)[\'"]\)',  # import('y')
            r'require\s*\([\'"]([^\'"]+)[\'"]\)',  # require('y')
            r'import\s+[\'"]([^\'"]+)[\'"]',  # import 'y'
        ]

        for pattern in import_patterns:
            for match in re.finditer(pattern, content):
                module = match.group(1)
                self._add_import(rel_path, module, dir_path)

        # export patterns
        export_patterns = [
            r'export\s+(?:const|let|var|function|class|async\s+function)\s+(\w+)',
            r'export\s+default\s+(?:function|class)?\s*(\w+)?',
            r'export\s*\{\s*([^}]+)\s*\}',
        ]

        for pattern in export_patterns:
            for match in re.finditer(pattern, content):
                names = match.group(1)
                if names:
                    for name in re.split(r'[,\s]+', names):
                        name = name.strip()
                        if name and not name.startswith("_"):
                            self.exports[rel_path].add(name)

    def _analyze_vue(self, rel_path: str, content: str):
        """Analyze Vue file"""
        # Extract <script> block (string-based to avoid regex HTML parsing pitfalls)
        script_open = content.find("<script")
        script_body_start = content.find(">", script_open) + 1 if script_open != -1 else -1
        script_end = content.find("</script>", script_body_start) if script_body_start > 0 else -1
        if script_body_start > 0 and script_end != -1:
            self._analyze_typescript(rel_path, content[script_body_start:script_end])

        # Vue component itself is an export
        self.exports[rel_path].add(Path(rel_path).stem)

    def _add_import(self, from_file: str, module: str, dir_path: str):
        """Add import relationship"""
        # Resolve relative path
        if module.startswith("."):
            # Relative import
            if module.startswith("./"):
                target = str(Path(dir_path) / module[2:])
            elif module.startswith("../"):
                target = str(Path(dir_path).parent / module[3:])
            else:
                target = str(Path(dir_path) / module[1:])
        elif module.startswith("@/") or module.startswith("~/"):
            # alias import (src/xxx)
            target = f"src/{module[2:]}"
        else:
            # External module, skip
            return

        # Try different file extensions
        for ext in self.extensions:
            candidate = target + ext
            if (self.project_root / candidate).exists():
                self.imports[from_file].add(candidate)
                self.imported_by[candidate].add(from_file)
                return

            # Try index file
            index_candidate = f"{target}/index{ext}"
            if (self.project_root / index_candidate).exists():
                self.imports[from_file].add(index_candidate)
                self.imported_by[index_candidate].add(from_file)
                return

    def analyze(self) -> DeadCodeReport:
        """Run full analysis"""
        report = DeadCodeReport()

        # Scan all files
        files = self.scan_directory()
        report.total_files = len(files)

        # Analyze each file
        for rel_path in files:
            self.analyze_file(rel_path)

        # Find orphan files (not referenced by any file)
        for rel_path in files:
            ref_count = len(self.imported_by.get(rel_path, set()))

            if ref_count == 0:
                # Check if it is an entry point
                if not self._is_entry_point(rel_path):
                    report.orphan_files.append(rel_path)
            elif ref_count <= 1:
                # Low-reference file
                report.low_reference_files.append((rel_path, ref_count))

        # Find circular dependencies
        for file_a in self.imports:
            for file_b in self.imports[file_a]:
                if file_a in self.imports.get(file_b, set()) and (file_b, file_a) not in report.circular_deps:
                        report.circular_deps.append((file_a, file_b))

        # Sort
        report.orphan_files.sort()
        report.low_reference_files.sort(key=lambda x: x[1])

        return report

    def print_report(self, report: DeadCodeReport):
        """Print report"""
        print(f"\n{'=' * 60}")
        print("Dead Code Analysis Report")
        print(f"{'=' * 60}")
        print(f"\nTotal files scanned: {report.total_files}")

        print(f"\n{'=' * 60}")
        print(f"ORPHAN FILES (never imported): {len(report.orphan_files)}")
        print(f"{'=' * 60}")
        if report.orphan_files:
            for f in report.orphan_files[:30]:
                print(f"  ❌ {f}")
            if len(report.orphan_files) > 30:
                print(f"  ... and {len(report.orphan_files) - 30} more")
        else:
            print("  ✅ No orphan files found")

        print(f"\n{'=' * 60}")
        print(f"LOW REFERENCE FILES (only 1 import): {len(report.low_reference_files)}")
        print(f"{'=' * 60}")
        if report.low_reference_files:
            for f, count in report.low_reference_files[:20]:
                print(f"  ⚠️ {f} ({count} references)")
        else:
            print("  ✅ All files have multiple references")

        print(f"\n{'=' * 60}")
        print(f"CIRCULAR DEPENDENCIES: {len(report.circular_deps)}")
        print(f"{'=' * 60}")
        if report.circular_deps:
            for a, b in report.circular_deps[:10]:
                print(f"  🔄 {a}")
                print(f"     ↔ {b}")
        else:
            print("  ✅ No circular dependencies found")


def detect_dead_code(project_path: Path) -> DeadCodeReport:
    """Convenience function: detect dead code"""
    detector = DeadCodeDetector(project_path)
    return detector.analyze()
