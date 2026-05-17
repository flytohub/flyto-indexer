"""Regex-based per-package call site + local call graph extractor.

Mirrors the engine-side Go collector so flyto-engine and flyto-indexer
stay in sync on what counts as a "call". This Python copy lives here
because:

  1. Customers running the indexer standalone (e.g. CLI users without
     flyto-engine) get the same Layer-3 data the engine would.
  2. The engine prefers the indexer's output via subprocess when
     available — the indexer can layer LSP-resolved edges on top, and
     the engine inherits them for free.

Limitations are the same as the Go side: no AST, no scope tracking,
dynamic dispatch invisible. See `call_sites_lsp.py` for the upgrade.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set


_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go"}
_SKIP_DIRS = {"node_modules", "vendor", "dist", ".git", "build", "target", "__pycache__", ".venv"}
_MAX_FILE_BYTES = 256 * 1024


# ── Per-language patterns ─────────────────────────────────────────────

# Python.
_PY_IMPORT_AS = re.compile(r"(?m)^\s*import\s+([a-zA-Z_][a-zA-Z0-9_.]+)(?:\s+as\s+([a-zA-Z_][a-zA-Z0-9_]*))?")
_PY_FROM_IMPORT = re.compile(r"(?m)^\s*from\s+([a-zA-Z_][a-zA-Z0-9_.]+)\s+import\s+(.+)$")
_PY_ATTR = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)((?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)")
_PY_DEF = re.compile(r"(?m)^([ \t]*)(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
_PY_CALL = re.compile(r"(?:^|[^.\w])([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")

# Reflection patterns (mirror engine).
_REFLECTION_PATTERNS = {
    ".py": [
        re.compile(r"\bgetattr\s*\(\s*[^,)]+\s*,\s*[a-zA-Z_]"),
        re.compile(r"\bsetattr\s*\(\s*[^,)]+\s*,\s*[a-zA-Z_]"),
        re.compile(r"\beval\s*\("),
        re.compile(r"\bexec\s*\("),
        re.compile(r"\b__import__\s*\("),
        re.compile(r"\bimportlib\.import_module\s*\("),
        re.compile(r"\bglobals\s*\(\s*\)\s*\["),
        re.compile(r"\blocals\s*\(\s*\)\s*\["),
    ],
    ".js": [
        re.compile(r"\beval\s*\("),
        re.compile(r"\bnew\s+Function\s*\("),
        re.compile(r"\bFunction\s*\(\s*['\"]"),
    ],
    ".go": [
        re.compile(r"\breflect\.[A-Z]"),
        re.compile(r"\bunsafe\.[A-Z]"),
    ],
}
for _alias in (".ts", ".jsx", ".tsx"):
    _REFLECTION_PATTERNS[_alias] = _REFLECTION_PATTERNS[".js"]


_NOISE_NAMES = {
    "self", "this", "cls", "super", "len", "str", "int", "float", "bool",
    "list", "dict", "set", "tuple", "type", "print", "range", "enumerate",
    "map", "filter", "open", "input", "any", "all", "abs", "min", "max",
    "True", "False", "None", "true", "false", "null", "undefined",
}
_KEYWORDS = {
    "if", "else", "for", "while", "return", "break", "continue", "import",
    "from", "as", "def", "class", "lambda", "yield", "with", "try", "except",
    "finally", "raise", "pass", "and", "or", "not", "in", "is", "global",
    "nonlocal", "func", "var", "const", "let", "switch", "case", "default",
    "go", "select", "chan", "map", "interface", "struct", "package",
    "function", "async", "await", "new", "this", "throw", "catch",
}


def _walk(root: Path):
    # Symlink defense: rglob follows symlinks by default. flyto-indexer
    # is run against arbitrary user paths from the CLI; a symlink to
    # /etc or ~/.ssh would otherwise be scanned. Skip any path whose
    # resolved form escapes the root (or whose immediate path is a
    # symlink). See profile/filesystem.py for the os.walk mirror.
    try:
        root_resolved = root.resolve()
    except OSError:
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.is_symlink():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(root_resolved)  # raises if escapes root
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in _CODE_EXTS:
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


def _collect_py_calls(src: str, known: Set[str], func_calls: Dict[str, Set[str]]) -> Dict[str, str]:
    """Extract attribute chains rooted at known packages. Returns a
    binding map (local_name → qualified_name) for use by the local
    call graph extractor."""
    aliases: Dict[str, str] = {}
    bindings: Dict[str, str] = {}

    for m in _PY_IMPORT_AS.finditer(src):
        pkg = m.group(1).split(".")[0]
        if pkg not in known:
            continue
        local = m.group(2) or pkg
        aliases[local] = pkg

    for m in _PY_FROM_IMPORT.finditer(src):
        full_origin = m.group(1)  # may be a dotted submodule path
        pkg = full_origin.split(".")[0]
        if pkg not in known:
            continue
        body = m.group(2).strip().lstrip("(").rstrip(")")
        for item in body.split(","):
            item = item.strip()
            if not item or item == "*":
                continue
            parts = item.split()
            real = parts[0]
            local = parts[2] if len(parts) >= 3 and parts[1] == "as" else real
            # Preserve the FULL origin path (`myapp.utils.MyDecomp`)
            # so the Layer-4 reexport map can resolve through
            # submodule chains. Top-level pkg is used only for
            # function_calls bucket key (pre-expansion).
            bindings[local] = f"{full_origin}.{real}"
            func_calls.setdefault(pkg, set()).add(f"{full_origin}.{real}")

    for m in _PY_ATTR.finditer(src):
        head = m.group(1)
        tail = m.group(2).lstrip(".")
        real = aliases.get(head)
        if real:
            func_calls.setdefault(real, set()).add(f"{real}.{tail}")
            continue
        if head in bindings:
            fqn_with_tail = f"{bindings[head]}.{tail}"
            top_pkg = fqn_with_tail.split(".", 1)[0]
            func_calls.setdefault(top_pkg, set()).add(fqn_with_tail)

    return {**aliases, **bindings}


def _collect_py_call_graph(rel_path: str, src: str, graph: Dict[str, Set[str]],
                           file_bindings: Dict[str, str] | None = None) -> None:
    """Build per-function callee map for Python.

    `file_bindings` (optional) carries the file's import-binding map
    so attribute chains rooted at an imported name (`MyDecomp.foo`)
    can be expanded to the import-origin form
    (`myapp.utils.MyDecomp.foo`). Without expansion the global
    reexport-map pass can't recognise the chain.
    """
    matches = list(_PY_DEF.finditer(src))
    if not matches:
        return
    for i, m in enumerate(matches):
        indent = m.group(1)
        name = m.group(2)
        body_start = src.find("\n", m.start())
        if body_start < 0:
            continue
        body_start += 1
        body_end = len(src)
        for j in range(i + 1, len(matches)):
            other = matches[j]
            if len(other.group(1)) <= len(indent):
                body_end = other.start()
                break
        body = _clamp_to_dedent(src, body_start, body_end, indent)
        fqn = f"{rel_path}:{name}"
        callees = graph.setdefault(fqn, set())
        _add_callees_from_body(body, callees, _PY_ATTR, _PY_CALL, file_bindings)


def _clamp_to_dedent(src: str, start: int, end: int, indent: str) -> str:
    """Trim Python body extent at first dedented non-blank line."""
    lines = src[start:end].splitlines(keepends=True)
    out: List[str] = []
    for line in lines:
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        leading = line[: len(line) - len(stripped)]
        if len(leading) <= len(indent):
            break
        out.append(line)
    return "".join(out)


def _add_callees_from_body(body: str, out: Set[str], attr_re, call_re,
                           bindings: Dict[str, str] | None = None) -> None:
    """Add callees from the body. When `bindings` is provided, rewrite
    attribute-chain heads through it so a chain like `MyDecomp.decompress`
    is recorded as both the raw form (`MyDecomp.decompress`) AND the
    binding-origin form (`myapp.utils.MyDecomp.decompress`). Layer-3's
    intersection then matches whichever shape the CVE keys on."""
    for m in attr_re.finditer(body):
        head = m.group(1)
        tail = m.group(2).lstrip(".")
        if head in _NOISE_NAMES:
            continue
        raw_chain = f"{head}.{tail}"
        out.add(raw_chain)
        if bindings and head in bindings:
            out.add(f"{bindings[head]}.{tail}")
    for m in call_re.finditer(body):
        name = m.group(1)
        if name in _NOISE_NAMES or name in _KEYWORDS:
            continue
        out.add(name)


def _detect_imports(src: str, ext: str) -> Set[str]:
    """Quick first-pass: which package names does this file import?
    Used as the `known` set for downstream attribute extraction."""
    out: Set[str] = set()
    if ext == ".py":
        for m in _PY_IMPORT_AS.finditer(src):
            out.add(m.group(1).split(".")[0])
        for m in _PY_FROM_IMPORT.finditer(src):
            out.add(m.group(1).split(".")[0])
    return out


def _file_to_module(rel_path: str) -> str:
    """Normalize a repo-relative path into a Python module dotted name.
    Strips the .py / .pyi suffix, drops `__init__`, replaces `/` with
    `.`. Returns "" for non-Python files. Used as the LHS of re-export
    keys so cross-file imports can be resolved.

    Examples:
        myapp/utils.py        → myapp.utils
        myapp/api/__init__.py → myapp.api
        myapp/handler.py      → myapp.handler
    """
    if not (rel_path.endswith(".py") or rel_path.endswith(".pyi")):
        return ""
    parts = rel_path.replace("\\", "/").split("/")
    if not parts:
        return ""
    last = parts[-1]
    last = last.removesuffix(".pyi").removesuffix(".py")
    if last == "__init__":
        parts = parts[:-1]
    else:
        parts[-1] = last
    return ".".join(p for p in parts if p)


def _build_py_reexport_map(project_root: Path) -> Dict[str, str]:
    """Walk every Python file once and record `from X import Y as Z`
    bindings as fully-qualified re-export edges:

        Key:   <module>.<local_name>      e.g. "myapp.utils.MyDecomp"
        Value: <origin_module>.<orig_name> e.g. "aiohttp.ZLibDecompressor"

    A second pass over user code can then resolve a chain like
    `myapp.utils.MyDecomp.decompress` by looking up `myapp.utils.MyDecomp`
    in this map and rewriting to `aiohttp.ZLibDecompressor.decompress`.

    Transitive resolution (re-export of a re-export) is handled by the
    consumer via fixpoint expansion — cheap because the map is small
    (one entry per `import` statement).

    Limitations:
      - Star imports (`from pkg import *`) are recorded as
        "<module>.*" with value "<pkg>.*" so the resolver can recognise
        the unknown shape and refuse to claim resolution.
      - Module-level assignments (`MyDecomp = aiohttp.ZLibDecompressor`)
        are NOT captured by the import regex; LSP catches these.
    """
    reexports: Dict[str, str] = {}
    for path in _walk(project_root):
        if path.suffix.lower() != ".py":
            continue
        rel = str(path.relative_to(project_root))
        module = _file_to_module(rel)
        if not module:
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # `from X import Y, Z as W`
        for m in _PY_FROM_IMPORT.finditer(src):
            origin_pkg = m.group(1)
            body = m.group(2).strip().lstrip("(").rstrip(")")
            for item in body.split(","):
                item = item.strip()
                if not item:
                    continue
                if item == "*":
                    reexports[f"{module}.*"] = f"{origin_pkg}.*"
                    continue
                parts = item.split()
                real = parts[0]
                local = parts[2] if len(parts) >= 3 and parts[1] == "as" else real
                reexports[f"{module}.{local}"] = f"{origin_pkg}.{real}"

        # `import X as Y` — Y is now a module reference within this module
        for m in _PY_IMPORT_AS.finditer(src):
            origin = m.group(1)
            local = m.group(2) or origin.split(".")[0]
            # Only record meaningful aliases — `import os` (no alias) doesn't
            # need a re-export entry; only the aliased form does.
            if m.group(2):
                reexports[f"{module}.{local}"] = origin
    return reexports


def _resolve_through_reexports(fqn: str, reexports: Dict[str, str], max_hops: int = 5) -> str:
    """Walk an FQN through the re-export map until it stabilises.

    Strategy: try the longest prefix first — if the chain is
    `myapp.utils.MyDecomp.decompress`, look up
    `myapp.utils.MyDecomp` (3-segment prefix); if hit, replace prefix
    with the origin (`aiohttp.ZLibDecompressor`) and keep the
    remainder (`decompress`).

    Bounded at max_hops to defend against pathological mutual
    re-exports (rare but theoretically possible).
    """
    for _ in range(max_hops):
        parts = fqn.split(".")
        replaced = False
        # Try longer prefixes first (more specific matches win).
        for cut in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:cut])
            if prefix in reexports:
                origin = reexports[prefix]
                if origin.endswith(".*"):
                    return fqn  # star import — give up resolution
                fqn = origin + "." + ".".join(parts[cut:]) if cut < len(parts) else origin
                replaced = True
                break
        if not replaced:
            break
    return fqn


def scan_project_call_sites(project_root: Path) -> dict:
    """Walk the project, return {function_calls, local_call_graph,
    reflection_files, reexport_count, stats}. Layer-4 expansion runs
    by default — every collected attribute chain is rewritten through
    the project-wide re-export map before being recorded."""
    function_calls: Dict[str, Set[str]] = {}
    local_call_graph: Dict[str, Set[str]] = {}
    reflection_files: List[str] = []

    # Layer-4: build re-export map BEFORE collecting calls so the
    # collector can rewrite indirect references to their origin.
    reexports = _build_py_reexport_map(project_root)

    for path in _walk(project_root):
        ext = path.suffix.lower()
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Normalize to forward slashes — Path.relative_to on Windows
        # emits backslashes which leak into the call-graph FQN
        # ("myapp\\handler.py:helper") and break every downstream
        # consumer that keys by the POSIX-style path (tests, the
        # flyto-engine reachability layer, exported indexes shared
        # between Win-dev and Linux-CI). One source of truth.
        rel = str(path.relative_to(project_root)).replace("\\", "/")

        if ext == ".py":
            known = _detect_imports(src, ext)
            file_bindings = _collect_py_calls(src, known, function_calls)
            _collect_py_call_graph(rel, src, local_call_graph, file_bindings)

        # Reflection scan — single pass over file.
        for re_pat in _REFLECTION_PATTERNS.get(ext, []):
            if re_pat.search(src):
                reflection_files.append(rel)
                break

    # Layer-4 expansion pass — for every recorded FQN, look up its
    # prefix in the re-export map and append the resolved-origin
    # form alongside. We KEEP the original entry too so verify can
    # match on either shape (some CVEs key on the wrapper name).
    if reexports:
        expanded: Dict[str, Set[str]] = {}
        for pkg, fqns in function_calls.items():
            for fqn in fqns:
                expanded.setdefault(pkg, set()).add(fqn)
                resolved = _resolve_through_reexports(fqn, reexports)
                if resolved != fqn:
                    origin_pkg = resolved.split(".")[0]
                    expanded.setdefault(origin_pkg, set()).add(resolved)
        function_calls = expanded

        # Same for the call graph: any callee that resolves to a
        # different origin gets BOTH the original and the resolved
        # name added — Layer-3's intersection then matches either.
        for fn, callees in list(local_call_graph.items()):
            extras: Set[str] = set()
            for c in callees:
                resolved = _resolve_through_reexports(c, reexports)
                if resolved != c:
                    extras.add(resolved)
            if extras:
                callees.update(extras)

    return {
        "function_calls": {k: sorted(v) for k, v in function_calls.items()},
        "local_call_graph": {k: sorted(v) for k, v in local_call_graph.items()},
        "reflection_files": reflection_files,
        "stats": {
            "total_packages_with_calls": len(function_calls),
            "total_local_functions": len(local_call_graph),
            "total_reflection_files": len(reflection_files),
            "total_reexport_edges": len(reexports),
        },
    }
