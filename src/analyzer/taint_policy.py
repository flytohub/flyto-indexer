"""Taint source/sink policy and repository-local rule loading.

This module owns declarative policy composition. It never walks project ASTs or
performs cross-file resolution.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .taint_rules import SINKS
from .taint_shapes import normalize_requirements

logger = logging.getLogger(__name__)


CATEGORY_SEVERITY = {
    "sql_injection": "critical",
    "rce": "critical",
    "xss": "high",
    "path_traversal": "high",
    "deserialization": "critical",
    "ssrf": "high",
    "ssti": "high",
    "open_redirect": "medium",
    "xxe": "high",
    "ldap_injection": "high",
    "nosql_injection": "high",
    "crlf_injection": "medium",
    "redos": "medium",
    "prototype_pollution": "high",
}

def _source_matches(pattern: str, text: str) -> bool:
    """Whether a source pattern occurs in `text` at a name boundary.

    Sources were matched with a bare substring test, so FastAPI's `Cookie(`
    marker matched `SimpleCookie(`, `File(` matched `NamedTemporaryFile(`, and
    `input(` matched `make_input(`. Measured over 38,899 files that is 5,122
    matches, and every sampled one is a different callable.

    The two pattern shapes need different left boundaries, and the corpus says
    which:

    * A dotted pattern is deliberately matched as the tail of an object
      expression -- `request.get_json(` is meant to catch
      `flask_request.get_json(`, which is how a request threaded through a
      parameter is read. Underscore therefore stays a boundary. A letter or
      digit never precedes a dotted match in the corpus at all, so refusing it
      costs nothing.
    * A bare-name marker is one specific callable, and any longer identifier
      ending in it is a different one. Underscore is not a boundary there.

    A pattern ending in an identifier character also has a right boundary, so
    `request.data` no longer matches `request.database`.
    """
    if not pattern:
        return False
    dotted = "." in pattern
    tail_is_name = pattern[-1].isalnum() or pattern[-1] == "_"
    start = 0
    while True:
        i = text.find(pattern, start)
        if i < 0:
            return False
        start = i + 1
        if i > 0:
            previous = text[i - 1]
            if previous.isalnum() or (previous == "_" and not dotted):
                continue
        end = i + len(pattern)
        if tail_is_name and end < len(text):
            following = text[end]
            if following.isalnum() or following == "_":
                continue
        return True

def _flatten_sinks() -> list[tuple[str, str, str, str, tuple]]:
    """Return flat list: (pattern, vuln_type, severity, recommendation, requires).

    A rule may carry a fourth element: the argument-shape requirements that
    have to hold before the match counts. See analyzer.taint_shapes.
    """
    out = []
    for vuln_type, entries in SINKS.items():
        for entry in entries:
            pattern, severity, rec = entry[0], entry[1], entry[2]
            requires = normalize_requirements(entry[3] if len(entry) > 3 else None)
            out.append((pattern, vuln_type, severity, rec, requires))
    return out

FLAT_SINKS = _flatten_sinks()

def _load_yaml_rules(project_root: Path) -> dict | None:
    """Load taint rules from .flyto-rules.yaml (taint: block) or taint_rules.yaml.

    Lookup order (first hit wins):
      1. .flyto-rules.yaml `taint:` block  — preferred, unified with other rules
      2. .flyto-index/taint_rules.yaml     — legacy location
      3. taint_rules.yaml at project root  — legacy location
    """
    try:
        import yaml  # optional dependency
    except ImportError:
        logger.debug("PyYAML not installed; skipping taint yaml rules")
        return None

    unified = project_root / ".flyto-rules.yaml"
    if unified.is_file():
        try:
            with open(unified) as f:
                data = yaml.safe_load(f) or {}
            taint_block = data.get("taint")
            if isinstance(taint_block, dict) and taint_block:
                return taint_block
        except Exception as e:
            logger.debug("Failed to load %s: %s", unified, e)

    for path in (
        project_root / ".flyto-index" / "taint_rules.yaml",
        project_root / "taint_rules.yaml",
    ):
        if path.is_file():
            try:
                with open(path) as f:
                    return yaml.safe_load(f)
            except Exception as e:
                logger.debug("Failed to load %s: %s", path, e)
                return None
    return None

def _apply_yaml_rules(
    yaml_cfg: dict,
    sources: dict[str, list[str]],
    flat_sinks: list[tuple[str, str, str, str, tuple]],
    sanitizers: list[tuple[str, list[str]]],
) -> tuple[dict, list, list]:
    """Merge YAML rules into working copies of sources/sinks/sanitizers."""
    # Extra sources
    for entry in yaml_cfg.get("sources") or []:
        pat = entry.get("pattern", "")
        lang = entry.get("language", "python")
        if pat:
            sources.setdefault(lang, []).append(pat)

    # Extra sinks
    for entry in yaml_cfg.get("sinks") or []:
        pat = entry.get("pattern", "")
        vuln = entry.get("vuln_type", "custom")
        sev = entry.get("severity", "high")
        rec = entry.get("recommendation", "Review this sink for taint flow")
        requires = normalize_requirements(entry.get("requires"))
        if pat:
            flat_sinks.append((pat, vuln, sev, rec, requires))

    # Extra sanitizers
    for entry in yaml_cfg.get("sanitizers") or []:
        pat = entry.get("pattern", "")
        cleanses = entry.get("cleanses", ["*"])
        if pat:
            sanitizers.append((pat, cleanses))

    # Overrides: remove
    overrides = yaml_cfg.get("overrides", {})
    remove_src = set(overrides.get("remove_sources", []))
    remove_snk = set(overrides.get("remove_sinks", []))

    if remove_src:
        for lang in sources:
            sources[lang] = [s for s in sources[lang] if s not in remove_src]
    if remove_snk:
        flat_sinks = [s for s in flat_sinks if s[0] not in remove_snk]

    return sources, flat_sinks, sanitizers

