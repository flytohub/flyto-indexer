#!/usr/bin/env python3
"""
Code quality analysis CLI

Usage:
  python analyze.py <command> /path/to/project [args]

Basic commands:
  ls          - List directory contents (e.g. ls src/)
  read        - Read file contents (e.g. read src/main.py)
  grep        - Search file contents (e.g. grep . "pattern")

Index commands:
  map         - Generate PROJECT_MAP (file level)
  outline     - Generate project outline (concise)
  symbols     - Generate symbol index (function/class level)
  search      - Search files (e.g. search . payment)
  find        - Search functions/classes (e.g. find . topUp)

Analysis commands:
  complexity  - Complexity analysis (find overly complex functions)
  coverage    - Test coverage analysis (find untested modules)
  duplicates  - Duplicate code detection (find copy-pasted code)
  api         - API format consistency check
  security    - Security scan
  all         - Run all analyses
"""

import sys
import json
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from analyzer.complexity import ComplexityAnalyzer
from analyzer.coverage import CoverageAnalyzer
from analyzer.duplicates import DuplicateDetector
from analyzer.api_consistency import APIConsistencyChecker
from analyzer.security import SecurityScanner
from mapper.project_map import ProjectMapGenerator, quick_search
from mapper.symbol_index import SymbolIndexer, search_symbol

# Ignored directories
IGNORE_DIRS = {
    'node_modules', '__pycache__', '.git', 'dist', 'build',
    '.venv', 'venv', '.pytest_cache', '.mypy_cache', '.flyto-index',
    'vendor', 'static', '.next', '.nuxt', 'coverage'
}

# Supported file extensions
CODE_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.vue',
    '.java', '.go', '.rs', '.rb', '.php',
    '.c', '.cpp', '.h', '.hpp', '.cs',
    '.json', '.yaml', '.yml', '.toml', '.md'
}


def cmd_ls(target_path: Path):
    """List directory contents"""
    if not target_path.is_dir():
        print(f"Error: {target_path} is not a directory")
        return

    print(f"\n{'='*70}")
    print(f"Directory: {target_path}")
    print(f"{'='*70}\n")

    dirs = []
    files = []

    for item in sorted(target_path.iterdir()):
        if item.name.startswith('.') and item.name not in {'.env.example', '.gitignore'}:
            continue
        if item.is_dir():
            if item.name not in IGNORE_DIRS:
                dirs.append(item)
        else:
            files.append(item)

    # Display directories
    if dirs:
        print("Directories:")
        for d in dirs:
            count = sum(1 for _ in d.rglob('*') if _.is_file())
            print(f"  📁 {d.name}/ ({count} files)")
        print()

    # Display files
    if files:
        print("Files:")
        for f in files:
            size = f.stat().st_size
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size // 1024} KB"
            else:
                size_str = f"{size // (1024 * 1024)} MB"
            print(f"  📄 {f.name} ({size_str})")

    print(f"\nTotal: {len(dirs)} directories, {len(files)} files")


def cmd_read(file_path: Path):
    """Read file contents"""
    if not file_path.is_file():
        print(f"Error: {file_path} is not a file")
        return

    print(f"\n{'='*70}")
    print(f"File: {file_path}")
    print(f"{'='*70}\n")

    try:
        content = file_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        print("Error: Binary file, cannot display")
        return

    lines = content.split('\n')
    total_lines = len(lines)

    # Display line numbers
    width = len(str(total_lines))
    for i, line in enumerate(lines, 1):
        print(f"{i:>{width}}│ {line}")

    print(f"\n{'='*70}")
    print(f"Total: {total_lines} lines, {len(content)} characters")


