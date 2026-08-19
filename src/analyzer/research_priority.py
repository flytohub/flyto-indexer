"""Security Research Priority ranking.

Turns the scanners this repository already has into one ordered answer to a
single question a human security researcher actually asks:

    "Which parts of this codebase are worth my next hour?"

Every individual signal already exists here and is useful on its own — taint
reachability, sink severity, function complexity, git churn, test gaps, weak
error handling. None of them is a ranking. A researcher handed 200 findings
reads none of them; handed 20 ordered code paths with the reason attached,
they read the first one.

Design constraints:
  - **Ranked by evidence strength, and the evidence is named.** A proven
    source-to-sink flow outranks an unproven one, and every candidate carries
    the `evidence` tier that put it there:

        proven_flow_cross_function  the taint engine traced input across calls
        proven_flow_in_function     traced within one function
        sanitized_flow              a sanitizer claims this flow; verify it
        source_and_sink_same_function   both present, link NOT proven
        sink_with_file_source       sink here, input enters this file elsewhere
        sink_only_entry_point       dangerous sink on an indexed entry point

    The unproven tiers exist because on real projects the proven ones are
    frequently empty — the engine's cross-function resolution is name-based,
    so a large FastAPI service can show hundreds of sources and thousands of
    sinks and still yield zero complete flows. Returning nothing there would
    be accurate and useless. These tiers say "worth reading", never "is a
    bug", and they are labelled so a researcher can tell the difference at a
    glance. Pass `include_unproven=False` for proven flows only.
  - **One candidate per function.** Ten flows in one function are one lead
    with `flow_count: 10`, not ten leads.
  - **No fabricated signal.** A signal that cannot be computed (no git repo,
    no index, non-Python file) is `None`, is excluded from the weighted mean,
    and is named in `coverage.signals_unavailable`. Scores are renormalized
    over the signals that were actually available, so a missing signal never
    silently reads as a zero.
  - **Truncation is reported.** The taint engine has hard caps. When a scan
    hits one, `coverage.truncated` says so — "found nothing" and "stopped
    looking" must not look alike.

The score is a means of ordering, never a verdict. `reasons` carries why each
candidate placed where it did so a human can disagree with the ranking in a
few seconds instead of re-deriving it.
"""
from __future__ import annotations

import ast
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

try:  # package-relative first, matching the rest of src/analyzer
    from .complexity import _is_test_file, measure_indexed_function
    from .error_handling import analyze_error_handling
    from .taint import (
        CATEGORY_SEVERITY,
        FLAT_SINKS,
        MAX_FINDINGS,
        MAX_FUNCTIONS,
        SKIP_DIR_PATTERNS,
        TaintAnalyzer,
        _apply_yaml_rules,
        _load_yaml_rules,
    )
    from .taint_rules import SANITIZERS, SOURCES
except ImportError:  # pragma: no cover - flat-layout fallback used by the CLI
    from analyzer.complexity import (  # type: ignore
        _is_test_file,
        measure_indexed_function,
    )
    from analyzer.error_handling import analyze_error_handling  # type: ignore
    from analyzer.taint import (  # type: ignore
        CATEGORY_SEVERITY,
        FLAT_SINKS,
        MAX_FINDINGS,
        MAX_FUNCTIONS,
        SKIP_DIR_PATTERNS,
        TaintAnalyzer,
        _apply_yaml_rules,
        _load_yaml_rules,
    )
    from analyzer.taint_rules import SANITIZERS, SOURCES  # type: ignore


# ── Tunables ────────────────────────────────────────────────────────────────

#: Relative pull of each signal. Weights are renormalized per candidate over
#: the signals that could actually be computed, so these are ratios, not a
#: budget that must sum to 1.0.
DEFAULT_WEIGHTS: dict[str, float] = {
    "reachability": 0.28,     # unsanitized source -> sink, cross-function is worse
    "sink_severity": 0.22,    # what the sink can do if reached
    "entry_exposure": 0.10,   # the function sits on an indexed route / entry point
    "complexity": 0.13,       # hard to review by eye => bugs survive review
    "churn": 0.11,            # recently and repeatedly edited
    "test_gap": 0.09,         # no test file maps to this source file
    "error_handling": 0.07,   # swallowed / bare except around the sink
}

