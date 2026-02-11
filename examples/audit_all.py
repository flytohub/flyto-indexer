#!/usr/bin/env python3
"""
Full project LLM audit.

Perform LLM audit on all flyto projects and generate PROJECT_MAP.
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime

# Load environment variables
def load_dotenv():
    env_files = [
        Path(__file__).parent.parent / ".env",
        Path("/path/to/your/projects/flyto-pro/.env"),
    ]
    for env_file in env_files:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
            break

load_dotenv()

# Set up paths
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))
os.chdir(src_path)

from auditor.llm_auditor import LLMAuditor

# Flyto projects root directory
FLYTOHUB_ROOT = Path("/path/to/your/projects")

# Projects to audit
PROJECTS = [
    "flyto-core",
    "flyto-pro",
    "flyto-cloud",
    "flyto-cloud-dev",
    "flyto-modules-pro",
]

# Paths to ignore
IGNORE_PATTERNS = [
    "node_modules", "__pycache__", ".git", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index",
    ".nuxt", ".output", "coverage", "test", "tests",
    "__init__.py", "conftest.py"
]

# Supported file extensions
EXTENSIONS = [".py", ".vue", ".ts", ".tsx"]


def should_skip(path: str) -> bool:
    """Check whether the file should be skipped"""
    for pattern in IGNORE_PATTERNS:
        if pattern in path:
            return True
    return False


def collect_files(project_path: Path) -> list[tuple[str, str, str]]:
    """Collect files from the project"""
    files = []

    for ext in EXTENSIONS:
        for file_path in project_path.rglob(f"*{ext}"):
            rel_path = str(file_path.relative_to(project_path))

            if should_skip(rel_path):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                # Skip files that are too short
                if len(content) < 50:
                    continue

                # Infer language
                lang_map = {".py": "python", ".vue": "vue", ".ts": "typescript", ".tsx": "typescript"}
                language = lang_map.get(ext, "unknown")

                files.append((rel_path, content, language))
            except Exception:
                continue

    return files


def audit_project(project_name: str, auditor: LLMAuditor) -> dict:
    """Audit a single project"""
    project_path = FLYTOHUB_ROOT / project_name

    if not project_path.exists():
        return {"error": f"Project not found: {project_name}"}

    print(f"\n{'='*60}")
    print(f"Auditing: {project_name}")
    print(f"{'='*60}")

    # Collect files
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

    # Audit each file
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

            # Build index
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
    print("Flyto Indexer - Full Project LLM Audit")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    # Check API key
    if not os.getenv("OPENAI_API_KEY"):
        print("\n❌ OPENAI_API_KEY not set")
        return

    # Create auditor
    auditor = LLMAuditor(provider="openai", model="gpt-4o-mini")
    print(f"\nUsing: OpenAI gpt-4o-mini")

    # Audit all projects
    all_results = {}
    total_files = 0

    for project_name in PROJECTS:
        result = audit_project(project_name, auditor)
        all_results[project_name] = result
        total_files += len(result.get("files", {}))

    # Merge results
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
        # Merge files (with project prefix)
        for path, audit in result.get("files", {}).items():
            full_path = f"{project_name}/{path}"
            audit["project"] = project_name
            merged["files"][full_path] = audit

        # Merge categories
        for cat, paths in result.get("categories", {}).items():
            if cat not in merged["categories"]:
                merged["categories"][cat] = []
            merged["categories"][cat].extend([f"{project_name}/{p}" for p in paths])

        # Merge api_map
        for api, paths in result.get("api_map", {}).items():
            if api not in merged["api_map"]:
                merged["api_map"][api] = []
            merged["api_map"][api].extend([f"{project_name}/{p}" for p in paths])

        # Merge keyword_index
        for kw, paths in result.get("keyword_index", {}).items():
            if kw not in merged["keyword_index"]:
                merged["keyword_index"][kw] = []
            merged["keyword_index"][kw].extend([f"{project_name}/{p}" for p in paths])

    # Save results
    output_dir = FLYTOHUB_ROOT / "flyto-indexer" / ".flyto-index"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save merged results
    merged_path = output_dir / "PROJECT_MAP.json"
    merged_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    print(f"\n✅ Saved: {merged_path}")

    # Save per-project results
    for project_name, result in all_results.items():
        project_map_path = output_dir / f"PROJECT_MAP_{project_name}.json"
        project_map_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    # Statistics
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
