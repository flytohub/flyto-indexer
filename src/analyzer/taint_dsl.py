"""
Taint DSL — read/write helpers for the `taint:` block in .flyto-rules.yaml.

Pairs with analyzer.taint (the analysis engine). This module only handles
the YAML CRUD so the engine stays focused on detection.

Schema (in .flyto-rules.yaml)
-----------------------------
    taint:
      sources:
        - pattern: "request.json[*]"
          language: python        # python | javascript | go
          taint_type: user_input  # optional free-form label
      sinks:
        - pattern: "subprocess.*"
          vuln_type: rce
          severity: critical      # critical | high | medium | low
          recommendation: "Use arg list, no shell=True"
      sanitizers:
        - pattern: "shlex.quote(*)"
          cleanses: ["rce"]       # or ["*"] for all
      overrides:
        remove_sources: [...]
        remove_sinks: [...]

All entries are merged with the built-in defaults in taint_rules.py.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _load(project_root: Path) -> dict:
    """Load .flyto-rules.yaml as a dict (empty dict if missing)."""
    try:
        import yaml
    except ImportError:
        return {}

    path = project_root / ".flyto-rules.yaml"
    if not path.is_file():
        return {}
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.debug("Failed to read %s: %s", path, e)
        return {}


def _save(project_root: Path, data: dict) -> Path:
    import yaml
    path = project_root / ".flyto-rules.yaml"
    if "version" not in data:
        data["version"] = 1
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return path


# A word that YAML reads back as itself, so the file's own style leaves it
# bare: `vuln_type: rce`, but `pattern: "os.system("`.
_BARE_WORD = re.compile(r"\A[A-Za-z_][A-Za-z0-9_-]*\Z")
_YAML_KEYWORDS = frozenset(
    {"y", "n", "yes", "no", "true", "false", "on", "off", "null"}
)


def _render_value(value) -> str:
    """A value written the way the rest of the file writes its values.

    Existing entries read `vuln_type: rce`, `pattern: "eval("`, and
    `cleanses: ["rce"]` -- not the block style PyYAML defaults to. JSON is a
    subset of YAML, so dumping as JSON reproduces the quoted scalars and flow
    lists; bare words are the one case written without quotes.
    """
    import json

    if (
        isinstance(value, str)
        and _BARE_WORD.match(value)
        and value.lower() not in _YAML_KEYWORDS
    ):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def _dump_entry(entry: dict, indent: str) -> str:
    """One list item, in the layout the file already uses."""
    lines = []
    for position, (key, value) in enumerate(entry.items()):
        bullet = "- " if position == 0 else "  "
        lines.append(f"{indent}{bullet}{key}: {_render_value(value)}\n")
    return "".join(lines)


def _block_bounds(lines: list[str], key: str, indent: str) -> "tuple[int, int] | None":
    """Line range of `key:` and its body, or None when the key is absent.

    The end is the line after the block's last content line -- trailing blank
    lines and comments that line up with the *next* key are left outside, so an
    insertion does not land between a section comment and the section it
    introduces. A comment indented deeper than the block belongs to the block
    and stays inside it.
    """
    for i, line in enumerate(lines):
        if line.strip() != f"{key}:":
            continue
        if line[: len(line) - len(line.lstrip())] != indent:
            continue
        end = len(lines)
        for j in range(i + 1, len(lines)):
            body = lines[j]
            if not body.strip():
                continue
            body_indent = len(body) - len(body.lstrip())
            if body_indent <= len(indent) and not (
                body.lstrip().startswith("#") and body_indent > len(indent)
            ):
                end = j
                break
        while end > i + 1:
            previous = lines[end - 1]
            outdented = len(previous) - len(previous.lstrip()) <= len(indent)
            if not previous.strip() or (previous.lstrip().startswith("#") and outdented):
                end -= 1
                continue
            break
        return i, end
    return None


def _has_inline_value(lines: list[str], key: str, indent: str) -> bool:
    """True when `key:` carries its value on the same line."""
    opener = f"{indent}{key}:"
    return any(
        line.startswith(opener) and line[len(opener):].strip()
        for line in lines
    )


def _list_indent(lines: list[str], start: int, end: int) -> "str | None":
    """The indent the items of an existing list are written at."""
    for line in lines[start:end]:
        stripped = line.lstrip()
        if stripped.startswith("- "):
            return line[: len(line) - len(stripped)]
    return None


def _append_entry_in_place(path: Path, list_key: str, entry: dict) -> bool:
    """Add one rule by editing text, leaving every other byte alone.

    Round-tripping the document through the parser is what turned adding a
    single sanitizer into a 332-line rewrite that deleted both of the file's
    comments. PyYAML cannot preserve them and a comment-preserving parser would
    be a new dependency, so the entry is spliced in as text instead.

    Returns False when the file is not shaped in a way this can edit safely.
    The caller must then leave the file alone rather than rewrite it.
    """
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    taint = _block_bounds(lines, "taint", "")
    if taint is None:
        lines.append("\ntaint:\n  " + list_key + ":\n")
        lines.append(_dump_entry(entry, "    "))
        path.write_text("".join(lines), encoding="utf-8")
        return True

    t_start, t_end = taint
    if _has_inline_value(lines[t_start + 1:t_end], list_key, "  "):
        # `sinks: [...]` or `sinks: null` -- appending a second `sinks:` key
        # would silently shadow the first one.
        return False
    inner = _block_bounds(lines[t_start + 1:t_end], list_key, "  ")
    if inner is None:
        lines.insert(t_end, "  " + list_key + ":\n" + _dump_entry(entry, "    "))
        path.write_text("".join(lines), encoding="utf-8")
        return True

    l_start, l_end = inner[0] + t_start + 1, inner[1] + t_start + 1
    indent = _list_indent(lines, l_start + 1, l_end)
    if indent is None:
        # `sinks:` with no items under it -- an empty list, a flow-style one, or
        # a null. Splicing an item in would change what the key means.
        return False
    lines.insert(l_end, _dump_entry(entry, indent))
    path.write_text("".join(lines), encoding="utf-8")
    return True


def _remove_entry_in_place(path: Path, list_key: str, pattern: str) -> bool:
    """Delete one rule by editing text, for the same reason as the append."""
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    taint = _block_bounds(lines, "taint", "")
    if taint is None:
        return False
    t_start, t_end = taint
    inner = _block_bounds(lines[t_start + 1:t_end], list_key, "  ")
    if inner is None:
        return False
    l_start, l_end = inner[0] + t_start + 1, inner[1] + t_start + 1
    indent = _list_indent(lines, l_start + 1, l_end)
    if indent is None:
        return False

    import yaml

    starts = [
        i for i in range(l_start + 1, l_end)
        if lines[i].startswith(indent + "- ")
    ]
    for position, item_start in enumerate(starts):
        item_end = starts[position + 1] if position + 1 < len(starts) else l_end
        text = "".join(
            line[len(indent):] if line.startswith(indent) else line
            for line in lines[item_start:item_end]
        )
        try:
            parsed = yaml.safe_load(text)
        except Exception:
            return False
        if not (isinstance(parsed, list) and len(parsed) == 1):
            return False
        item = parsed[0]
        if isinstance(item, dict) and item.get("pattern") == pattern:
            del lines[item_start:item_end]
            remaining = l_end - (item_end - item_start)
            if not any(
                lines[i].startswith(indent + "- ")
                for i in range(l_start + 1, remaining)
            ):
                # The key would be left with nothing under it, which reads back
                # as null rather than an empty list.
                del lines[l_start]
            path.write_text("".join(lines), encoding="utf-8")
            return True
    return False


def _declared_taint(project_root: Path) -> dict:
    """The `taint:` mapping already in the file, or an empty one."""
    block = _load(project_root).get("taint")
    return block if isinstance(block, dict) else {}


def _has_pattern(block: dict, list_key: str, pattern: str) -> bool:
    items = block.get(list_key)
    if not isinstance(items, list):
        return False
    return any(
        isinstance(existing, dict) and existing.get("pattern") == pattern
        for existing in items
    )


def _commit_addition(project_root: Path, list_key: str, entry: dict, kind: str) -> dict:
    """Write one new rule, creating the file only when there is none."""
    path = project_root / ".flyto-rules.yaml"
    if not path.is_file():
        _save(project_root, {"version": 1, "taint": {list_key: [entry]}})
    elif not _append_entry_in_place(path, list_key, entry):
        return {
            "error": (
                f"taint.{list_key} in {path.name} is not shaped for an automatic "
                "edit. Add the entry below by hand rather than have this "
                "rewrite the file."
            ),
            "snippet": _dump_entry(entry, "    "),
            "path": str(path),
        }
    return {"status": "added", "kind": kind, "pattern": entry["pattern"], "path": str(path)}


# ── Public CRUD ────────────────────────────────────────────────────────────

def add_taint_source(
    project_root: Path,
    pattern: str,
    language: str = "python",
    taint_type: str | None = None,
) -> dict:
    """Add a source pattern to `.flyto-rules.yaml → taint.sources`."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        return {"error": "PyYAML not installed"}

    entry: dict = {"pattern": pattern, "language": language}
    if taint_type:
        entry["taint_type"] = taint_type

    if _has_pattern(_declared_taint(project_root), "sources", pattern):
        return {"status": "already_exists", "pattern": pattern}
    return _commit_addition(project_root, "sources", entry, "source")