_SEVERITY_VALUE = {"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.3}

#: Commit count at which the churn signal saturates.
_CHURN_SATURATION = 20

#: Complexity score at which the complexity signal saturates. The canonical
#: formula in complexity.py only starts scoring past its own thresholds, so a
#: score of 25 is already an unusually dense function.
_COMPLEXITY_SATURATION = 25

#: Error-handling categories that make a reachable sink harder to reason about.
_ERROR_CATEGORY_VALUE = {
    "empty_except": 1.0,
    "swallowed_error": 0.9,
    "bare_except": 0.8,
    "no_error_handling": 0.5,
    "unhandled_async": 0.5,
}

#: Reachability value per evidence tier. Proven flows outrank everything an
#: unproven pattern pass can produce — the gap is the point.
EVIDENCE_REACHABILITY = {
    "proven_flow_cross_function": 1.0,
    "proven_flow_in_function": 0.8,
    "source_and_sink_same_function": 0.5,
    "sink_with_file_source": 0.35,
    "sanitized_flow": 0.3,
    "sink_only_entry_point": 0.2,
    "operator_input_and_sink": 0.15,
}

_EVIDENCE_REASON = {
    "proven_flow_cross_function": "untrusted input reaches this sink across {hops} hops",
    "proven_flow_in_function": "untrusted input reaches this sink in-function",
    "source_and_sink_same_function": (
        "input and a dangerous sink share this function — link NOT proven, read it"
    ),
    "sink_with_file_source": (
        "dangerous sink here, untrusted input enters elsewhere in this file — link NOT proven"
    ),
    "sink_only_entry_point": "dangerous sink in an indexed entry-point file — no input traced",
    "operator_input_and_sink": (
        "sink fed by operator input (argv / prompt), not a remote request — "
        "only interesting under a local threat model"
    ),
    "sanitized_flow": "sanitizer claimed on this flow — verify it covers this sink",
}

#: Sink patterns that only mean anything in JavaScript. The rule tables are
#: shared across languages, so scanning Python source text for them matched
#: JS written inside Python string literals (gradio ships `.innerHTML = ...`
#: inside template strings) and produced XSS leads in .py files.
_JS_ONLY_SINKS = frozenset({
    ".innerHTML", "document.write(", "v-html", ".outerHTML",
    "insertAdjacentHTML(", "dangerouslySetInnerHTML",
})

#: Sources supplied by whoever runs the program, not by a remote attacker.
#: They are real sources for a CLI threat model and stay in the list, but a
#: lead built on them must not outrank one built on a request.
_OPERATOR_SOURCES = ("input(", "sys.argv", "argparse", "click.prompt(")

#: Files parsed by the unproven pass before it stops. The pass is a second
#: read of the tree; this keeps it proportional to the taint scan itself.
MAX_UNPROVEN_FILES = 3000
#: Unproven candidates kept before ranking, worst-severity first.
MAX_UNPROVEN_CANDIDATES = 500

DEFAULT_TOP_N = 20
#: Hard ceiling on returned candidates regardless of caller request.
MAX_TOP_N = 200


# ── Data model ──────────────────────────────────────────────────────────────


@dataclass
class ResearchCandidate:
    """One code path worth a human researcher's time, with its reasoning."""

    file: str
    function: str
    line: int
    category: str
    severity: str
    score: float = 0.0
    signals: dict[str, float | None] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    source_expr: str = ""
    sink_expr: str = ""
    flow_path: list[str] = field(default_factory=list)
    flow_count: int = 1
    categories: list[str] = field(default_factory=list)
    sanitized: bool = False
    evidence: str = "proven_flow_in_function"
    proven: bool = True

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "function": self.function,
            "line": self.line,
            "score": round(self.score, 1),
            "category": self.category,
            "categories": self.categories,
            "severity": self.severity,
            "evidence": self.evidence,
            "proven": self.proven,
            "flow_count": self.flow_count,
            "sanitized": self.sanitized,
            "source": self.source_expr,
            "sink": self.sink_expr,
            "flow_path": self.flow_path,
            "signals": {
                name: (None if value is None else round(value, 3))
                for name, value in self.signals.items()
            },
            "reasons": self.reasons,
        }


@dataclass
class ResearchPriorityReport:
    """Ranked candidates plus an honest account of what the scan could see."""

    candidates: list[ResearchCandidate] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    total_candidates: int = 0
    total_flows: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "returned": len(self.candidates),
            "total_candidates": self.total_candidates,
            "total_flows": self.total_flows,
            "weights": self.weights,
            "coverage": self.coverage,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


# ── Signal collectors ───────────────────────────────────────────────────────


