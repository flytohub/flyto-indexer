"""Built-in and project-configured taint propagation policy.

Propagation defaults live outside the AST engine so adding or reviewing a
propagator does not expand the main traversal authority.
"""

from __future__ import annotations


_RECEIVER_PROPAGATORS = frozenset({
    "append", "extend", "add", "insert", "update", "setdefault",
    "MergeFrom", "CopyFrom", "MergeFromString", "ParseFromString",
})

_POSITIONAL_PROPAGATORS = {
    "parse_dict": (0, 1),
    "ParseDict": (0, 1),
    "Parse": (0, 1),
    "Merge": (0, 1),
}

def _yaml_propagators(yaml_cfg: dict) -> tuple[set[str], dict[str, tuple[int, int]]]:
    """Parse `taint.propagators` from .flyto-rules.yaml.

    Two shapes, matched by the callee's short name:
      - receiver: `{name: my_add, receiver: true}` — a tainted argument taints
        the receiver (`recv.my_add(taint)`).
      - positional: `{name: my_populate, from: 0, to: 1}` — a tainted `from`
        argument taints the `to` argument (`my_populate(src, dst)`).
    """
    extra_receiver: set[str] = set()
    extra_positional: dict[str, tuple[int, int]] = {}
    for entry in yaml_cfg.get("propagators", []) or []:
        name = entry.get("name") or entry.get("pattern") or ""
        if not name:
            continue
        if entry.get("to") is not None and entry.get("from") is not None:
            try:
                extra_positional[name] = (int(entry["from"]), int(entry["to"]))
            except (TypeError, ValueError):
                continue
        elif entry.get("receiver"):
            extra_receiver.add(name)
    return extra_receiver, extra_positional

