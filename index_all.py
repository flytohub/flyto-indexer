#!/usr/bin/env python3
"""
Index projects from config

Usage:
    python index_all.py                    # Use config/projects.yaml
    python index_all.py /path/to/config    # Use custom config
    python index_all.py --discover /path   # Auto-discover projects in path
"""

import json
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.engine import IndexEngine
from src.mapper.project_map import ProjectMapGenerator


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


def index_project(project_name: str, project_path: Path, output_dir: Path) -> dict:
    """Index a single project."""
    if not project_path.exists():
        print(f"  [!] {project_name} not found: {project_path}")
        return None

    print(f"  Scanning {project_name}...")

    try:
        engine = IndexEngine(project_name, project_path, output_dir / project_name)
        result = engine.scan(incremental=False)

        print(f"     Files: {result['files_scanned']}, Symbols: {result['symbols_found']}")

        return {
            "project": project_name,
            "root_path": str(project_path),
            "files": {k: v.to_dict() for k, v in engine.index.files.items()},
            "symbols": {k: v.to_dict() for k, v in engine.index.symbols.items()},
            "dependencies": {k: v.to_dict() for k, v in engine.index.dependencies.items()},
            "stats": result,
        }
    except Exception as e:
        print(f"     [x] Error: {e}")
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

    args = sys.argv[1:]
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
    print("=" * 60)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Combined index
    combined_index = {
        "workspace": workspace_name,
        "projects": [],
        "project_roots": {},  # Store project paths for MCP server
        "files": {},
        "symbols": {},
        "dependencies": {},
    }

    combined_map = {
        "projects": [],
        "total_files": 0,
        "files": {},
        "categories": {},
    }

    total_files = 0
    total_symbols = 0

    for i, proj in enumerate(projects):
        name = proj["name"]
        path = Path(proj["path"])

        print(f"\n[{i + 1}/{len(projects)}] {name}")

        # Index
        index_data = index_project(name, path, output_dir)
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

    index_file = output_dir / "index.json"
    index_file.write_text(json.dumps(combined_index, indent=2, ensure_ascii=False))
    print(f"  [ok] index.json ({index_file.stat().st_size // 1024} KB)")

    map_file = output_dir / "PROJECT_MAP.json"
    map_file.write_text(json.dumps(combined_map, indent=2, ensure_ascii=False))
    print(f"  [ok] PROJECT_MAP.json ({map_file.stat().st_size // 1024} KB)")

    # Summary
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"  Projects: {len(combined_index['projects'])}")
    print(f"  Files: {total_files}")
    print(f"  Symbols: {total_symbols}")
    print(f"\n  Index: {output_dir}")


if __name__ == "__main__":
    main()
