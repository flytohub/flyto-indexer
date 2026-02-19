"""
Semantic Diff — Signature change detection for indexed symbols.

Detects whether a code change is a breaking signature change (params added/removed/renamed)
or just a safe body edit. Used by diff_impact.py to classify change risk.

Supports: Python (AST-based), JS/TS/Go/Rust/Java (regex-based).
"""

import ast
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class ChangeKind(str, Enum):
    SIGNATURE_CHANGE = "signature_change"  # params changed
    RENAME = "rename"                       # function name changed
    RETURN_TYPE_CHANGE = "return_type_change"
    BODY_CHANGE = "body_change"             # safe — only body changed
    ADDED = "added"
    DELETED = "deleted"


@dataclass
class Signature:
    name: str
    params: List[str] = field(default_factory=list)       # param names
    param_types: List[str] = field(default_factory=list)   # type annotations
    return_type: Optional[str] = None
    is_async: bool = False


# ---------------------------------------------------------------------------
# Python: AST-based extraction (most accurate)
# ---------------------------------------------------------------------------

def _extract_python_signatures(content: str) -> List[Signature]:
    """Extract function/method signatures from Python source using AST."""
    signatures = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return signatures

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        params = []
        param_types = []

        for arg in node.args.args:
            name = arg.arg
            if name == "self" or name == "cls":
                continue
            params.append(name)
            if arg.annotation:
                param_types.append(ast.dump(arg.annotation))
            else:
                param_types.append("")

        # *args
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
            param_types.append("")

        # **kwargs
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")
            param_types.append("")

        # keyword-only args
        for arg in node.args.kwonlyargs:
            params.append(arg.arg)
            if arg.annotation:
                param_types.append(ast.dump(arg.annotation))
            else:
                param_types.append("")

        return_type = None
        if node.returns:
            return_type = ast.dump(node.returns)

        signatures.append(Signature(
            name=node.name,
            params=params,
            param_types=param_types,
            return_type=return_type,
            is_async=isinstance(node, ast.AsyncFunctionDef),
        ))

    return signatures


# ---------------------------------------------------------------------------
# JS/TS: regex-based extraction
# ---------------------------------------------------------------------------

_JS_FUNC_PATTERN = re.compile(
    r'(?:export\s+)?'
    r'(?P<async>async\s+)?'
    r'(?:function\s+(?P<fname>\w+)|'      # function foo(
    r'(?:const|let|var)\s+(?P<vname>\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])*=>|'  # const foo = (...) =>
    r'(?P<mname>\w+)\s*\()'               # method(
    r'\s*\((?P<params>[^)]*)\)'
    r'(?:\s*:\s*(?P<rtype>[^{=]+?))?'
    r'\s*[{=]',
    re.MULTILINE,
)

_JS_SIMPLE_FUNC = re.compile(
    r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)(?:\s*:\s*([^{]+?))?\s*\{',
    re.MULTILINE,
)

_JS_ARROW = re.compile(
    r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)(?:\s*:\s*([^=]+?))?\s*=>',
    re.MULTILINE,
)

_JS_METHOD = re.compile(
    r'(?:async\s+)?(\w+)\s*\(([^)]*)\)(?:\s*:\s*([^{]+?))?\s*\{',
    re.MULTILINE,
)


def _extract_js_signatures(content: str) -> List[Signature]:
    """Extract function signatures from JS/TS source using regex."""
    signatures = []
    seen_names = set()

    for pattern in [_JS_SIMPLE_FUNC, _JS_ARROW, _JS_METHOD]:
        for m in pattern.finditer(content):
            name = m.group(1)
            if not name or name in seen_names:
                continue
            # Skip control flow keywords
            if name in ("if", "for", "while", "switch", "catch", "return", "class", "new"):
                continue
            seen_names.add(name)

            raw_params = m.group(2).strip()
            params = []
            param_types = []
            if raw_params:
                for p in raw_params.split(","):
                    p = p.strip()
                    if not p:
                        continue
                    # Handle destructured params like { a, b }: Type
                    if p.startswith("{") or p.startswith("["):
                        params.append(p.split("}")[0] + "}" if "{" in p else p.split("]")[0] + "]")
                        param_types.append("")
                        continue
                    # Handle typed params: name: type = default
                    parts = p.split(":", 1)
                    pname = parts[0].strip().lstrip(".")  # handle ...rest
                    params.append(pname)
                    if len(parts) > 1:
                        ptype = parts[1].split("=")[0].strip()
                        param_types.append(ptype)
                    else:
                        param_types.append("")

            return_type = m.group(3).strip() if m.group(3) else None

            is_async = "async" in content[max(0, m.start() - 20):m.start() + 10]
            signatures.append(Signature(
                name=name,
                params=params,
                param_types=param_types,
                return_type=return_type,
                is_async=is_async,
            ))

    return signatures