def cmd_grep(project_path: Path, pattern: str = None):
    """Search file contents"""
    if not pattern:
        if len(sys.argv) > 3:
            pattern = sys.argv[3]
        else:
            print("Usage: python analyze.py grep /path/to/project <pattern>")
            print("Example: python analyze.py grep . 'def.*async'")
            print("Example: python analyze.py grep . 'TODO|FIXME'")
            return

    print(f"\n{'='*70}")
    print(f"Grep: '{pattern}' in {project_path.name}")
    print(f"{'='*70}\n")

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        print(f"Error: Invalid regex pattern - {e}")
        return

    matches = []
    files_searched = 0

    def should_skip(path: Path) -> bool:
        for part in path.parts:
            if part in IGNORE_DIRS:
                return True
        return False

    for file_path in project_path.rglob('*'):
        if not file_path.is_file():
            continue
        if should_skip(file_path):
            continue
        if file_path.suffix not in CODE_EXTENSIONS:
            continue

        files_searched += 1

        try:
            content = file_path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, PermissionError):
            continue

        for line_num, line in enumerate(content.split('\n'), 1):
            if regex.search(line):
                rel_path = file_path.relative_to(project_path)
                matches.append({
                    'file': str(rel_path),
                    'line': line_num,
                    'content': line.strip()[:100]
                })

    if not matches:
        print(f"No matches found (searched {files_searched} files)")
        return

    # Display grouped by file
    current_file = None
    for m in matches[:100]:  # Limit display to 100 matches
        if m['file'] != current_file:
            current_file = m['file']
            print(f"\n{current_file}:")
        print(f"  {m['line']:>4}│ {m['content']}")

    print(f"\n{'='*70}")
    print(f"Found {len(matches)} matches in {files_searched} files")
    if len(matches) > 100:
        print(f"(showing first 100)")


def analyze_complexity(project_path: Path):
    """Analyze complexity"""
    analyzer = ComplexityAnalyzer(project_path)
    report = analyzer.analyze()
    analyzer.print_report(report)
    return report


def analyze_coverage(project_path: Path):
    """Analyze test coverage"""
    analyzer = CoverageAnalyzer(project_path)
    report = analyzer.analyze()
    analyzer.print_report(report)
    return report


def analyze_duplicates(project_path: Path):
    """Analyze duplicate code"""
    detector = DuplicateDetector(project_path, min_lines=6)
    report = detector.analyze()
    detector.print_report(report)
    return report


def analyze_api(project_path: Path):
    """Analyze API consistency"""
    checker = APIConsistencyChecker(project_path)
    report = checker.analyze()
    checker.print_report(report)
    return report


def analyze_security(project_path: Path):
    """Security scan"""
    scanner = SecurityScanner(project_path)
    report = scanner.analyze()
    scanner.print_report(report)
    return report


def generate_map(project_path: Path):
    """Generate PROJECT_MAP"""
    generator = ProjectMapGenerator(project_path)
    project_map = generator.generate()

    # Output to file
    output_dir = project_path / ".flyto-index"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "PROJECT_MAP.json"
    output_file.write_text(json.dumps(project_map, indent=2, ensure_ascii=False))

    print(f"\n{'='*70}")
    print(f"PROJECT_MAP Generated: {project_path.name}")
    print(f"{'='*70}")
    print(f"\nTotal files: {project_map['total_files']}")
    print(f"Categories: {len(project_map['categories'])}")
    print(f"\nSaved to: {output_file}")

    # Display category statistics
    print(f"\n{'='*70}")
    print("Categories:")
    print(f"{'='*70}")
    for cat, paths in sorted(project_map["categories"].items(), key=lambda x: -len(x[1])):
        print(f"  [{cat}] {len(paths)} files")

    return project_map


def generate_outline(project_path: Path):
    """Generate project outline"""
    generator = ProjectMapGenerator(project_path)
    outline = generator.generate_outline()

    # Output to file
    output_dir = project_path / ".flyto-index"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "OUTLINE.md"
    output_file.write_text(outline)

    print(outline)
    print(f"\n---\nSaved to: {output_file}")

    return outline


def search_files(project_path: Path, query: str = None):
    """Search files"""
    if not query:
        if len(sys.argv) > 3:
            query = " ".join(sys.argv[3:])
        else:
            print("Usage: python analyze.py search /path/to/project <query>")
            print("Example: python analyze.py search . payment auth")
            return

    results = quick_search(project_path, query)

    print(f"\n{'='*70}")
    print(f"Search Files: '{query}' in {project_path.name}")
    print(f"{'='*70}")

    if not results:
        print("\nNo results found")
        return

    print(f"\nFound {len(results)} files:\n")

    for i, r in enumerate(results, 1):
        print(f"{i}. {r['path']}")
        print(f"   Purpose: {r['purpose']}")
        print(f"   Category: [{r['category']}]")
        if r['exports']:
            print(f"   Exports: {', '.join(r['exports'][:5])}")
        print()

    return results


