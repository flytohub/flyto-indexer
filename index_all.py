#!/usr/bin/env python3
"""
索引 flytohub 下所有專案

用法：
    python index_all.py
"""

import json
import sys
from pathlib import Path

# 添加 src 到 path
sys.path.insert(0, str(Path(__file__).parent))

from src.engine import IndexEngine
from src.mapper.project_map import ProjectMapGenerator

FLYTOHUB_ROOT = Path("/Library/其他專案/flytohub")
OUTPUT_DIR = Path(__file__).parent / ".flyto-index"

# 要索引的專案
PROJECTS = [
    "flyto-core",
    "flyto-pro",
    "flyto-cloud",
    "flyto-cloud-dev",
    "flyto-indexer",
    "flyto-i18n",
    "flyto-landing-page",
    "flyto-modules-pro",
    "flyto-evolution-log",
    "templates",
]


def index_project(project_name: str) -> dict:
    """索引單個專案"""
    project_path = FLYTOHUB_ROOT / project_name
    if not project_path.exists():
        print(f"  ⚠️  {project_name} not found, skipping")
        return None

    print(f"  📁 Scanning {project_name}...")

    try:
        engine = IndexEngine(project_name, project_path, OUTPUT_DIR / project_name)
        result = engine.scan(incremental=False)

        print(f"     Files: {result['files_scanned']}, Symbols: {result['symbols_found']}, Deps: {result['dependencies_found']}")

        # 返回索引數據
        return {
            "project": project_name,
            "root_path": str(project_path),
            "files": {k: v.to_dict() for k, v in engine.index.files.items()},
            "symbols": {k: v.to_dict() for k, v in engine.index.symbols.items()},
            "dependencies": {k: v.to_dict() for k, v in engine.index.dependencies.items()},
            "stats": result,
        }
    except Exception as e:
        print(f"     ❌ Error: {e}")
        return None


def generate_project_map(project_name: str) -> dict:
    """生成專案的 PROJECT_MAP"""
    project_path = FLYTOHUB_ROOT / project_name
    if not project_path.exists():
        return None

    try:
        generator = ProjectMapGenerator(project_path)
        return generator.generate()
    except Exception as e:
        print(f"     ❌ Map error: {e}")
        return None


def main():
    print("=" * 60)
    print("Indexing all flytohub projects")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 合併索引
    combined_index = {
        "projects": [],
        "files": {},
        "symbols": {},
        "dependencies": {},
    }

    # 合併 PROJECT_MAP
    combined_map = {
        "projects": [],
        "total_files": 0,
        "files": {},
        "categories": {},
        "keyword_index": {},
        "api_map": {},
    }

    total_files = 0
    total_symbols = 0
    total_deps = 0

    for project_name in PROJECTS:
        print(f"\n[{PROJECTS.index(project_name) + 1}/{len(PROJECTS)}] {project_name}")

        # 索引
        index_data = index_project(project_name)
        if index_data:
            combined_index["projects"].append(project_name)

            # 合併 files（加上 project 前綴）
            for path, fdata in index_data["files"].items():
                full_path = f"{project_name}/{path}"
                combined_index["files"][full_path] = fdata

            # 合併 symbols
            for sid, sdata in index_data["symbols"].items():
                combined_index["symbols"][sid] = sdata

            # 合併 dependencies
            for did, ddata in index_data["dependencies"].items():
                combined_index["dependencies"][did] = ddata

            total_files += index_data["stats"]["files_scanned"]
            total_symbols += index_data["stats"]["symbols_found"]
            total_deps += index_data["stats"]["dependencies_found"]

        # PROJECT_MAP
        map_data = generate_project_map(project_name)
        if map_data:
            combined_map["projects"].append(project_name)
            combined_map["total_files"] += map_data.get("total_files", 0)

            # 合併 files
            for path, finfo in map_data.get("files", {}).items():
                full_path = f"{project_name}/{path}"
                combined_map["files"][full_path] = finfo

            # 合併 categories
            for cat, paths in map_data.get("categories", {}).items():
                if cat not in combined_map["categories"]:
                    combined_map["categories"][cat] = []
                combined_map["categories"][cat].extend([f"{project_name}/{p}" for p in paths])

    # 保存合併索引
    print("\n" + "=" * 60)
    print("Saving combined index...")

    index_file = OUTPUT_DIR / "index.json"
    index_file.write_text(json.dumps(combined_index, indent=2, ensure_ascii=False))
    print(f"  ✅ index.json ({index_file.stat().st_size // 1024} KB)")

    map_file = OUTPUT_DIR / "PROJECT_MAP.json"
    map_file.write_text(json.dumps(combined_map, indent=2, ensure_ascii=False))
    print(f"  ✅ PROJECT_MAP.json ({map_file.stat().st_size // 1024} KB)")

    # 總結
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Projects indexed: {len(combined_index['projects'])}")
    print(f"  Total files: {total_files}")
    print(f"  Total symbols: {total_symbols}")
    print(f"  Total dependencies: {total_deps}")
    print(f"\n  Index saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
