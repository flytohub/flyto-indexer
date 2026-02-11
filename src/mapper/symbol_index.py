"""
Symbol index — precise location of functions and classes

Enables AI to find:
- "Where is the topUp function?" -> src/composables/useWallet.ts:45
- "PaymentService class" -> src/services/payment.py:12
"""

import ast
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class Symbol:
    """Code symbol (function/class/method)"""
    name: str
    kind: str  # function, class, method, const, interface, type
    file: str
    line: int
    end_line: int = 0
    parent: str = ""  # Parent class (if this is a method)
    params: list[str] = field(default_factory=list)
    returns: str = ""
    docstring: str = ""
    exported: bool = True


class SymbolIndexer:
    """Symbol indexer"""

    def __init__(
        self,
        project_root: Path,
        extensions: list[str] = None,
        ignore_patterns: list[str] = None,
    ):
        self.project_root = project_root
        self.extensions = extensions or [
            ".py", ".ts", ".tsx", ".js", ".jsx", ".vue",
            ".java", ".go",
        ]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".nuxt", ".output", "vendor",
            ".pytest_cache", "coverage", ".next",
        ]

    def _should_skip(self, path: str) -> bool:
        for pattern in self.ignore_patterns:
            if pattern in path:
                return True
        return False

    def extract_python_symbols(self, rel_path: str, content: str) -> list[Symbol]:
        """Extract Python symbols"""
        symbols = []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return symbols

        for node in ast.iter_child_nodes(tree):
            # Class
            if isinstance(node, ast.ClassDef):
                docstring = ast.get_docstring(node) or ""
                symbols.append(Symbol(
                    name=node.name,
                    kind="class",
                    file=rel_path,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    docstring=docstring[:200] if docstring else "",
                    exported=not node.name.startswith("_"),
                ))

                # Class methods
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not item.name.startswith("_") or item.name in ["__init__", "__call__"]:
                            params = [arg.arg for arg in item.args.args if arg.arg != "self"]
                            method_doc = ast.get_docstring(item) or ""
                            symbols.append(Symbol(
                                name=item.name,
                                kind="method",
                                file=rel_path,
                                line=item.lineno,
                                end_line=item.end_lineno or item.lineno,
                                parent=node.name,
                                params=params[:5],
                                docstring=method_doc[:200] if method_doc else "",
                                exported=not item.name.startswith("_"),
                            ))

            # Top-level functions
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    params = [arg.arg for arg in node.args.args]
                    docstring = ast.get_docstring(node) or ""
                    symbols.append(Symbol(
                        name=node.name,
                        kind="function",
                        file=rel_path,
                        line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        params=params[:5],
                        docstring=docstring[:200] if docstring else "",
                        exported=True,
                    ))

        return symbols

    def extract_typescript_symbols(self, rel_path: str, content: str) -> list[Symbol]:
        """Extract TypeScript/JavaScript symbols"""
        symbols = []
        lines = content.split("\n")

        current_class = None
        brace_depth = 0

        for i, line in enumerate(lines):
            line_num = i + 1
            stripped = line.strip()

            # Track brace depth (simplified)
            brace_depth += line.count("{") - line.count("}")

            # export class Name
            class_match = re.match(r'(?:export\s+)?class\s+(\w+)', stripped)
            if class_match:
                current_class = class_match.group(1)
                exported = "export" in stripped
                symbols.append(Symbol(
                    name=current_class,
                    kind="class",
                    file=rel_path,
                    line=line_num,
                    exported=exported,
                ))
                continue

            # export interface Name
            interface_match = re.match(r'(?:export\s+)?interface\s+(\w+)', stripped)
            if interface_match:
                symbols.append(Symbol(
                    name=interface_match.group(1),
                    kind="interface",
                    file=rel_path,
                    line=line_num,
                    exported="export" in stripped,
                ))
                continue

            # export type Name
            type_match = re.match(r'(?:export\s+)?type\s+(\w+)\s*=', stripped)
            if type_match:
                symbols.append(Symbol(
                    name=type_match.group(1),
                    kind="type",
                    file=rel_path,
                    line=line_num,
                    exported="export" in stripped,
                ))
                continue

            # export function name / export async function name
            func_match = re.match(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)', stripped)
            if func_match:
                name = func_match.group(1)
                params = [p.strip().split(":")[0].strip() for p in func_match.group(2).split(",") if p.strip()]
                symbols.append(Symbol(
                    name=name,
                    kind="function",
                    file=rel_path,
                    line=line_num,
                    params=params[:5],
                    exported="export" in stripped,
                ))
                continue

            # export const name = (...) =>
            const_func_match = re.match(r'(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>', stripped)
            if const_func_match:
                name = const_func_match.group(1)
                params = [p.strip().split(":")[0].strip() for p in const_func_match.group(2).split(",") if p.strip()]
                symbols.append(Symbol(
                    name=name,
                    kind="function",
                    file=rel_path,
                    line=line_num,
                    params=params[:5],
                    exported="export" in stripped,
                ))
                continue

            # Class methods (inside a class)
            if current_class and brace_depth > 0:
                method_match = re.match(r'(?:async\s+)?(\w+)\s*\(([^)]*)\)', stripped)
                if method_match and not stripped.startswith(("if", "for", "while", "switch", "//")):
                    name = method_match.group(1)
                    if name not in ["constructor", "if", "for", "while", "switch", "catch"]:
                        params = [p.strip().split(":")[0].strip() for p in method_match.group(2).split(",") if p.strip()]
                        symbols.append(Symbol(
                            name=name,
                            kind="method",
                            file=rel_path,
                            line=line_num,
                            parent=current_class,
                            params=params[:5],
                            exported=True,
                        ))

            # Reset class tracking
            if brace_depth == 0:
                current_class = None

        return symbols

    def extract_vue_symbols(self, rel_path: str, content: str) -> list[Symbol]:
        """Extract Vue symbols"""
        symbols = []

        # Component name
        component_name = Path(rel_path).stem
        symbols.append(Symbol(
            name=component_name,
            kind="component",
            file=rel_path,
            line=1,
            exported=True,
        ))

        # Extract script block
        script_match = re.search(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
        if script_match:
            script_content = script_match.group(1)
            script_start = content[:content.find("<script")].count("\n") + 1

            # Adjust line numbers
            ts_symbols = self.extract_typescript_symbols(rel_path, script_content)
            for sym in ts_symbols:
                sym.line += script_start
                symbols.append(sym)

        return symbols

    def extract_java_symbols(self, rel_path: str, content: str) -> list[Symbol]:
        """Extract Java symbols"""
        symbols = []
        lines = content.split("\n")

        current_class = None

        for i, line in enumerate(lines):
            line_num = i + 1

            # public class Name
            class_match = re.search(r'(?:public\s+)?(?:abstract\s+)?class\s+(\w+)', line)
            if class_match:
                current_class = class_match.group(1)
                symbols.append(Symbol(
                    name=current_class,
                    kind="class",
                    file=rel_path,
                    line=line_num,
                    exported="public" in line,
                ))
                continue

            # public interface Name
            interface_match = re.search(r'(?:public\s+)?interface\s+(\w+)', line)
            if interface_match:
                symbols.append(Symbol(
                    name=interface_match.group(1),
                    kind="interface",
                    file=rel_path,
                    line=line_num,
                    exported="public" in line,
                ))
                continue

            # public method
            method_match = re.search(r'(?:public|protected|private)\s+(?:static\s+)?(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(([^)]*)\)', line)
            if method_match:
                name = method_match.group(1)
                params_str = method_match.group(2)
                params = []
                if params_str.strip():
                    for p in params_str.split(","):
                        parts = p.strip().split()
                        if len(parts) >= 2:
                            params.append(parts[-1])

                # Determine if constructor or method
                kind = "constructor" if current_class and name == current_class else "method"
                if kind == "constructor":
                    continue  # Skip constructors

                symbols.append(Symbol(
                    name=name,
                    kind="method" if current_class else "function",
                    file=rel_path,
                    line=line_num,
                    parent=current_class or "",
                    params=params[:5],
                    exported="public" in line,
                ))

        return symbols

    def extract_go_symbols(self, rel_path: str, content: str) -> list[Symbol]:
        """Extract Go symbols"""
        symbols = []
        lines = content.split("\n")

        for i, line in enumerate(lines):
            line_num = i + 1

            # type Name struct/interface
            type_match = re.search(r'type\s+(\w+)\s+(struct|interface)', line)
            if type_match:
                name = type_match.group(1)
                kind = "class" if type_match.group(2) == "struct" else "interface"
                symbols.append(Symbol(
                    name=name,
                    kind=kind,
                    file=rel_path,
                    line=line_num,
                    exported=name[0].isupper(),
                ))
                continue

            # func (receiver) Name(params)
            method_match = re.search(r'func\s+\((\w+)\s+\*?(\w+)\)\s+(\w+)\s*\(([^)]*)\)', line)
            if method_match:
                receiver_type = method_match.group(2)
                name = method_match.group(3)
                params_str = method_match.group(4)
                params = [p.strip().split()[0] for p in params_str.split(",") if p.strip()]

                symbols.append(Symbol(
                    name=name,
                    kind="method",
                    file=rel_path,
                    line=line_num,
                    parent=receiver_type,
                    params=params[:5],
                    exported=name[0].isupper(),
                ))
                continue

            # func Name(params)
            func_match = re.search(r'func\s+(\w+)\s*\(([^)]*)\)', line)
            if func_match:
                name = func_match.group(1)
                params_str = func_match.group(2)
                params = [p.strip().split()[0] for p in params_str.split(",") if p.strip()]

                symbols.append(Symbol(
                    name=name,
                    kind="function",
                    file=rel_path,
                    line=line_num,
                    params=params[:5],
                    exported=name[0].isupper(),
                ))

        return symbols

    def index_file(self, rel_path: str) -> list[Symbol]:
        """Index a single file"""
        full_path = self.project_root / rel_path

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:
            return []

        ext = Path(rel_path).suffix

        if ext == ".py":
            return self.extract_python_symbols(rel_path, content)
        elif ext in [".ts", ".tsx", ".js", ".jsx"]:
            return self.extract_typescript_symbols(rel_path, content)
        elif ext == ".vue":
            return self.extract_vue_symbols(rel_path, content)
        elif ext == ".java":
            return self.extract_java_symbols(rel_path, content)
        elif ext == ".go":
            return self.extract_go_symbols(rel_path, content)
        else:
            return []

    def build_index(self) -> dict:
        """Build complete index"""
        all_symbols = []

        # Scan all files
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))

                if self._should_skip(rel_path):
                    continue

                symbols = self.index_file(rel_path)
                all_symbols.extend(symbols)

        # Build index structure
        index = {
            "project": self.project_root.name,
            "total_symbols": len(all_symbols),
            "symbols": {},  # name -> [locations]
            "classes": {},  # class_name -> {methods, file, line}
            "functions": {},  # func_name -> [{file, line, params}]
            "by_file": {},  # file -> [symbols]
        }

        for sym in all_symbols:
            # Index by name
            if sym.name not in index["symbols"]:
                index["symbols"][sym.name] = []
            index["symbols"][sym.name].append({
                "file": sym.file,
                "line": sym.line,
                "kind": sym.kind,
                "parent": sym.parent,
            })

            # Index by file
            if sym.file not in index["by_file"]:
                index["by_file"][sym.file] = []
            index["by_file"][sym.file].append({
                "name": sym.name,
                "kind": sym.kind,
                "line": sym.line,
                "parent": sym.parent,
                "params": sym.params,
            })

            # Class index
            if sym.kind == "class":
                index["classes"][sym.name] = {
                    "file": sym.file,
                    "line": sym.line,
                    "methods": [],
                }
            elif sym.kind == "method" and sym.parent:
                if sym.parent in index["classes"]:
                    index["classes"][sym.parent]["methods"].append({
                        "name": sym.name,
                        "line": sym.line,
                        "params": sym.params,
                    })

            # Function index
            if sym.kind == "function":
                if sym.name not in index["functions"]:
                    index["functions"][sym.name] = []
                index["functions"][sym.name].append({
                    "file": sym.file,
                    "line": sym.line,
                    "params": sym.params,
                })

        return index

    def search(self, index: dict, query: str, limit: int = 10) -> list[dict]:
        """Search for a symbol"""
        query_lower = query.lower()
        results = []

        for name, locations in index.get("symbols", {}).items():
            if query_lower in name.lower():
                for loc in locations:
                    score = 3 if name.lower() == query_lower else 1
                    if name.lower().startswith(query_lower):
                        score += 1

                    results.append({
                        "name": name,
                        "kind": loc["kind"],
                        "file": loc["file"],
                        "line": loc["line"],
                        "parent": loc.get("parent", ""),
                        "score": score,
                    })

        results.sort(key=lambda x: (-x["score"], x["name"]))
        return results[:limit]


def build_symbol_index(project_path: Path, output_path: Path = None) -> dict:
    """Convenience function: build symbol index"""
    indexer = SymbolIndexer(project_path)
    index = indexer.build_index()

    if output_path:
        output_path.write_text(json.dumps(index, indent=2, ensure_ascii=False))

    return index


def search_symbol(project_path: Path, query: str, limit: int = 10) -> list[dict]:
    """Convenience function: search for a symbol"""
    index_file = project_path / ".flyto-index" / "SYMBOL_INDEX.json"

    if index_file.exists():
        index = json.loads(index_file.read_text())
    else:
        indexer = SymbolIndexer(project_path)
        index = indexer.build_index()

    indexer = SymbolIndexer(project_path)
    return indexer.search(index, query, limit)
