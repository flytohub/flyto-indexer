"""
Vue SFC (Single File Component) scanner.

Uses the tokenizer for script block analysis (token-aware comment/string skipping).
"""

import re
from pathlib import Path
from typing import Optional

try:
    from ..models import Dependency, DependencyType, Symbol, SymbolType
    from .base import BaseScanner
    from .tokenizer import strip_comments_and_strings
except ImportError:
    from models import Dependency, DependencyType, Symbol, SymbolType
    from scanner.base import BaseScanner
    from scanner.tokenizer import strip_comments_and_strings

# Standard HTML elements — anything not in this set is a component reference
_HTML_ELEMENTS = frozenset({
    "a", "abbr", "address", "area", "article", "aside", "audio", "b", "base",
    "bdi", "bdo", "blockquote", "body", "br", "button", "canvas", "caption",
    "cite", "code", "col", "colgroup", "data", "datalist", "dd", "del",
    "details", "dfn", "dialog", "div", "dl", "dt", "em", "embed", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
    "h6", "head", "header", "hgroup", "hr", "html", "i", "iframe", "img",
    "input", "ins", "kbd", "label", "legend", "li", "link", "main", "map",
    "mark", "menu", "meta", "meter", "nav", "noscript", "object", "ol",
    "optgroup", "option", "output", "p", "param", "picture", "pre",
    "progress", "q", "rp", "rt", "ruby", "s", "samp", "script", "search",
    "section", "select", "slot", "small", "source", "span", "strong",
    "style", "sub", "summary", "sup", "table", "tbody", "td", "template",
    "textarea", "tfoot", "th", "thead", "time", "title", "tr", "track",
    "u", "ul", "var", "video", "wbr",
    # SVG elements
    "svg", "path", "circle", "rect", "line", "polyline", "polygon", "ellipse",
    "g", "defs", "use", "text", "tspan", "image", "clippath", "mask",
    "pattern", "filter", "lineargradient", "radialgradient", "stop",
    "feblend", "fecolormatrix", "fecomponenttransfer", "fecomposite",
    "feconvolvematrix", "fediffuselighting", "fedisplacementmap", "feflood",
    "fegaussianblur", "feimage", "femerge", "femergenode", "femorphology",
    "feoffset", "fespecularlighting", "fetile", "feturbulence",
    "foreignobject", "animate", "animatetransform", "set",
    # Vue built-in elements
    "component", "transition", "transition-group", "keep-alive",
    "teleport", "suspense",
})


