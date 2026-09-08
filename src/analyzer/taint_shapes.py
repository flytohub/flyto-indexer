"""Argument-shape gates for sink rules.

A sink pattern is matched as a substring, so a rule that wants
``collection.find(`` has to name the receiver -- and then it misses
``users.find(``, ``self.db.orders.find(``, and every other name a project gives
its collection. Dropping the receiver (``.find(``) matches all of them and also
matches ``line.find(",")``, which is `str.find` and not a database at all.

The distinguishing fact is not the receiver's name, it is the shape of the
argument: a Mongo query takes a mapping, `str.find` takes a string. So a rule
can state that:

    sinks:
      - pattern: ".find("
        vuln_type: nosql_injection
        requires:
          - {arg: 0, shape: mapping}

Requirements are evaluated against the call's AST and every one must hold.

    {arg: 0 | "any" | "after_first", shape: ...}  argument shape
    {callee_tail: ["re.search", ...]}             dotted callee ends here
    {keyword: "shell", equals: true}              keyword argument value
    {min_args: 1} / {max_args: 1}                 argument count
    {not: {...}}                                  negation of one requirement

Shapes fail open, on purpose. ``shape: mapping`` passes unless the argument is
*provably* something else -- a string literal, or a name bound to one earlier in
the same function. An unknown expression stays a candidate, because a gate that
guesses is a gate that drops real flows silently.
"""

import ast

# Literal shapes this can prove. Anything else answers None, meaning unknown.
_MAPPING = "mapping"
_SEQUENCE = "sequence"
_SCALAR = "scalar"

_MAPPING_BUILDERS = {"dict", "OrderedDict", "defaultdict"}
_SEQUENCE_BUILDERS = {"list", "tuple", "set", "frozenset"}


def _unwrap(expr: ast.expr) -> ast.expr:
    while isinstance(expr, ast.Await):
        expr = expr.value
    return expr


def provable_shape(expr: ast.expr, bindings: "dict[str, ast.expr] | None" = None,
                   _depth: int = 0) -> "str | None":
    """The shape of `expr` when it can be proven, else None.

    `bindings` maps a local name to the literal it was last assigned, so
    ``sep = ","`` followed by ``line.find(sep)`` is provably a scalar.
    """
    expr = _unwrap(expr)

    if isinstance(expr, (ast.Dict, ast.DictComp)):
        return _MAPPING
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set, ast.ListComp, ast.SetComp)):
        return _SEQUENCE
    if isinstance(expr, ast.Constant):
        return _SCALAR if expr.value is not None else None
    if isinstance(expr, ast.JoinedStr):
        return _SCALAR  # an f-string is a string, though not a constant one
    if isinstance(expr, ast.Call):
        name = expr.func.id if isinstance(expr.func, ast.Name) else None
        if name in _MAPPING_BUILDERS:
            return _MAPPING
        if name in _SEQUENCE_BUILDERS:
            return _SEQUENCE
        return None
    if isinstance(expr, ast.Name) and bindings and _depth < 4:
        bound = bindings.get(expr.id)
        if bound is not None:
            return provable_shape(bound, bindings, _depth + 1)
    return None


def is_constant_literal(expr: ast.expr, bindings=None, _depth: int = 0) -> bool:
    """Whether `expr` is written out in the source rather than assembled.

    Separate from `provable_shape` because an f-string is a string but is not
    constant: `re.compile(f"^{prefix}")` is exactly the dynamic pattern the
    redos rule is looking for.
    """
    expr = _unwrap(expr)
    if isinstance(expr, ast.Constant):
        return True
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        parts = list(getattr(expr, "elts", []))
        if isinstance(expr, ast.Dict):
            parts = [v for v in expr.values if v is not None]
            parts += [k for k in expr.keys if k is not None]
        return all(is_constant_literal(part, bindings, _depth + 1) for part in parts)
    if isinstance(expr, ast.Name) and bindings and _depth < 4:
        bound = bindings.get(expr.id)
        if bound is not None:
            return is_constant_literal(bound, bindings, _depth + 1)
    return False


def _selected_args(call: ast.Call, position) -> "list[ast.expr] | None":
    """The arguments a requirement addresses, or None when there are none."""
    if position == "any":
        chosen = list(call.args)
    elif position == "after_first":
        chosen = list(call.args[1:])
    elif isinstance(position, bool) or not isinstance(position, int):
        return None
    elif position < len(call.args):
        chosen = [call.args[position]]
    else:
        return None
    return chosen or None


def _shape_holds(call: ast.Call, requirement: dict, bindings) -> bool:
    wanted = requirement.get("shape")
    chosen = _selected_args(call, requirement.get("arg", 0))
    if chosen is None:
        # The rule addresses an argument the call does not have.
        return False
    if not wanted:
        return True

    def one(arg: ast.expr) -> bool:
        if wanted == "constant":
            return is_constant_literal(arg, bindings)
        if wanted == "dynamic":
            return not is_constant_literal(arg, bindings)
        proven = provable_shape(arg, bindings)
        if proven is None:
            return True  # unknown stays a candidate
        return proven == wanted

    # "any" asks whether some argument has the shape; a fixed position and
    # "after_first" ask whether all of the ones named do.
    if requirement.get("arg") == "any":
        return any(one(arg) for arg in chosen)
    return all(one(arg) for arg in chosen)


def _callee_tail_holds(call_str: str, tails: "list[str]") -> bool:
    """The dotted callee ends with one of `tails`, at a name boundary.

    ``re.search`` must not be satisfied by ``store.search`` -- the segment
    before the tail has to end the previous name, not continue it.
    """
    for tail in tails:
        if call_str == tail:
            return True
        if call_str.endswith(tail):
            prefix = call_str[: -len(tail)]
            if prefix == "" or prefix.endswith("."):
                return True
    return False


def _keyword_holds(call: ast.Call, requirement: dict) -> bool:
    name = requirement.get("keyword")
    for keyword in call.keywords:
        if keyword.arg != name:
            continue
        if "equals" not in requirement:
            return True
        value = keyword.value
        return (
            isinstance(value, ast.Constant) and value.value == requirement["equals"]
        )
    return False


def requirement_holds(
    call: ast.Call, call_str: str, requirement: dict, bindings=None,
) -> bool:
    """Whether one requirement holds for this call."""
    if not isinstance(requirement, dict):
        return True
    if "not" in requirement:
        return not requirement_holds(call, call_str, requirement["not"], bindings)
    if "callee_tail" in requirement:
        tails = requirement["callee_tail"]
        if isinstance(tails, str):
            tails = [tails]
        return _callee_tail_holds(call_str, list(tails))
    if "keyword" in requirement:
        return _keyword_holds(call, requirement)
    if "min_args" in requirement and len(call.args) < requirement["min_args"]:
        return False
    if "max_args" in requirement and len(call.args) > requirement["max_args"]:
        return False
    if "arg" in requirement or "shape" in requirement:
        return _shape_holds(call, requirement, bindings)
    return True


def call_satisfies(call: ast.Call, call_str: str, requirements, bindings=None) -> bool:
    """Whether every requirement on a sink rule holds for this call."""
    if not requirements:
        return True
    return all(
        requirement_holds(call, call_str, requirement, bindings)
        for requirement in requirements
    )


def normalize_requirements(raw) -> tuple:
    """A rule's `requires:` as a tuple of dicts, whatever shape it was written in."""
    if not raw:
        return ()
    if isinstance(raw, dict):
        raw = [raw]
    return tuple(item for item in raw if isinstance(item, dict))
