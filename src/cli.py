"""
Command-line interface for Flyto Indexer.

Usage:
    flyto-index init [path]
    flyto-index scan <path> [--full]
    flyto-index status [path]
    flyto-index impact <symbol_id> --path <project_path>
    flyto-index context --path <project_path> [--query <query>]
    flyto-index outline <path>
    flyto-index brief [path]
    flyto-index describe <file_path> [--summary "..."] [--path <project_path>]
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Flyto Indexer - Code audit and smart indexing"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init 命令
    init_parser = subparsers.add_parser("init", help="Initialize .flyto/ in a project")
    init_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    init_parser.add_argument("--name", help="Project name (default: directory name)")
    init_parser.add_argument("--no-gitignore", action="store_true", help="Do not add .flyto/ to .gitignore")
    init_parser.add_argument("--index", action="store_true", help="Run indexer immediately after init")

    # scan 命令
    scan_parser = subparsers.add_parser("scan", help="Scan project and build index")
    scan_parser.add_argument("path", help="Project root path")
    scan_parser.add_argument("--full", action="store_true", help="Full rebuild (not incremental)")
    scan_parser.add_argument("--name", help="Project name (default: directory name)")
    scan_parser.add_argument("--output", help="Index output directory")

    # impact 命令
    impact_parser = subparsers.add_parser("impact", help="Query impact of a symbol")
    impact_parser.add_argument("symbol_id", help="Symbol ID to check")
    impact_parser.add_argument("--path", required=True, help="Project root path")
    impact_parser.add_argument("--depth", type=int, default=3, help="Max depth")

    # context 命令
    context_parser = subparsers.add_parser("context", help="Get context for AI")
    context_parser.add_argument("--path", required=True, help="Project root path")
    context_parser.add_argument("--query", help="Natural language query")
    context_parser.add_argument("--files", nargs="+", help="Specific files to get L1")
    context_parser.add_argument("--symbols", nargs="+", help="Specific symbols to get L2")
    context_parser.add_argument("--level", choices=["l0", "l1", "l2", "auto"], default="auto")

    # status 命令
    status_parser = subparsers.add_parser("status", help="Show .flyto/ status for a project")
    status_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    status_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    # brief 命令
    brief_parser = subparsers.add_parser("brief", help="Generate < 500 token project overview")
    brief_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")

    # describe 命令
    describe_parser = subparsers.add_parser("describe", help="Read or write file descriptions")
    describe_parser.add_argument("file_path", help="File path relative to project root (e.g., src/api/auth.py)")
    describe_parser.add_argument("--summary", help="One-liner description to write")
    describe_parser.add_argument("--path", default=".", help="Project root path (default: current directory)")
    describe_parser.add_argument("--source", default="ai", help="Description source (default: ai)")

    # outline 命令
    outline_parser = subparsers.add_parser("outline", help="Generate project outline (L0)")
    outline_parser.add_argument("path", help="Project root path")
    outline_parser.add_argument("--name", help="Project name")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == "init":
            result = cmd_init(args)
        elif args.command == "scan":
            result = cmd_scan(args)
        elif args.command == "status":
            result = cmd_status(args)
        elif args.command == "impact":
            result = cmd_impact(args)
        elif args.command == "context":
            result = cmd_context(args)
        elif args.command == "brief":
            result = cmd_brief(args)
        elif args.command == "describe":
            result = cmd_describe(args)
        elif args.command == "outline":
            result = cmd_outline(args)
        else:
            parser.print_help()
            return

        # 輸出結果
        if result is None:
            pass  # Command handled its own output
        elif isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_init(args):
    """Initialize .flyto/ in a project directory."""
    from datetime import datetime, timezone

    project_path = Path(args.path).resolve()
    project_name = args.name or project_path.name

    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)

    flyto_dir = project_path / ".flyto"
    nav_dir = flyto_dir / "nav"
    index_dir = flyto_dir / "index"

    if flyto_dir.exists():
        print(f".flyto/ already exists at {flyto_dir}")
        print("Use 'flyto-index scan' to update the index.")
        return {"ok": True, "message": "already exists", "path": str(flyto_dir)}

    # Create minimal structure
    nav_dir.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write flyto.json
    flyto_json = {
        "schemaVersion": 1,
        "generatedAt": now,
        "project": {"name": project_name, "root": "."},
        "generator": {"name": "flyto-indexer", "version": "0.2.0"},
        "paths": {
            "map": "nav/map.json",
            "descriptions": "descriptions.jsonl",
            "indexSummary": "index/summary.json",
        },
    }
    (flyto_dir / "flyto.json").write_text(
        json.dumps(flyto_json, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Write empty nav/map.json
    map_json = {
        "schemaVersion": 1,
        "generatedAt": now,
        "categories": [],
        "hotspots": [],
    }
    (nav_dir / "map.json").write_text(
        json.dumps(map_json, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Write empty descriptions.jsonl
    desc_path = flyto_dir / "descriptions.jsonl"
    if not desc_path.exists():
        desc_path.write_text("", encoding="utf-8")

    # Write empty index/summary.json
    summary_json = {
        "schemaVersion": 1,
        "generatedAt": now,
        "counts": {"files": 0, "folders": 0, "symbols": 0, "languages": {}},
        "stalenessHint": {"recommendedReindexAfterHours": 24},
    }
    (index_dir / "summary.json").write_text(
        json.dumps(summary_json, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Initialized .flyto/ at {flyto_dir}")

    # Add to .gitignore
    if not args.no_gitignore:
        gitignore_path = project_path / ".gitignore"
        entry = ".flyto/"
        already_ignored = False
        if gitignore_path.exists():
            content = gitignore_path.read_text()
            already_ignored = entry in content.splitlines()
        if not already_ignored:
            with open(gitignore_path, "a") as f:
                if gitignore_path.exists() and not gitignore_path.read_text().endswith("\n"):
                    f.write("\n")
                f.write(f"\n# Flyto index (generated)\n{entry}\n")
            print(f"Added '{entry}' to .gitignore")

    # Optionally run indexer
    if args.index:
        print("\nRunning indexer...")
        args_scan = argparse.Namespace(
            path=str(project_path), full=False, name=project_name, output=None
        )
        return cmd_scan(args_scan)

    return {"ok": True, "path": str(flyto_dir)}


def cmd_status(args):
    """Show .flyto/ status for a project."""
    project_path = Path(args.path).resolve()
    flyto_dir = project_path / ".flyto"

    if not flyto_dir.exists():
        print(f"No .flyto/ found at {project_path}")
        print("Run 'flyto-index init' to initialize.")
        return {"ok": False, "error": "no .flyto/ found"}

    # Read flyto.json
    flyto_json = {}
    flyto_path = flyto_dir / "flyto.json"
    if flyto_path.exists():
        flyto_json = json.loads(flyto_path.read_text(encoding="utf-8"))

    # Read summary.json
    summary = {}
    summary_path = flyto_dir / "index" / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # Count tags
    tags_path = flyto_dir / "tags" / "symbol_tags.jsonl"
    tag_counts = {}
    tag_total = 0
    if tags_path.exists():
        for line in tags_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                tag = json.loads(line)
                kind = tag.get("kind", "unknown")
                tag_counts[kind] = tag_counts.get(kind, 0) + 1
                tag_total += 1
            except json.JSONDecodeError:
                pass

    # Count descriptions
    desc_path = flyto_dir / "descriptions.jsonl"
    desc_count = 0
    if desc_path.exists():
        content = desc_path.read_text(encoding="utf-8").strip()
        if content:
            desc_count = len(content.split("\n"))

    # Build result
    counts = summary.get("counts", {})
    tag_stats = summary.get("tags", {})
    desc_stats = summary.get("descriptions", {})
    result = {
        "ok": True,
        "project": flyto_json.get("project", {}).get("name", "unknown"),
        "schemaVersion": flyto_json.get("schemaVersion", 0),
        "generatedAt": flyto_json.get("generatedAt", ""),
        "generator": flyto_json.get("generator", {}).get("version", ""),
        "counts": counts,
        "tags": tag_stats if tag_stats else tag_counts,
        "tags_total": tag_total,
        "descriptions": desc_stats if desc_stats else {"total": desc_count},
    }

    if hasattr(args, "as_json") and args.as_json:
        return result

    # Human-readable output
    print(f"Flyto Status: {result['project']}")
    print(f"  Schema:     v{result['schemaVersion']}")
    print(f"  Generated:  {result['generatedAt']}")
    print(f"  Generator:  flyto-indexer {result['generator']}")
    print()
    print(f"  Files:      {counts.get('files', 0)}")
    print(f"  Symbols:    {counts.get('symbols', 0)}")
    print(f"  Languages:  {counts.get('languages', {})}")
    print()
    if tag_stats:
        print(f"  Tags:")
        print(f"    Dead code:      {tag_stats.get('dead_code', 0)} symbols ({tag_stats.get('dead_code_lines', 0)} lines)")
        print(f"    TDD covered:    {tag_stats.get('tdd_covered', 0)} / {tag_stats.get('tdd_testable', 0)} testable")
        print(f"    TDD uncovered:  {tag_stats.get('tdd_uncovered', 0)}")
    elif tag_counts:
        print(f"  Tags: {tag_counts}")
    else:
        print(f"  Tags: (none)")
    print()
    if desc_stats:
        desc_total = desc_stats.get("total", 0)
        desc_fresh = desc_stats.get("fresh", 0)
        hs_total = desc_stats.get("hotspot_total", 0)
        hs_covered = desc_stats.get("hotspot_covered", 0)
        print(f"  Descriptions: {desc_fresh}/{desc_total} files covered")
        if hs_total > 0:
            print(f"    Hotspots:   {hs_covered}/{hs_total} covered")
    else:
        print(f"  Descriptions: {desc_count} entries")

    return result


def cmd_scan(args):
    """執行 scan 命令"""
    # 設定路徑
    import sys
    import os
    src_path = Path(__file__).parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    os.chdir(src_path)

    from engine import IndexEngine

    project_path = Path(args.path).resolve()
    project_name = args.name or project_path.name
    index_dir = Path(args.output) if args.output else None

    engine = IndexEngine(project_name, project_path, index_dir)
    result = engine.scan(incremental=not args.full)

    return result


def cmd_impact(args):
    """執行 impact 命令"""
    import sys
    import os
    src_path = Path(__file__).parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    os.chdir(src_path)

    from engine import IndexEngine

    project_path = Path(args.path).resolve()
    project_name = project_path.name

    engine = IndexEngine(project_name, project_path)
    result = engine.impact(args.symbol_id, args.depth)

    return result


def cmd_context(args):
    """執行 context 命令"""
    import sys
    import os
    src_path = Path(__file__).parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    os.chdir(src_path)

    from engine import IndexEngine

    project_path = Path(args.path).resolve()
    project_name = project_path.name

    engine = IndexEngine(project_name, project_path)
    result = engine.context(
        query=args.query,
        paths=args.files,
        symbols=args.symbols,
        level=args.level,
    )

    return result


def cmd_outline(args):
    """執行 outline 命令"""
    import sys
    import os
    src_path = Path(__file__).parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    os.chdir(src_path)

    from engine import IndexEngine

    project_path = Path(args.path).resolve()
    project_name = args.name or project_path.name

    engine = IndexEngine(project_name, project_path)
    return engine.outline()


def cmd_brief(args):
    """Generate or display .flyto/brief.md."""
    from .flyto_output import generate_brief_from_flyto

    project_path = Path(args.path).resolve()
    content = generate_brief_from_flyto(project_path)
    print(content)


def cmd_describe(args):
    """Read or write file descriptions in .flyto/descriptions.jsonl."""
    from datetime import datetime, timezone
    import hashlib

    project_path = Path(args.path).resolve()
    flyto_dir = project_path / ".flyto"
    desc_path = flyto_dir / "descriptions.jsonl"

    if not flyto_dir.exists():
        print(f"No .flyto/ found at {project_path}", file=sys.stderr)
        print("Run 'flyto-index init' to initialize.", file=sys.stderr)
        sys.exit(1)

    file_path = args.file_path

    if args.summary:
        # Write mode: append/update description
        # Compute file hash if the file exists
        full_file_path = project_path / file_path
        file_hash = ""
        if full_file_path.exists():
            content = full_file_path.read_bytes()
            file_hash = hashlib.sha256(content).hexdigest()[:16]

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "path": file_path,
            "hash": file_hash,
            "one_liner": args.summary,
            "source": args.source,
            "updatedAt": now,
        }
        line = json.dumps(entry, ensure_ascii=False)

        # Append to descriptions.jsonl
        with open(desc_path, "a", encoding="utf-8") as f:
            if desc_path.exists() and desc_path.stat().st_size > 0:
                # Check if file ends with newline
                with open(desc_path, "rb") as check:
                    check.seek(-1, 2)
                    if check.read(1) != b"\n":
                        f.write("\n")
            f.write(line + "\n")

        print(f"Updated: {file_path}")
        print(f"  {args.summary}")

    else:
        # Read mode: find latest description for this file
        if not desc_path.exists():
            print(f"No descriptions found. Run 'flyto-index scan' first.", file=sys.stderr)
            sys.exit(1)

        latest = None
        for line in desc_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if entry.get("path") == file_path:
                    latest = entry
            except json.JSONDecodeError:
                pass

        if latest:
            # Check staleness
            full_file_path = project_path / file_path
            stale = False
            if full_file_path.exists() and latest.get("hash"):
                import hashlib
                current_hash = hashlib.sha256(full_file_path.read_bytes()).hexdigest()[:16]
                if current_hash != latest["hash"]:
                    stale = True

            print(f"File: {file_path}")
            print(f"  {latest.get('one_liner', '(no description)')}")
            if stale:
                print(f"  [STALE] File has changed since this description was written.")
            print(f"  Source: {latest.get('source', 'unknown')}")
            print(f"  Updated: {latest.get('updatedAt', 'unknown')}")
        else:
            print(f"No description found for: {file_path}")


if __name__ == "__main__":
    main()
