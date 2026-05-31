"""
Import Graph Health metrics — compute coupling, cohesion, and stability
metrics from the existing dependency graph.

Metrics:
  - Fan-in (Ca): how many modules depend on this module (afferent coupling)
  - Fan-out (Ce): how many modules this module depends on (efferent coupling)
  - Instability: Ce / (Ca + Ce) — 0=stable, 1=unstable
  - God module: fan-in > threshold (too many dependents)
  - Coupling score: overall project coupling density
  - Circular dependency detection (from existing dead_code analysis)

Pure Python stdlib, no external dependencies.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    "test", "tests", "__tests__",
})

_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java", ".vue",
})


@dataclass
class ModuleMetrics:
    """Metrics for a single module (file)."""
    path: str
    fan_in: int = 0        # how many files import this
    fan_out: int = 0       # how many files this imports
    instability: float = 0.0   # Ce / (Ca + Ce)
    is_god_module: bool = False
    importers: list[str] = field(default_factory=list)   # who imports this
    imports: list[str] = field(default_factory=list)      # what this imports


@dataclass
class ImportHealthReport:
    """Import graph health analysis result."""
    total_modules: int = 0
    total_edges: int = 0
    coupling_density: float = 0.0      # edges / (modules^2)
    avg_fan_in: float = 0.0
    avg_fan_out: float = 0.0
    avg_instability: float = 0.0
    god_modules: list[ModuleMetrics] = field(default_factory=list)   # fan_in > threshold
    unstable_modules: list[ModuleMetrics] = field(default_factory=list)  # instability > 0.8
    stable_modules: list[ModuleMetrics] = field(default_factory=list)    # instability < 0.2
    circular_deps: list[tuple[str, str]] = field(default_factory=list)
    all_modules: list[ModuleMetrics] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_modules": self.total_modules,
            "total_edges": self.total_edges,
            "coupling_density": round(self.coupling_density, 4),
            "avg_fan_in": round(self.avg_fan_in, 1),
            "avg_fan_out": round(self.avg_fan_out, 1),
            "avg_instability": round(self.avg_instability, 2),
            "god_module_count": len(self.god_modules),
            "god_modules": [{"path": m.path, "fan_in": m.fan_in} for m in self.god_modules[:10]],
            "unstable_count": len(self.unstable_modules),
            "circular_dep_count": len(self.circular_deps),
        }


def _extract_imports_from_file(filepath: Path, project_root: Path) -> list[str]:
    """Extract imported module paths from a source file."""
    imports = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    suffix = filepath.suffix

    if suffix == ".py":
        # Python imports
        for match in re.finditer(
            r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
            content, re.MULTILINE,
        ):
            mod = match.group(1) or match.group(2)
            if mod and not mod.startswith(("os", "sys", "re", "json", "typing",
                                           "pathlib", "collections", "dataclasses",
                                           "abc", "enum", "functools", "logging",
                                           "datetime", "math", "hashlib", "io",
                                           "subprocess", "threading", "time",
                                           "tempfile", "shutil", "ast", "copy",
                                           "inspect", "unittest", "textwrap",
                                           "argparse", "contextlib", "itertools")):
                imports.append(mod.replace(".", "/"))

    elif suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue"):
        # JS/TS imports
        for match in re.finditer(
            r"""(?:import\s+.*?\s+from\s+|require\s*\(\s*)["']([./][^"']+)["']""",
            content,
        ):
            path = match.group(1)
            if path.startswith("./") or path.startswith("../"):
                imports.append(path)

    elif suffix == ".go":
        # Go imports
        for match in re.finditer(r'"([^"]+)"', content[:5000]):
            imp = match.group(1)
            if "/" in imp and not imp.startswith(("fmt", "os", "io", "net", "log",
                                                   "sync", "time", "path", "math",
                                                   "sort", "strings", "strconv",
                                                   "context", "errors", "encoding",
                                                   "crypto", "testing", "reflect")):
                imports.append(imp)

    return imports


def _resolve_import(imp: str, source_file: Path, project_root: Path, all_files: set[str]) -> str | None:
    """Try to resolve an import string to an actual file path."""
    # For relative imports
    if imp.startswith("./") or imp.startswith("../"):
        base = source_file.parent
        resolved = (base / imp).resolve()
        for ext in ("", ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", "/index.ts", "/index.js"):
            candidate = str(resolved) + ext
            try:
                rel = str(Path(candidate).relative_to(project_root)).replace("\\", "/")
            except (ValueError, OSError):
                continue
            if rel in all_files:
                return rel

    # For Python dotted imports — try as path
    py_path = imp.replace("/", "/") + ".py"
    if py_path in all_files:
        return py_path

    init_path = imp + "/__init__.py"
    if init_path in all_files:
        return init_path

    return None


def analyze_import_health(
    project_root: str | Path,
    index: dict | None = None,
    god_module_threshold: int = 15,
) -> ImportHealthReport:
    """Analyze import graph health metrics.

    Can use pre-built index (dependencies/reverse_index) or scan from scratch.
    """
    project_root = Path(project_root)
    report = ImportHealthReport()

    # Strategy 1: Use index if available (much faster)
    if index and index.get("dependencies"):
        return _analyze_from_index(index, god_module_threshold)

    # Strategy 2: Build import graph from scratch
    all_files: set[str] = set()
    file_paths: list[Path] = []

    for fpath in sorted(project_root.rglob("*")):
        if not fpath.is_file():
            continue
        if any(skip in fpath.parts for skip in _SKIP_DIRS):
            continue
        if fpath.suffix not in _CODE_EXTENSIONS:
            continue
        rel = str(fpath.relative_to(project_root)).replace("\\", "/")
        all_files.add(rel)
        file_paths.append(fpath)

    # Build adjacency: file -> [files it imports]
    imports_map: dict[str, set[str]] = defaultdict(set)
    imported_by: dict[str, set[str]] = defaultdict(set)

    for fpath in file_paths:
        rel = str(fpath.relative_to(project_root)).replace("\\", "/")
        raw_imports = _extract_imports_from_file(fpath, project_root)

        for imp in raw_imports:
            resolved = _resolve_import(imp, fpath, project_root, all_files)
            if resolved and resolved != rel:
                imports_map[rel].add(resolved)
                imported_by[resolved].add(rel)

    return _build_report(all_files, imports_map, imported_by, god_module_threshold)


def _analyze_from_index(index: dict, threshold: int) -> ImportHealthReport:
    """Build report from pre-built index data."""
    deps = index.get("dependencies", {})
    symbols = index.get("symbols", {})

    # Build file-level import graph from symbol dependencies
    file_imports: dict[str, set[str]] = defaultdict(set)
    file_imported_by: dict[str, set[str]] = defaultdict(set)
    all_files: set[str] = set()

    for sym_id, sym in symbols.items():
        path = sym.get("path", "")
        if path:
            all_files.add(path)

    for dep_id, dep in deps.items():
        if dep.get("type") not in ("imports", "calls", "uses", "extends", "implements"):
            continue
        source_id = dep.get("source", "")
        target_id = dep.get("target", "")
        source_sym = symbols.get(source_id, {})
        target_sym = symbols.get(target_id, {})
        source_file = source_sym.get("path", "")
        target_file = target_sym.get("path", "")

        if source_file and target_file and source_file != target_file:
            file_imports[source_file].add(target_file)
            file_imported_by[target_file].add(source_file)

    return _build_report(all_files, file_imports, file_imported_by, threshold)


def _build_report(
    all_files: set[str],
    imports_map: dict[str, set[str]],
    imported_by: dict[str, set[str]],
    god_threshold: int,
) -> ImportHealthReport:
    """Build ImportHealthReport from import graph data."""
    report = ImportHealthReport()
    report.total_modules = len(all_files)

    total_edges = sum(len(v) for v in imports_map.values())
    report.total_edges = total_edges

    if report.total_modules > 1:
        report.coupling_density = total_edges / (report.total_modules ** 2)

    modules: list[ModuleMetrics] = []
    total_fan_in = 0
    total_fan_out = 0
    total_instability = 0.0

    for f in sorted(all_files):
        fan_in = len(imported_by.get(f, set()))
        fan_out = len(imports_map.get(f, set()))
        total = fan_in + fan_out
        instability = (fan_out / total) if total > 0 else 0.5

        m = ModuleMetrics(
            path=f, fan_in=fan_in, fan_out=fan_out,
            instability=round(instability, 3),
            is_god_module=fan_in >= god_threshold,
            importers=sorted(imported_by.get(f, set()))[:10],
            imports=sorted(imports_map.get(f, set()))[:10],
        )
        modules.append(m)

        total_fan_in += fan_in
        total_fan_out += fan_out
        total_instability += instability

    report.all_modules = modules

    if modules:
        report.avg_fan_in = total_fan_in / len(modules)
        report.avg_fan_out = total_fan_out / len(modules)
        report.avg_instability = total_instability / len(modules)

    report.god_modules = sorted(
        [m for m in modules if m.is_god_module],
        key=lambda m: -m.fan_in,
    )
    report.unstable_modules = sorted(
        [m for m in modules if m.instability > 0.8 and m.fan_out >= 3],
        key=lambda m: -m.instability,
    )[:20]
    report.stable_modules = sorted(
        [m for m in modules if m.instability < 0.2 and m.fan_in >= 3],
        key=lambda m: m.instability,
    )[:20]

    # Detect circular dependencies
    for f, deps in imports_map.items():
        for dep in deps:
            if f in imports_map.get(dep, set()):
                pair = tuple(sorted([f, dep]))
                if pair not in [(a, b) for a, b in report.circular_deps]:
                    report.circular_deps.append(pair)

    return report
