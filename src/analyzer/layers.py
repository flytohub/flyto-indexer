"""
Architecture Layer Rules — declarative layer membership and import constraints.

Enforces architectural intent: which parts of the codebase may import which.
Reads declarations from .flyto-rules.yaml and checks the import graph.

Pure Python stdlib (PyYAML only for write-back, same as rules.py).

Schema (in .flyto-rules.yaml)
-----------------------------
    layers:
      - name: ui
        paths: ["src/components/**", "src/pages/**"]
        can_import: [lib, hooks, types]
        reason: "UI is the top layer"

      - name: lib
        paths: ["src/lib/**"]
        cannot_import: [ui]
        reason: "lib must be UI-agnostic"

    cross_imports_deny:
      - from: "src/features/a/**"
        to: "src/features/b/**"
        reason: "features must not cross-import"

    path_aliases:        # optional; tsconfig paths auto-read when available
      "@/*": "src/*"

Semantics
---------
- `paths`: globs defining layer membership (FIRST matching layer wins)
- `can_import`: whitelist — only these layers (+own) may be imported
- `cannot_import`: blacklist — overrides whitelist
- Files not in any layer: unconstrained
- External packages (npm / pypi / go stdlib): ignored

Supported languages
-------------------
- Python (.py)
- TypeScript / JavaScript (.ts .tsx .js .jsx .mjs .cjs)
- Vue (.vue — script block)
- Go (.go — resolved via go.mod module path)
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .rules import _collect_files, _glob_match

logger = logging.getLogger(__name__)

# ── Data models ─────────────────────────────────────────────────────────────

@dataclass
class LayerDef:
    name: str
    paths: list[str]
    can_import: list[str] = field(default_factory=list)
    cannot_import: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class LayerViolation:
    from_file: str
    to_file: str
    from_layer: str
    to_layer: str
    line: int
    kind: str           # "cannot_import" | "not_in_can_import" | "cross_imports_deny"
    reason: str
    severity: str = "high"


@dataclass
class LayerReport:
    layers: list[LayerDef] = field(default_factory=list)
    violations: list[LayerViolation] = field(default_factory=list)
    files_checked: int = 0
    edges_checked: int = 0
    edges_skipped: int = 0


# ── Import extractors ───────────────────────────────────────────────────────

_PY_IMPORT = re.compile(
    r"^\s*(?:import\s+([\w.]+)|from\s+((?:\.+)?[\w.]*)\s+import\s)",
    re.MULTILINE,
)

_TS_IMPORT = re.compile(
    r"""^\s*(?:import\s+(?:[^'"]*?from\s+)?['"]([^'"]+)['"]"""
    r"""|export\s+[^'"]*?from\s+['"]([^'"]+)['"])""",
    re.MULTILINE,
)
_TS_REQUIRE = re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)""")
_TS_DYNAMIC = re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)""")

_GO_IMPORT_SINGLE = re.compile(r'^\s*import\s+"([^"]+)"', re.MULTILINE)
_GO_IMPORT_BLOCK = re.compile(r'import\s*\(\s*(.*?)\)', re.DOTALL)
_GO_BLOCK_LINE = re.compile(r'"([^"]+)"')

_VUE_SCRIPT_BLOCK = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL)

PY_EXTS = {".py"}
TS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
GO_EXTS = {".go"}
VUE_EXTS = {".vue"}
ALL_EXTS = PY_EXTS | TS_EXTS | GO_EXTS | VUE_EXTS


def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _extract_python_imports(content: str) -> list[tuple[str, int]]:
    out = []
    for m in _PY_IMPORT.finditer(content):
        mod = m.group(1) or m.group(2)
        if mod:
            out.append((mod, _line_of(content, m.start())))
    return out


def _extract_ts_imports(content: str) -> list[tuple[str, int]]:
    out = []
    for m in _TS_IMPORT.finditer(content):
        src = m.group(1) or m.group(2)
        if src:
            out.append((src, _line_of(content, m.start())))
    for m in _TS_REQUIRE.finditer(content):
        out.append((m.group(1), _line_of(content, m.start())))
    for m in _TS_DYNAMIC.finditer(content):
        out.append((m.group(1), _line_of(content, m.start())))
    return out


