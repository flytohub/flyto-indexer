#!/usr/bin/env python3
"""
全專案 LLM 審計

對所有 flyto 專案進行 LLM 審計，生成 PROJECT_MAP。
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime

# 載入環境變數
def load_dotenv():
    env_files = [
        Path(__file__).parent.parent / ".env",
        Path("/Library/其他專案/flytohub/flyto-pro/.env"),
    ]
    for env_file in env_files:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
            break

load_dotenv()

# 設定路徑
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))
os.chdir(src_path)

from auditor.llm_auditor import LLMAuditor

# Flyto 專案根目錄
FLYTOHUB_ROOT = Path("/Library/其他專案/flytohub")

# 要審計的專案
PROJECTS = [
    "flyto-core",
    "flyto-pro",
    "flyto-cloud",
    "flyto-cloud-dev",
    "flyto-modules-pro",
]

# 忽略的路徑
IGNORE_PATTERNS = [
    "node_modules", "__pycache__", ".git", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index",
    ".nuxt", ".output", "coverage", "test", "tests",
    "__init__.py", "conftest.py"
]

# 支援的副檔名
EXTENSIONS = [".py", ".vue", ".ts", ".tsx"]


def should_skip(path: str) -> bool:
    """檢查是否應該跳過"""
    for pattern in IGNORE_PATTERNS:
        if pattern in path:
            return True
    return False


def collect_files(project_path: Path) -> list[tuple[str, str, str]]:
    """收集專案中的檔案"""
    files = []

    for ext in EXTENSIONS:
        for file_path in project_path.rglob(f"*{ext}"):
            rel_path = str(file_path.relative_to(project_path))

            if should_skip(rel_path):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                # 跳過太短的檔案
                if len(content) < 50:
                    continue

                # 推斷語言
                lang_map = {".py": "python", ".vue": "vue", ".ts": "typescript", ".tsx": "typescript"}
                language = lang_map.get(ext, "unknown")

                files.append((rel_path, content, language))
            except Exception:
                continue

    return files


def audit_project(project_name: str, auditor: LLMAuditor) -> dict:
    """審計單個專案"""
    project_path = FLYTOHUB_ROOT / project_name

    if not project_path.exists():
        return {"error": f"Project not found: {project_name}"}

    print(f"\n{'='*60}")
    print(f"Auditing: {project_name}")
    print(f"{'='*60}")

    # 收集檔案
    files = collect_files(project_path)
    print(f"Found {len(files)} files to audit")

    if not files:
        return {"files": {}, "categories": {}, "keyword_index": {}}

    result = {
        "project": project_name,
        "audited_at": datetime.now().isoformat(),
        "files": {},
        "categories": {},
        "api_map": {},
        "keyword_index": {}
    }

    # 審計每個檔案
    try:
        from tqdm import tqdm
        iterator = tqdm(files, desc=f"Auditing {project_name}")
    except ImportError:
        iterator = files

    for rel_path, content, language in iterator:
        try:
            audit = auditor.audit_file(rel_path, content, language)

            if audit.get("error"):
                print(f"\n  ⚠️ {rel_path}: {audit['error']}")
                continue

            result["files"][rel_path] = audit

            # 建立索引
            category = audit.get("category", "unknown")
            if category not in result["categories"]:
                result["categories"][category] = []
            result["categories"][category].append(rel_path)

            for api in audit.get("apis", []):
                if api and api not in result["api_map"]:
                    result["api_map"][api] = []
                if api:
                    result["api_map"][api].append(rel_path)

            for keyword in audit.get("keywords", []):
                if keyword:
                    kw_lower = keyword.lower()
                    if kw_lower not in result["keyword_index"]:
                        result["keyword_index"][kw_lower] = []
                    result["keyword_index"][kw_lower].append(rel_path)

        except Exception as e:
            print(f"\n  ❌ {rel_path}: {e}")
            continue

    print(f"\nAudited {len(result['files'])} files")
    print(f"Categories: {list(result['categories'].keys())}")
    print(f"Keywords: {len(result['keyword_index'])}")

    return result


def main():
    print("\n" + "="*60)
    print("Flyto Indexer - 全專案 LLM 審計")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    # 檢查 API key
    if not os.getenv("OPENAI_API_KEY"):
        print("\n❌ OPENAI_API_KEY not set")
        return

    # 建立審計器
    auditor = LLMAuditor(provider="openai", model="gpt-4o-mini")
    print(f"\nUsing: OpenAI gpt-4o-mini")

    # 審計所有專案
    all_results = {}
    total_files = 0

    for project_name in PROJECTS:
        result = audit_project(project_name, auditor)
        all_results[project_name] = result
        total_files += len(result.get("files", {}))

    # 合併結果
    merged = {
        "audited_at": datetime.now().isoformat(),
        "total_files": total_files,
        "projects": list(PROJECTS),
        "files": {},
        "categories": {},
        "api_map": {},
        "keyword_index": {}
    }

    for project_name, result in all_results.items():
        # 合併 files（加上專案前綴）
        for path, audit in result.get("files", {}).items():
            full_path = f"{project_name}/{path}"
            audit["project"] = project_name
            merged["files"][full_path] = audit

        # 合併 categories
        for cat, paths in result.get("categories", {}).items():
            if cat not in merged["categories"]:
                merged["categories"][cat] = []
            merged["categories"][cat].extend([f"{project_name}/{p}" for p in paths])

        # 合併 api_map
        for api, paths in result.get("api_map", {}).items():
            if api not in merged["api_map"]:
                merged["api_map"][api] = []
            merged["api_map"][api].extend([f"{project_name}/{p}" for p in paths])

        # 合併 keyword_index
        for kw, paths in result.get("keyword_index", {}).items():
            if kw not in merged["keyword_index"]:
                merged["keyword_index"][kw] = []
            merged["keyword_index"][kw].extend([f"{project_name}/{p}" for p in paths])

    # 保存結果
    output_dir = FLYTOHUB_ROOT / "flyto-indexer" / ".flyto-index"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 保存合併結果
    merged_path = output_dir / "PROJECT_MAP.json"
    merged_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    print(f"\n✅ Saved: {merged_path}")

    # 保存每個專案的結果
    for project_name, result in all_results.items():
        project_map_path = output_dir / f"PROJECT_MAP_{project_name}.json"
        project_map_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    # 統計
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total files audited: {total_files}")
    print(f"Categories: {len(merged['categories'])}")
    print(f"Keywords: {len(merged['keyword_index'])}")
    print(f"APIs: {len(merged['api_map'])}")

    print("\nTop categories:")
    sorted_cats = sorted(merged["categories"].items(), key=lambda x: len(x[1]), reverse=True)
    for cat, paths in sorted_cats[:10]:
        print(f"  {cat}: {len(paths)} files")

    print("\nTop keywords:")
    sorted_kws = sorted(merged["keyword_index"].items(), key=lambda x: len(x[1]), reverse=True)
    for kw, paths in sorted_kws[:10]:
        print(f"  {kw}: {len(paths)} files")

    print("\n" + "="*60)
    print("Done!")
    print("="*60)


if __name__ == "__main__":
    main()