def add_taint_sink(
    project_root: Path,
    pattern: str,
    vuln_type: str = "custom",
    severity: str = "high",
    recommendation: str = "",
) -> dict:
    """Add a sink pattern to `.flyto-rules.yaml → taint.sinks`."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        return {"error": "PyYAML not installed"}

    entry: dict = {
        "pattern": pattern,
        "vuln_type": vuln_type,
        "severity": severity,
    }
    if recommendation:
        entry["recommendation"] = recommendation

    if _has_pattern(_declared_taint(project_root), "sinks", pattern):
        return {"status": "already_exists", "pattern": pattern}
    return _commit_addition(project_root, "sinks", entry, "sink")


def add_taint_sanitizer(
    project_root: Path,
    pattern: str,
    cleanses: list[str] | None = None,
) -> dict:
    """Add a sanitizer to `.flyto-rules.yaml → taint.sanitizers`."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        return {"error": "PyYAML not installed"}

    entry: dict = {
        "pattern": pattern,
        "cleanses": list(cleanses) if cleanses else ["*"],
    }

    if _has_pattern(_declared_taint(project_root), "sanitizers", pattern):
        return {"status": "already_exists", "pattern": pattern}
    return _commit_addition(project_root, "sanitizers", entry, "sanitizer")


def remove_taint_rule(
    project_root: Path, kind: str, pattern: str,
) -> dict:
    """Remove a taint rule by pattern. `kind` is 'source' | 'sink' | 'sanitizer'."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        return {"error": "PyYAML not installed"}

    collection = {"source": "sources", "sink": "sinks", "sanitizer": "sanitizers"}.get(kind)
    if not collection:
        return {"error": f"Unknown kind: {kind}"}

    path = project_root / ".flyto-rules.yaml"
    if not _has_pattern(_declared_taint(project_root), collection, pattern):
        return {"status": "not_found", "pattern": pattern}

    if not _remove_entry_in_place(path, collection, pattern):
        return {
            "error": (
                f"{pattern!r} is declared in taint.{collection} but not in a form "
                f"this can delete without rewriting {path.name}. Remove it by hand."
            ),
            "path": str(path),
        }
    return {"status": "removed", "kind": kind, "pattern": pattern, "path": str(path)}


def list_taint_rules(project_root: Path) -> dict:
    """Show the taint block declared in .flyto-rules.yaml (project-specific only)."""
    data = _load(project_root)
    block = data.get("taint") or {}
    return {
        "sources": list(block.get("sources") or []),
        "sinks": list(block.get("sinks") or []),
        "sanitizers": list(block.get("sanitizers") or []),
        "propagators": list(block.get("propagators") or []),
        "overrides": dict(block.get("overrides") or {}),
    }