def _normalize(path: str) -> str:
    """Repo-relative, forward-slashed. Only a leading `./` is stripped —
    stripping any leading dot would turn `.claude/x` into `claude/x` and make
    hidden-directory checks silently miss."""
    text = str(path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _is_hidden_path(rel: str) -> bool:
    """True for anything inside a dot-directory (.claude/worktrees, .venv, …).

    Those trees are copies or tooling, not the code under review; ranking them
    produces duplicate leads that push real ones off the list.
    """
    return any(part.startswith(".") for part in rel.split("/")[:-1])


def _churn_by_file(project_root: Path, since_days: int) -> tuple[dict[str, int], str]:
    """Commits per file over the window. Returns ({}, reason) when unavailable.

    Shares `git_history` with the `git_churn` tool, so a repeated scan in the
    same process reuses one `git log` and churn here means exactly what
    `git_churn` reports. The analyzer layer may not import the tool surface,
    which is why that plumbing lives one layer down.
    """
    try:
        try:
            from ..git_history import find_git_root, get_cached_log
        except ImportError:  # pragma: no cover - flat-layout fallback
            from git_history import find_git_root, get_cached_log  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive
        return {}, f"git_churn: git intelligence unavailable ({exc})"

    git_root = find_git_root(str(project_root))
    if not git_root:
        return {}, "git_churn: not inside a git repository"

    try:
        entries = get_cached_log(git_root, (f"--since={since_days} days ago",))
    except Exception as exc:
        return {}, f"git_churn: git log failed ({exc})"

    # Paths from git are repo-root relative; candidates are project-root
    # relative. Strip the project prefix when the project is a subdirectory.
    try:
        prefix = Path(project_root).resolve().relative_to(Path(git_root).resolve())
        prefix_str = "" if str(prefix) == "." else str(prefix).replace("\\", "/")
    except ValueError:
        prefix_str = ""

    counts: dict[str, int] = {}
    for entry in entries:
        for raw in entry.get("files", []):
            rel = _normalize(raw)
            if prefix_str:
                if not rel.startswith(prefix_str + "/"):
                    continue
                rel = rel[len(prefix_str) + 1:]
            if rel:
                counts[rel] = counts.get(rel, 0) + 1
    if not counts:
        return {}, f"git_churn: no commits in the last {since_days} days"
    return counts, ""


def _entry_point_files(index: dict | None) -> set[str]:
    """Files the index marks as routes / API handlers / entry points."""
    if not index:
        return set()
    files: set[str] = set()

    routes = index.get("routes") or {}
    if isinstance(routes, dict):
        candidates: list = []
        for value in routes.values():
            candidates.extend(value if isinstance(value, list) else [value])
    else:
        candidates = list(routes)
    for item in candidates:
        if isinstance(item, dict):
            path = item.get("file") or item.get("path") or ""
            if path:
                files.add(_normalize(path))

    for item in index.get("api_endpoints") or []:
        if isinstance(item, dict):
            path = item.get("file") or item.get("path") or ""
            if path:
                files.add(_normalize(path))

    for item in index.get("entry_points") or []:
        if isinstance(item, dict):
            path = item.get("file") or item.get("path") or ""
            if path:
                files.add(_normalize(path))
        elif isinstance(item, str):
            files.add(_normalize(item))

    return files


def _error_handling_index(project_root: Path) -> tuple[dict[tuple[str, str], float], str]:
    """Map (file, function) -> worst error-handling signal value."""
    try:
        report = analyze_error_handling(project_root)
    except Exception as exc:  # pragma: no cover - defensive
        return {}, f"error_handling: analysis failed ({exc})"

    worst: dict[tuple[str, str], float] = {}
    for issue in report.issues:
        value = _ERROR_CATEGORY_VALUE.get(issue.category, 0.4)
        key = (_normalize(issue.file), issue.func_name or "")
        if value > worst.get(key, 0.0):
            worst[key] = value
    return worst, ""


class _EnclosingFunction(ast.NodeVisitor):
    """Find the innermost function definition containing a 1-based line."""

    def __init__(self, line: int):
        self.line = line
        self.best: ast.FunctionDef | ast.AsyncFunctionDef | None = None

    def _consider(self, node) -> None:
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start)
        if start <= self.line <= end and (
            self.best is None or start >= self.best.lineno
        ):
            self.best = node

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._consider(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._consider(node)
        self.generic_visit(node)


class _FileFacts:
    """Per-file lazily computed facts: enclosing function name + complexity."""

    def __init__(self, project_root: Path):
        self._root = project_root
        self._cache: dict[str, tuple[str, ast.Module] | None] = {}

    def _load(self, rel_path: str):
        if rel_path in self._cache:
            return self._cache[rel_path]
        result = None
        full = self._root / rel_path
        if full.suffix == ".py" and full.is_file():
            try:
                content = full.read_text(encoding="utf-8", errors="ignore")
                result = (content, ast.parse(content))
            except (OSError, SyntaxError, ValueError):
                result = None
        self._cache[rel_path] = result
        return result

    def _measure(self, rel_path: str, content: str, node) -> float | None:
        lines = content.split("\n")
        start = max(node.lineno - 1, 0)
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        body = "\n".join(lines[start:end])
        symbol = {
            "type": "function",
            "path": rel_path,
            "name": node.name,
            "params": [a.arg for a in node.args.args],
            "start_line": node.lineno,
        }
        measured = measure_indexed_function(f"{rel_path}:{node.name}", symbol, body)
        if measured is None:
            return None
        return min(1.0, float(measured.get("score", 0)) / _COMPLEXITY_SATURATION)

    def enclosing_by_name(self, rel_path: str, func_name: str) -> tuple[str, float | None]:
        """Complexity for a function addressed by name rather than by line."""
        loaded = self._load(rel_path)
        if loaded is None or not func_name:
            return func_name, None
        content, tree = loaded
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_name
            ):
                return func_name, self._measure(rel_path, content, node)
        return func_name, None

    def enclosing(self, rel_path: str, line: int) -> tuple[str, float | None]:
        """Return (function_name, complexity_signal 0..1 or None)."""
        loaded = self._load(rel_path)
        if loaded is None:
            return "", None
        content, tree = loaded
        finder = _EnclosingFunction(line)
        finder.visit(tree)
        node = finder.best
        if node is None:
            return "", None

        return node.name, self._measure(rel_path, content, node)


