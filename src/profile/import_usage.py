"""Per-package import usage extraction.

Mirrors flyto-engine's internal/scanner collectImports so the exported profile's
``import_counts`` / ``import_files`` are byte-for-byte compatible with what the
engine produces on its own self-scan path. flyto-engine's CVE reachability
(internal/correlate, pulse.go) cross-matches a vulnerable dependency's package
name against ``import_files[pkg]`` to anchor the finding to real source files;
if the upload path omits these maps, package-level CVE reachability is silently
empty (see integrations/flyto-engine.md).

Keys are normalised to the dependency-manifest package name (``express``,
``github.com/gin-gonic/gin`` and its bare ``gin``) so they line up with the
names CVE findings use.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

# Mirror collectImports: language import patterns.
# Python: `import x`, `from x import y` — x must be >= 2 chars.
_PY_IMPORT = re.compile(r"(?m)^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]{1,})")
# JS/TS: `import ... from 'x'`, `require('x')`, `import('x')` — skip relative.
_JS_IMPORT = re.compile(
    r"""(?:import[^'"]*['"]|require\s*\(\s*['"]|import\s*\(\s*['"])"""
    r"""([@a-zA-Z][@a-zA-Z0-9_\-\.\/]+)['"]"""
)
# Go: only the contents inside an `import ( ... )` block.
_GO_IMPORT_BLOCK = re.compile(r"(?s)import\s*\(([^)]+)\)")
# A single Go import line inside the block (optional alias / blank import,
# tolerate trailing comment).
_GO_IMPORT_LINE = re.compile(
    r"""(?m)^\s*(?:[a-zA-Z_][a-zA-Z0-9_]*\s+)?"("""
    r"""(?:[a-zA-Z][a-zA-Z0-9_\-]*\.)+[a-zA-Z0-9_\-\/]+"""
    r"""|[a-zA-Z][a-zA-Z0-9_\-]*/[a-zA-Z0-9_\-\/]+"""
    r"""|[a-zA-Z][a-zA-Z0-9_\-]+)"\s*(?://.*|/\*.*)?$"""
)

_SKIP_DIRS = {
    "node_modules", "vendor", "dist", ".git",
    "build", "target", "__pycache__", ".venv",
}
_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".java", ".kt"}
_MAX_FILE_BYTES = 256 * 1024

_GO_VERSION_SUFFIX = re.compile(r"^v[0-9]+$")


def _first_import_segment(raw: str) -> str:
    """Normalise a non-Go import token to its top-level package name."""
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("@"):
        # @scope/name → keep; @scope/name/sub → @scope/name
        parts = raw.split("/", 2)
        if len(parts) >= 2:
            return parts[0] + "/" + parts[1]
        return raw
    # Trim at first "/" (python subpackage, npm subpath).
    i = raw.find("/")
    if i >= 0:
        raw = raw[:i]
    # Python dotted: cryptography.hazmat → cryptography.
    i = raw.find(".")
    if i >= 0:
        raw = raw[:i]
    return raw


def _is_likely_package_name(name: str) -> bool:
    if len(name) < 2:
        return False
    c = name[0]
    if not (("a" <= c <= "z") or c == "@"):
        return False
    # All-caps/underscore/digit tokens are env vars/constants, not packages.
    return any("a" <= ch <= "z" for ch in name)


def _go_import_keys(raw: str) -> List[str]:
    """Turn a raw Go import path into the identifiers a manifest entry may use.

    Skips stdlib (first segment has no dot). Records the full path, the bare
    last segment, and any version-suffixed module path so a manifest entry like
    ``github.com/jackc/pgx/v5`` matches imports of ``.../v5/stdlib``.
    """
    parts = raw.split("/")
    if not parts or "." not in parts[0]:
        return []  # stdlib or malformed
    keys = [raw]
    last = parts[-1]
    bare_name = last
    if _GO_VERSION_SUFFIX.match(last) and len(parts) >= 2:
        bare_name = parts[-2]
    if bare_name != raw:
        keys.append(bare_name)
    for i in range(len(parts) - 1, 0, -1):
        if _GO_VERSION_SUFFIX.match(parts[i]):
            module_path = "/".join(parts[: i + 1])
            if module_path != raw and module_path != bare_name:
                keys.append(module_path)
            break
    return keys


def compute_import_usage(project_path: Path) -> Tuple[Dict[str, int], Dict[str, List[str]]]:
    """Walk the project and return (import_counts, import_files).

    import_counts[pkg] = number of files importing pkg.
    import_files[pkg]  = sorted list of repo-relative paths importing pkg.
    """
    counts: Dict[str, int] = {}
    files: Dict[str, List[str]] = {}
    root = project_path.resolve()

    for path in root.rglob("*"):
        if path.is_dir():
            continue
        # Skip excluded directories anywhere in the path.
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        ext = path.suffix.lower()
        if ext not in _CODE_EXTS:
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            data = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        file_refs = set()
        if ext != ".go":
            for pattern in (_PY_IMPORT, _JS_IMPORT):
                for m in pattern.finditer(data):
                    pkg = _first_import_segment(m.group(1))
                    if pkg and _is_likely_package_name(pkg):
                        file_refs.add(pkg)
        else:
            for block in _GO_IMPORT_BLOCK.finditer(data):
                for m in _GO_IMPORT_LINE.finditer(block.group(1)):
                    for key in _go_import_keys(m.group(1)):
                        if _is_likely_package_name(key):
                            file_refs.add(key)

        rel = str(path.relative_to(root))
        for pkg in file_refs:
            counts[pkg] = counts.get(pkg, 0) + 1
            files.setdefault(pkg, []).append(rel)

    for pkg in files:
        files[pkg].sort()
    return counts, files