def _extract_go_imports(content: str) -> list[tuple[str, int]]:
    out = []
    for m in _GO_IMPORT_SINGLE.finditer(content):
        out.append((m.group(1), _line_of(content, m.start())))
    for block in _GO_IMPORT_BLOCK.finditer(content):
        base_offset = block.start(1)
        for line_m in _GO_BLOCK_LINE.finditer(block.group(1)):
            out.append((line_m.group(1), _line_of(content, base_offset + line_m.start())))
    return out


def _extract_vue_imports(content: str) -> list[tuple[str, int]]:
    out = []
    for m in _VUE_SCRIPT_BLOCK.finditer(content):
        script = m.group(1)
        block_line = _line_of(content, m.start(1))
        for src, rel_line in _extract_ts_imports(script):
            out.append((src, block_line + rel_line - 1))
    return out


def extract_imports(file_path: Path, content: str) -> list[tuple[str, int]]:
    ext = file_path.suffix.lower()
    if ext in PY_EXTS:
        return _extract_python_imports(content)
    if ext in TS_EXTS:
        return _extract_ts_imports(content)
    if ext in GO_EXTS:
        return _extract_go_imports(content)
    if ext in VUE_EXTS:
        return _extract_vue_imports(content)
    return []


# ── Import resolution ──────────────────────────────────────────────────────

def _load_tsconfig_aliases(project_root: Path) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for cfg_name in ("tsconfig.json", "tsconfig.app.json", "tsconfig.base.json"):
        cfg = project_root / cfg_name
        if not cfg.is_file():
            continue
        try:
            text = cfg.read_text(encoding="utf-8", errors="ignore")
            text = re.sub(r"//[^\n]*", "", text)
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
            data = json.loads(text)
        except Exception:
            continue

        opts = data.get("compilerOptions") or {}
        paths = opts.get("paths") or {}
        base = opts.get("baseUrl") or "."
        for alias, targets in paths.items():
            if not isinstance(targets, list) or not targets:
                continue
            target = targets[0]
            if alias.endswith("/*") and target.endswith("/*"):
                try:
                    resolved = (project_root / base / target[:-2]).resolve()
                    rel = resolved.relative_to(project_root.resolve())
                    aliases[alias[:-2]] = str(rel)
                except ValueError:
                    continue
    return aliases


def _load_go_module(project_root: Path) -> str | None:
    gomod = project_root / "go.mod"
    if not gomod.is_file():
        return None
    try:
        content = gomod.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r"^module\s+(\S+)", content, re.MULTILINE)
    return m.group(1) if m else None


def _probe_file(candidate: Path) -> Path | None:
    if candidate.is_file():
        return candidate
    for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".go"):
        probe = candidate.with_suffix(ext) if candidate.suffix else Path(str(candidate) + ext)
        if probe.is_file():
            return probe
    if candidate.is_dir():
        for name in ("index.ts", "index.tsx", "index.js", "index.jsx", "__init__.py"):
            f = candidate / name
            if f.is_file():
                return f
    return None


def _resolve_relative(source_file: Path, import_path: str) -> Path | None:
    source_dir = source_file.parent

    if import_path.startswith("."):
        # Python-style relative: ".foo", "..foo.bar"
        if not (import_path.startswith("./") or import_path.startswith("../")):
            dots = len(import_path) - len(import_path.lstrip("."))
            rest = import_path[dots:].replace(".", "/")
            base = source_dir
            for _ in range(dots - 1):
                base = base.parent
            return _probe_file(base / rest if rest else base)

        # JS/TS-style: "./foo", "../foo"
        return _probe_file((source_dir / import_path).resolve())

    return None


def _resolve_alias(
    import_path: str, project_root: Path, aliases: dict[str, str],
) -> Path | None:
    for alias_prefix, target_prefix in aliases.items():
        if import_path == alias_prefix or import_path.startswith(alias_prefix + "/"):
            rest = import_path[len(alias_prefix):].lstrip("/")
            return _probe_file(project_root / target_prefix / rest)
    return None


def _resolve_go(
    import_path: str, project_root: Path, go_module: str | None,
) -> Path | None:
    if not go_module:
        return None
    if import_path == go_module:
        rel = ""
    elif import_path.startswith(go_module + "/"):
        rel = import_path[len(go_module) + 1:]
    else:
        return None
    pkg_dir = project_root / rel if rel else project_root
    if pkg_dir.is_dir():
        non_test = [f for f in pkg_dir.glob("*.go") if not f.name.endswith("_test.go")]
        if non_test:
            return non_test[0]
        any_go = list(pkg_dir.glob("*.go"))
        if any_go:
            return any_go[0]
    return None


