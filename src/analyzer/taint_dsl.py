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


def _taint_block(data: dict) -> dict:
    block = data.get("taint")
    if not isinstance(block, dict):
        block = {}
        data["taint"] = block
    return block


def _append_unique(lst_key: str, block: dict, entry: dict, dedup_key: str) -> bool:
    """Append entry to block[lst_key], return True if appended, False if duplicate."""
    items = block.get(lst_key)
    if not isinstance(items, list):
        items = []
        block[lst_key] = items
    target = entry.get(dedup_key)
    for existing in items:
        if isinstance(existing, dict) and existing.get(dedup_key) == target:
            return False
    items.append(entry)
    return True


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

    data = _load(project_root)
    block = _taint_block(data)
    entry: dict = {"pattern": pattern, "language": language}
    if taint_type:
        entry["taint_type"] = taint_type

    added = _append_unique("sources", block, entry, "pattern")
    if not added:
        return {"status": "already_exists", "pattern": pattern}

    path = _save(project_root, data)
    return {"status": "added", "kind": "source", "pattern": pattern, "path": str(path)}


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

    data = _load(project_root)
    block = _taint_block(data)
    entry: dict = {
        "pattern": pattern,
        "vuln_type": vuln_type,
        "severity": severity,
    }
    if recommendation:
        entry["recommendation"] = recommendation

    added = _append_unique("sinks", block, entry, "pattern")
    if not added:
        return {"status": "already_exists", "pattern": pattern}

    path = _save(project_root, data)
    return {"status": "added", "kind": "sink", "pattern": pattern, "path": str(path)}


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

    data = _load(project_root)
    block = _taint_block(data)
    entry: dict = {
        "pattern": pattern,
        "cleanses": list(cleanses) if cleanses else ["*"],
    }

    added = _append_unique("sanitizers", block, entry, "pattern")
    if not added:
        return {"status": "already_exists", "pattern": pattern}

    path = _save(project_root, data)
    return {"status": "added", "kind": "sanitizer", "pattern": pattern, "path": str(path)}


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

    data = _load(project_root)
    block = data.get("taint") or {}
    items = block.get(collection)
    if not isinstance(items, list):
        return {"status": "not_found", "pattern": pattern}

    before = len(items)
    block[collection] = [
        i for i in items
        if not (isinstance(i, dict) and i.get("pattern") == pattern)
    ]
    if len(block[collection]) == before:
        return {"status": "not_found", "pattern": pattern}

    path = _save(project_root, data)
    return {"status": "removed", "kind": kind, "pattern": pattern, "path": str(path)}


def list_taint_rules(project_root: Path) -> dict:
    """Show the taint block declared in .flyto-rules.yaml (project-specific only)."""
    data = _load(project_root)
    block = data.get("taint") or {}
    return {
        "sources": list(block.get("sources") or []),
        "sinks": list(block.get("sinks") or []),
        "sanitizers": list(block.get("sanitizers") or []),
        "overrides": dict(block.get("overrides") or {}),
    }
