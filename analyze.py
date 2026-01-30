#!/usr/bin/env python3
"""
程式碼品質分析 CLI

用法：
  python analyze.py <command> /path/to/project [args]

基礎命令：
  ls          - 列出目錄內容（如: ls src/）
  read        - 讀取檔案內容（如: read src/main.py）
  grep        - 搜尋檔案內容（如: grep . "pattern"）

索引命令：
  map         - 產生 PROJECT_MAP（檔案層級）
  outline     - 產生專案大綱（簡潔版）
  symbols     - 產生 Symbol 索引（函數/類別層級）
  search      - 搜尋檔案（如: search . payment）
  find        - 搜尋函數/類別（如: find . topUp）

分析命令：
  complexity  - 複雜度分析（找出過度複雜的函數）
  coverage    - 測試覆蓋分析（找出沒有測試的模組）
  duplicates  - 重複碼偵測（找出 copy-paste 的程式碼）
  api         - API 格式一致性檢查
  security    - 安全掃描
  all         - 執行所有分析
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

# 忽略的目錄
IGNORE_DIRS = {
    'node_modules', '__pycache__', '.git', 'dist', 'build',
    '.venv', 'venv', '.pytest_cache', '.mypy_cache', '.flyto-index',
    'vendor', 'static', '.next', '.nuxt', 'coverage'
}

# 支援的副檔名
CODE_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.vue',
    '.java', '.go', '.rs', '.rb', '.php',
    '.c', '.cpp', '.h', '.hpp', '.cs',
    '.json', '.yaml', '.yml', '.toml', '.md'
}


def cmd_ls(target_path: Path):
    """列出目錄內容"""
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

    # 顯示目錄
    if dirs:
        print("Directories:")
        for d in dirs:
            count = sum(1 for _ in d.rglob('*') if _.is_file())
            print(f"  📁 {d.name}/ ({count} files)")
        print()

    # 顯示檔案
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
    """讀取檔案內容"""
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

    # 顯示行號
    width = len(str(total_lines))
    for i, line in enumerate(lines, 1):
        print(f"{i:>{width}}│ {line}")

    print(f"\n{'='*70}")
    print(f"Total: {total_lines} lines, {len(content)} characters")


def cmd_grep(project_path: Path, pattern: str = None):
    """搜尋檔案內容"""
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

    # 按檔案分組顯示
    current_file = None
    for m in matches[:100]:  # 限制顯示 100 筆
        if m['file'] != current_file:
            current_file = m['file']
            print(f"\n{current_file}:")
        print(f"  {m['line']:>4}│ {m['content']}")

    print(f"\n{'='*70}")
    print(f"Found {len(matches)} matches in {files_searched} files")
    if len(matches) > 100:
        print(f"(showing first 100)")


def analyze_complexity(project_path: Path):
    """分析複雜度"""
    analyzer = ComplexityAnalyzer(project_path)
    report = analyzer.analyze()
    analyzer.print_report(report)
    return report


def analyze_coverage(project_path: Path):
    """分析測試覆蓋"""
    analyzer = CoverageAnalyzer(project_path)
    report = analyzer.analyze()
    analyzer.print_report(report)
    return report


def analyze_duplicates(project_path: Path):
    """分析重複碼"""
    detector = DuplicateDetector(project_path, min_lines=6)
    report = detector.analyze()
    detector.print_report(report)
    return report


def analyze_api(project_path: Path):
    """分析 API 一致性"""
    checker = APIConsistencyChecker(project_path)
    report = checker.analyze()
    checker.print_report(report)
    return report


def analyze_security(project_path: Path):
    """安全掃描"""
    scanner = SecurityScanner(project_path)
    report = scanner.analyze()
    scanner.print_report(report)
    return report


def generate_map(project_path: Path):
    """產生 PROJECT_MAP"""
    generator = ProjectMapGenerator(project_path)
    project_map = generator.generate()

    # 輸出到檔案
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

    # 顯示分類統計
    print(f"\n{'='*70}")
    print("Categories:")
    print(f"{'='*70}")
    for cat, paths in sorted(project_map["categories"].items(), key=lambda x: -len(x[1])):
        print(f"  [{cat}] {len(paths)} files")

    return project_map


def generate_outline(project_path: Path):
    """產生專案大綱"""
    generator = ProjectMapGenerator(project_path)
    outline = generator.generate_outline()

    # 輸出到檔案
    output_dir = project_path / ".flyto-index"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "OUTLINE.md"
    output_file.write_text(outline)

    print(outline)
    print(f"\n---\nSaved to: {output_file}")

    return outline


def search_files(project_path: Path, query: str = None):
    """搜尋檔案"""
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
    """產生 Symbol 索引"""
    indexer = SymbolIndexer(project_path)
    index = indexer.build_index()

    # 輸出到檔案
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

    # 顯示一些統計
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
    """搜尋函數/類別"""
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
    """執行所有分析"""
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

    # 總結
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

    # 基礎命令（操作檔案/目錄）
    basic_commands = {
        "ls": cmd_ls,
        "read": cmd_read,
        "grep": cmd_grep,
    }

    # 專案命令（操作專案目錄）
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