def generate_symbols(project_path: Path):
    """Generate symbol index"""
    indexer = SymbolIndexer(project_path)
    index = indexer.build_index()

    # Output to file
    output_dir = project_path / ".flyto-index"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "SYMBOL_INDEX.json"
    output_file.write_text(json.dumps(index, indent=2, ensure_ascii=False))

    print(f"\n{'='*70}")
    print(f"Symbol Index Generated: {project_path.name}")
    print(f"{'='*70}")
    print(f"\nTotal symbols: {index['total_symbols']}")
    print(f"Classes: {len(index['classes'])}")
    print(f"Functions: {len(index['functions'])}")
    print(f"Files indexed: {len(index['by_file'])}")
    print(f"\nSaved to: {output_file}")

    # Display some statistics
    print(f"\n{'='*70}")
    print("Top Classes (by method count):")
    print(f"{'='*70}")
    sorted_classes = sorted(
        index['classes'].items(),
        key=lambda x: len(x[1].get('methods', [])),
        reverse=True
    )[:10]
    for name, info in sorted_classes:
        method_count = len(info.get('methods', []))
        print(f"  {name}: {method_count} methods ({info['file']}:{info['line']})")

    return index


def find_symbol(project_path: Path, query: str = None):
    """Search functions/classes"""
    if not query:
        if len(sys.argv) > 3:
            query = " ".join(sys.argv[3:])
        else:
            print("Usage: python analyze.py find /path/to/project <symbol_name>")
            print("Example: python analyze.py find . topUp")
            print("Example: python analyze.py find . PaymentService")
            return

    results = search_symbol(project_path, query)

    print(f"\n{'='*70}")
    print(f"Find Symbol: '{query}' in {project_path.name}")
    print(f"{'='*70}")

    if not results:
        print("\nNo symbols found")
        return

    print(f"\nFound {len(results)} symbols:\n")

    for i, r in enumerate(results, 1):
        location = f"{r['file']}:{r['line']}"
        if r['parent']:
            print(f"{i}. {r['parent']}.{r['name']} ({r['kind']})")
        else:
            print(f"{i}. {r['name']} ({r['kind']})")
        print(f"   Location: {location}")
        print()

    return results


def analyze_all(project_path: Path):
    """Run all analyses"""
    print(f"\n{'#'*70}")
    print(f"# Full Analysis: {project_path.name}")
    print(f"{'#'*70}")

    results = {}

    print("\n[1/5] Complexity Analysis...")
    results["complexity"] = analyze_complexity(project_path)

    print("\n[2/5] Test Coverage Analysis...")
    results["coverage"] = analyze_coverage(project_path)

    print("\n[3/5] Duplicate Code Analysis...")
    results["duplicates"] = analyze_duplicates(project_path)

    print("\n[4/5] API Consistency Check...")
    results["api"] = analyze_api(project_path)

    print("\n[5/5] Security Scan...")
    results["security"] = analyze_security(project_path)

    # Summary
    print(f"\n{'#'*70}")
    print("# SUMMARY")
    print(f"{'#'*70}")

    print(f"\n  Complex functions: {len(results['complexity'].complex_functions)}")
    print(f"  Test coverage: {results['coverage'].coverage_rate:.1f}%")
    print(f"  Duplicate blocks: {len(results['duplicates'].duplicate_blocks)}")
    print(f"  API issues: {len(results['api'].issues)}")
    print(f"  Security issues: {len(results['security'].issues)} (critical: {results['security'].critical_count})")

    return results


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    target_path = Path(sys.argv[2]).resolve()

    if not target_path.exists():
        print(f"Error: {target_path} not found")
        sys.exit(1)

    # Basic commands (file/directory operations)
    basic_commands = {
        "ls": cmd_ls,
        "read": cmd_read,
        "grep": cmd_grep,
    }

    # Project commands (project directory operations)
    project_commands = {
        "map": generate_map,
        "outline": generate_outline,
        "symbols": generate_symbols,
        "search": search_files,
        "find": find_symbol,
        "complexity": analyze_complexity,
        "coverage": analyze_coverage,
        "duplicates": analyze_duplicates,
        "api": analyze_api,
        "security": analyze_security,
        "all": analyze_all,
    }

    all_commands = {**basic_commands, **project_commands}

    if command not in all_commands:
        print(f"Unknown command: {command}")
        print(f"Available: {', '.join(all_commands.keys())}")
        sys.exit(1)

    all_commands[command](target_path)


if __name__ == "__main__":
    main()