def _test_gap_lookup(index: dict | None, project: str | None):
    """Return (callable(file)->bool has_test, unavailable_reason)."""
    if not index or not index.get("symbols"):
        return None, "test_gap: no index available (run `flyto-index scan` first)"
    try:
        try:
            from ..test_mapper import TestMapper
        except ImportError:  # pragma: no cover - flat-layout fallback
            from test_mapper import TestMapper  # type: ignore
        mapper = TestMapper(index, project)
        mapper.build()
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"test_gap: test mapping failed ({exc})"

    def has_test(path: str) -> bool:
        return bool(mapper.find_test(path, project))

    return has_test, ""


# ── Ranking ─────────────────────────────────────────────────────────────────


def _evidence_for_flow(flow) -> str:
    if flow.sanitized:
        return "sanitized_flow"
    hops = len(flow.path or flow.flow_chain or [])
    if hops > 1:
        # Cross-function reach: the sink's own file does not show the danger,
        # which is exactly the class a file-local reviewer misses.
        return "proven_flow_cross_function"
    return "proven_flow_in_function"


def _score_candidate(
    candidate: ResearchCandidate,
    weights: dict[str, float],
) -> float:
    """Weighted mean over the signals that were actually measurable.

    Renormalizing (rather than treating an unmeasurable signal as 0) is what
    keeps a repo with no git history from scoring uniformly lower than one
    with it.
    """
    total_weight = 0.0
    total = 0.0
    for name, value in candidate.signals.items():
        if value is None:
            continue
        weight = weights.get(name, 0.0)
        if weight <= 0:
            continue
        total_weight += weight
        total += weight * value
    if total_weight <= 0:
        return 0.0
    return 100.0 * total / total_weight


def _build_reasons(candidate: ResearchCandidate, churn_commits: int | None) -> list[str]:
    """Plain-language why. First line is always the evidence tier."""
    reasons: list[str] = []
    sig = candidate.signals

    template = _EVIDENCE_REASON.get(candidate.evidence, candidate.evidence)
    reasons.append(template.format(hops=len(candidate.flow_path)))

    if candidate.severity in ("critical", "high"):
        reasons.append(f"{candidate.category} sink ({candidate.severity})")
    if candidate.flow_count > 1:
        reasons.append(f"{candidate.flow_count} distinct flows land in this function")

    if sig.get("entry_exposure"):
        reasons.append("file is an indexed route / entry point")

    complexity = sig.get("complexity")
    if complexity is not None and complexity >= 0.4:
        reasons.append("dense function — easy for a reviewer to miss the path")

    churn = sig.get("churn")
    if churn is not None and churn >= 0.3 and churn_commits:
        reasons.append(f"changed in {churn_commits} recent commits")

    if sig.get("test_gap") == 1.0:
        reasons.append("no test file maps to this source file")

    error = sig.get("error_handling")
    if error is not None and error >= 0.8:
        reasons.append("errors are swallowed near this path")

    return reasons


