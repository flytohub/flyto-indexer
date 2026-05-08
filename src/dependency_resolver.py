"""
Dependency resolution mixin for IndexEngine.

Resolves raw call names, imports, re-exports, and API calls to full
symbol IDs.  Extracted from engine.py to reduce cognitive load.
"""

import logging

from .models import Dependency, DependencyType, SymbolType

logger = logging.getLogger(__name__)


class DependencyResolverMixin:
    """Mixin that provides all dependency-resolution methods.

    Expects the host class to expose:
        self.index          – ProjectIndex
        self.project_name   – str
    """

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

    # ------------------------------------------------------------------
    # Shared utilities (used by other mixins via self.*)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_path_from_sid(symbol_id: str) -> str:
        """Extract file path from a symbol_id like 'project:path/to/file.py:type:name'."""
        parts = symbol_id.split(":")
        if len(parts) >= 2:
            return parts[1]
        return ""

    def _extract_path(self, source_id: str) -> str:
        """Extract file path from source_id"""
        return self._extract_path_from_sid(source_id)

    def _dep_in_changed_paths(self, dep, changed_paths: set) -> bool:
        """Return True if a dependency's source file is in changed_paths."""
        if changed_paths is None:
            return True
        source_path = self._extract_path(dep.source_id)
        return source_path in changed_paths

    def _build_name_lookup(self):
        """Build symbol name -> symbol_id lookup table."""
        name_to_ids = {}
        for sid, symbol in self.index.symbols.items():
            name = symbol.name
            if name not in name_to_ids:
                name_to_ids[name] = []
            name_to_ids[name].append(sid)
        return name_to_ids

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    def _resolve_dependencies(self, changed_paths: set = None):
        """
        Resolve dependencies, converting raw call names to full symbol IDs

        Improved: only resolves when the source file actually imports the target.
        When changed_paths is provided, only re-resolves deps whose source file
        is in changed_paths. Lookup tables are always built from the full index (cheap).
        """
        name_to_ids = self._build_name_lookup()
        file_imports = self._build_file_imports()
        module_to_symbols = self._build_module_to_symbols()
        self._resolve_api_call_deps(changed_paths=changed_paths)
        self._resolve_call_deps(name_to_ids, file_imports, module_to_symbols, changed_paths=changed_paths)
        self._resolve_re_export_deps(file_imports, changed_paths=changed_paths)
        self._resolve_same_file_calls(changed_paths=changed_paths)
        self._resolve_global_fallback(name_to_ids, changed_paths=changed_paths)

    def _resolve_go_implementations(self):
        """Cross-file: match Go struct method sets against interface method sets.

        Runs after _resolve_dependencies(). For each project, collects all
        interface method sets and struct method sets, then creates
        DependencyType.IMPLEMENTS edges where a struct satisfies an interface
        and no such edge exists yet (from in-file detection).
        """
        interfaces = {}   # {(project, iface_name): set(method_names)}
        struct_methods = {}  # {(project, struct_name): set(method_names)}

        for sid, sym in self.index.symbols.items():
            stype = sym.symbol_type.value if hasattr(sym.symbol_type, 'value') else str(sym.symbol_type)
            name = sym.name
            params = sym.params or []
            project = sid.split(":")[0] if ":" in sid else ""

            if stype == "interface" and params:
                interfaces[(project, name)] = set(params)
            elif stype == "method" and "." in name:
                receiver, method = name.split(".", 1)
                struct_methods.setdefault((project, receiver), set()).add(method)

        if not interfaces or not struct_methods:
            return

        # Collect existing IMPLEMENTS edges to avoid duplicates
        existing_impls = set()
        for dep in self.index.dependencies.values():
            if dep.dep_type == DependencyType.IMPLEMENTS:
                existing_impls.add((dep.source_id, dep.target_id))

        # Build lookup: (project, name, type_prefix) -> symbol_id
        # for quick struct/interface sid resolution
        sid_lookup = {}  # (project, type_prefix, name) -> sid
        for sid in self.index.symbols:
            parts = sid.split(":")
            if len(parts) >= 4:
                proj = parts[0]
                sym_type = parts[-2]  # "class" or "interface"
                sym_name = parts[-1]
                key = (proj, sym_type, sym_name)
                # Keep first match (or could keep all, but one is sufficient)
                if key not in sid_lookup:
                    sid_lookup[key] = sid

        new_count = 0
        for (proj, struct_name), methods in struct_methods.items():
            for (iproj, iface_name), iface_methods in interfaces.items():
                if proj != iproj:
                    continue  # only within same project
                if not iface_methods or not iface_methods.issubset(methods):
                    continue

                struct_sid = sid_lookup.get((proj, "class", struct_name))
                iface_sid = sid_lookup.get((proj, "interface", iface_name))

                if not struct_sid or not iface_sid:
                    continue
                if (struct_sid, iface_sid) in existing_impls:
                    continue

                dep = Dependency(
                    source_id=struct_sid,
                    target_id=iface_sid,
                    dep_type=DependencyType.IMPLEMENTS,
                    source_line=0,
                    metadata={"kind": "cross_file"},
                )
                self.index.dependencies[dep.id] = dep
                existing_impls.add((struct_sid, iface_sid))

                # Update reverse index if it exists
                if self.index.reverse_index is not None:
                    if iface_sid not in self.index.reverse_index:
                        self.index.reverse_index[iface_sid] = []
                    if struct_sid not in self.index.reverse_index[iface_sid]:
                        self.index.reverse_index[iface_sid].append(struct_sid)

                new_count += 1

        if new_count > 0:
            logger.debug("Cross-file Go implementations resolved: %d new edges", new_count)

    def _build_file_imports(self):
        """Build file path -> imports mapping. imports format: {imported_name: module_path}"""
        file_imports = {}  # path -> {name: module}
        for _dep_id, dep in self.index.dependencies.items():
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
        return file_imports

    def _build_module_to_symbols(self):
        """Build module path -> symbol_ids mapping. Used to find actual symbols from import paths."""
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
        return module_to_symbols

    def _resolve_api_call_deps(self, changed_paths: set = None):
        """Resolve API_CALLS -> API symbols cross-reference."""
        # API symbol names are "METHOD /path" (e.g., "GET /api/users")
        api_path_map = {}  # url_path -> [symbol_ids]
        for sid, symbol in self.index.symbols.items():
            if symbol.symbol_type == SymbolType.API:
                # Extract URL path from name (format: "METHOD /path")
                parts = symbol.name.split(" ", 1)
                url_path = parts[1] if len(parts) == 2 else symbol.name
                if url_path not in api_path_map:
                    api_path_map[url_path] = []
                api_path_map[url_path].append(sid)

        for _dep_id, dep in self.index.dependencies.items():
            if dep.dep_type.value != "api_calls":
                continue
            if not self._dep_in_changed_paths(dep, changed_paths):
                continue
            target_url = dep.target_id
            dep_method = dep.metadata.get("method", "GET")
            # Try exact URL match first
            candidates = api_path_map.get(target_url, [])
            if not candidates:
                # Try suffix match
                for api_path, api_sids in api_path_map.items():
                    if target_url.endswith(api_path) or api_path.endswith(target_url):
                        candidates = api_sids
                        break
            if candidates:
                # Prefer method match
                resolved = candidates[0]
                for cid in candidates:
                    sym = self.index.symbols.get(cid)
                    if sym and sym.name.startswith(dep_method + " "):
                        resolved = cid
                        break
                dep.metadata["resolved_target"] = resolved

    def _resolve_call_deps(self, name_to_ids, file_imports, module_to_symbols, changed_paths: set = None):
        """Resolve each call dependency using import information and module lookups."""
        for _dep_id, dep in self.index.dependencies.items():
            if dep.dep_type.value != "calls":
                continue
            if not self._dep_in_changed_paths(dep, changed_paths):
                continue

            target = dep.target_id
            source_path = self._extract_path(dep.source_id)
            if not source_path:
                continue

            imports = file_imports.get(source_path, {})

            # Handle simple calls: useToast()
            call_name = target.split('.')[0]  # Take the first part
            resolved = self._resolve_simple_call(call_name, imports, name_to_ids, module_to_symbols)

            # Handle method calls: obj.method()
            if not resolved and "." in target:
                resolved = self._resolve_method_call(target, imports)

            # Update resolved_target
            if resolved:
                dep.metadata["resolved_target"] = resolved

    def _resolve_simple_call(self, call_name, imports, name_to_ids, module_to_symbols):
        """Resolve a simple call (e.g. useToast()) via imports and name lookup.

        Returns resolved symbol ID or None.
        """
        if call_name not in imports:
            return None

        resolved = None
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

        return resolved

    def _resolve_method_call(self, target, imports):
        """Resolve a method call (e.g. obj.method()) via imports.

        Returns resolved symbol ID or None.
        """
        parts = target.split(".")
        obj_name = parts[0]
        method_name = parts[-1]

        # Check if obj_name has an import
        if obj_name in imports:
            # Find symbol in Class.method format
            for sid, sym in self.index.symbols.items():
                if sym.name == target or sym.name.endswith(f".{method_name}"):
                    return sid

        return None

    def _build_re_export_map(self):
        """Build re-export map from index dependencies.

        Returns:
            dict mapping (exporter_path, exported_name) to original_module.
            Star re-exports use "*" as the name key.
        """
        re_export_map = {}
        for _dep_id, dep in self.index.dependencies.items():
            if dep.dep_type.value != "re_exports":
                continue
            exporter_path = self._extract_path(dep.source_id)
            if not exporter_path:
                continue
            original_module = dep.metadata.get("original_module", "")
            names = dep.metadata.get("names", [])
            is_star = dep.metadata.get("star", False)
            for name in names:
                re_export_map[(exporter_path, name)] = original_module
            if is_star:
                re_export_map[(exporter_path, "*")] = original_module
        return re_export_map

    @staticmethod
    def _match_exporter_path(import_module: str, exp_path: str) -> bool:
        """Check if an import module name matches an exporter's file path.

        Handles direct name/path matches, __init__.py/index.ts directory
        access patterns, and @/ or ./ alias resolution.
        """
        exp_base = exp_path.rsplit('/', 1)[-1].rsplit('.', 1)[0]
        exp_parent = exp_path.rsplit('.', 1)[0]
        exp_dir = exp_path.rsplit('/', 1)[0] if '/' in exp_path else ''
        is_init = exp_base in ('__init__', 'index')

        if import_module in (exp_base, exp_parent, exp_path):
            return True
        if is_init and (import_module == exp_dir or
                        import_module.endswith('/' + exp_dir.rsplit('/', 1)[-1]) if exp_dir else False):
            return True
        if import_module.replace('@/', 'src/').replace('./', '') in exp_path:
            return True
        return False

    def _resolve_re_export_deps(self, file_imports, changed_paths: set = None):
        """Resolve dependencies through re-export chains."""
        re_export_map = self._build_re_export_map()

        if re_export_map:
            # Build exporter_path set for quick lookup
            exporter_paths = {path for (path, _name) in re_export_map}

            # For each unresolved import dependency, check if target module
            # matches a re-exporter and resolve through the chain
            for _dep_id, dep in self.index.dependencies.items():
                if dep.dep_type.value not in ('calls', 'uses'):
                    continue
                if not self._dep_in_changed_paths(dep, changed_paths):
                    continue
                if dep.metadata.get("resolved_target"):
                    continue  # Already resolved

                source_path = self._extract_path(dep.source_id)
                if not source_path:
                    continue

                target = dep.target_id
                call_name = target.split('.')[0]
                imports = file_imports.get(source_path, {})
                if call_name not in imports:
                    continue

                import_module = imports[call_name]

                # Check if any exporter path matches the import module
                for exp_path in exporter_paths:
                    if not self._match_exporter_path(import_module, exp_path):
                        continue

                    # Look up the original module through the re-export chain
                    original_module = re_export_map.get((exp_path, call_name))
                    if not original_module:
                        # Try wildcard (star re-export)
                        original_module = re_export_map.get((exp_path, "*"))
                    if not original_module:
                        continue

                    # Find the original symbol in the original module
                    for sid, sym in self.index.symbols.items():
                        if sym.name == call_name:
                            sym_base = sym.path.rsplit('/', 1)[-1].rsplit('.', 1)[0]
                            sym_parent = sym.path.rsplit('.', 1)[0]
                            if original_module in (sym_base, sym_parent, sym.path):
                                dep.metadata["resolved_target"] = sid
                                dep.metadata["resolved_via_reexport"] = exp_path
                                break
                    if dep.metadata.get("resolved_target"):
                        break

    def _resolve_same_file_calls(self, changed_paths: set = None):
        """Resolve unresolved calls to symbols defined in the same file."""
        # For unresolved calls, check if the target exists as a symbol in the
        # same file. This catches local function calls that don't require imports.
        file_symbols = {}  # path -> {name: symbol_id}
        for sid, symbol in self.index.symbols.items():
            path = symbol.path
            if path not in file_symbols:
                file_symbols[path] = {}
            file_symbols[path][symbol.name] = sid
            # Also index short name for methods (Class.method -> method)
            if '.' in symbol.name:
                short = symbol.name.split('.')[-1]
                # Don't overwrite a direct match with a method short name
                if short not in file_symbols[path]:
                    file_symbols[path][short] = sid

        for _dep_id, dep in self.index.dependencies.items():
            if dep.dep_type.value != "calls":
                continue
            if not self._dep_in_changed_paths(dep, changed_paths):
                continue
            if dep.metadata.get("resolved_target"):
                continue

            source_path = self._extract_path(dep.source_id)
            if not source_path:
                continue

            target = dep.target_id
            local_syms = file_symbols.get(source_path, {})
            if not local_syms:
                continue

            # Exact match (e.g. "foo" or "Class.method")
            if target in local_syms:
                dep.metadata["resolved_target"] = local_syms[target]
                continue

            # Simple name match (first part of dotted name)
            call_name = target.split('.')[0]
            if call_name in local_syms:
                dep.metadata["resolved_target"] = local_syms[call_name]
                continue

            # For chained method calls (e.g. self.service.do_thing),
            # try matching the last segment as a method in same file
            if '.' in target:
                method_name = target.split('.')[-1]
                for sym_name, sym_id in local_syms.items():
                    if '.' in sym_name and sym_name.endswith('.' + method_name):
                        dep.metadata["resolved_target"] = sym_id
                        break

    def _resolve_global_fallback(self, name_to_ids, changed_paths: set = None):
        """Resolve still-unresolved calls via single-candidate name match (unambiguous only)."""
        for _dep_id, dep in self.index.dependencies.items():
            if dep.dep_type.value != "calls":
                continue
            if not self._dep_in_changed_paths(dep, changed_paths):
                continue
            if dep.metadata.get("resolved_target"):
                continue

            target = dep.target_id
            call_name = target.split('.')[0]

            # Skip built-in names
            if call_name.lower() in self.BUILTIN_NAMES:
                continue

            candidates = name_to_ids.get(call_name, [])
            if len(candidates) == 1:
                dep.metadata["resolved_target"] = candidates[0]