class VueScanner(BaseScanner):
    """
    Vue single-file component scanner

    Extracts:
    - component (entire component)
    - template (template block)
    - From script setup (token-aware):
      - imports
      - composables (use*)
      - refs/reactive
      - computed
      - functions
      - props with types (defineProps)
      - emits (defineEmits)
    - Template component references (PascalCase and kebab-case)
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
        self._extract_block(content, "style")

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

        # Metadata to accumulate
        meta_props = []
        meta_emits = []
        meta_composables = []
        meta_template_refs = []

        if script:
            script_content = script["content"]
            script_start = script["start_line"]

            # Use tokenizer to strip comments/strings for reliable matching
            cleaned = strip_comments_and_strings(script_content, "ts")

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

            # Extract composable calls from cleaned source (use* calls)
            meta_composables = self._extract_composables(cleaned)

            # Extract functions from script (using cleaned source)
            funcs = self._extract_functions_token_aware(script_content, cleaned, script_start)
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

            # Extract calls using cleaned source
            calls = self._extract_calls_token_aware(cleaned, script_start)
            for call in calls:
                dep = Dependency(
                    source_id=comp_symbol.id,
                    target_id=call["name"],
                    dep_type=DependencyType.CALLS,
                    source_line=call["line"],
                    metadata={"raw_call": True},
                )
                dependencies.append(dep)

            # Extract API calls (fetch/axios/etc.)
            api_calls = self._extract_api_calls(script_content, script_start)
            for api_call in api_calls:
                dep = Dependency(
                    source_id=comp_symbol.id,
                    target_id=api_call["url"],
                    dep_type=DependencyType.API_CALLS,
                    source_line=api_call["line"],
                    metadata={
                        "method": api_call["method"],
                        "url": api_call["url"],
                        "raw_url": api_call["raw_url"],
                    },
                )
                dependencies.append(dep)

            # Extract defineProps/defineEmits with types
            props_emits = self._extract_props_emits(script_content, cleaned)
            meta_props = props_emits.get("props", [])
            meta_emits = props_emits.get("emits", [])

            # Try to extract component name from defineComponent({name: ...})
            name_match = re.search(
                r'defineComponent\s*\(\s*\{[^}]*name\s*:\s*[\'"](\w+)[\'"]',
                script_content, re.DOTALL,
            )
            if name_match:
                explicit_name = name_match.group(1)
                comp_symbol.name = explicit_name
                comp_symbol.exports = [explicit_name]

        # Extract component references from template
        if template:
            template_refs_with_lines = self._extract_template_component_refs(template["content"])
            meta_template_refs = list({name for name, _ in template_refs_with_lines})
            for ref_name, ref_line in template_refs_with_lines:
                dep = Dependency(
                    source_id=comp_symbol.id,
                    target_id=ref_name,
                    dep_type=DependencyType.USES,
                    source_line=template["start_line"] + ref_line,
                    metadata={"template_ref": True},
                )
                dependencies.append(dep)

        # Store enriched metadata
        comp_symbol.metadata = {
            "props": meta_props,
            "emits": meta_emits,
            "composables": meta_composables,
            "template_refs": sorted(meta_template_refs),
        }

        # Generate summary
        comp_symbol.summary = self._generate_summary(
            comp_symbol.name, template, script, dependencies
        )

        return symbols, dependencies

    def _extract_composables(self, cleaned: str) -> list[str]:
        """Extract composable calls (use*) from cleaned script source."""
        composables = []
        seen = set()
        for match in re.finditer(r'\b(use[A-Z]\w*)\s*\(', cleaned):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                composables.append(name)
        return composables

    def _extract_template_component_refs(self, template_content: str) -> list[tuple[str, int]]:
        """
        Extract component references from Vue template.

        Detects PascalCase tags like <MyComponent />, <RouterLink>, etc.
        Also detects kebab-case tags that aren't standard HTML elements.
        Returns list of (component_name, relative_line_number).
        """
        refs = []
        seen = set()

        # Match PascalCase component tags: <MyComponent or <MyComponent/>
        # Must start with uppercase to distinguish from HTML elements
        for match in re.finditer(r'<([A-Z][a-zA-Z0-9]+)', template_content):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                line = template_content[:match.start()].count('\n')
                refs.append((name, line))

        # Also match kebab-case component tags: <my-component>
        # (Vue auto-resolves PascalCase imports to kebab-case usage)
        for match in re.finditer(r'<([a-z][\w]*(?:-[\w]+)+)', template_content):
            kebab_name = match.group(1)
            # Skip standard HTML elements
            if kebab_name.lower() in _HTML_ELEMENTS:
                continue
            # Convert kebab-case to PascalCase
            pascal_name = ''.join(word.capitalize() for word in kebab_name.split('-'))
            if pascal_name not in seen:
                seen.add(pascal_name)
                line = template_content[:match.start()].count('\n')
                refs.append((pascal_name, line))

        return refs

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

    def _extract_functions_token_aware(self, script: str, cleaned: str, offset: int) -> list[dict]:
        """Extract function definitions using token-aware cleaned source."""
        functions = []
        lines = script.splitlines()

        # function xxx()
        for match in re.finditer(r'^\s*(async\s+)?function\s+(\w+)\s*\(', cleaned, re.MULTILINE):
            func_name = match.group(2)
            start_line_idx = cleaned[:match.start()].count('\n')
            end_line_idx = self._find_block_end(lines, start_line_idx)
            content = "\n".join(lines[start_line_idx:end_line_idx + 1])
            functions.append({
                "name": func_name,
                "start_line": offset + start_line_idx + 1,
                "end_line": offset + end_line_idx + 1,
                "content": content,
            })

        # const xxx = () => or const xxx = async () =>
        for match in re.finditer(
            r'^\s*const\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>',
            cleaned, re.MULTILINE,
        ):
            func_name = match.group(1)
            start_line_idx = cleaned[:match.start()].count('\n')
            end_line_idx = self._find_block_end(lines, start_line_idx)
            content = "\n".join(lines[start_line_idx:end_line_idx + 1])
            functions.append({
                "name": func_name,
                "start_line": offset + start_line_idx + 1,
                "end_line": offset + end_line_idx + 1,
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

    def _extract_props_emits(self, script: str, cleaned: str) -> dict:
        """Extract defineProps and defineEmits with type information.

        Supports:
        - defineProps<{ name: Type }>()
        - defineProps({ name: { type: String, ... } })
        - defineProps(['prop1', 'prop2'])
        - defineEmits<{ (e: 'event'): void }>()
        - defineEmits(['event1', 'event2'])
        """
        result = {"props": [], "emits": []}

        # --- defineProps ---

        # Type-based: defineProps<{ name: string; count?: number }>()
        match = re.search(r"defineProps\s*<\s*\{([^}]+)\}\s*>", script, re.DOTALL)
        if match:
            props_str = match.group(1)
            for pm in re.finditer(r"(\w+)\s*(\?)?\s*:\s*([^;\n,]+)", props_str):
                prop_name = pm.group(1)
                optional = bool(pm.group(2))
                prop_type = pm.group(3).strip().rstrip(";,")
                entry = {"name": prop_name, "type": prop_type}
                if optional:
                    entry["optional"] = True
                result["props"].append(entry)
        else:
            # Object syntax: defineProps({ name: { type: String, default: '' } })
            match = re.search(r"defineProps\s*\(\s*\{(.+?)\}\s*\)", script, re.DOTALL)
            if match:
                props_body = match.group(1)
                # Match prop: { type: X } or prop: X
                for pm in re.finditer(
                    r"(\w+)\s*:\s*(?:\{[^}]*type\s*:\s*(\w+)[^}]*\}|(\w+))",
                    props_body,
                ):
                    prop_name = pm.group(1)
                    prop_type = pm.group(2) or pm.group(3) or "unknown"
                    result["props"].append({"name": prop_name, "type": prop_type})
            else:
                # Array syntax: defineProps(['prop1', 'prop2'])
                match = re.search(r"defineProps\s*\(\s*\[([^\]]+)\]", script)
                if match:
                    for pm in re.finditer(r"['\"](\w+)['\"]", match.group(1)):
                        result["props"].append({"name": pm.group(1), "type": "any"})

        # --- defineEmits ---

        # Type-based: defineEmits<{ (e: 'update', value: string): void }>()
        match = re.search(r"defineEmits\s*<\s*\{([^}]+)\}\s*>", script, re.DOTALL)
        if match:
            emits_str = match.group(1)
            for em in re.finditer(r"\(e:\s*['\"](\w+)['\"]", emits_str):
                result["emits"].append(em.group(1))
        else:
            # Array syntax: defineEmits(['event1', 'event2'])
            match = re.search(r"defineEmits\s*\(\s*\[([^\]]+)\]", script)
            if match:
                for em in re.finditer(r"['\"](\w+)['\"]", match.group(1)):
                    result["emits"].append(em.group(1))

        return result

    def _extract_calls_token_aware(self, cleaned: str, offset: int) -> list[dict]:
        """
        Extract function calls using token-aware cleaned source.

        Uses cleaned source to avoid false positives from strings/comments.
        """
        calls = []
        seen = set()

        skip_keywords = {
            'if', 'for', 'while', 'switch', 'catch', 'function', 'return',
            'new', 'typeof', 'instanceof', 'delete', 'void', 'throw',
            'async', 'await', 'import', 'export', 'from', 'class',
            'const', 'let', 'var', 'else', 'try', 'finally',
            'defineProps', 'defineEmits', 'defineExpose', 'defineOptions',
            'defineSlots', 'defineModel', 'withDefaults',
        }

        skip_builtins = {
            'console', 'Math', 'JSON', 'Object', 'Array', 'String',
            'Number', 'Boolean', 'Date', 'Promise', 'Error',
        }

        pattern = r'(\b[a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)*)\s*\('

        for match in re.finditer(pattern, cleaned):
            name = match.group(1)
            rel_line = cleaned[:match.start()].count('\n') + 1
            line = offset + rel_line

            first_part = name.split('.')[0]
            if first_part in skip_keywords or first_part in skip_builtins:
                continue

            key = (name, line)
            if key not in seen:
                seen.add(key)
                calls.append({
                    "name": name,
                    "line": line,
                })

        return calls

    # Patterns for detecting frontend HTTP API calls (must contain /api/)
    _API_CALL_PATTERNS = [
        # fetch("/api/..."), useFetch("/api/..."), useAsyncData("/api/..."), $fetch("/api/...")
        re.compile(r'''(?:fetch|useFetch|useAsyncData|\$fetch)\s*\(\s*[`"']([^`"']*?/api/[^`"']*?)[`"']'''),
        # axios.get("/path"), axios.post("/path"), etc. — known HTTP client, any URL
        re.compile(r'''axios\s*\.\s*(get|post|put|delete|patch)\s*\(\s*[`"']([^`"']*?)[`"']''', re.I),
        # api.get("/path"), $api.post("/path"), http.get("/path") — known HTTP client, any URL
        re.compile(r'''(?:\$?api|http|\$http|request)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*[`"']([^`"']*?)[`"']''', re.I),
        # Generic: any string literal with /api/ in a function call context
        re.compile(r'''[`"']([^`"']*?/api/[^`"']*?)[`"']\s*[,)]'''),
    ]

    # Regex to strip query params and normalize template variables
    _TEMPLATE_VAR_RE = re.compile(r'\$\{[^}]*\}')

    @staticmethod
    def _normalize_api_url(url: str) -> str:
        """Normalize API URL: strip query params, replace template vars with *."""
        url = url.split('?')[0]
        url = VueScanner._TEMPLATE_VAR_RE.sub('*', url)
        return url

    def _extract_api_calls(self, script: str, offset: int) -> list[dict]:
        """
        Extract frontend HTTP API calls (fetch, axios, $http, etc.)

        Only detects URLs containing '/api/' to avoid false positives.

        Args:
            script: Script content
            offset: Line offset (script start line)

        Returns list of {method, url, line, raw_url}.
        """
        results = []
        seen: set[str] = set()

        for pattern in self._API_CALL_PATTERNS:
            for match in pattern.finditer(script):
                groups = match.groups()
                if len(groups) == 1:
                    method = "GET"
                    raw_url = groups[0]
                else:
                    method = groups[0].upper()
                    raw_url = groups[1]

                url = self._normalize_api_url(raw_url)
                rel_line = script[:match.start()].count('\n') + 1
                line = offset + rel_line

                # Deduplicate by normalized URL per file
                if url in seen:
                    continue
                seen.add(url)

                results.append({
                    "method": method,
                    "url": url,
                    "line": line,
                    "raw_url": raw_url,
                })

        return results

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
            and not d.metadata.get("template_ref")
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