# ── Unproven-evidence pass ──────────────────────────────────────────────────


def _is_attack_surface(rel_path: str) -> bool:
    """False for demo, example, script and generated trees.

    Ranking those next to library code is what buried gradio's real upload
    path under nine demo apps and CLI helpers: an `open()` in `demo/` is not a
    lead, it is sample code shipped for humans to read.
    """
    try:
        try:
            from ..profile.filesystem import classify_path
        except ImportError:  # pragma: no cover - flat-layout fallback
            from profile.filesystem import classify_path  # type: ignore
        if classify_path(rel_path) != "source":
            return False
    except Exception:  # pragma: no cover - defensive
        pass
    head = rel_path.split("/", 1)[0].lower()
    return head not in {"scripts", "script", "tools", "bin", "docs", "benchmarks"}


def _is_operator_source(source_expr: str) -> bool:
    """True when the input comes from whoever runs the program."""
    return any(marker in source_expr for marker in _OPERATOR_SOURCES)


def _rule_tables(project_root: Path):
    """Sources / sinks / sanitizers, with the project's own YAML rules merged.

    Sharing the loader with the taint engine means a sink declared through
    `add_taint_sink` ranks here too, instead of being visible to one tool only.
    """
    sources = {lang: list(pats) for lang, pats in SOURCES.items()}
    flat_sinks = list(FLAT_SINKS)
    sanitizers = list(SANITIZERS)
    try:
        yaml_cfg = _load_yaml_rules(project_root)
        if yaml_cfg:
            sources, flat_sinks, sanitizers = _apply_yaml_rules(
                yaml_cfg, sources, flat_sinks, sanitizers
            )
    except Exception:  # pragma: no cover - defensive, rules are optional
        pass
    flat_sinks = [entry for entry in flat_sinks if entry[0] not in _JS_ONLY_SINKS]
    return sources.get("python", []), flat_sinks, [s[0] for s in sanitizers]


def _sink_present(text: str, pattern: str) -> bool:
    """Substring match on a token boundary.

    A bare `pattern in text` made `exec(` match `create_subprocess_exec(` and
    `Template(` match `ResourceTemplate(`.
    """
    start = 0
    while True:
        idx = text.find(pattern, start)
        if idx < 0:
            return False
        start = idx + 1
        if idx > 0 and not pattern.startswith("."):
            prev = text[idx - 1]
            if prev.isalnum() or prev == "_":
                continue
        # `.raw` must not match `.rawtext`; a pattern ending in `(` is already
        # delimited and is followed by its arguments.
        end = idx + len(pattern)
        if (
            (pattern[-1].isalnum() or pattern[-1] == "_")
            and end < len(text)
            and (text[end].isalnum() or text[end] == "_")
        ):
            continue
        return True


def _worst_sink(text: str, flat_sinks) -> tuple[str, str, str] | None:
    """Return (pattern, category, severity) for the worst sink present."""
    best = None
    best_rank = -1.0
    for pattern, category, severity, _rec in flat_sinks:
        if not _sink_present(text, pattern):
            continue
        rank = _SEVERITY_VALUE.get(severity, 0.3)
        if rank > best_rank:
            best_rank = rank
            best = (pattern, category, severity)
    return best


#: A full statement shape, not a bare keyword. Matching on "from" or "where"
#: alone pulls in ordinary prose — an error message like
#: f"Cannot transition from {state}" scored as dynamic SQL and put an ORM
#: endpoint at the top of the list.
_SQL_STATEMENT = re.compile(
    r"\bselect\b.*\bfrom\b"
    r"|\binsert\s+into\b"
    r"|\bupdate\b.*\bset\b"
    r"|\bdelete\s+from\b"
    r"|\bwhere\b.*[=<>]",
    re.IGNORECASE | re.DOTALL,
)


def _looks_like_sql(text: str) -> bool:
    return bool(_SQL_STATEMENT.search(text))


