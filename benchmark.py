#!/usr/bin/env python3
"""
Benchmark flyto-indexer against CPython stdlib (Lib/).

Usage:
    python benchmark.py                           # auto-clone CPython
    python benchmark.py --repo /path/to/cpython   # use local copy
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).parent / "src"))

from engine import IndexEngine

CPYTHON_URL = "https://github.com/python/cpython.git"
CPYTHON_SHALLOW_ARGS = ["--depth", "1", "--filter=blob:none", "--sparse"]

# Known symbols that must exist after scanning CPython Lib/
KNOWN_SYMBOLS = [
    "json.dumps",
    "json.loads",
    "JSONDecoder",
    "JSONEncoder",
    "PathFinder",
    "ABCMeta",
    "HTTPServer",
    "ThreadPoolExecutor",
    "deque",
    "namedtuple",
]


def clone_cpython(target_dir: Path) -> Path:
    """Shallow-clone CPython into target_dir, sparse-checkout Lib/ only."""
    print(f"Cloning CPython (shallow + sparse) into {target_dir} ...")
    subprocess.run(
        ["git", "clone", *CPYTHON_SHALLOW_ARGS, CPYTHON_URL, str(target_dir)],
        check=True,
        capture_output=True,
    )
    # Sparse checkout: only Lib/
    subprocess.run(
        ["git", "sparse-checkout", "set", "Lib"],
        cwd=str(target_dir),
        check=True,
        capture_output=True,
    )
    print("Clone complete.")
    return target_dir


def run_benchmark(repo_path: Path) -> dict:
    """Run the full benchmark and return results."""
    lib_path = repo_path / "Lib"
    if not lib_path.is_dir():
        print(f"Error: {lib_path} not found. Is this a CPython repo?")
        sys.exit(1)

    # Count .py files
    py_files = list(lib_path.rglob("*.py"))
    print(f"Found {len(py_files)} Python files in {lib_path}")

    # Use a temp dir for index output
    with tempfile.TemporaryDirectory(prefix="flyto-bench-idx-") as idx_dir:
        engine = IndexEngine("cpython", lib_path, index_dir=Path(idx_dir))

        # Start memory tracking
        tracemalloc.start()

        t_start = time.monotonic()
        result = engine.scan(incremental=False)
        t_end = time.monotonic()

        # Peak memory
        _, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    wall_time = round(t_end - t_start, 3)

    # Validate known symbols
    all_symbol_names = set()
    for sid in engine.index.symbols:
        sym = engine.index.symbols[sid]
        all_symbol_names.add(sym.name)

    found = []
    missing = []
    for name in KNOWN_SYMBOLS:
        if name in all_symbol_names:
            found.append(name)
        else:
            missing.append(name)

    benchmark_result = {
        "repo": str(repo_path),
        "scan_path": str(lib_path),
        "python_files": len(py_files),
        "files_scanned": result["files_scanned"],
        "symbols_found": result["symbols_found"],
        "dependencies_found": result["dependencies_found"],
        "errors": result["errors"],
        "wall_time_s": wall_time,
        "timing": result.get("timing", {}),
        "peak_memory_mb": round(peak_mem / 1024 / 1024, 1),
        "known_symbols": {
            "checked": len(KNOWN_SYMBOLS),
            "found": len(found),
            "missing": missing,
        },
    }

    return benchmark_result


def print_table(result: dict):
    """Pretty-print benchmark results as a table."""
    print("\n" + "=" * 60)
    print("  flyto-indexer Benchmark — CPython stdlib")
    print("=" * 60)

    rows = [
        ("Python files", str(result["python_files"])),
        ("Files scanned", str(result["files_scanned"])),
        ("Symbols found", str(result["symbols_found"])),
        ("Dependencies found", str(result["dependencies_found"])),
        ("Errors", str(result["errors"])),
        ("", ""),
        ("Wall time", f"{result['wall_time_s']:.3f}s"),
    ]

    timing = result.get("timing", {})
    if timing:
        rows.append(("  scan_files", f"{timing.get('scan_files', 0):.3f}s"))
        rows.append(("  resolve_deps", f"{timing.get('resolve_deps', 0):.3f}s"))
        rows.append(("  build_reverse", f"{timing.get('build_reverse', 0):.3f}s"))
        rows.append(("  save_index", f"{timing.get('save_index', 0):.3f}s"))

    rows.append(("", ""))
    rows.append(("Peak memory", f"{result['peak_memory_mb']:.1f} MB"))

    ks = result["known_symbols"]
    rows.append(("Known symbols", f"{ks['found']}/{ks['checked']} found"))
    if ks["missing"]:
        rows.append(("  missing", ", ".join(ks["missing"])))

    for label, value in rows:
        if not label and not value:
            print("-" * 60)
        else:
            print(f"  {label:<25} {value}")

    print("=" * 60)

    # Performance rating
    files = result["files_scanned"]
    wall = result["wall_time_s"]
    if wall > 0 and files > 0:
        fps = files / wall
        print(f"  Throughput: {fps:.0f} files/sec")
    print()


def main():
    parser = argparse.ArgumentParser(description="Benchmark flyto-indexer on CPython stdlib")
    parser.add_argument("--repo", type=str, help="Path to local CPython repo (must have Lib/)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of table")
    args = parser.parse_args()

    repo_path = None
    tmp_dir = None

    try:
        if args.repo:
            repo_path = Path(args.repo)
            if not repo_path.exists():
                print(f"Error: {repo_path} does not exist")
                sys.exit(1)
        else:
            # Auto-clone to temp dir
            tmp_dir = tempfile.mkdtemp(prefix="flyto-bench-")
            repo_path = clone_cpython(Path(tmp_dir) / "cpython")

        result = run_benchmark(repo_path)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_table(result)

    finally:
        if tmp_dir:
            print(f"Cleaning up {tmp_dir} ...")
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
