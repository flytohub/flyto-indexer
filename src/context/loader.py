"""
Progressive Context Loading

L0: Project outline (directory tree + one line per file) -> a few hundred to ~1000 tokens
L1: File summary (exports/imports/main features) -> detailed info for matched files
L2: Code snippets (only the needed chunks) -> actual code to examine

Usage flow:
1. AI reads L0 first, decides which files to look at
2. Read L1 (only for candidate files)
3. Read L2 (only necessary snippets)
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from ..models import ProjectIndex, Symbol, SymbolType
except ImportError:
    from models import ProjectIndex, Symbol, SymbolType


@dataclass
class L0Context:
    """L0 Project outline"""
    project: str
    tree: str              # Directory tree (text format)
    file_map: dict         # {path: one_line_summary}
    entry_points: list     # Entry points
    routes: dict           # Route table
    api_endpoints: list    # API list

    def to_text(self, max_files: int = 100) -> str:
        """Convert to text (for AI consumption)"""
        lines = [
            f"# Project: {self.project}",
            "",
            "## Directory Structure",
            "```",
            self.tree,
            "```",
            "",
            "## File Map",
        ]

        # Display grouped by directory
        sorted_files = sorted(self.file_map.items())[:max_files]
        current_dir = ""
        for path, summary in sorted_files:
            dir_name = str(Path(path).parent)
            if dir_name != current_dir:
                current_dir = dir_name
                lines.append(f"\n### {dir_name}/")
            file_name = Path(path).name
            lines.append(f"- `{file_name}`: {summary}")

        if self.entry_points:
            lines.extend([
                "",
                "## Entry Points",
                *[f"- {e}" for e in self.entry_points],
            ])

        if self.routes:
            lines.extend([
                "",
                "## Routes",
                *[f"- `{path}` → {comp}" for path, comp in list(self.routes.items())[:20]],
            ])

        if self.api_endpoints:
            lines.extend([
                "",
                "## API Endpoints",
                *[f"- `{e['method']} {e['path']}`: {e.get('summary', '')}" for e in self.api_endpoints[:20]],
            ])

        return "\n".join(lines)

    def token_estimate(self) -> int:
        """Estimate token count"""
        text = self.to_text()
        return len(text) // 4  # Rough estimate


@dataclass
class L1Context:
    """L1 File summary"""
    path: str
    language: str
    summary: str
    imports: list[str]
    exports: list[str]
    symbols: list[dict]    # [{name, type, summary, line}]
    dependencies: list[str]  # Dependencies on other files

    def to_text(self) -> str:
        lines = [
            f"# File: {self.path}",
            f"Language: {self.language}",
            "",
            "## Summary",
            self.summary,
            "",
        ]

        if self.imports:
            lines.extend([
                "## Imports",
                *[f"- {i}" for i in self.imports[:20]],
                "",
            ])

        if self.exports:
            lines.extend([
                "## Exports",
                *[f"- {e}" for e in self.exports],
                "",
            ])

        if self.symbols:
            lines.extend([
                "## Symbols",
            ])
            for s in self.symbols:
                line_info = f" (L{s['line']})" if s.get('line') else ""
                summary = f": {s['summary']}" if s.get('summary') else ""
                lines.append(f"- `{s['type']}` **{s['name']}**{line_info}{summary}")
            lines.append("")

        if self.dependencies:
            lines.extend([
                "## Dependencies",
                *[f"- {d}" for d in self.dependencies],
            ])

        return "\n".join(lines)


@dataclass
class L2Context:
    """L2 Code snippets"""
    symbol_id: str
    path: str
    name: str
    symbol_type: str
    start_line: int
    end_line: int
    content: str

    def to_text(self) -> str:
        return f"""# {self.symbol_id}
File: {self.path}
Lines: {self.start_line}-{self.end_line}