def _has_dynamic_sql(node) -> bool:
    """True when the function builds a SQL string at runtime.

    Without this, every ORM endpoint scores as a SQL-injection lead: an
    SQLAlchemy handler calls `db.execute(query)` on a `select()` with bound
    parameters, which matches the `db.execute` sink and reads as dangerous
    while being exactly the safe pattern. A researcher discards those in
    seconds; a ranking that spends its top ten slots on them is worthless. So
    the SQL tier requires evidence of runtime string construction — an
    f-string, concatenation, %-format, .format(), or a raw `text(...)` escape
    hatch — over something that reads as SQL.
    """
    for child in ast.walk(node):
        # f"... WHERE id = {x}"
        if isinstance(child, ast.JoinedStr):
            literal = "".join(
                part.value for part in child.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if _looks_like_sql(literal):
                return True
        # "SELECT ..." + x   /   "SELECT ... %s" % x
        elif isinstance(child, ast.BinOp) and isinstance(child.op, (ast.Add, ast.Mod)):
            for side in (child.left, child.right):
                if (
                    isinstance(side, ast.Constant)
                    and isinstance(side.value, str)
                    and _looks_like_sql(side.value)
                ):
                    return True
        elif isinstance(child, ast.Call):
            func = child.func
            # "SELECT ... {}".format(x)
            if isinstance(func, ast.Attribute) and func.attr == "format":
                target = func.value
                if (
                    isinstance(target, ast.Constant)
                    and isinstance(target.value, str)
                    and _looks_like_sql(target.value)
                ):
                    return True
            # sqlalchemy.text("...") — the documented raw-SQL escape hatch
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "text" and child.args:
                return True
    return False


def _first_present(text: str, patterns) -> str:
    for pattern in patterns:
        if pattern in text:
            return pattern
    return ""


def _unproven_seeds(
    project_root: Path,
    entry_files: set[str],
    covered: set[tuple[str, str]],
    *,
    limit_files: int = MAX_UNPROVEN_FILES,
    limit_candidates: int = MAX_UNPROVEN_CANDIDATES,
) -> tuple[list[dict], bool, int]:
    """Find functions worth reading that no complete flow reached.

    A second, deliberately weaker pass: it proves nothing, so every seed it
    returns is labelled with the tier of evidence behind it. Returns
    (seeds, truncated, parameterized_sql_suppressed).
    """
    source_patterns, flat_sinks, sanitizer_patterns = _rule_tables(project_root)
    seeds: list[dict] = []
    files_scanned = 0
    truncated = False
    orm_suppressed = 0

    for path in sorted(project_root.rglob("*.py")):
        if files_scanned >= limit_files:
            truncated = True
            break
        try:
            rel = _normalize(str(path.relative_to(project_root)))
        except ValueError:  # pragma: no cover - defensive
            continue
        if (
            SKIP_DIR_PATTERNS.search(rel)
            or _is_hidden_path(rel)
            or _is_test_file(rel)
            or not _is_attack_surface(rel)
        ):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        files_scanned += 1

        if not _worst_sink(content, flat_sinks):
            continue
        file_source = _first_present(content, source_patterns)
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            continue

        file_lines = content.split("\n")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            key = (rel, node.name)
            if key in covered:
                continue
            start = max(node.lineno - 1, 0)
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            body = "\n".join(file_lines[start:end])

            sink = _worst_sink(body, flat_sinks)
            if sink is None:
                continue
            sink_pattern, category, severity = sink

            if category == "sql_injection" and not _has_dynamic_sql(node):
                # Parameterized / ORM query. Not a lead.
                orm_suppressed += 1
                continue

            func_source = _first_present(body, source_patterns)
            if func_source:
                evidence = (
                    "operator_input_and_sink"
                    if _is_operator_source(func_source)
                    else "source_and_sink_same_function"
                )
                source_expr = func_source
            elif file_source:
                evidence = (
                    "operator_input_and_sink"
                    if _is_operator_source(file_source)
                    else "sink_with_file_source"
                )
                source_expr = file_source
            elif rel in entry_files:
                evidence = "sink_only_entry_point"
                source_expr = ""
            else:
                # A dangerous call with no input anywhere near it is not a
                # lead, it is noise. Drop it rather than pad the list.
                continue

            sanitizer = _first_present(body, sanitizer_patterns)
            seeds.append({
                "file": rel,
                "function": node.name,
                "line": node.lineno,
                "category": category,
                "severity": severity,
                "evidence": evidence,
                "sink": sink_pattern,
                "source": source_expr,
                "sanitizer": sanitizer,
            })
            covered.add(key)

    if len(seeds) > limit_candidates:
        seeds.sort(
            key=lambda s: (
                -_SEVERITY_VALUE.get(s["severity"], 0.3),
                -EVIDENCE_REACHABILITY.get(s["evidence"], 0.0),
                s["file"],
            ),
        )
        seeds = seeds[:limit_candidates]
        truncated = True

    return seeds, truncated, orm_suppressed


# ── Entry point ─────────────────────────────────────────────────────────────


def rank_research_priority(
    project_root: str | Path,
    *,
    index: dict | None = None,
    project: str | None = None,
    top_n: int = DEFAULT_TOP_N,
    since_days: int = 180,
    weights: dict[str, float] | None = None,
    include_sanitized: bool = True,
    include_unproven: bool = True,
) -> ResearchPriorityReport:
    """Rank the code paths most worth a security researcher's next hour.

    Args:
        project_root: Directory to analyze.
        index: An already-loaded flyto index. Optional; enables the test-gap
            and entry-point signals when present, and lets the taint engine
            trace across functions.
        project: Project name used to scope test mapping.
        top_n: Candidates to return (capped at MAX_TOP_N).
        since_days: Churn window.
        weights: Override for DEFAULT_WEIGHTS.
        include_sanitized: Keep flows a sanitizer claimed to neutralize. They
            rank low but stay visible, because a wrong sanitizer is a finding.
        include_unproven: Also seed candidates from the weaker evidence tiers
            (see module docstring). Without this, a project whose flows the
            engine cannot complete returns an empty list.

    Returns:
        ResearchPriorityReport — ordered candidates plus a coverage block that
        states which signals were unavailable, how many candidates came from
        each evidence tier, and whether any scan hit its caps.
    """
    started = time.monotonic()
    root = Path(project_root)
    effective_weights = dict(DEFAULT_WEIGHTS)
    if weights:
        effective_weights.update(weights)
    top_n = max(1, min(int(top_n), MAX_TOP_N))

    unavailable: list[str] = []

    analyzer = TaintAnalyzer(root, index=index)
    result = analyzer.analyze_full()
    flows = list(result.taint_flows)
    if include_sanitized:
        flows += list(result.suppressed_taint_flows)

    churn_counts, churn_reason = _churn_by_file(root, since_days)
    if churn_reason:
        unavailable.append(churn_reason)

    error_map, error_reason = _error_handling_index(root)
    if error_reason:
        unavailable.append(error_reason)

    has_test, test_reason = _test_gap_lookup(index, project)
    if test_reason:
        unavailable.append(test_reason)

    entry_files = _entry_point_files(index)
    if not entry_files:
        unavailable.append(
            "entry_exposure: no routes / entry points in the index for this project"
        )

    facts = _FileFacts(root)
    churn_commits_by_key: dict[tuple[str, str], int] = {}

    def build_signals(rel: str, func_name: str, reach: float, severity: str) -> dict:
        """Assemble one candidate's signal bundle. None means 'not measured'."""
        _name, complexity = facts.enclosing_by_name(rel, func_name)
        commits = churn_counts.get(rel)
        churn_value = None
        if churn_counts:
            churn_value = min(
                1.0,
                math.log1p(commits or 0) / math.log1p(_CHURN_SATURATION),
            )
        churn_commits_by_key[(rel, func_name)] = commits or 0
        return {
            "reachability": reach,
            "sink_severity": _SEVERITY_VALUE.get(severity, 0.3),
            "entry_exposure": (1.0 if rel in entry_files else 0.0) if entry_files else None,
            "complexity": complexity,
            "churn": churn_value,
            "test_gap": (None if has_test is None else (0.0 if has_test(rel) else 1.0)),
            "error_handling": (
                error_map.get((rel, func_name), 0.0) if error_map else None
            ),
        }

    by_key: dict[tuple[str, str], ResearchCandidate] = {}

    # ── Tier 1: proven flows from the taint engine ──
    for flow in flows:
        rel = _normalize(flow.sink_file or flow.file_path)
        line = flow.sink_line or flow.line
        func_name, _complexity = facts.enclosing(rel, line)
        key = (rel, func_name or f"line:{line}")

        evidence = _evidence_for_flow(flow)
        reach = EVIDENCE_REACHABILITY[evidence]
        severity = flow.severity or CATEGORY_SEVERITY.get(flow.category, "medium")

        existing = by_key.get(key)
        if existing is not None:
            existing.flow_count += 1
            if flow.category not in existing.categories:
                existing.categories.append(flow.category)
            # Keep the worst flow as this function's headline.
            better = (_SEVERITY_VALUE.get(severity, 0.3), reach) > (
                _SEVERITY_VALUE.get(existing.severity, 0.3),
                existing.signals.get("reachability") or 0.0,
            )
            if better:
                existing.category = flow.category
                existing.severity = severity
                existing.line = line
                existing.evidence = evidence
                existing.sanitized = flow.sanitized
                existing.source_expr = flow.source_expr
                existing.sink_expr = flow.sink_expr
                existing.flow_path = list(flow.path or flow.flow_chain or [])
                existing.signals["reachability"] = reach
                existing.signals["sink_severity"] = _SEVERITY_VALUE.get(severity, 0.3)
            continue

        by_key[key] = ResearchCandidate(
            file=rel,
            function=func_name,
            line=line,
            category=flow.category,
            severity=severity,
            source_expr=flow.source_expr,
            sink_expr=flow.sink_expr,
            flow_path=list(flow.path or flow.flow_chain or []),
            sanitized=flow.sanitized,
            evidence=evidence,
            proven=True,
            categories=[flow.category],
            signals=build_signals(rel, func_name, reach, severity),
        )

    # ── Tier 2: unproven leads, clearly labelled as such ──
    unproven_truncated = False
    orm_suppressed = 0
    tier_counts: dict[str, int] = {}
    if include_unproven:
        covered = {(c.file, c.function) for c in by_key.values() if c.function}
        seeds, unproven_truncated, orm_suppressed = _unproven_seeds(
            root, entry_files, covered
        )
        for seed in seeds:
            reach = EVIDENCE_REACHABILITY[seed["evidence"]]
            if seed["sanitizer"]:
                # A sanitizer in scope is weak evidence of safety, not proof —
                # damp the lead, keep it visible.
                reach *= 0.6
            candidate = ResearchCandidate(
                file=seed["file"],
                function=seed["function"],
                line=seed["line"],
                category=seed["category"],
                severity=seed["severity"],
                source_expr=seed["source"],
                sink_expr=seed["sink"],
                evidence=seed["evidence"],
                proven=False,
                categories=[seed["category"]],
                signals=build_signals(
                    seed["file"], seed["function"], reach, seed["severity"]
                ),
            )
            if seed["sanitizer"]:
                candidate.reasons.append(
                    f"a sanitizer ({seed['sanitizer']}) appears in this function"
                )
            by_key[(seed["file"], seed["function"])] = candidate

    candidates = list(by_key.values())
    for candidate in candidates:
        candidate.score = _score_candidate(candidate, effective_weights)
        key = (candidate.file, candidate.function or f"line:{candidate.line}")
        extra = candidate.reasons
        candidate.reasons = _build_reasons(
            candidate, churn_commits_by_key.get(key)
        ) + extra
        tier_counts[candidate.evidence] = tier_counts.get(candidate.evidence, 0) + 1

    candidates.sort(key=lambda c: (-c.score, c.file, c.line, c.function))

    truncated_findings = len(result.taint_flows) >= MAX_FINDINGS
    coverage = {
        "signals_unavailable": unavailable,
        "taint_sources_seen": result.total_sources,
        "taint_sinks_seen": result.total_sinks,
        "proven_flows": len(flows),
        "by_evidence": tier_counts,
        "parameterized_sql_suppressed": orm_suppressed,
        "truncated": truncated_findings or unproven_truncated,
        "limits": {
            "max_findings": MAX_FINDINGS,
            "max_functions_per_file": MAX_FUNCTIONS,
            "max_unproven_files": MAX_UNPROVEN_FILES,
            "max_unproven_candidates": MAX_UNPROVEN_CANDIDATES,
        },
    }
    notes = []
    if truncated_findings:
        notes.append(
            f"taint scan stopped at MAX_FINDINGS={MAX_FINDINGS}; the proven tier "
            "covers only the flows found before the cap"
        )
    if unproven_truncated:
        notes.append(
            "the unproven pass hit its file or candidate cap; leads beyond it "
            "were not considered"
        )
    if not flows:
        notes.append(
            "the taint engine completed no source-to-sink flow in this project; "
            + (
                "every candidate returned is an unproven lead"
                if include_unproven
                else "unproven leads are disabled, so an empty list here means "
                "'nothing proven', not 'nothing to look at'"
            )
        )
    if notes:
        coverage["truncation_note"] = "; ".join(notes)

    return ResearchPriorityReport(
        candidates=candidates[:top_n],
        weights=effective_weights,
        coverage=coverage,
        total_candidates=len(candidates),
        total_flows=len(flows),
        elapsed_seconds=time.monotonic() - started,
    )
