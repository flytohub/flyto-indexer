"""
Symbol Resolver - Resolves raw call names to actual symbol IDs.

This module provides functionality to:
1. Build an export map from indexed symbols
2. Resolve function/method calls to their symbol IDs using import context
3. Handle various import patterns (relative, alias, package)
4. Multi-language support: Python, JS/TS, Go, Rust, Java
"""

from typing import Optional
import re


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
        - Go packages: github.com/user/pkg
        - Rust crates: crate::module, super::module
        - Java packages: com.example.package
        """
        source_project = self._extract_project(source_file)
        source_lang = self._detect_language(source_file)

        # Language-specific normalization
        normalized_module = self._normalize_module_path(module, source_lang, source_file)

        # Try to find matching symbol
        for sym_id, sym in self.symbols.items():
            sym_path = sym.get("path", "")
            sym_name = sym.get("name", "")
            sym_project = self._extract_project(sym_id)

            # Check if export name matches
            if export_name != sym_name:
                continue

            # Check if module path matches symbol path
            if normalized_module in sym_path:
                return sym_id

            # Check last segment match (filename)
            module_file = module.split("/")[-1].split(".")[-1]  # Handle both / and .
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

    def _normalize_module_path(self, module: str, lang: str, source_file: str) -> str:
        """Normalize module path based on language."""
        if lang == "javascript" or lang == "typescript" or lang == "vue":
            # Handle @/ alias (common in Vue/React projects)
            return module.replace("@/", "src/").replace("@", "src")

        elif lang == "go":
            # Go: github.com/user/pkg -> pkg (last segment)
            # Or internal/pkg -> internal/pkg
            if "/" in module:
                # For external packages, use last segment
                if module.startswith("github.com/") or module.startswith("golang.org/"):
                    return module.split("/")[-1]
                return module
            return module

        elif lang == "rust":
            # Rust: crate::module::submod -> module/submod
            # super::module -> ../module
            # self::module -> ./module
            path = module.replace("::", "/")
            path = re.sub(r'^crate/', '', path)
            path = re.sub(r'^super/', '../', path)
            path = re.sub(r'^self/', './', path)
            return path

        elif lang == "java":
            # Java: com.example.package.Class -> com/example/package/Class
            return module.replace(".", "/")

        return module

    def _detect_language(self, file_path: str) -> str:
        """Detect language from file extension."""
        ext_map = {
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
        for ext, lang in ext_map.items():
            if file_path.endswith(ext):
                return lang
        return "unknown"

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

