"""
Symbol Resolver - Resolves raw call names to actual symbol IDs.

This module provides functionality to:
1. Build an export map from indexed symbols
2. Resolve function/method calls to their symbol IDs using import context
3. Handle various import patterns (relative, alias, package)
"""

from typing import Optional


class SymbolResolver:
    """
    Resolve raw call names to actual symbol IDs.

    Example:
        import { useModuleSchema } from '@/composables/useModuleSchema'
        useModuleSchema()  # → resolves to flyto-cloud:src/composables/useModuleSchema.js:composable:useModuleSchema
    """

    def __init__(self, index: dict):
        """
        Initialize resolver with index data.

        Args:
            index: The loaded index containing symbols and dependencies
        """
        self.index = index
        self.symbols = index.get("symbols", {})
        self._export_map: dict[str, list[str]] = {}  # name → [symbol_ids]
        self._build_export_map()

    def _build_export_map(self):
        """Build mapping from exported names to their symbol IDs."""
        for sym_id, sym in self.symbols.items():
            name = sym.get("name", "")
            exports = sym.get("exports", [])

            # Add symbol name itself
            if name:
                if name not in self._export_map:
                    self._export_map[name] = []
                if sym_id not in self._export_map[name]:
                    self._export_map[name].append(sym_id)

            # Add all exports
            for exp in exports:
                if exp not in self._export_map:
                    self._export_map[exp] = []
                if sym_id not in self._export_map[exp]:
                    self._export_map[exp].append(sym_id)

    def resolve(
        self,
        call_name: str,
        source_file: str,
        imports: list[dict]
    ) -> Optional[str]:
        """
        Resolve call name to symbol ID using import context.

        Args:
            call_name: The function/method being called (e.g., "useModuleSchema", "store.dispatch")
            source_file: Path of the file making the call (for project context)
            imports: List of imports in that file [{module, names, line}, ...]

        Returns:
            Resolved symbol_id or None if not found
        """
        # Step 1: Check if call_name matches any imported name
        for imp in imports:
            imported_names = imp.get("names", [])
            if call_name in imported_names:
                # Found the import, now resolve the module path
                module = imp.get("module", "")
                resolved = self._resolve_module_export(module, call_name, source_file)
                if resolved:
                    return resolved

        # Step 2: Handle method calls like "obj.method"
        if "." in call_name:
            parts = call_name.split(".")
            obj_name = parts[0]
            method_name = parts[-1]

            # Find what obj_name is imported as
            for imp in imports:
                if obj_name in imp.get("names", []):
                    module = imp.get("module", "")
                    resolved = self._resolve_method(module, method_name, source_file)
                    if resolved:
                        return resolved

        # Step 3: Fallback - try direct match from export_map
        if call_name in self._export_map:
            candidates = self._export_map[call_name]
            if not candidates:
                return None

            # Prefer same project
            source_project = self._extract_project(source_file)
            for c in candidates:
                if c.startswith(source_project + ":"):
                    return c
            return candidates[0]

        return None

    def _resolve_module_export(
        self,
        module: str,
        export_name: str,
        source_file: str
    ) -> Optional[str]:
        """
        Resolve a module path + export name to symbol ID.

        Handles:
        - Relative imports: ./foo, ../utils
        - Alias imports: @/composables/useAuth
        - Package imports: vue, pinia
        """
        source_project = self._extract_project(source_file)

        # Try to find matching symbol
        for sym_id, sym in self.symbols.items():
            sym_path = sym.get("path", "")
            sym_name = sym.get("name", "")
            sym_project = self._extract_project(sym_id)

            # Check if export name matches
            if export_name != sym_name:
                continue

            # Check path similarity
            # Handle @/ alias (common in Vue projects)
            normalized_module = module.replace("@/", "src/").replace("@", "src")

            # Check if module path matches symbol path
            if normalized_module in sym_path:
                return sym_id

            # Check last segment match (filename)
            module_file = module.split("/")[-1]
            path_file = sym_path.split("/")[-1].split(".")[0]
            if module_file == path_file:
                # Prefer same project
                if source_project == sym_project:
                    return sym_id

        # If no exact match, try export_map with project preference
        if export_name in self._export_map:
            candidates = self._export_map[export_name]
            for c in candidates:
                if c.startswith(source_project + ":"):
                    return c
            if candidates:
                return candidates[0]

        return None

    def _resolve_method(
        self,
        module: str,
        method_name: str,
        source_file: str
    ) -> Optional[str]:
        """
        Resolve Class.method or module.function pattern.

        Args:
            module: The module path where the object was imported from
            method_name: The method being called
            source_file: Source file for project context
        """
        source_project = self._extract_project(source_file)

        # Look for method symbols
        for sym_id, sym in self.symbols.items():
            name = sym.get("name", "")
            # Match Class.method pattern
            if name.endswith(f".{method_name}"):
                sym_project = self._extract_project(sym_id)
                # Prefer same project
                if source_project == sym_project:
                    return sym_id

        # Also check plain method_name in export_map
        if method_name in self._export_map:
            candidates = self._export_map[method_name]
            for c in candidates:
                if c.startswith(source_project + ":"):
                    return c
            if candidates:
                return candidates[0]

        return None

    def _extract_project(self, path_or_id: str) -> str:
        """Extract project name from path or symbol ID."""
        if ":" in path_or_id:
            return path_or_id.split(":")[0]
        if "/" in path_or_id:
            return path_or_id.split("/")[0]
        return ""

    def get_all_references(self, symbol_id: str) -> list[dict]:
        """
        Find all places that reference this symbol (by name match).

        This is a supplementary search that looks for the symbol name
        in other symbols' content.

        Args:
            symbol_id: The symbol ID to find references for

        Returns:
            List of reference info dicts
        """
        target_symbol = self.symbols.get(symbol_id)
        if not target_symbol:
            return []

        target_name = target_symbol.get("name", "")
        if not target_name:
            return []

        references = []
        for sym_id, sym in self.symbols.items():
            if sym_id == symbol_id:
                continue

            content = sym.get("content", "")
            # Simple check: does the content contain the target name?
            if target_name in content:
                # Try to find the line number
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if target_name in line:
                        references.append({
                            "symbol_id": sym_id,
                            "path": sym.get("path", ""),
                            "name": sym.get("name", ""),
                            "type": sym.get("type", ""),
                            "line": sym.get("start_line", 0) + i,
                            "context": line.strip()[:100],
                        })
                        break  # Only first occurrence per symbol

        return references
