"""
Main indexing engine - orchestrates the entire flow.

Usage:
1. engine.scan() - Scan project, build index
2. engine.impact(symbol_id) - Query impact scope
3. engine.context(query) - Get relevant context (L0->L1->L2)
"""

import json
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

from .models import ProjectIndex, Symbol, Dependency, FileManifest, SymbolType
from .scanner import (
    PythonScanner, VueScanner, TypeScriptScanner,
    GoScanner, RustScanner, JavaScanner, ScanResult
)
from .indexer import IncrementalIndexer, scan_directory_hashes, compute_file_hash
from .context.loader import ContextLoader, L0Context, L1Context, L2Context


class IndexEngine:
    """
    Indexing engine

    Main features:
    1. scan() - Scan project, incrementally update index
    2. impact() - Query impact scope
    3. context() - Get context (from shallow to deep)
    """

    def __init__(
        self,
        project_name: str,
        project_root: Path,
        index_dir: Optional[Path] = None
    ):
        self.project_name = project_name
        self.project_root = Path(project_root)
        self.index_dir = index_dir or (self.project_root / ".flyto-index")

        # Initialize components
        self.scanners = [
            PythonScanner(project_name),
            VueScanner(project_name),
            TypeScriptScanner(project_name),
            GoScanner(project_name),
            RustScanner(project_name),
            JavaScanner(project_name),
        ]
        self.incremental = IncrementalIndexer(self.project_root, self.index_dir)

        # Load or initialize index
        self.index = self._load_or_create_index()

    def scan(self, incremental: bool = True) -> dict:
        """
        Scan project, build/update index

        Args:
            incremental: Whether to perform incremental update (only update changed files)

        Returns:
            Scan result summary
        """
        # Collect all supported extensions
        extensions = []
        for scanner in self.scanners:
            extensions.extend(scanner.supported_extensions)

        # Scan directory to get all file hashes
        current_hashes = scan_directory_hashes(
            self.project_root,
            extensions,
            ignore_patterns=[
                "node_modules", "__pycache__", ".git", "dist", "build",
                ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto"
            ]
        )

        # Detect changes
        if incremental:
            changes = self.incremental.detect_changes(current_hashes)
            files_to_scan = changes.all_changed()
        else:
            # Full rebuild
            changes = None
            files_to_scan = list(current_hashes.keys())
            self.index = self._create_empty_index()

        # Scan files
        result = ScanResult()
        for rel_path in files_to_scan:
            file_path = self.project_root / rel_path
            if not file_path.exists():
                continue

            # Check file size (skip files larger than 1MB)
            file_size = file_path.stat().st_size
            if file_size > 1_048_576:
                result.add_error(rel_path, f"File too large: {file_size:,} bytes (max 1MB)")
                continue

            # Find the matching scanner
            scanner = self._get_scanner(file_path)
            if not scanner:
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                symbols, deps = scanner.scan_file(Path(rel_path), content)
                manifest = scanner.create_file_manifest(Path(rel_path), content, symbols)
                result.add_file_result(symbols, deps, manifest)
            except (SyntaxError, UnicodeDecodeError, OSError, ValueError) as e:
                result.add_error(rel_path, str(e))

        # Update index
        self._update_index(result, changes)

        # Resolve dependencies and build reverse index
        self._resolve_dependencies()
        self._build_reverse_index()

        # Save index
        self._save_index()

        # Apply incremental changes to manifest
        if incremental and changes:
            self.incremental.apply_changes(
                changes,
                result.manifests,
                result.symbols,
                result.dependencies
            )

        return {
            "project": self.project_name,
            "files_scanned": len(files_to_scan),
            "symbols_found": len(result.symbols),
            "dependencies_found": len(result.dependencies),
            "errors": len(result.errors),
            "changes": changes.summary() if changes else "full rebuild",
        }

    def impact(self, symbol_id: str, max_depth: int = 3) -> dict:
        """
        Query impact scope

        If this symbol is changed, which other symbols are affected?

        Args:
            symbol_id: Symbol ID (full or short format)
            max_depth: Maximum traversal depth

        Returns:
            Impact chain structure
        """
        # Try to resolve symbol_id
        full_id = self._resolve_symbol_id(symbol_id)
        if not full_id:
            return {"error": f"Symbol not found: {symbol_id}"}

        # Get impact chain
        chain = self.index.get_impact_chain(full_id, max_depth)

        # Add symbol info
        result = {
            "symbol": full_id,
            "symbol_info": self.index.symbols[full_id].to_dict() if full_id in self.index.symbols else None,
            "impact_chain": [],
        }

        for level in chain["levels"]:
            level_info = {
                "depth": level["depth"],
                "affected": [],
            }
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

    def context(
        self,
        query: Optional[str] = None,
        paths: Optional[list[str]] = None,
        symbols: Optional[list[str]] = None,
        level: str = "auto"
    ) -> dict:
        """
        Get context (from shallow to deep)

        Args:
            query: Natural language query (uses L0 to locate, then fetches L1/L2)
            paths: Specified file paths (directly fetches L1)
            symbols: Specified symbol IDs (directly fetches L2)
            level: "l0", "l1", "l2", or "auto"

        Returns:
            Context content
        """
        loader = ContextLoader(self.index)

        # Always load L0 first (used for locating)
        l0 = loader.load_l0()

        if level == "l0" or (level == "auto" and not query and not paths and not symbols):
            return {
                "level": "l0",
                "content": l0.to_text(),
                "token_estimate": l0.token_estimate(),
            }

        # If symbols are specified, directly fetch L2
        if symbols:
            l2_list = []
            for sid in symbols:
                full_id = self._resolve_symbol_id(sid)
                if full_id:
                    l2 = loader.load_l2(full_id)
                    if l2:
                        l2_list.append(l2)
            return {
                "level": "l2",
                "content": "\n\n---\n\n".join(l2.to_text() for l2 in l2_list),
                "symbols": [l2.symbol_id for l2 in l2_list],
            }

        # If paths are specified, fetch L1
        if paths:
            l1_list = []
            for path in paths:
                l1 = loader.load_l1(path)
                if l1:
                    l1_list.append(l1)
            return {
                "level": "l1",
                "content": "\n\n---\n\n".join(l1.to_text() for l1 in l1_list),
                "files": [l1.path for l1 in l1_list],
            }

        # If query is provided, use L0 to locate then fetch L2
        if query:
            l2_list = loader.load_l2_by_query(query, top_k=5)
            return {
                "level": "l2",
                "query": query,
                "content": "\n\n---\n\n".join(l2.to_text() for l2 in l2_list if l2),
                "symbols": [l2.symbol_id for l2 in l2_list if l2],
                "l0_summary": f"Project has {len(self.index.files)} files, {len(self.index.symbols)} symbols",
            }

        return {"error": "No query, paths, or symbols provided"}

    def outline(self) -> str:
        """
        Generate project outline (L0) as text

        This is for AI consumption, used for quick navigation
        """
        loader = ContextLoader(self.index)
        l0 = loader.load_l0()
        return l0.to_text()

    def _get_scanner(self, file_path: Path):
        """Get the matching scanner"""
        for scanner in self.scanners:
            if scanner.can_scan(file_path):
                return scanner
        return None

    def _resolve_symbol_id(self, symbol_id: str) -> Optional[str]:
        """Resolve symbol ID (supports short format)"""
        # Full format
        if symbol_id in self.index.symbols:
            return symbol_id

        # Short format: prepend project name
        full_id = f"{self.project_name}:{symbol_id}"
        if full_id in self.index.symbols:
            return full_id

        # Exact name match (match the last colon-separated segment)
        for sid in self.index.symbols:
            if sid.endswith(f":{symbol_id}"):
                return sid

        return None

    def _load_or_create_index(self) -> ProjectIndex:
        """Load or create index"""
        index_file = self.index_dir / "index.json"
        if index_file.exists():
            try:
                data = json.loads(index_file.read_text())
                return self._deserialize_index(data)
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning("Failed to load index from %s: %s", index_file, e)
        return self._create_empty_index()

    def _create_empty_index(self) -> ProjectIndex:
        """Create empty index"""
        return ProjectIndex(
            project=self.project_name,
            root_path=str(self.project_root),
        )

    def _update_index(self, result: ScanResult, changes=None):
        """Update index"""
        # For incremental update, first remove old data for changed files
        if changes:
            for path in changes.all_changed() + changes.deleted:
                # Remove old symbols
                to_remove = [
                    sid for sid in self.index.symbols
                    if self.index.symbols[sid].path == path
                ]
                for sid in to_remove:
                    del self.index.symbols[sid]

                # Remove old dependencies
                to_remove = [
                    did for did, dep in self.index.dependencies.items()
                    if dep.source_id.startswith(f"{self.project_name}:{path}:")
                ]
                for did in to_remove:
                    del self.index.dependencies[did]

                # Remove old manifest
                if path in self.index.files:
                    del self.index.files[path]

        # Add new data
        for symbol in result.symbols:
            self.index.symbols[symbol.id] = symbol

        for dep in result.dependencies:
            self.index.dependencies[dep.id] = dep

        for manifest in result.manifests:
            self.index.files[manifest.path] = manifest

    def _resolve_dependencies(self):
        """
        Resolve dependencies, converting raw call names to full symbol IDs

        Improved: only resolves when the source file actually imports the target
        """
        # Build symbol name -> symbol_id lookup table
        name_to_ids = {}
        for sid, symbol in self.index.symbols.items():
            name = symbol.name
            if name not in name_to_ids:
                name_to_ids[name] = []
            name_to_ids[name].append(sid)

        # Build file path -> imports mapping
        # imports format: {imported_name: module_path}
        file_imports = {}  # path -> {name: module}
        for dep_id, dep in self.index.dependencies.items():
            if dep.dep_type.value != "imports":
                continue

            source_path = self._extract_path(dep.source_id)
            if not source_path:
                continue

            if source_path not in file_imports:
                file_imports[source_path] = {}

            module = dep.target_id
            names = dep.metadata.get("names", [])
            for name in names:
                file_imports[source_path][name] = module

        # Build module path -> symbol_ids mapping
        # Used to find actual symbols from import paths
        module_to_symbols = {}
        for sid, symbol in self.index.symbols.items():
            path = symbol.path
            # Generate possible module names from path
            # e.g., src/composables/useToast.js -> useToast, composables/useToast
            base = path.rsplit('/', 1)[-1].rsplit('.', 1)[0]  # useToast
            parent = path.rsplit('.', 1)[0]  # src/composables/useToast

            for mod_key in [base, parent, path]:
                if mod_key not in module_to_symbols:
                    module_to_symbols[mod_key] = []
                if sid not in module_to_symbols[mod_key]:
                    module_to_symbols[mod_key].append(sid)

        # Resolve each call dependency
        for dep_id, dep in self.index.dependencies.items():
            if dep.dep_type.value != "calls":
                continue

            target = dep.target_id
            source_path = self._extract_path(dep.source_id)
            if not source_path:
                continue

            resolved = None
            imports = file_imports.get(source_path, {})

            # Handle simple calls: useToast()
            call_name = target.split('.')[0]  # Take the first part

            if call_name in imports:
                # Found import, resolve using module path
                module = imports[call_name]

                # Method 1: Look up from module_to_symbols
                for mod_key in [module, module.split('/')[-1], call_name]:
                    if mod_key in module_to_symbols:
                        candidates = module_to_symbols[mod_key]
                        # Find name match
                        for cid in candidates:
                            sym = self.index.symbols.get(cid)
                            if sym and sym.name == call_name:
                                resolved = cid
                                break
                        if resolved:
                            break

                # Method 2: Look up from name_to_ids, but check path similarity
                if not resolved and call_name in name_to_ids:
                    candidates = name_to_ids[call_name]
                    for cid in candidates:
                        sym = self.index.symbols.get(cid)
                        if sym:
                            # Check if module path is related to symbol path
                            norm_module = module.replace('@/', 'src/').replace('./', '')
                            if norm_module in sym.path or sym.path.endswith(f"/{call_name}."):
                                resolved = cid
                                break

            # Handle method calls: obj.method()
            if not resolved and "." in target:
                parts = target.split(".")
                obj_name = parts[0]
                method_name = parts[-1]

                # Check if obj_name has an import
                if obj_name in imports:
                    # Find symbol in Class.method format
                    for sid, sym in self.index.symbols.items():
                        if sym.name == target or sym.name.endswith(f".{method_name}"):
                            resolved = sid
                            break

            # Update resolved_target
            if resolved:
                dep.metadata["resolved_target"] = resolved

    def _extract_path(self, source_id: str) -> str:
        """Extract file path from source_id"""
        if ":" in source_id:
            parts = source_id.split(":")
            if len(parts) >= 2:
                return parts[1]
        return ""

    # Language built-in names, excluded from reference tracking (cannot trace to definition)
    BUILTIN_NAMES = {
        # Python built-ins
        'str', 'int', 'float', 'bool', 'dict', 'list', 'tuple', 'set',
        'len', 'range', 'type', 'isinstance', 'hasattr', 'getattr', 'setattr',
        'open', 'print', 'input', 'format', 'sorted', 'filter', 'map', 'zip',
        'min', 'max', 'sum', 'abs', 'round', 'enumerate', 'reversed',
        # JS built-ins
        'console', 'window', 'document', 'Array', 'Object', 'String', 'Number',
        'JSON', 'Math', 'Date', 'Promise', 'fetch', 'setTimeout', 'setInterval',
        'parseInt', 'parseFloat', 'isNaN', 'isFinite', 'encodeURI', 'decodeURI',
        # Vue/React built-in hooks
        'ref', 'reactive', 'computed', 'watch', 'watchEffect',
        'onMounted', 'onUnmounted', 'onBeforeMount', 'onBeforeUnmount',
        'useState', 'useEffect', 'useCallback', 'useMemo', 'useRef', 'useContext',
        'defineProps', 'defineEmits', 'defineExpose',
    }

    # Tracked dependency types (not just calls)
    TRACKED_DEP_TYPES = {'calls', 'extends', 'implements', 'uses'}

    def _build_reverse_index(self):
        """
        Build reverse index: symbol_id -> referenced by whom

        Improved:
        - Tracks multiple dependency types (calls, extends, implements, uses)
        - Only filters language built-in names
        - Deduplicates by project path (avoids double-counting from forks)
        """
        reverse_index = {}  # symbol_id -> [caller_ids]

        for dep_id, dep in self.index.dependencies.items():
            # Track multiple dependency types
            if dep.dep_type.value not in self.TRACKED_DEP_TYPES:
                continue

            # Get target (prefer resolved, otherwise use raw target)
            resolved = dep.metadata.get("resolved_target")

            # For extends/implements, use target_id directly
            if not resolved and dep.dep_type.value in ('extends', 'implements'):
                # Try to resolve from name_to_ids
                target_name = dep.target_id
                for sid, sym in self.index.symbols.items():
                    if sym.name == target_name:
                        resolved = sid
                        break

            # For uses type, try to resolve from target_id
            # target_id format e.g.: @/composables/useToast:composable:useToast
            # or: ../composables/useToast:composable:useToast
            if not resolved and dep.dep_type.value == 'uses':
                target = dep.target_id
                if ':' in target:
                    # Extract type:name portion
                    parts = target.split(':')
                    if len(parts) >= 3:
                        sym_type = parts[-2]
                        sym_name = parts[-1]
                        # Find matching symbol in symbols
                        for sid, sym in self.index.symbols.items():
                            if (sym.name == sym_name and
                                sym.symbol_type.value == sym_type and
                                sid.startswith(self.project_name + ":")):
                                resolved = sid
                                break

            if not resolved:
                continue

            # Check if target symbol exists
            target_symbol = self.index.symbols.get(resolved)
            if not target_symbol:
                continue

            target_name = target_symbol.name.split('.')[-1]  # Take the last part

            # Only filter language built-ins (cannot trace to definition)
            if target_name.lower() in self.BUILTIN_NAMES:
                continue

            source = dep.source_id

            if resolved not in reverse_index:
                reverse_index[resolved] = []

            if source not in reverse_index[resolved]:
                reverse_index[resolved].append(source)

        # Save reverse index
        self.index.reverse_index = reverse_index

        # Calculate reference count for each symbol
        # Deduplicate by project path (avoids double-counting from forks)
        for sid, symbol in self.index.symbols.items():
            callers = reverse_index.get(sid, [])
            # Extract unique file paths (strip project prefix)
            unique_paths = set()
            for caller_id in callers:
                parts = caller_id.split(":", 2)
                if len(parts) >= 2:
                    # Use path:type:name as unique identifier
                    unique_paths.add(":".join(parts[1:]))
            symbol.reference_count = len(unique_paths)

    def _save_index(self, separate_content: bool = True):
        """
        Save index

        Args:
            separate_content: If True, save content to content.jsonl separately
        """
        self.index_dir.mkdir(parents=True, exist_ok=True)
        index_file = self.index_dir / "index.json"
        content_file = self.index_dir / "content.jsonl"

        # Save content to JSONL if requested
        if separate_content:
            with open(content_file, 'w', encoding='utf-8') as f:
                for symbol in self.index.symbols.values():
                    if symbol.content:
                        record = symbol.to_content_record()
                        f.write(json.dumps(record, ensure_ascii=False) + '\n')

        # Main index without content (compact mode)
        data = {
            "project": self.index.project,
            "root_path": self.index.root_path,
            "indexed_at": datetime.now().isoformat(),
            "files": {k: v.to_dict() for k, v in self.index.files.items()},
            "symbols": {
                k: v.to_dict(include_content=not separate_content, compact=True)
                for k, v in self.index.symbols.items()
            },
            "dependencies": {k: v.to_dict() for k, v in self.index.dependencies.items()},
            "entry_points": self.index.entry_points,
            "routes": self.index.routes,
            "api_endpoints": self.index.api_endpoints,
            "reverse_index": self.index.reverse_index,
            "has_content_file": separate_content,
        }

        index_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _deserialize_index(self, data: dict) -> ProjectIndex:
        """Deserialize index from JSON"""
        index = ProjectIndex(
            project=data["project"],
            root_path=data["root_path"],
            entry_points=data.get("entry_points", []),
            routes=data.get("routes", {}),
            api_endpoints=data.get("api_endpoints", []),
            reverse_index=data.get("reverse_index", {}),
        )

        # Restore files
        for path, fdata in data.get("files", {}).items():
            index.files[path] = FileManifest(
                path=fdata["path"],
                content_hash=fdata["hash"],
                line_count=fdata["lines"],
                symbols=fdata.get("symbols", []),
                last_indexed=fdata.get("indexed_at", ""),
            )

        # Load content from JSONL if available
        content_map = {}
        if data.get("has_content_file"):
            content_file = self.index_dir / "content.jsonl"
            if content_file.exists():
                content_map = self._load_content_file(content_file)

        # Restore symbols
        for sid, sdata in data.get("symbols", {}).items():
            # Get content from content_map or from inline data
            content = content_map.get(sid, sdata.get("content", ""))

            index.symbols[sid] = Symbol(
                project=sdata["project"],
                path=sdata["path"],
                symbol_type=SymbolType(sdata["type"]),
                name=sdata["name"],
                start_line=sdata.get("start_line", 0),
                end_line=sdata.get("end_line", 0),
                content=content,
                content_hash=sdata.get("content_hash", ""),
                summary=sdata.get("summary", ""),
                language=sdata.get("language", ""),
                exports=sdata.get("exports", []),
                imports=sdata.get("imports", []),
                reference_count=sdata.get("ref_count", 0),
            )

        # Restore dependencies
        for did, ddata in data.get("dependencies", {}).items():
            from .models import DependencyType
            index.dependencies[did] = Dependency(
                source_id=ddata["source"],
                target_id=ddata["target"],
                dep_type=DependencyType(ddata["type"]),
                source_line=ddata.get("line", 0),
                metadata=ddata.get("metadata", {}),
            )

        return index

    def _load_content_file(self, content_file: Path) -> dict:
        """Load content from JSONL file."""
        content_map = {}
        try:
            with open(content_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        content_map[record["id"]] = record["content"]
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning("Failed to load content file %s: %s", content_file, e)
        return content_map
