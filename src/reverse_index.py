"""
Reverse-index mixin for IndexEngine.

Builds the "who references whom" reverse mapping and calculates
per-symbol reference counts.  Extracted from engine.py.
"""

import logging
import re

logger = logging.getLogger(__name__)


class ReverseIndexMixin:
    """Mixin that provides all reverse-index methods.

    Expects the host class to expose (via self or other mixins):
        self.index              – ProjectIndex
        self.project_name       – str
        self.BUILTIN_NAMES      – set  (from DependencyResolverMixin)
        self._extract_path()           (from DependencyResolverMixin)
        self._extract_path_from_sid()  (from DependencyResolverMixin)
        self._build_name_lookup()      (from DependencyResolverMixin)
    """

    # Tracked dependency types (not just calls)
    TRACKED_DEP_TYPES = {'calls', 'extends', 'implements', 'uses', 're_exports'}

    def _build_reverse_index(self, changed_paths: set = None):
        """
        Build reverse index: symbol_id -> referenced by whom

        Improved:
        - Tracks multiple dependency types (calls, extends, implements, uses)
        - Only filters language built-in names
        - Deduplicates by project path (avoids double-counting from forks)

        When changed_paths is provided, does an incremental update:
        1. Purge stale entries from changed_paths
        2. Re-add only deps involving changed_paths
        3. Recalculate ref counts only for affected symbols
        """
        if changed_paths is not None:
            # Incremental mode: start from existing reverse_index
            reverse_index = dict(self.index.reverse_index) if self.index.reverse_index else {}

            # Phase 1: Purge stale entries
            # Remove callers whose source file is in changed_paths
            affected_targets = set()
            for target_sid, callers in list(reverse_index.items()):
                new_callers = [
                    c for c in callers
                    if self._extract_path_from_sid(c) not in changed_paths
                ]
                if len(new_callers) != len(callers):
                    affected_targets.add(target_sid)
                    reverse_index[target_sid] = new_callers

            # Remove target keys whose path is in changed_paths
            for target_sid in list(reverse_index.keys()):
                if self._extract_path_from_sid(target_sid) in changed_paths:
                    affected_targets.add(target_sid)
                    del reverse_index[target_sid]

            # Phase 2: Re-add deps involving changed_paths
            self._build_reverse_from_deps_incremental(reverse_index, changed_paths, affected_targets)
            name_to_ids = self._build_name_lookup()
            self._build_reverse_from_imports_incremental(reverse_index, name_to_ids, changed_paths, affected_targets)
            self._detect_dict_dispatch_refs_incremental(reverse_index, changed_paths, affected_targets)

            # Save reverse index
            self.index.reverse_index = reverse_index

            # Phase 3: Recalculate ref counts only for affected symbols
            self._calculate_reference_counts(reverse_index, affected_only=affected_targets)
        else:
            # Full rebuild
            reverse_index = self._build_reverse_from_deps()
            name_to_ids = self._build_name_lookup()
            self._build_reverse_from_imports(reverse_index, name_to_ids)
            self._detect_dict_dispatch_refs(reverse_index)

            # Save reverse index
            self.index.reverse_index = reverse_index

            self._calculate_reference_counts(reverse_index)

    def _resolve_dep_target(self, dep):
        """Resolve a dependency to a target symbol ID.

        Handles extends/implements (by name match) and uses (by type:name match).
        Returns the resolved symbol ID, or None if unresolvable or a built-in.
        """
        resolved = dep.metadata.get("resolved_target")

        # For extends/implements, match by target name
        if not resolved and dep.dep_type.value in ('extends', 'implements'):
            target_name = dep.target_id
            for sid, sym in self.index.symbols.items():
                if sym.name == target_name:
                    resolved = sid
                    break

        # For uses, parse target_id format: path:type:name
        if not resolved and dep.dep_type.value == 'uses':
            target = dep.target_id
            if ':' in target:
                parts = target.split(':')
                if len(parts) >= 3:
                    sym_type = parts[-2]
                    sym_name = parts[-1]
                    for sid, sym in self.index.symbols.items():
                        if (sym.name == sym_name and
                            sym.symbol_type.value == sym_type and
                            sid.startswith(self.project_name + ":")):
                            resolved = sid
                            break

        if not resolved:
            return None

        target_symbol = self.index.symbols.get(resolved)
        if not target_symbol:
            return None

        # Filter language built-ins
        target_name = target_symbol.name.split('.')[-1]
        if target_name.lower() in self.BUILTIN_NAMES:
            return None

        return resolved

    def _build_reverse_from_deps(self):
        """Build initial reverse index from tracked dependency types."""
        reverse_index = {}

        for _dep_id, dep in self.index.dependencies.items():
            if dep.dep_type.value not in self.TRACKED_DEP_TYPES:
                continue

            resolved = self._resolve_dep_target(dep)
            if not resolved:
                continue

            source = dep.source_id
            if resolved not in reverse_index:
                reverse_index[resolved] = []
            if source not in reverse_index[resolved]:
                reverse_index[resolved].append(source)

        return reverse_index

    def _build_reverse_from_deps_incremental(self, reverse_index, changed_paths, affected_targets):
        """Re-add dep-based reverse entries for deps involving changed_paths."""
        for _dep_id, dep in self.index.dependencies.items():
            if dep.dep_type.value not in self.TRACKED_DEP_TYPES:
                continue
            source_path = self._extract_path(dep.source_id)
            if source_path not in changed_paths:
                continue

            resolved = self._resolve_dep_target(dep)
            if not resolved:
                continue

            source = dep.source_id
            if resolved not in reverse_index:
                reverse_index[resolved] = []
            if source not in reverse_index[resolved]:
                reverse_index[resolved].append(source)
            affected_targets.add(resolved)

    def _build_path_to_module_keys(self):
        """Build mapping from file path to set of module keys for import resolution."""
        path_to_module_keys = {}
        for symbol in self.index.symbols.values():
            path = symbol.path
            if path in path_to_module_keys:
                continue
            base = path.rsplit('/', 1)[-1].rsplit('.', 1)[0]
            parent = path.rsplit('.', 1)[0]
            keys = {base, parent, path}
            if base in ('__init__', 'index') and '/' in path:
                dir_name = path.rsplit('/', 1)[0]
                keys.add(dir_name)
                if '/' in dir_name:
                    keys.add(dir_name.rsplit('/', 1)[-1])
            path_to_module_keys[path] = keys
        return path_to_module_keys

    def _resolve_import_name(self, name, target_module, norm_module, candidates, path_to_module_keys):
        """Resolve an imported name to a symbol ID using module path matching."""
        if name.lower() in self.BUILTIN_NAMES or len(name) <= 1:
            return None
        if not candidates:
            return None
        for cid in candidates:
            sym = self.index.symbols.get(cid)
            if not sym:
                continue
            mod_keys = path_to_module_keys.get(sym.path, set())
            if (target_module in mod_keys or
                    norm_module in sym.path or
                    any(target_module.endswith(k) or k.endswith(target_module)
                        for k in mod_keys if len(k) > 3)):
                return cid
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _add_reverse_entry(self, reverse_index, resolved, source):
        """Add source to resolved's reverse index entry if not already present."""
        if resolved not in reverse_index:
            reverse_index[resolved] = []
        if source not in reverse_index[resolved]:
            reverse_index[resolved].append(source)

    def _build_reverse_from_imports_incremental(self, reverse_index, name_to_ids, changed_paths, affected_targets):
        """Re-add import-based reverse entries for deps involving changed_paths."""
        path_to_module_keys = self._build_path_to_module_keys()

        for dep in self.index.dependencies.values():
            if dep.dep_type.value != "imports":
                continue
            source_path = self._extract_path(dep.source_id)
            if source_path not in changed_paths:
                continue

            norm_module = dep.target_id.replace('@/', 'src/').replace('./', '').replace('../', '')

            for name in dep.metadata.get("names", []):
                resolved = self._resolve_import_name(
                    name, dep.target_id, norm_module,
                    name_to_ids.get(name, []), path_to_module_keys,
                )
                if resolved:
                    self._add_reverse_entry(reverse_index, resolved, dep.source_id)
                    affected_targets.add(resolved)

    def _detect_dict_dispatch_refs_incremental(self, reverse_index, changed_paths, affected_targets):
        """Re-detect dict dispatch refs for symbols in changed_paths."""
        _file_content_cache = {}
        MAX_CONTENT_SCAN = 100_000

        for sid, symbol in self.index.symbols.items():
            if symbol.path not in changed_paths:
                continue
            if sid in reverse_index:
                continue
            if symbol.symbol_type.value not in ('function', 'method'):
                continue
            bare_name = symbol.name.split('.')[-1] if '.' in symbol.name else symbol.name
            if not bare_name or len(bare_name) <= 2:
                continue
            file_key = f"{symbol.project}:{symbol.path}"
            if file_key not in _file_content_cache:
                parts = []
                total_size = 0
                for other_id, other_sym in self.index.symbols.items():
                    if other_sym.path == symbol.path and other_sym.project == symbol.project:
                        content = other_sym.content
                        if content:
                            total_size += len(content)
                            if total_size > MAX_CONTENT_SCAN:
                                break
                            parts.append((other_id, content))
                _file_content_cache[file_key] = parts if total_size <= MAX_CONTENT_SCAN else []
            name_pat = re.compile(r'\b' + re.escape(bare_name) + r'\b')
            for other_id, content in _file_content_cache[file_key]:
                if other_id == sid:
                    continue
                if name_pat.search(content):
                    if sid not in reverse_index:
                        reverse_index[sid] = []
                    if other_id not in reverse_index[sid]:
                        reverse_index[sid].append(other_id)
                    affected_targets.add(sid)
                    break

    def _build_reverse_from_imports(self, reverse_index, name_to_ids):
        """Add import-based references to the reverse index.

        If file A imports symbol X, then X's reverse index should include A.
        """
        path_to_module_keys = self._build_path_to_module_keys()

        for dep in self.index.dependencies.values():
            if dep.dep_type.value != "imports":
                continue

            norm_module = dep.target_id.replace('@/', 'src/').replace('./', '').replace('../', '')

            for name in dep.metadata.get("names", []):
                resolved = self._resolve_import_name(
                    name, dep.target_id, norm_module,
                    name_to_ids.get(name, []), path_to_module_keys,
                )
                if resolved:
                    self._add_reverse_entry(reverse_index, resolved, dep.source_id)

    def _detect_dict_dispatch_refs(self, reverse_index):
        """Detect dict dispatch / bare reference patterns for unreferenced symbols.

        For unreferenced symbols, check if their name appears as a bare
        reference (dict value, list element, callback) in a sibling symbol
        in the same file.
        """
        _file_content_cache = {}  # file_key -> [(symbol_id, content)]
        MAX_CONTENT_SCAN = 100_000  # Skip files larger than 100KB total

        for sid, symbol in self.index.symbols.items():
            if sid in reverse_index:
                continue  # Already has callers
            if symbol.symbol_type.value not in ('function', 'method'):
                continue  # Only check functions/methods

            bare_name = symbol.name.split('.')[-1] if '.' in symbol.name else symbol.name
            if not bare_name or len(bare_name) <= 2:
                continue  # Too short, would cause false positives

            file_key = f"{symbol.project}:{symbol.path}"
            if file_key not in _file_content_cache:
                parts = []
                total_size = 0
                for other_id, other_sym in self.index.symbols.items():
                    if other_sym.path == symbol.path and other_sym.project == symbol.project:
                        content = other_sym.content
                        if content:
                            total_size += len(content)
                            if total_size > MAX_CONTENT_SCAN:
                                break
                            parts.append((other_id, content))
                _file_content_cache[file_key] = parts if total_size <= MAX_CONTENT_SCAN else []

            name_pat = re.compile(r'\b' + re.escape(bare_name) + r'\b')
            for other_id, content in _file_content_cache[file_key]:
                if other_id == sid:
                    continue
                if name_pat.search(content):
                    # Found a reference in a sibling symbol
                    if sid not in reverse_index:
                        reverse_index[sid] = []
                    if other_id not in reverse_index[sid]:
                        reverse_index[sid].append(other_id)
                    break  # One caller is enough to mark as referenced

    def _calculate_reference_counts(self, reverse_index, affected_only: set = None):
        """Calculate reference count for each symbol, deduplicating by project path.

        Args:
            reverse_index: The full reverse index dict.
            affected_only: If provided, only recalculate counts for these symbol IDs.
        """
        symbols_to_update = affected_only if affected_only is not None else self.index.symbols.keys()
        for sid in symbols_to_update:
            symbol = self.index.symbols.get(sid)
            if not symbol:
                continue
            callers = reverse_index.get(sid, [])
            # Extract unique file paths (strip project prefix)
            unique_paths = set()
            for caller_id in callers:
                parts = caller_id.split(":", 2)
                if len(parts) >= 2:
                    # Use path:type:name as unique identifier
                    unique_paths.add(":".join(parts[1:]))
            symbol.reference_count = len(unique_paths)
