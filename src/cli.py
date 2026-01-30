"""
Command-line interface for Flyto Indexer.

Usage:
    flyto-index scan <path> [--full]
    flyto-index impact <symbol_id> --path <project_path>
    flyto-index context --path <project_path> [--query <query>]
    flyto-index outline <path>
    flyto-index sync <path> [--full]
    flyto-index search <query> --path <project_path>
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

    # outline 命令
    outline_parser = subparsers.add_parser("outline", help="Generate project outline (L0)")
    outline_parser.add_argument("path", help="Project root path")
    outline_parser.add_argument("--name", help="Project name")

    # sync 命令
    sync_parser = subparsers.add_parser("sync", help="Sync index to Qdrant vector database")
    sync_parser.add_argument("path", help="Project root path")
    sync_parser.add_argument("--full", action="store_true", help="Full sync (not incremental)")
    sync_parser.add_argument("--name", help="Project name")

    # search 命令
    search_parser = subparsers.add_parser("search", help="Semantic search in vector database")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--path", required=True, help="Project root path")
    search_parser.add_argument("--limit", type=int, default=10, help="Max results")
    search_parser.add_argument("--type", help="Filter by symbol type")
    search_parser.add_argument("--threshold", type=float, default=0.5, help="Score threshold")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == "scan":
            result = cmd_scan(args)
        elif args.command == "impact":
            result = cmd_impact(args)
        elif args.command == "context":
            result = cmd_context(args)
        elif args.command == "outline":
            result = cmd_outline(args)
        elif args.command == "sync":
            result = cmd_sync(args)
        elif args.command == "search":
            result = cmd_search(args)
        else:
            parser.print_help()
            return

        # 輸出結果
        if isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


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


def cmd_sync(args):
    """執行 sync 命令 - 同步到 Qdrant"""
    import sys
    import os
    src_path = Path(__file__).parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    os.chdir(src_path)

    from store.sync import sync_index_to_qdrant

    project_path = Path(args.path).resolve()
    index_file = project_path / ".flyto-index" / "index.json"
    project_name = args.name or project_path.name

    if not index_file.exists():
        return {"ok": False, "error": f"Index not found. Run 'flyto-index scan {args.path}' first."}

    result = sync_index_to_qdrant(
        index_file=index_file,
        project_name=project_name,
        incremental=not args.full,
        show_progress=True,
    )

    return result


def cmd_search(args):
    """執行 search 命令 - 語義搜尋"""
    import sys
    import os
    src_path = Path(__file__).parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    os.chdir(src_path)

    from store.sync import SyncManager

    project_path = Path(args.path).resolve()
    project_name = project_path.name

    sync_manager = SyncManager(project_name)
    result = sync_manager.search(
        query=args.query,
        limit=args.limit,
        filter_type=args.type,
        score_threshold=args.threshold,
    )

    return result


if __name__ == "__main__":
    main()
