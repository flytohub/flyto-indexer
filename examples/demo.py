#!/usr/bin/env python3
"""
Demo script for flyto-indexer.

使用方式：
    python demo.py /path/to/your/project

這會：
1. 掃描專案，建立索引
2. 輸出 L0 大綱
3. 示範影響分析
"""

import sys
import os
from pathlib import Path

# 設定路徑，讓相對 import 可以工作
project_root = Path(__file__).parent.parent
src_path = project_root / "src"

# 建立一個臨時的 package 結構
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# 修改模組搜尋路徑
os.chdir(src_path)

# 現在可以 import 了
from models import ProjectIndex, Symbol, Dependency, FileManifest, SymbolType, DependencyType
from scanner.python import PythonScanner
from scanner.vue import VueScanner
from scanner.base import ScanResult
from indexer.incremental import IncrementalIndexer, scan_directory_hashes, compute_file_hash
from context.loader import ContextLoader


class SimpleIndexEngine:
    """簡化版 IndexEngine（用於 demo）"""

    def __init__(self, project_name: str, project_root: Path, index_dir: Path = None):
        self.project_name = project_name
        self.project_root = Path(project_root)
        self.index_dir = index_dir or (self.project_root / ".flyto-index")

        self.scanners = [
            PythonScanner(project_name),
            VueScanner(project_name),
        ]
        self.incremental = IncrementalIndexer(self.project_root, self.index_dir)
        self.index = ProjectIndex(project=project_name, root_path=str(self.project_root))

    def scan(self, incremental: bool = True) -> dict:
        """掃描專案"""
        extensions = []
        for scanner in self.scanners:
            extensions.extend(scanner.supported_extensions)

        current_hashes = scan_directory_hashes(
            self.project_root,
            extensions,
            ignore_patterns=[
                "node_modules", "__pycache__", ".git", "dist", "build",
                ".venv", "venv", ".pytest_cache", ".flyto-index"
            ]
        )

        if incremental:
            changes = self.incremental.detect_changes(current_hashes)
            files_to_scan = changes.all_changed()
        else:
            changes = None
            files_to_scan = list(current_hashes.keys())

        result = ScanResult()
        for rel_path in files_to_scan:
            file_path = self.project_root / rel_path
            if not file_path.exists():
                continue

            scanner = self._get_scanner(file_path)
            if not scanner:
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                symbols, deps = scanner.scan_file(Path(rel_path), content)
                manifest = scanner.create_file_manifest(Path(rel_path), content, symbols)
                result.add_file_result(symbols, deps, manifest)
            except Exception as e:
                result.add_error(rel_path, str(e))

        # 更新索引
        for symbol in result.symbols:
            self.index.symbols[symbol.id] = symbol
        for dep in result.dependencies:
            self.index.dependencies[dep.id] = dep
        for manifest in result.manifests:
            self.index.files[manifest.path] = manifest

        return {
            "project": self.project_name,
            "files_scanned": len(files_to_scan),
            "symbols_found": len(result.symbols),
            "dependencies_found": len(result.dependencies),
            "errors": len(result.errors),
            "changes": changes.summary() if changes else "full rebuild",
        }

    def outline(self) -> str:
        """生成 L0 大綱"""
        loader = ContextLoader(self.index)
        l0 = loader.load_l0()
        return l0.to_text()

    def impact(self, symbol_id: str, max_depth: int = 3) -> dict:
        """查詢影響範圍"""
        full_id = self._resolve_symbol_id(symbol_id)
        if not full_id:
            return {"error": f"Symbol not found: {symbol_id}"}

        chain = self.index.get_impact_chain(full_id, max_depth)
        result = {
            "symbol": full_id,
            "impact_chain": [],
        }

        for level in chain["levels"]:
            level_info = {"depth": level["depth"], "affected": []}
            for sid in level["symbols"]:
                if sid in self.index.symbols:
                    s = self.index.symbols[sid]
                    level_info["affected"].append({
                        "id": sid,
                        "path": s.path,
                        "type": s.symbol_type.value,
                        "name": s.name,
                    })
            result["impact_chain"].append(level_info)

        return result

    def _get_scanner(self, file_path: Path):
        for scanner in self.scanners:
            if scanner.can_scan(file_path):
                return scanner
        return None

    def _resolve_symbol_id(self, symbol_id: str):
        if symbol_id in self.index.symbols:
            return symbol_id
        full_id = f"{self.project_name}:{symbol_id}"
        if full_id in self.index.symbols:
            return full_id
        for sid in self.index.symbols:
            if sid.endswith(symbol_id):
                return sid
        return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python demo.py <project_path>")
        print("Example: python demo.py /Library/其他專案/flytohub/flyto-core")
        return

    project_path = Path(sys.argv[1]).resolve()
    if not project_path.exists():
        print(f"Error: Path does not exist: {project_path}")
        return

    project_name = project_path.name
    print(f"\n{'='*60}")
    print(f"Flyto Indexer Demo")
    print(f"Project: {project_name}")
    print(f"Path: {project_path}")
    print(f"{'='*60}\n")

    # 1. 建立引擎
    print("[1/4] Initializing engine...")
    engine = SimpleIndexEngine(project_name, project_path)

    # 2. 掃描專案
    print("[2/4] Scanning project...")
    result = engine.scan(incremental=False)  # 第一次用 full scan
    print(f"  - Files scanned: {result['files_scanned']}")
    print(f"  - Symbols found: {result['symbols_found']}")
    print(f"  - Dependencies: {result['dependencies_found']}")
    print(f"  - Changes: {result['changes']}")

    # 3. 輸出 L0 大綱
    print("\n[3/4] Generating L0 outline...")
    outline = engine.outline()
    print("\n" + "="*60)
    print("L0 OUTLINE (first 2000 chars):")
    print("="*60)
    print(outline[:2000])
    if len(outline) > 2000:
        print(f"\n... (truncated, total {len(outline)} chars)")

    # 4. 示範影響分析
    print("\n[4/4] Impact analysis demo...")
    if engine.index.symbols:
        sample_symbol = None
        for sid, symbol in engine.index.symbols.items():
            if symbol.symbol_type.value in ("function", "method", "component"):
                sample_symbol = sid
                break

        if sample_symbol:
            print(f"\nAnalyzing impact of: {sample_symbol}")
            impact = engine.impact(sample_symbol, max_depth=2)
            print(f"  Symbol: {impact.get('symbol')}")
            chain = impact.get('impact_chain', [])
            if chain:
                for level in chain:
                    print(f"  Level {level['depth']}:")
                    for affected in level['affected'][:5]:
                        print(f"    - {affected['path']}:{affected['name']}")
            else:
                print("  No impact found (no callers)")
        else:
            print("  No suitable symbol found for demo")
    else:
        print("  No symbols indexed")

    print(f"\n{'='*60}")
    print(f"Demo completed!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