```
{self.content}
```"""


class ContextLoader:
    """
    Context loader

    Implements progressive loading strategy
    """

    def __init__(self, index: ProjectIndex):
        self.index = index

    def load_l0(self) -> L0Context:
        """
        Load L0 outline

        This is the lightest level, letting AI locate which files to examine
        """
        # Generate directory tree
        tree = self._generate_tree()

        # Generate file map (one line per file)
        file_map = {}
        for path, _manifest in self.index.files.items():
            # Find the main symbol for this file
            main_symbol = self._find_main_symbol(path)
            if main_symbol:
                file_map[path] = main_symbol.summary or f"{main_symbol.symbol_type.value}: {main_symbol.name}"
            else:
                file_map[path] = self._infer_file_purpose(path)

        return L0Context(
            project=self.index.project,
            tree=tree,
            file_map=file_map,
            entry_points=self.index.entry_points,
            routes=self.index.routes,
            api_endpoints=self.index.api_endpoints,
        )

    def load_l1(self, path: str) -> Optional[L1Context]:
        """
        Load L1 file summary

        Only loaded when the file is confirmed to be needed
        """
        if path not in self.index.files:
            return None

        manifest = self.index.files[path]

        # Collect symbols for this file
        symbols = []
        imports = []
        exports = []
        main_summary = ""

        for symbol_id in manifest.symbols:
            if symbol_id in self.index.symbols:
                symbol = self.index.symbols[symbol_id]
                symbols.append({
                    "name": symbol.name,
                    "type": symbol.symbol_type.value,
                    "summary": symbol.summary,
                    "line": symbol.start_line,
                })
                imports.extend(symbol.imports)
                exports.extend(symbol.exports)

                # Use main symbol's summary as file summary
                if symbol.symbol_type in (SymbolType.COMPONENT, SymbolType.CLASS):
                    main_summary = symbol.summary

        # Collect dependencies
        dependencies = []
        for dep in self.index.dependencies.values():
            if dep.source_id.startswith(f"{self.index.project}:{path}:"):
                dependencies.append(dep.target_id)

        # Infer language
        ext = Path(path).suffix
        lang_map = {".py": "python", ".vue": "vue", ".ts": "typescript", ".js": "javascript"}
        language = lang_map.get(ext, ext[1:])

        return L1Context(
            path=path,
            language=language,
            summary=main_summary or self._infer_file_purpose(path),
            imports=list(set(imports)),
            exports=list(set(exports)),
            symbols=symbols,
            dependencies=list(set(dependencies)),
        )

    def load_l2(self, symbol_id: str) -> Optional[L2Context]:
        """
        Load L2 code snippets

        Only loaded when code actually needs to be examined
        """
        if symbol_id not in self.index.symbols:
            return None

        symbol = self.index.symbols[symbol_id]

        return L2Context(
            symbol_id=symbol_id,
            path=symbol.path,
            name=symbol.name,
            symbol_type=symbol.symbol_type.value,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            content=symbol.content,
        )

    def load_l2_by_query(self, query: str, top_k: int = 5) -> list[L2Context]:
        """
        Load relevant L2 snippets based on query

        Uses vector search (requires external vector store)
        """
        # TODO: Integrate vector search
        # Currently using simple keyword matching
        results = []
        query_lower = query.lower()

        for symbol_id, symbol in self.index.symbols.items():
            if symbol.symbol_type == SymbolType.FILE:
                continue

            score = 0
            # Name match
            if query_lower in symbol.name.lower():
                score += 10
            # Summary match
            if symbol.summary and query_lower in symbol.summary.lower():
                score += 5
            # Content match
            if query_lower in symbol.content.lower():
                score += 1

            if score > 0:
                results.append((score, symbol_id))

        # Sort and take top_k
        results.sort(reverse=True)
        return [self.load_l2(sid) for _, sid in results[:top_k]]

    def _generate_tree(self, max_depth: int = 3) -> str:
        """Generate directory tree"""
        paths = sorted(self.index.files.keys())
        if not paths:
            return "(empty)"

        # Simplified: only show up to specified depth
        tree_lines = []
        seen_dirs = set()

        for path in paths:
            parts = Path(path).parts[:max_depth]
            for i, part in enumerate(parts):
                dir_path = "/".join(parts[:i + 1])
                if dir_path not in seen_dirs:
                    seen_dirs.add(dir_path)
                    indent = "  " * i
                    tree_lines.append(f"{indent}{part}/")

        return "\n".join(tree_lines[:50])  # Limit line count

    def _find_main_symbol(self, path: str) -> Optional[Symbol]:
        """Find the main symbol of a file (component/class)"""
        for symbol in self.index.symbols.values():
            if symbol.path == path and symbol.symbol_type in (SymbolType.COMPONENT, SymbolType.CLASS):
                return symbol
        return None

    def _infer_file_purpose(self, path: str) -> str:
        """Infer file purpose from path"""
        path_lower = path.lower()

        if "test" in path_lower:
            return "Test file"
        if "composable" in path_lower or path_lower.startswith("use"):
            return "Vue composable"
        if "store" in path_lower:
            return "State store"
        if "api" in path_lower or "service" in path_lower:
            return "API service"
        if "component" in path_lower:
            return "UI component"
        if "page" in path_lower or "view" in path_lower:
            return "Page view"
        if "util" in path_lower or "helper" in path_lower:
            return "Utility functions"
        if "config" in path_lower:
            return "Configuration"
        if "router" in path_lower:
            return "Router definition"
        if "model" in path_lower:
            return "Data model"

        return Path(path).stem