def resolve_import(
    source_file: Path,
    import_path: str,
    project_root: Path,
    aliases: dict[str, str] | None = None,
    go_module: str | None = None,
    line_content: str | None = None,
    line_num_0based: int | None = None,
) -> Path | None:
    """Resolve an import to a project-local file, or None if external/unresolved.

    Resolution order (fast path first):
      1. Relative path (./foo, ../foo, Python .foo)
      2. tsconfig / user alias
      3. Go module path (go.mod)
      4. LSP textDocument/definition (precision layer — picks up anything the
         static heuristics missed, including complex tsconfig paths, namespace
         packages, and gopls vendor directories).

    Steps 1-3 are stdlib-only and run in microseconds; step 4 only fires when
    the LSP server is available for the source language and the earlier steps
    came back empty.
    """
    resolved = _resolve_relative(source_file, import_path)
    if resolved:
        return resolved
    if aliases:
        resolved = _resolve_alias(import_path, project_root, aliases)
        if resolved:
            return resolved
    if source_file.suffix.lower() in GO_EXTS:
        go_resolved = _resolve_go(import_path, project_root, go_module)
        if go_resolved:
            return go_resolved

    # LSP fallback — precision layer for everything the static heuristics
    # couldn't pin down (requires line context).
    if line_content is not None and line_num_0based is not None:
        try:
            from ..lsp.resolver import resolve_import_via_lsp
        except ImportError:
            try:
                from lsp.resolver import resolve_import_via_lsp
            except ImportError:
                return None
        return resolve_import_via_lsp(
            project_root, source_file, line_content, line_num_0based, import_path,
        )

    return None


# ── Layer membership & check ───────────────────────────────────────────────

def _file_layer(file_path: str, layers: list[LayerDef]) -> str | None:
    for layer in layers:
        for pat in layer.paths:
            if _glob_match(file_path, pat):
                return layer.name
    return None


def _parse_layers(rules: dict) -> list[LayerDef]:
    raw = rules.get("layers") or []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        paths = entry.get("paths") or []
        if not name or not paths:
            continue
        out.append(LayerDef(
            name=name,
            paths=list(paths),
            can_import=list(entry.get("can_import") or []),
            cannot_import=list(entry.get("cannot_import") or []),
            reason=entry.get("reason", ""),
        ))
    return out


def _check_layer_edge(
    from_file: str, to_file: str, line: int,
    layers_by_name: dict[str, LayerDef],
    file_layer_cache: dict[str, str | None],
) -> LayerViolation | None:
    from_layer = file_layer_cache.get(from_file)
    to_layer = file_layer_cache.get(to_file)
    if not from_layer or not to_layer or from_layer == to_layer:
        return None

    layer = layers_by_name.get(from_layer)
    if not layer:
        return None

    if to_layer in layer.cannot_import:
        return LayerViolation(
            from_file=from_file, to_file=to_file,
            from_layer=from_layer, to_layer=to_layer,
            line=line, kind="cannot_import",
            reason=layer.reason or f"{from_layer} cannot import {to_layer}",
        )

    if layer.can_import and to_layer not in layer.can_import:
        return LayerViolation(
            from_file=from_file, to_file=to_file,
            from_layer=from_layer, to_layer=to_layer,
            line=line, kind="not_in_can_import",
            reason=layer.reason or
                   f"{from_layer} may only import {layer.can_import}, not {to_layer}",
        )

    return None


