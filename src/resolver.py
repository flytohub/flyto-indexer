"""
Symbol Resolver - Resolves raw call names to actual symbol IDs.

This module provides functionality to:
1. Build indexed lookup maps from symbols for O(1) resolution
2. Resolve function/method calls to their symbol IDs using import context
3. Handle various import patterns (relative, alias, package, re-exports)
4. Multi-language support: Python, JS/TS, Go, Rust, Java

Performance: Pre-builds 4 indexes on init for fast resolution:
  - export_map: name → [symbol_ids]
  - path_index: normalized_path_segment → [symbol_ids]
  - basename_index: filename_without_ext → [symbol_ids]
  - method_index: method_name → [symbol_ids with Class.method names]
"""

import os
import re
from collections import defaultdict
from typing import Optional


# Language detection by file extension
_EXT_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}


class SymbolResolver:
    """
    Resolve raw call names to actual symbol IDs using pre-built indexes.

    Example:
        import { useModuleSchema } from '@/composables/useModuleSchema'
        useModuleSchema()  # → resolves to flyto-cloud:src/composables/useModuleSchema.js:composable:useModuleSchema
    """

    def __init__(self, index: dict):
        self.index = index
        self.symbols = index.get("symbols", {})

        # Pre-built indexes for O(1) lookups
        self._export_map: dict[str, list[str]] = defaultdict(list)  # name → [symbol_ids]
        self._path_index: dict[str, list[str]] = defaultdict(list)  # path_segment → [symbol_ids]
        self._basename_index: dict[str, list[str]] = defaultdict(list)  # filename_no_ext → [symbol_ids]
        self._method_index: dict[str, list[str]] = defaultdict(list)  # method_name → [symbol_ids]
        self._project_symbols: dict[str, set[str]] = defaultdict(set)  # project → {symbol_ids}

        self._build_indexes()

    def _build_indexes(self):
        """Build all lookup indexes from symbols in a single pass."""
        for sym_id, sym in self.symbols.items():
            name = sym.get("name", "")
            path = sym.get("path", "")
            exports = sym.get("exports", [])

            # Extract project
            project = sym_id.split(":")[0] if ":" in sym_id else ""
            if project:
                self._project_symbols[project].add(sym_id)

            # 1. Export map: symbol name and explicit exports
            if name:
                if sym_id not in self._export_map[name]:
                    self._export_map[name].append(sym_id)
            for exp in exports:
                if sym_id not in self._export_map[exp]:
                    self._export_map[exp].append(sym_id)

            # 2. Path index: index by multiple path segments for flexible matching
            if path:
                # Full path (e.g., "src/composables/useToast.js")
                self._path_index[path].append(sym_id)

                # Path without extension (e.g., "src/composables/useToast")
                path_no_ext = os.path.splitext(path)[0]
                if path_no_ext != path:
                    self._path_index[path_no_ext].append(sym_id)

                # Normalized for @/ alias (e.g., "src/composables/useToast")
                if path.startswith("src/"):
                    alias_path = path[4:]  # strip "src/"
                    self._path_index[alias_path].append(sym_id)
                    alias_no_ext = os.path.splitext(alias_path)[0]
                    if alias_no_ext != alias_path:
                        self._path_index[alias_no_ext].append(sym_id)

                # 3. Basename index (e.g., "useToast" from "src/composables/useToast.js")
                basename = os.path.splitext(os.path.basename(path))[0]
                if basename:
                    self._basename_index[basename].append(sym_id)

            # 4. Method index: for "Class.method" patterns
            if name and "." in name:
                method_name = name.rsplit(".", 1)[-1]
                self._method_index[method_name].append(sym_id)

    def resolve(
        self,
        module: str,
        export_name: str,
        source_file: str
    ) -> Optional[str]:
        """
        Resolve a module path + export name to symbol ID.

        This is the main entry point for resolution. Uses indexed lookups
        with fallback strategies.

        Args:
            module: Import module path (e.g., "@/composables/useToast", "../utils")
            export_name: The exported name being imported (e.g., "useToast")
            source_file: Source file path for context (project, language)

        Returns:
            Resolved symbol ID, or None if not found
        """
        source_project = self._extract_project(source_file)
        source_lang = self._detect_language(source_file)

        # Strategy 1: Normalized path + name match (fastest, most accurate)
        normalized = self._normalize_module_path(module, source_lang, source_file)
        result = self._resolve_by_path_and_name(normalized, export_name, source_project)
        if result:
            return result

        # Strategy 2: Basename match (handles common short imports)
        module_basename = self._extract_module_basename(module)
        if module_basename:
            result = self._resolve_by_basename(module_basename, export_name, source_project)
            if result:
                return result

        # Strategy 3: Export map with project preference
        result = self._resolve_by_export_map(export_name, source_project)
        if result:
            return result

        return None

    def resolve_method(
        self,
        module: str,
        method_name: str,
        source_file: str
    ) -> Optional[str]:
        """
        Resolve Class.method or module.function pattern.

        Args:
            module: The module path where the object was imported from
            method_name: The method being called (e.g., "topUp" from "useWallet.topUp()")
            source_file: Source file for project context
        """
        source_project = self._extract_project(source_file)

        # Strategy 1: Check method_index (Class.method symbols)
        if method_name in self._method_index:
            candidates = self._method_index[method_name]
            # Prefer same project
            for c in candidates:
                if c.startswith(source_project + ":"):
                    return c
            if candidates:
                return candidates[0]

        # Strategy 2: Check export_map for plain method_name
        return self._resolve_by_export_map(method_name, source_project)

    # -- Internal resolution strategies --

    def _resolve_by_path_and_name(
        self,
        normalized_module: str,
        export_name: str,
        source_project: str,
    ) -> Optional[str]:
        """Resolve using path index + name matching. O(candidates) lookup."""
        # Try path_index with the normalized module path
        candidates = self._path_index.get(normalized_module, [])
        if not candidates:
            # Try stripping common prefixes/suffixes
            for variant in self._path_variants(normalized_module):
                candidates = self._path_index.get(variant, [])
                if candidates:
                    break

        if not candidates:
            return None

        # Filter by export name
        same_project = []
        other_project = []
        for sym_id in candidates:
            sym = self.symbols.get(sym_id, {})
            sym_name = sym.get("name", "")
            if sym_name != export_name:
                continue
            if sym_id.startswith(source_project + ":"):
                same_project.append(sym_id)
            else:
                other_project.append(sym_id)

        return same_project[0] if same_project else (other_project[0] if other_project else None)

    def _resolve_by_basename(
        self,
        basename: str,
        export_name: str,
        source_project: str,
    ) -> Optional[str]:
        """Resolve using basename index. Handles 'import X from ./X' patterns."""
        candidates = self._basename_index.get(basename, [])
        if not candidates:
            return None

        same_project = []
        other_project = []
        for sym_id in candidates:
            sym = self.symbols.get(sym_id, {})
            sym_name = sym.get("name", "")
            if sym_name != export_name:
                continue
            if sym_id.startswith(source_project + ":"):
                same_project.append(sym_id)
            else:
                other_project.append(sym_id)

        return same_project[0] if same_project else (other_project[0] if other_project else None)

    def _resolve_by_export_map(
        self,
        export_name: str,
        source_project: str,
    ) -> Optional[str]:
        """Resolve using export map with project preference. Broadest fallback."""
        candidates = self._export_map.get(export_name, [])
        if not candidates:
            return None

        # Prefer same project
        for c in candidates:
            if c.startswith(source_project + ":"):
                return c
        return candidates[0]

    # -- Path normalization --

    def _normalize_module_path(self, module: str, lang: str, source_file: str) -> str:
        """Normalize module path based on language conventions."""
        if lang in ("javascript", "typescript", "vue"):
            # Handle @/ alias (common in Vue/React projects)
            normalized = module.replace("@/", "src/").replace("@", "src")
            # Strip leading ./
            if normalized.startswith("./"):
                # Resolve relative to source file directory
                source_dir = os.path.dirname(source_file)
                # Remove project prefix from source_dir if present
                if ":" in source_dir:
                    source_dir = source_dir.split(":", 1)[1]
                normalized = os.path.normpath(os.path.join(source_dir, normalized[2:]))
            elif normalized.startswith("../"):
                source_dir = os.path.dirname(source_file)
                if ":" in source_dir:
                    source_dir = source_dir.split(":", 1)[1]
                normalized = os.path.normpath(os.path.join(source_dir, normalized))
            return normalized

        elif lang == "python":
            # Handle Python relative imports
            if module.startswith("."):
                source_dir = os.path.dirname(source_file)
                if ":" in source_dir:
                    source_dir = source_dir.split(":", 1)[1]
                # Count leading dots: . = current dir, .. = parent, etc.
                dots = 0
                for ch in module:
                    if ch == ".":
                        dots += 1
                    else:
                        break
                remainder = module[dots:].replace(".", "/")
                # Go up (dots - 1) directories from source_dir
                base = source_dir
                for _ in range(dots - 1):
                    base = os.path.dirname(base)
                if remainder:
                    return os.path.normpath(os.path.join(base, remainder))
                return base
            # Absolute Python imports: package.module -> package/module
            return module.replace(".", "/")

        elif lang == "go":
            if "/" in module:
                parts = module.split("/")
                if "." in parts[0]:  # External package (e.g., github.com/...)
                    return parts[-1]
                return module
            return module

        elif lang == "rust":
            path = module.replace("::", "/")
            path = re.sub(r'^crate/', '', path)
            path = re.sub(r'^super/', '../', path)
            path = re.sub(r'^self/', './', path)
            return path

        elif lang == "java":
            return module.replace(".", "/")

        return module

    def _path_variants(self, path: str) -> list[str]:
        """Generate path variants for flexible matching."""
        variants = []
        # Without extension
        base, ext = os.path.splitext(path)
        if ext:
            variants.append(base)
        # Common JS/TS extensions
        for try_ext in (".js", ".ts", ".jsx", ".tsx", ".vue", ".py"):
            variants.append(path + try_ext)
            if not ext:
                variants.append(base + try_ext)
        # With /index suffix (e.g., "utils" -> "utils/index")
        variants.append(path + "/index")
        variants.append(path + "/index.js")
        variants.append(path + "/index.ts")
        return variants

    @staticmethod
    def _extract_module_basename(module: str) -> str:
        """Extract the last meaningful segment from a module path."""
        # Remove extension
        base = module.rsplit(".", 1)[0] if "." in module.split("/")[-1] else module
        # Get last segment
        segment = base.rsplit("/", 1)[-1]
        # Handle Go :: separator
        segment = segment.rsplit("::", 1)[-1]
        # Handle Java . separator (already handled by removing extension check)
        return segment

    @staticmethod
    def _detect_language(file_path: str) -> str:
        """Detect language from file extension."""
        for ext, lang in _EXT_LANG_MAP.items():
            if file_path.endswith(ext):
                return lang
        return "unknown"

    @staticmethod
    def _extract_project(path_or_id: str) -> str:
        """Extract project name from path or symbol ID."""
        if ":" in path_or_id:
            return path_or_id.split(":")[0]
        if "/" in path_or_id:
            return path_or_id.split("/")[0]
        return ""

    # -- Backward compatibility aliases --

    def _resolve_module_export(self, module: str, export_name: str, source_file: str) -> Optional[str]:
        """Backward-compatible alias for resolve()."""
        return self.resolve(module, export_name, source_file)

    def _resolve_method(self, module: str, method_name: str, source_file: str) -> Optional[str]:
        """Backward-compatible alias for resolve_method()."""
        return self.resolve_method(module, method_name, source_file)
