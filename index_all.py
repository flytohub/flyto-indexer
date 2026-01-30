#!/usr/bin/env python3
"""
Index projects from config

Usage:
    python index_all.py                    # Use config/projects.yaml
    python index_all.py /path/to/config    # Use custom config
    python index_all.py --discover /path   # Auto-discover projects in path
    python index_all.py --full             # Force full rebuild (no incremental)
"""

import gzip
import json
import sys
import yaml
import hashlib
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from src.engine import IndexEngine
from src.mapper.project_map import ProjectMapGenerator


# Workspace manifest for incremental indexing
MANIFEST_FILE = "workspace_manifest.json"


def load_workspace_manifest(output_dir: Path) -> dict:
    """Load file hashes from previous run."""
    manifest_path = output_dir / MANIFEST_FILE
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text())
        except Exception:
            pass
    return {"projects": {}, "indexed_at": None}


def save_workspace_manifest(output_dir: Path, manifest: dict):
    """Save file hashes for next run."""
    manifest_path = output_dir / MANIFEST_FILE
    manifest["indexed_at"] = datetime.now().isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def compute_project_hash(project_path: Path) -> str:
    """Compute a hash representing project state (based on file mtimes)."""
    if not project_path.exists():
        return ""

    mtimes = []
    extensions = [".py", ".vue", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"]
    ignore_dirs = {"node_modules", "__pycache__", ".git", "dist", "build", ".venv", "venv", "target"}

    for ext in extensions:
        for f in project_path.rglob(f"*{ext}"):
            # Skip ignored directories
            if any(ignored in f.parts for ignored in ignore_dirs):
                continue
            try:
                mtimes.append(f"{f.relative_to(project_path)}:{f.stat().st_mtime}")
            except OSError:
                pass

    # Sort for consistent hash
    mtimes.sort()
    content = "\n".join(mtimes)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def load_projects_config(config_path: Path = None) -> dict:
    """Load projects from config file."""
    if config_path is None:
        config_path = Path(__file__).parent / "config" / "projects.yaml"

    if not config_path.exists():
        print(f"Config not found: {config_path}")
        print("Creating default config...")
        create_default_config(config_path)

    with open(config_path) as f:
        return yaml.safe_load(f)


def create_default_config(config_path: Path):
    """Create default projects.yaml."""
    default = {
        "workspace": {
            "name": "my-workspace",
            "output_dir": ".flyto-index",
        },
        "projects": [
            {
                "name": "example-project",
                "path": "/path/to/your/project",
            }
        ],
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        yaml.dump(default, f, default_flow_style=False)
    print(f"Created: {config_path}")
    print("Please edit this file and run again.")


def discover_projects(parent_path: Path) -> list[dict]:
    """Auto-discover projects in a directory."""
    projects = []
    for child in sorted(parent_path.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue

        # Check if it's a project
        is_project = (
            (child / ".git").exists() or
            (child / "package.json").exists() or
            (child / "pyproject.toml").exists() or
            (child / "Cargo.toml").exists() or
            (child / "go.mod").exists()
        )

        if is_project:
            projects.append({
                "name": child.name,
                "path": str(child),
            })

    return projects


def index_project(project_name: str, project_path: Path, output_dir: Path, incremental: bool = True) -> dict:
    """Index a single project."""
    if not project_path.exists():
        print(f"  [!] {project_name} not found: {project_path}")
        return None

    mode = "incremental" if incremental else "full"
    print(f"  Scanning {project_name} ({mode})...")

    try:
        engine = IndexEngine(project_name, project_path, output_dir / project_name)
        result = engine.scan(incremental=incremental)

        print(f"     Files: {result['files_scanned']}, Symbols: {result['symbols_found']}")

        return {
            "project": project_name,
            "root_path": str(project_path),
            "files": {k: v.to_dict() for k, v in engine.index.files.items()},
            "symbols": {k: v.to_dict() for k, v in engine.index.symbols.items()},
            "dependencies": {k: v.to_dict() for k, v in engine.index.dependencies.items()},
            "reverse_index": engine.index.reverse_index,
            "stats": result,
        }
    except Exception as e:
        print(f"     [x] Error: {e}")
        import traceback
        traceback.print_exc()
        return None


def generate_project_map(project_path: Path) -> dict:
    """Generate PROJECT_MAP for a project."""
    if not project_path.exists():
        return None

    try:
        generator = ProjectMapGenerator(project_path)
        return generator.generate()
    except Exception as e:
        print(f"     [x] Map error: {e}")
        return None


def main():
    # Parse arguments
    config_path = None
    discover_path = None
    force_full = False

    args = sys.argv[1:]
    if "--full" in args:
        force_full = True
        args.remove("--full")
    if "--discover" in args:
        idx = args.index("--discover")
        if idx + 1 < len(args):
            discover_path = Path(args[idx + 1])
    elif args:
        config_path = Path(args[0])

    # Load or discover projects
    if discover_path:
        print(f"Discovering projects in: {discover_path}")
        projects = discover_projects(discover_path)
        output_dir = discover_path / ".flyto-index"
        workspace_name = discover_path.name
    else:
        config = load_projects_config(config_path)
        if not config:
            return

        workspace = config.get("workspace", {})
        workspace_name = workspace.get("name", "workspace")
        output_dir = Path(workspace.get("output_dir", ".flyto-index"))

        # Handle relative output_dir
        if not output_dir.is_absolute():
            output_dir = Path(__file__).parent / output_dir

        projects = config.get("projects", [])

    if not projects:
        print("No projects to index.")
        return

    print("=" * 60)
    print(f"Indexing: {workspace_name}")
    print(f"Projects: {len(projects)}")
    print(f"Output: {output_dir}")
    print(f"Mode: {'full rebuild' if force_full else 'incremental'}")
    print("=" * 60)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Load workspace manifest for incremental indexing
    ws_manifest = load_workspace_manifest(output_dir)
    new_manifest = {"projects": {}}

    # Load existing combined index for incremental merge
    existing_index = {}
    existing_index_path = output_dir / "index.json"
    if not force_full and existing_index_path.exists():
        try:
            existing_index = json.loads(existing_index_path.read_text())
        except Exception:
            pass

    # Combined index
    combined_index = {
        "workspace": workspace_name,
        "projects": [],
        "project_roots": {},  # Store project paths for MCP server
        "files": {},
        "symbols": {},
        "dependencies": {},
        "reverse_index": {},  # symbol_id -> [caller_ids]
        "indexed_at": datetime.now().isoformat(),
    }

    combined_map = {
        "projects": [],
        "total_files": 0,
        "files": {},
        "categories": {},
    }

    total_files = 0
    total_symbols = 0
    skipped_projects = 0

    for i, proj in enumerate(projects):
        name = proj["name"]
        path = Path(proj["path"])

        print(f"\n[{i + 1}/{len(projects)}] {name}")

        # Compute project hash for incremental check
        proj_hash = compute_project_hash(path)
        new_manifest["projects"][name] = proj_hash

        # Check if project has changed
        if not force_full and ws_manifest["projects"].get(name) == proj_hash:
            print(f"  [skip] No changes detected")
            skipped_projects += 1

            # Use existing data from previous index
            if existing_index:
                combined_index["projects"].append(name)
                combined_index["project_roots"][name] = str(path)

                # Copy existing files, symbols, dependencies for this project
                for fpath, fdata in existing_index.get("files", {}).items():
                    if fpath.startswith(f"{name}/"):
                        combined_index["files"][fpath] = fdata

                for sid, sdata in existing_index.get("symbols", {}).items():
                    if sid.startswith(f"{name}:"):
                        combined_index["symbols"][sid] = sdata
                        total_symbols += 1

                for did, ddata in existing_index.get("dependencies", {}).items():
                    if did.startswith(f"{name}:"):
                        combined_index["dependencies"][did] = ddata

                # Copy existing reverse_index for this project
                for target_id, callers in existing_index.get("reverse_index", {}).items():
                    if target_id.startswith(f"{name}:"):
                        if target_id not in combined_index["reverse_index"]:
                            combined_index["reverse_index"][target_id] = []
                        for caller in callers:
                            if caller not in combined_index["reverse_index"][target_id]:
                                combined_index["reverse_index"][target_id].append(caller)

            continue

        # Index (incremental within project)
        index_data = index_project(name, path, output_dir, incremental=not force_full)
        if index_data:
            combined_index["projects"].append(name)
            combined_index["project_roots"][name] = str(path)

            # Merge files
            for fpath, fdata in index_data["files"].items():
                full_path = f"{name}/{fpath}"
                combined_index["files"][full_path] = fdata

            # Merge symbols
            for sid, sdata in index_data["symbols"].items():
                combined_index["symbols"][sid] = sdata

            # Merge dependencies
            for did, ddata in index_data["dependencies"].items():
                combined_index["dependencies"][did] = ddata

            # Merge reverse_index
            for target_id, callers in index_data.get("reverse_index", {}).items():
                if target_id not in combined_index["reverse_index"]:
                    combined_index["reverse_index"][target_id] = []
                for caller in callers:
                    if caller not in combined_index["reverse_index"][target_id]:
                        combined_index["reverse_index"][target_id].append(caller)

            total_files += index_data["stats"]["files_scanned"]
            total_symbols += index_data["stats"]["symbols_found"]

        # PROJECT_MAP
        map_data = generate_project_map(path)
        if map_data:
            combined_map["projects"].append(name)
            combined_map["total_files"] += map_data.get("total_files", 0)

            for mpath, finfo in map_data.get("files", {}).items():
                full_path = f"{name}/{mpath}"
                combined_map["files"][full_path] = finfo

            for cat, paths in map_data.get("categories", {}).items():
                if cat not in combined_map["categories"]:
                    combined_map["categories"][cat] = []
                combined_map["categories"][cat].extend([f"{name}/{p}" for p in paths])

    # Save
    print("\n" + "=" * 60)
    print("Saving index...")

    # Save content to separate JSONL file for size reduction
    content_file = output_dir / "content.jsonl"
    with open(content_file, 'w', encoding='utf-8') as f:
        for sid, sdata in combined_index["symbols"].items():
            content = sdata.get("content", "")
            if content:
                record = {"id": sid, "content": content}
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
    print(f"  [ok] content.jsonl ({content_file.stat().st_size // 1024} KB)")

    # Strip content from symbols and skip empty fields for compact index
    compact_symbols = {}
    for sid, sdata in combined_index["symbols"].items():
        compact = {
            "project": sdata["project"],
            "path": sdata["path"],
            "type": sdata["type"],
            "name": sdata["name"],
            "start_line": sdata.get("start_line", 0),
            "end_line": sdata.get("end_line", 0),
            "language": sdata.get("language", ""),
        }
        # Only include non-empty optional fields
        if sdata.get("content_hash"):
            compact["content_hash"] = sdata["content_hash"]
        if sdata.get("summary"):
            compact["summary"] = sdata["summary"]
        if sdata.get("exports"):
            compact["exports"] = sdata["exports"]
        if sdata.get("imports"):
            compact["imports"] = sdata["imports"]
        if sdata.get("ref_count", 0) > 0:
            compact["ref_count"] = sdata["ref_count"]
        compact_symbols[sid] = compact

    combined_index["symbols"] = compact_symbols
    combined_index["has_content_file"] = True

    # Save as gzip for smaller size
    index_file = output_dir / "index.json.gz"
    with gzip.open(index_file, 'wt', encoding='utf-8') as f:
        json.dump(combined_index, f, ensure_ascii=False)
    print(f"  [ok] index.json.gz ({index_file.stat().st_size // 1024} KB)")

    map_file = output_dir / "PROJECT_MAP.json.gz"
    with gzip.open(map_file, 'wt', encoding='utf-8') as f:
        json.dump(combined_map, f, ensure_ascii=False)
    print(f"  [ok] PROJECT_MAP.json.gz ({map_file.stat().st_size // 1024} KB)")

    # Save workspace manifest for next incremental run
    save_workspace_manifest(output_dir, new_manifest)
    print(f"  [ok] {MANIFEST_FILE}")

    # Summary
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"  Projects: {len(combined_index['projects'])}")
    print(f"  Skipped (unchanged): {skipped_projects}")
    print(f"  Files: {total_files}")
    print(f"  Symbols: {total_symbols}")
    print(f"\n  Index: {output_dir}")


if __name__ == "__main__":
    main()