def check_layers(project_root: Path, rules: dict | None = None) -> LayerReport:
    """Check import-graph compliance against layer declarations."""
    if rules is None:
        from .rules import load_rules as _load
        rules = _load(project_root) or {}

    layers = _parse_layers(rules)
    cross_deny = rules.get("cross_imports_deny") or []
    if not layers and not cross_deny:
        return LayerReport()

    report = LayerReport(layers=layers)
    layers_by_name = {l.name: l for l in layers}

    aliases = _load_tsconfig_aliases(project_root)
    for k, v in (rules.get("path_aliases") or {}).items():
        key = k[:-2] if k.endswith("/*") else k
        val = v[:-2] if v.endswith("/*") else v
        aliases[key] = val

    go_module = _load_go_module(project_root)
    all_files = _collect_files(project_root)

    file_layer_cache: dict[str, str | None] = {}
    for fpath in all_files:
        if Path(fpath).suffix.lower() in ALL_EXTS:
            file_layer_cache[fpath] = _file_layer(fpath, layers)

    project_root_resolved = project_root.resolve()

    for fpath in list(file_layer_cache.keys()):
        full = project_root / fpath
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        report.files_checked += 1
        imports = extract_imports(full, content)
        content_lines = content.split("\n")

        for imp, line in imports:
            line_content = content_lines[line - 1] if 0 < line <= len(content_lines) else ""
            target = resolve_import(
                full, imp, project_root, aliases, go_module,
                line_content=line_content,
                line_num_0based=line - 1,
            )
            if not target:
                report.edges_skipped += 1
                continue
            try:
                rel_target = str(target.resolve().relative_to(project_root_resolved))
            except ValueError:
                report.edges_skipped += 1
                continue

            if rel_target not in file_layer_cache:
                file_layer_cache[rel_target] = _file_layer(rel_target, layers)

            report.edges_checked += 1

            v = _check_layer_edge(fpath, rel_target, line, layers_by_name, file_layer_cache)
            if v:
                report.violations.append(v)
                continue

            for entry in cross_deny:
                if not isinstance(entry, dict):
                    continue
                frm = entry.get("from")
                to = entry.get("to")
                if not frm or not to:
                    continue
                if _glob_match(fpath, frm) and _glob_match(rel_target, to):
                    report.violations.append(LayerViolation(
                        from_file=fpath, to_file=rel_target,
                        from_layer=file_layer_cache.get(fpath) or "",
                        to_layer=file_layer_cache.get(rel_target) or "",
                        line=line, kind="cross_imports_deny",
                        reason=entry.get("reason", "denied cross import"),
                        severity=entry.get("severity", "high"),
                    ))

    return report


def check_layers_dict(project_root: Path) -> dict:
    report = check_layers(project_root)
    return {
        "layers": [
            {
                "name": l.name,
                "paths": l.paths,
                "can_import": l.can_import,
                "cannot_import": l.cannot_import,
                "reason": l.reason,
            }
            for l in report.layers
        ],
        "files_checked": report.files_checked,
        "edges_checked": report.edges_checked,
        "edges_skipped": report.edges_skipped,
        "total_violations": len(report.violations),
        "violations": [
            {
                "from_file": v.from_file,
                "to_file": v.to_file,
                "from_layer": v.from_layer,
                "to_layer": v.to_layer,
                "line": v.line,
                "kind": v.kind,
                "reason": v.reason,
                "severity": v.severity,
            }
            for v in report.violations[:100]
        ],
    }


# ── Rule writing ────────────────────────────────────────────────────────────

def add_layer(
    project_root: Path,
    name: str,
    paths: list[str],
    can_import: list[str] | None = None,
    cannot_import: list[str] | None = None,
    reason: str | None = None,
) -> dict:
    """Add a layer definition to .flyto-rules.yaml."""
    try:
        import yaml
    except ImportError:
        return {"error": "PyYAML not installed"}

    rules_path = project_root / ".flyto-rules.yaml"
    if rules_path.is_file():
        try:
            with open(rules_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            data = {}
    else:
        data = {"version": 1}

    if "version" not in data:
        data["version"] = 1
    if "layers" not in data or not isinstance(data["layers"], list):
        data["layers"] = []

    existing = {l.get("name") for l in data["layers"] if isinstance(l, dict)}
    if name in existing:
        return {"status": "already_exists", "name": name}

    entry: dict = {"name": name, "paths": list(paths)}
    if can_import:
        entry["can_import"] = list(can_import)
    if cannot_import:
        entry["cannot_import"] = list(cannot_import)
    if reason:
        entry["reason"] = reason

    data["layers"].append(entry)

    with open(rules_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return {"status": "added", "name": name, "path": str(rules_path)}


def remove_layer(project_root: Path, name: str) -> dict:
    try:
        import yaml
    except ImportError:
        return {"error": "PyYAML not installed"}

    rules_path = project_root / ".flyto-rules.yaml"
    if not rules_path.is_file():
        return {"error": "No .flyto-rules.yaml found"}

    try:
        with open(rules_path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        return {"error": str(e)}

    if not isinstance(data.get("layers"), list):
        return {"status": "not_found", "name": name}

    before = len(data["layers"])
    data["layers"] = [l for l in data["layers"]
                      if not (isinstance(l, dict) and l.get("name") == name)]
    if len(data["layers"]) == before:
        return {"status": "not_found", "name": name}

    with open(rules_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return {"status": "removed", "name": name}