# ---------------------------------------------------------------------------
# Go: regex-based extraction
# ---------------------------------------------------------------------------

_GO_FUNC = re.compile(
    r'func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(([^)]*)\)(?:\s*\(([^)]*)\)|\s*(\w[^\s{]*))?',
    re.MULTILINE,
)


def _extract_go_signatures(content: str) -> List[Signature]:
    signatures = []
    for m in _GO_FUNC.finditer(content):
        name = m.group(1)
        raw_params = m.group(2).strip()
        params = []
        param_types = []
        if raw_params:
            for p in raw_params.split(","):
                p = p.strip()
                if not p:
                    continue
                parts = p.rsplit(" ", 1)
                params.append(parts[0].strip())
                param_types.append(parts[1].strip() if len(parts) > 1 else "")

        return_type = (m.group(3) or m.group(4) or "").strip() or None

        signatures.append(Signature(
            name=name,
            params=params,
            param_types=param_types,
            return_type=return_type,
        ))
    return signatures


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_signatures(content: str, language: str) -> List[Signature]:
    """Extract function signatures from source code.

    Args:
        content: Source code string
        language: "python", "javascript", "typescript", "go"
    """
    lang = language.lower()
    if lang in ("python", "py"):
        return _extract_python_signatures(content)
    elif lang in ("javascript", "typescript", "js", "ts", "jsx", "tsx"):
        return _extract_js_signatures(content)
    elif lang in ("go", "golang"):
        return _extract_go_signatures(content)
    # Unsupported language — return empty (body_change assumed)
    return []


def compare_signatures(old: Signature, new: Signature) -> ChangeKind:
    """Compare two signatures and classify the change.

    Returns the most severe ChangeKind detected.
    """
    if old.name != new.name:
        return ChangeKind.RENAME

    if old.params != new.params or old.param_types != new.param_types:
        return ChangeKind.SIGNATURE_CHANGE

    if old.return_type != new.return_type:
        return ChangeKind.RETURN_TYPE_CHANGE

    return ChangeKind.BODY_CHANGE


def _detect_language(path: str) -> str:
    """Detect language from file extension."""
    if path.endswith(".py"):
        return "python"
    elif path.endswith((".js", ".jsx", ".mjs")):
        return "javascript"
    elif path.endswith((".ts", ".tsx", ".mts")):
        return "typescript"
    elif path.endswith((".vue",)):
        return "javascript"
    elif path.endswith(".go"):
        return "go"
    return ""


def classify_symbol_change(
    symbol_name: str,
    old_content: str,
    new_content: str,
    path: str,
) -> ChangeKind:
    """Classify how a symbol changed by comparing old vs new content.

    Args:
        symbol_name: Name of the function/class
        old_content: Previous source code of the symbol
        new_content: Current source code of the symbol
        path: File path (used to detect language)
    """
    if not old_content and new_content:
        return ChangeKind.ADDED
    if old_content and not new_content:
        return ChangeKind.DELETED
    if old_content == new_content:
        return ChangeKind.BODY_CHANGE

    language = _detect_language(path)
    if not language:
        return ChangeKind.BODY_CHANGE

    old_sigs = extract_signatures(old_content, language)
    new_sigs = extract_signatures(new_content, language)

    # Find matching signature by name
    old_sig = None
    new_sig = None
    for s in old_sigs:
        if s.name == symbol_name:
            old_sig = s
            break
    for s in new_sigs:
        if s.name == symbol_name:
            new_sig = s
            break

    if old_sig and new_sig:
        return compare_signatures(old_sig, new_sig)

    # If name not found in new sigs, check if renamed
    if old_sig and not new_sig and new_sigs:
        return ChangeKind.RENAME

    return ChangeKind.BODY_CHANGE
