"""Declaring project guards in `.flyto-rules.yaml`.

`agent_guards.py` answers whether a call is a guard. This writes the answer a
project gives for itself. They are separate because recognition runs on every
scan and writing runs once, by hand, when somebody decides their helper counts.

Mirrors `taint_dsl.py`: a project already extends sanitizers this way, and a
guard is the same kind of statement — "this function of ours does that job".
Without a writer the only way to declare a guard is to hand-edit YAML, which is
how a feature ends up unused.
"""

from __future__ import annotations

from pathlib import Path

from .agent_guards import DOMAINS

RULES_FILE = ".flyto-rules.yaml"


def _load(project_root: Path) -> tuple[dict, Path]:
    import yaml

    path = project_root / RULES_FILE
    if not path.exists():
        return {}, path
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return (loaded if isinstance(loaded, dict) else {}), path


def add_agent_guard(project_root: Path, domain: str, name: str) -> dict:
    """Declare `name` as a guard for `domain` in this project's rules file.

    A declared guard is conclusive: the analyzer stops reporting the operations
    it protects. That is a claim the project is making about its own code, which
    is why it has to be written down rather than inferred.
    """
    if domain not in DOMAINS:
        return {"error": f"unknown guard domain: {domain}", "valid": list(DOMAINS)}
    name = name.strip()
    if not name:
        return {"error": "guard name is empty"}
    try:
        import yaml
    except ImportError:
        return {"error": "PyYAML not installed"}

    config, path = _load(project_root)
    section = config.get("agent_guards")
    if not isinstance(section, dict):
        section = {}
    existing = section.get(domain)
    if isinstance(existing, str):
        existing = [existing]
    if not isinstance(existing, list):
        existing = []
    if name in existing:
        return {"status": "already_exists", "domain": domain, "guard": name}

    section[domain] = existing + [name]
    config["agent_guards"] = section
    path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return {"status": "added", "domain": domain, "guard": name, "file": str(path)}


def remove_agent_guard(project_root: Path, domain: str, name: str) -> dict:
    """Undeclare a guard. Built-in recognition is unaffected."""
    if domain not in DOMAINS:
        return {"error": f"unknown guard domain: {domain}", "valid": list(DOMAINS)}
    try:
        import yaml
    except ImportError:
        return {"error": "PyYAML not installed"}

    config, path = _load(project_root)
    section = config.get("agent_guards")
    if not isinstance(section, dict):
        return {"status": "not_found", "domain": domain, "guard": name}
    existing = section.get(domain)
    if isinstance(existing, str):
        existing = [existing]
    if not isinstance(existing, list) or name not in existing:
        return {"status": "not_found", "domain": domain, "guard": name}

    remaining = [n for n in existing if n != name]
    if remaining:
        section[domain] = remaining
    else:
        section.pop(domain, None)
    if section:
        config["agent_guards"] = section
    else:
        config.pop("agent_guards", None)
    path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return {"status": "removed", "domain": domain, "guard": name, "file": str(path)}


def list_agent_guards(project_root: Path) -> dict:
    """Show what this project declared. Built-in names are not included.

    Keeping built-ins out is deliberate: this answers "what did we decide",
    which is the thing a reviewer needs to check, not "what does the tool know".
    """
    config, _ = _load(project_root)
    section = config.get("agent_guards")
    if not isinstance(section, dict):
        return {"declared": {}, "use_shape": True}
    declared = {}
    for domain in DOMAINS:
        names = section.get(domain)
        if isinstance(names, str):
            names = [names]
        if isinstance(names, list) and names:
            declared[domain] = [n for n in names if isinstance(n, str)]
    return {"declared": declared, "use_shape": bool(section.get("use_shape", True))}
