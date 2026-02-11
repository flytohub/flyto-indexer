#!/usr/bin/env python3
"""
Incremental audit - only audit files that have changed.

Usage:
    python examples/audit_incremental.py           # Incremental audit
    python examples/audit_incremental.py --full    # Force full audit
    python examples/audit_incremental.py --dry-run # Only show changes, do not audit
"""

import sys
import os
import json
import argparse
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

from auditor.incremental_audit import IncrementalAuditor
from auditor.llm_auditor import LLMAuditor

# Flyto projects
FLYTOHUB_ROOT = Path("/path/to/your/projects")
PROJECTS = [
    "flyto-core",
    "flyto-pro",
    "flyto-cloud",
    "flyto-cloud-dev",
    "flyto-modules-pro",
]

# Index directory
INDEX_DIR = FLYTOHUB_ROOT / "flyto-indexer" / ".flyto-index"


def main():
    parser = argparse.ArgumentParser(description="Incremental audit for Flyto projects")
    parser.add_argument("--full", action="store_true", help="Force full audit")
    parser.add_argument("--dry-run", action="store_true", help="Only show changes, do not audit")
    parser.add_argument("--project", type=str, help="Only audit the specified project")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("Flyto Indexer - Incremental Audit")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Check API key
    if not args.dry_run and not os.getenv("OPENAI_API_KEY"):
        print("\n❌ OPENAI_API_KEY not set")
        return

    # Create auditor
    auditor = None
    if not args.dry_run:
        auditor = LLMAuditor(provider="openai", model="gpt-4o-mini")
        print(f"\nUsing: OpenAI gpt-4o-mini")

    # Select projects
    projects = [args.project] if args.project else PROJECTS

    # Load existing PROJECT_MAP
    merged_map_path = INDEX_DIR / "PROJECT_MAP.json"
    merged_map = {}
    if merged_map_path.exists():
        merged_map = json.loads(merged_map_path.read_text())

    total_stats = {
        "added": 0,
        "modified": 0,
        "deleted": 0,
        "unchanged": 0,
        "audited": 0,
    }

    # Audit each project
    for project_name in projects:
        project_path = FLYTOHUB_ROOT / project_name
        if not project_path.exists():
            print(f"\n⚠️ Project not found: {project_name}")
            continue

        print(f"\n{'=' * 60}")
        print(f"Project: {project_name}")
        print(f"{'=' * 60}")

        # Project index directory
        project_index_dir = INDEX_DIR / project_name
        incremental = IncrementalAuditor(project_path, project_index_dir)

        # Scan files
        current_files = incremental.scan_files()
        print(f"Found {len(current_files)} files")

        # Detect changes
        changes = incremental.find_changes(current_files)
        print(f"  Added:     {len(changes['added'])}")
        print(f"  Modified:  {len(changes['modified'])}")
        print(f"  Deleted:   {len(changes['deleted'])}")
        print(f"  Unchanged: {len(changes['unchanged'])}")

        # Display changed files
        if changes["added"]:
            print(f"\n  New files:")
            for f in changes["added"][:10]:
                print(f"    + {f}")
            if len(changes["added"]) > 10:
                print(f"    ... and {len(changes['added']) - 10} more")

        if changes["modified"]:
            print(f"\n  Modified files:")
            for f in changes["modified"][:10]:
                print(f"    ~ {f}")
            if len(changes["modified"]) > 10:
                print(f"    ... and {len(changes['modified']) - 10} more")

        if changes["deleted"]:
            print(f"\n  Deleted files:")
            for f in changes["deleted"][:10]:
                print(f"    - {f}")
            if len(changes["deleted"]) > 10:
                print(f"    ... and {len(changes['deleted']) - 10} more")

        # Dry run only displays, does not execute
        if args.dry_run:
            total_stats["added"] += len(changes["added"])
            total_stats["modified"] += len(changes["modified"])
            total_stats["deleted"] += len(changes["deleted"])
            total_stats["unchanged"] += len(changes["unchanged"])
            continue

        # Execute audit
        files_to_audit = changes["added"] + changes["modified"]
        if args.full:
            files_to_audit = list(current_files.keys())
            print(f"\n  Force full audit: {len(files_to_audit)} files")

        if files_to_audit:
            print(f"\n  Auditing {len(files_to_audit)} files...")
            new_audits = incremental.audit_files(files_to_audit, auditor)
            incremental.update_project_map(new_audits, changes["deleted"])
            incremental.save(current_files)
            print(f"  ✅ Audited {len(new_audits)} files")
            total_stats["audited"] += len(new_audits)
        elif changes["deleted"]:
            # Only deletions, update the index
            incremental.update_project_map({}, changes["deleted"])
            incremental.save(current_files)
            print("  ✅ Updated index (removed deleted files)")

        total_stats["added"] += len(changes["added"])
        total_stats["modified"] += len(changes["modified"])
        total_stats["deleted"] += len(changes["deleted"])
        total_stats["unchanged"] += len(changes["unchanged"])

    # Merge all projects' PROJECT_MAP
    if not args.dry_run:
        print("\n" + "=" * 60)
        print("Merging PROJECT_MAP...")

        merged = {
            "audited_at": datetime.now().isoformat(),
            "total_files": 0,
            "projects": projects,
            "files": {},
            "categories": {},
            "api_map": {},
            "keyword_index": {},
        }

        for project_name in projects:
            project_map_path = INDEX_DIR / project_name / "PROJECT_MAP.json"
            if not project_map_path.exists():
                continue

            project_map = json.loads(project_map_path.read_text())

            # Merge files
            for path, audit in project_map.get("files", {}).items():
                full_path = f"{project_name}/{path}"
                audit["project"] = project_name
                merged["files"][full_path] = audit

            # Merge categories
            for cat, paths in project_map.get("categories", {}).items():
                if cat not in merged["categories"]:
                    merged["categories"][cat] = []
                merged["categories"][cat].extend([f"{project_name}/{p}" for p in paths])

            # Merge api_map
            for api, paths in project_map.get("api_map", {}).items():
                if api not in merged["api_map"]:
                    merged["api_map"][api] = []
                merged["api_map"][api].extend([f"{project_name}/{p}" for p in paths])

            # Merge keyword_index
            for kw, paths in project_map.get("keyword_index", {}).items():
                if kw not in merged["keyword_index"]:
                    merged["keyword_index"][kw] = []
                merged["keyword_index"][kw].extend([f"{project_name}/{p}" for p in paths])

        merged["total_files"] = len(merged["files"])

        # Save
        merged_map_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
        print(f"✅ Saved: {merged_map_path}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Added:     {total_stats['added']}")
    print(f"Modified:  {total_stats['modified']}")
    print(f"Deleted:   {total_stats['deleted']}")
    print(f"Unchanged: {total_stats['unchanged']}")
    if not args.dry_run:
        print(f"Audited:   {total_stats['audited']}")

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
