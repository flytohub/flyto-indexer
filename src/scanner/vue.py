"""
Vue SFC (Single File Component) scanner.
"""

import re
from pathlib import Path
from typing import Optional

try:
    from .base import BaseScanner
    from ..models import Symbol, Dependency, SymbolType, DependencyType
except ImportError:
    from scanner.base import BaseScanner
    from models import Symbol, Dependency, SymbolType, DependencyType


class VueScanner(BaseScanner):
    """
    Vue single-file component scanner

    Extracts:
    - component (entire component)
    - template (template block)
    - From script setup:
      - imports
      - composables (use*)
      - refs/reactive
      - computed
      - functions
    """

    supported_extensions = [".vue"]

    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """Scan a Vue SFC file"""
        symbols = []
        dependencies = []

        rel_path = str(file_path)
        component_name = file_path.stem

        # Parse SFC blocks
        template = self._extract_block(content, "template")
        script = self._extract_block(content, "script")
        style = self._extract_block(content, "style")

        # Create component symbol
        comp_symbol = Symbol(
            project=self.project,
            path=rel_path,
            symbol_type=SymbolType.COMPONENT,
            name=component_name,
            start_line=1,
            end_line=len(content.splitlines()),
            content=content,
            language="vue",
            exports=[component_name],
        )
        comp_symbol.compute_hash()
        symbols.append(comp_symbol)

        if script:
            script_content = script["content"]
            script_start = script["start_line"]

            # Extract imports
            imports = self._extract_imports(script_content)
            for imp in imports:
                dep = Dependency(
                    source_id=comp_symbol.id,
                    target_id=imp["module"],
                    dep_type=DependencyType.IMPORTS,
                    source_line=script_start + imp["line"],
                    metadata={"names": imp["names"]},
                )
                dependencies.append(dep)

                # Specifically mark composable usage
                for name in imp["names"]:
                    if name.startswith("use"):
                        dep2 = Dependency(
                            source_id=comp_symbol.id,
                            target_id=f"{imp['module']}:composable:{name}",
                            dep_type=DependencyType.USES,
                            source_line=script_start + imp["line"],
                        )
                        dependencies.append(dep2)

            # Extract functions from script
            funcs = self._extract_functions(script_content, script_start)
            for func in funcs:
                func_symbol = Symbol(
                    project=self.project,
                    path=rel_path,
                    symbol_type=SymbolType.FUNCTION,
                    name=func["name"],
                    start_line=func["start_line"],
                    end_line=func["end_line"],
                    content=func["content"],
                    language="typescript",
                )
                func_symbol.compute_hash()
                symbols.append(func_symbol)

            # Extract calls (function invocations)
            calls = self._extract_calls(script_content, script_start)
            for call in calls:
                dep = Dependency(
                    source_id=comp_symbol.id,
                    target_id=call["name"],  # Raw call name, resolved later
                    dep_type=DependencyType.CALLS,
                    source_line=call["line"],
                    metadata={"raw_call": True},
                )
                dependencies.append(dep)

            # Extract defineProps/defineEmits
            props_emits = self._extract_props_emits(script_content)
            comp_symbol.metadata = {
                "props": props_emits.get("props", []),
                "emits": props_emits.get("emits", []),
            }

        # Generate summary
        comp_symbol.summary = self._generate_summary(
            component_name, template, script, dependencies
        )

        return symbols, dependencies

    def _extract_block(self, content: str, block_name: str) -> Optional[dict]:
        """Extract SFC block"""
        pattern = rf'<{block_name}[^>]*>(.*?)</{block_name}>'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            block_content = match.group(1)
            # Compute starting line number
            start_pos = match.start()
            start_line = content[:start_pos].count("\n") + 1
            return {
                "content": block_content,
                "start_line": start_line,
                "end_line": start_line + block_content.count("\n"),
            }
        return None

    def _extract_imports(self, script: str) -> list[dict]:
        """Extract import statements"""
        imports = []
        lines = script.splitlines()

        for i, line in enumerate(lines):
            # import { x, y } from 'module'
            match = re.match(
                r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]",
                line.strip()
            )
            if match:
                names = [n.strip().split(" as ")[0] for n in match.group(1).split(",")]
                imports.append({
                    "module": match.group(2),
                    "names": names,
                    "line": i + 1,
                })
                continue

            # import x from 'module'
            match = re.match(
                r"import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]",
                line.strip()
            )
            if match:
                imports.append({
                    "module": match.group(2),
                    "names": [match.group(1)],
                    "line": i + 1,
                })

        return imports

    def _extract_functions(self, script: str, offset: int) -> list[dict]:
        """Extract function definitions"""
        functions = []
        lines = script.splitlines()

        # Match function xxx() or const xxx = () =>
        # Simplified version, only matches single-line definition starts
        for i, line in enumerate(lines):
            # function xxx()
            match = re.match(r"\s*(async\s+)?function\s+(\w+)\s*\(", line)
            if match:
                func_name = match.group(2)
                # Find function end (simplified: find next same-level })
                end_line = self._find_block_end(lines, i)
                content = "\n".join(lines[i:end_line + 1])
                functions.append({
                    "name": func_name,
                    "start_line": offset + i + 1,
                    "end_line": offset + end_line + 1,
                    "content": content,
                })
                continue

            # const xxx = () => or const xxx = async () =>
            match = re.match(
                r"\s*const\s+(\w+)\s*=\s*(async\s+)?\([^)]*\)\s*=>",
                line
            )
            if match:
                func_name = match.group(1)
                end_line = self._find_block_end(lines, i)
                content = "\n".join(lines[i:end_line + 1])
                functions.append({
                    "name": func_name,
                    "start_line": offset + i + 1,
                    "end_line": offset + end_line + 1,
                    "content": content,
                })

        return functions

    def _find_block_end(self, lines: list[str], start: int) -> int:
        """Find the end line of a code block (based on brace matching)"""
        depth = 0
        started = False

        for i in range(start, len(lines)):
            line = lines[i]
            for char in line:
                if char == "{":
                    depth += 1
                    started = True
                elif char == "}":
                    depth -= 1

            if started and depth == 0:
                return i

        return len(lines) - 1

    def _extract_props_emits(self, script: str) -> dict:
        """Extract defineProps and defineEmits"""
        result = {"props": [], "emits": []}

        # defineProps
        match = re.search(r"defineProps<\{([^}]+)\}>", script, re.DOTALL)
        if match:
            # Simplified parsing: extract property names
            props_str = match.group(1)
            props = re.findall(r"(\w+)\s*[?:]", props_str)
            result["props"] = props

        # defineEmits
        match = re.search(r"defineEmits<\{([^}]+)\}>", script, re.DOTALL)
        if match:
            emits_str = match.group(1)
            emits = re.findall(r"\(e:\s*['\"](\w+)['\"]", emits_str)
            result["emits"] = emits

        return result

    def _extract_calls(self, script: str, offset: int) -> list[dict]:
        """
        Extract function calls

        Args:
            script: Script content
            offset: Line offset (script start line)

        Returns:
            List of dicts with 'name' and 'line' keys
        """
        calls = []
        seen = set()

        # Keywords to skip
        skip_keywords = {
            'if', 'for', 'while', 'switch', 'catch', 'function', 'return',
            'new', 'typeof', 'instanceof', 'delete', 'void', 'throw',
            'async', 'await', 'import', 'export', 'from', 'class',
            'const', 'let', 'var', 'else', 'try', 'finally',
        }

        # Skip common built-ins
        skip_builtins = {
            'console', 'Math', 'JSON', 'Object', 'Array', 'String',
            'Number', 'Boolean', 'Date', 'Promise', 'Error',
        }

        # Match function calls
        pattern = r'(\b[a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)*)\s*\('

        for match in re.finditer(pattern, script):
            name = match.group(1)
            rel_line = script[:match.start()].count('\n') + 1
            line = offset + rel_line

            first_part = name.split('.')[0]
            if first_part in skip_keywords or first_part in skip_builtins:
                continue

            # Skip if inside string (simple check)
            before = script[max(0, match.start()-50):match.start()]
            if before.count('"') % 2 == 1 or before.count("'") % 2 == 1:
                continue

            key = (name, line)
            if key not in seen:
                seen.add(key)
                calls.append({
                    "name": name,
                    "line": line,
                })

        return calls

    def _generate_summary(
        self,
        name: str,
        template: Optional[dict],
        script: Optional[dict],
        dependencies: list[Dependency]
    ) -> str:
        """Generate component summary (for L1)"""
        parts = [f"Vue component: {name}"]

        # Composables in use
        composables = [
            d.target_id.split(":")[-1]
            for d in dependencies
            if d.dep_type == DependencyType.USES
        ]
        if composables:
            parts.append(f"Uses: {', '.join(composables)}")

        # Template characteristics
        if template:
            tmpl = template["content"]
            # Detect router links
            if "router-link" in tmpl or "RouterLink" in tmpl:
                parts.append("Has router links")
            # Detect forms
            if "<form" in tmpl or "v-model" in tmpl:
                parts.append("Has form inputs")
            # Detect API call related patterns
            if "loading" in tmpl.lower():
                parts.append("Has loading state")

        return ". ".join(parts)
