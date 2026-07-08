"""AI-agent security-policy analyzer.

Detects AI-agent / MCP / sandbox boundary vulnerability classes that generic
SAST and the stock taint engine miss because they are *policy* / *absence* /
*cross-function* bugs rather than intra-function source->sink taint:

  - ssrf-no-guard          outbound HTTP on a caller-controlled URL with no SSRF guard
  - redirect-follow        a guarded HTTP module that still follows 30x redirects unrevalidated
  - unauth-route           a state-changing HTTP route with no auth dependency
  - dynamic-env-read       os.getenv/environ[...] with an attacker-influenced (parsed) name
  - key-to-endpoint        env-derived credential attached to a caller-controlled endpoint
  - file-write-no-guard    file write to a caller-controlled path with no sandbox guard

The load-bearing idea is a function-scoped REQUIRED-GUARD model plus a light
intra-function "is this argument caller-controlled?" check, which is what keeps
precision up (fixed-host clients / constant paths / bounded env selectors are
not flagged). Pure stdlib; mirrors the style of analyzer/taint.py.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, asdict
from pathlib import Path

SSRF_GUARDS = {"validate_url_with_env_config", "validate_url_ssrf",
               "enforce_outbound_url", "validate_url", "guarded_aiohttp_request"}
PATH_GUARDS = {"validate_path_with_env_config"}
CRED_GUARDS = {"assert_env_credential_endpoint_allowed"}
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "request", "goto"}
HTTP_RECEIVERS = {"session", "client", "sess", "http", "aiohttp", "requests",
                  "httpx", "_session", "s", "conn", "page", "driver", "browser"}
MUTATING_ROUTES = {"post", "put", "patch", "delete"}
# Known official provider hosts: outbound egress to these is expected, not SSRF.
# A base_url override to them is still a credential risk — that is covered by the
# separate key-to-endpoint rule — so here we only DOWNGRADE the ssrf signal.
KNOWN_PROVIDER_HOSTS = (
    "api.openai.com", "openai.azure.com", "api.anthropic.com", "anthropic.com",
    "googleapis.com", "generativelanguage.googleapis.com", "cohere.ai",
    "huggingface.co", "api-inference.huggingface.co", "slack.com",
    "hooks.slack.com", "discord.com", "discordapp.com", "office.com",
    "office365.com", "outlook.com", "telegram.org", "api.telegram.org",
    "qdrant", "pinecone.io", "githubusercontent.com", "api.github.com",
)
# Sensitive operations in a route handler that make missing auth HIGH severity.
SENSITIVE_IN_HANDLER = ("create_task", "post_callback", "subprocess", "os.system",
                        "X-Internal-Key", "execute", "ScanCtx", "session.")
# Substrings that mark an expression as caller/agent-controlled input.
SOURCE_PATTERNS = ("params.get(", "params[", ".params.get(", ".params[",
                   "request.", "req.", "self.get_argument", "self.params",
                   "arguments", "payload.get(", "body.get(", "kwargs.get(")

IGNORE_DIRS = {"tests", "test", "__pycache__", ".git", ".flyto-index"}

# CWE mapping per category — makes findings advisory/GHSA-shaped (細膩).
CWE = {
    "ssrf-no-guard": "CWE-918",
    "redirect-follow": "CWE-918",
    "unauth-route": "CWE-306",
    "dynamic-env-read": "CWE-522",
    "key-to-endpoint": "CWE-522",
    "file-write-no-guard": "CWE-22",
    "command-injection": "CWE-78",
    "unsafe-deserialization": "CWE-502",
    "code-injection": "CWE-95",
    "path-traversal-read": "CWE-22",
    "ssti": "CWE-1336",
}

# Decorator / method names that mark a function as reachable from the MCP
# execute_module surface or the hosted API (i.e. its params are attacker-
# influenced). Used to compute per-file reachability (the "is this actually
# reachable?" signal that plain static rules lack).
MCP_ENTRY_DECORATORS = ("register_module", "register", "tool", "mcp_tool", "app_route")
MCP_ENTRY_METHODS = ("execute", "run", "handle", "__call__")

# ── Deterministic exploitability score (0..100, explainable — no LLM) ────────
# Philosophy: decide with dimension scores; only the ambiguous middle band is
# escalated (to dynamic verify / LLM) downstream. Mirrors the engine's
# BlastRadius contract (deterministic, explainable weights).
CATEGORY_BASE = {
    "code-injection": 40, "unauth-route": 35, "key-to-endpoint": 35,
    "command-injection": 35, "unsafe-deserialization": 35, "ssti": 30,
    "ssrf-no-guard": 25, "redirect-follow": 25, "file-write-no-guard": 25,
    "dynamic-env-read": 20, "path-traversal-read": 12,
}
CONF_POINTS = {"high": 35, "medium": 18, "low": 5}
REACHABLE_POINTS = 25
BAND_CONFIRM = 70   # >= : deterministic confirm (no LLM)
BAND_DROP = 35      # <  : deterministic drop/low (no LLM); between => review (LLM)


@dataclass
class AgentFinding:
    file_path: str
    line: int
    category: str
    severity: str
    function: str
    message: str
    recommendation: str = ""
    confidence: str = "medium"  # high | medium | low — triage tier / AI-triage gate
    rule_id: str = ""           # stable rule identifier, e.g. "agent/ssrf-no-guard"
    cwe: str = ""               # CWE id, e.g. "CWE-918"
    mcp_reachable: bool = False # sink reachable from an MCP/module entrypoint (params attacker-influenced)
    exploitability: int = 0     # deterministic 0..100 score
    band: str = "review"        # confirm (auto) | review (→ verify/LLM) | drop
    score_factors: str = ""     # explainable breakdown of the score

    def to_dict(self) -> dict:
        return asdict(self)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + "." + node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


class AgentPolicyAnalyzer:
    def __init__(self, project_root: Path):
        self.root = Path(project_root)
        self.findings: list[AgentFinding] = []
        self._reachable: set[str] = set()   # per-file MCP-reachable function names
        self._file_has_entries = False       # per-file: exposes any MCP/route entry
        self.parse_failures = 0              # 穩定: no silent coverage gaps

    # ── caller-controlled (external) dataflow, intra-function ──────────────
    def _external_names(self, fn: ast.AST) -> set[str]:
        ext: set[str] = set()
        # A function's own parameters are caller-controlled: if a helper takes a
        # url/path/name and reaches a sink without guarding it, the guard
        # responsibility is unmet here (the sink is in a helper, the source is a
        # module param upstream — the cross-function boundary). Seed params
        # (except self/cls). Fixed-host string-literal args stay non-external.
        args = getattr(fn, "args", None)
        if args is not None:
            for a in (list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs)):
                # include self/cls: an instance attribute used as a URL/path in an
                # outbound sink (self.webhook_url, self.provider_url) is set from a
                # param/env in __init__ — caller-controlled across the boundary.
                ext.add(a.arg)
        assigns: list[tuple[str, str]] = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign):
                rhs = _unparse(n.value)
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        assigns.append((t.id, rhs))
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                assigns.append((n.target.id, _unparse(n.value)))
        changed = True
        while changed:
            changed = False
            for name, rhs in assigns:
                if name in ext:
                    continue
                if any(p in rhs for p in SOURCE_PATTERNS) or \
                   any(e in _tokens(rhs) for e in ext):
                    ext.add(name)
                    changed = True
        return ext

    def _is_external(self, arg: ast.AST, ext: set[str]) -> bool:
        if arg is None:
            return False
        src = _unparse(arg)
        if any(p in src for p in SOURCE_PATTERNS):
            return True
        names = {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)}
        return bool(names & ext)

    # ── per-function detectors ─────────────────────────────────────────────
    def _analyze_function(self, fn, rel, called, ext):
        sinks = _http_sinks(fn)
        has_ssrf = bool(called & SSRF_GUARDS)
        fn_src = _unparse(fn)
        provider_host = any(h in fn_src for h in KNOWN_PROVIDER_HOSTS)

        # ssrf-no-guard (caller-controlled url, no guard). DOWNGRADE when the
        # function talks to a known official provider host (expected egress; the
        # credential risk on a base_url override is covered by key-to-endpoint).
        for s in sinks:
            url = _url_arg(s)
            if not has_ssrf and self._is_external(url, ext):
                conf = "low" if provider_host else "high"
                self._add(rel, s.lineno, "ssrf-no-guard", "high", fn,
                          "outbound HTTP to a caller-controlled URL with no SSRF guard",
                          "Call the SSRF guard (enforce_outbound_url/validate_url_with_env_config) before the request.",
                          conf)
                break

        # redirect-follow (guarded module, follows redirects, no per-hop revalidation)
        if sinks and has_ssrf and "guarded_aiohttp_request" not in called:
            if any(_redirects_default_true(s) for s in sinks):
                self._add(rel, sinks[0].lineno, "redirect-follow", "high", fn,
                          "guarded HTTP module follows redirects without per-hop revalidation",
                          "Disable auto-redirects and revalidate every Location hop through the SSRF guard.",
                          "high")

        # unauth-route (state-changing route, no auth dependency). HIGH when the
        # handler does something sensitive (spawns tasks, outbound, secrets).
        rp = _route(fn)
        if rp and rp[0] in MUTATING_ROUTES and not _has_depends(fn):
            conf = "high" if any(s in fn_src for s in SENSITIVE_IN_HANDLER) else "medium"
            self._add(rel, fn.lineno, "unauth-route", "critical", fn,
                      f"state-changing route {rp[0].upper()} {rp[1]} has no auth dependency",
                      "Require an auth dependency (Depends) and fail closed when unconfigured.",
                      conf)

        # dynamic-env-read. HIGH when the env name is PARSED (interpolation:
        # parts[1] / split) — the denylist-bypass shape; else MEDIUM.
        for lineno, nm in _dynamic_env_reads(fn):
            if "is_env_var_allowed" not in called:
                import re as _re
                # parsed name inline, OR the name var is assigned from a
                # subscript/split upstream (e.g. env_var = parts[1]) = interpolation
                parsed = ("[" in nm or "split" in nm or "parts" in nm
                          or _re.search(rf"\b{_re.escape(nm)}\s*=\s*[^\n]*(\[|\.split\()", fn_src))
                conf = "high" if parsed else "medium"
                self._add(rel, lineno, "dynamic-env-read", "high", fn,
                          "os.getenv/environ with an attacker-influenced (parsed) variable name",
                          "Gate ${env.*}/dynamic env reads through the same allowlist as the env.get module.",
                          conf)
                break

        # command-injection. External input into a shell/command sink.
        for lineno, arg, shelly in _cmd_sinks(fn):
            if self._is_external(arg, ext):
                conf = "high" if shelly else "medium"
                self._add(rel, lineno, "command-injection", "critical", fn,
                          "caller-controlled input reaches a shell/command execution sink",
                          "Avoid shell=True; pass an argument list and validate/allowlist inputs.",
                          conf)
                break

        # unsafe-deserialization. External input into an unsafe loader.
        for lineno, arg in _deser_sinks(fn):
            if self._is_external(arg, ext):
                self._add(rel, lineno, "unsafe-deserialization", "high", fn,
                          "caller-controlled input is deserialized with an unsafe loader",
                          "Use safe loaders (yaml.safe_load, json); never unpickle/marshal untrusted data.",
                          "high")
                break

        # code-injection. eval/exec on caller input.
        for lineno, arg in _eval_sinks(fn):
            if self._is_external(arg, ext):
                self._add(rel, lineno, "code-injection", "critical", fn,
                          "caller-controlled input reaches eval/exec",
                          "Never eval/exec caller input; use explicit dispatch or a safe parser.",
                          "high")
                break

        # path-traversal-read. file read from a caller path, no sandbox guard.
        for lineno, patharg in _file_reads(fn):
            if not (called & PATH_GUARDS) and self._is_external(patharg, ext):
                self._add(rel, lineno, "path-traversal-read", "high", fn,
                          "file read from a caller-controlled path without the sandbox guard",
                          "Confine reads to FLYTO_SANDBOX_DIR via validate_path_with_env_config.",
                          "medium")
                break

        # ssti. template rendered from caller input.
        for lineno, arg in _ssti_sinks(fn):
            if self._is_external(arg, ext):
                self._add(rel, lineno, "ssti", "high", fn,
                          "caller-controlled input rendered as a server-side template",
                          "Never build templates from caller input; pass data as sandboxed variables.",
                          "high")
                break

        # file-write-no-guard. HIGH for arbitrary bytes (open+fetched content),
        # MEDIUM for format-constrained library writers (img.save/wb.save).
        writes_fetched = bool(sinks) or ".read()" in fn_src or "content" in fn_src
        for lineno, patharg, kind in _file_writes(fn):
            if not (called & PATH_GUARDS) and self._is_external(patharg, ext):
                conf = "high" if (kind == "open" and writes_fetched) else "medium"
                self._add(rel, lineno, "file-write-no-guard", "critical", fn,
                          "file write to a caller-controlled path without the sandbox guard",
                          "Route the path through validate_path_with_env_config to confine writes to FLYTO_SANDBOX_DIR.",
                          conf)
                break

    # ── file-level detector: key-to-endpoint (cross-function within a module) ─
    def _analyze_file_text(self, text: str, rel: str):
        has_env_key = "getenv" in text and any(
            k in text for k in ("API_KEY", "ANTHROPIC", "_SECRET", "_TOKEN"))
        has_auth = ("Authorization" in text or "Bearer" in text
                    or "api_key=" in text or "api_key =" in text)
        caller_endpoint = "base_url" in text
        guarded = "assert_env_credential_endpoint_allowed" in text
        if has_env_key and has_auth and caller_endpoint and not guarded:
            ln = next((i for i, l in enumerate(text.splitlines(), 1) if "base_url" in l), 1)
            self._add(rel, ln, "key-to-endpoint", "high", None,
                      "env-derived credential can reach a caller-controlled base_url without a trust check",
                      "Only attach the env credential to the official endpoint or a trusted-host allowlist.",
                      "high")

    def _add(self, rel, line, cat, sev, fn, msg, rec="", conf="medium"):
        mcp = self._file_has_entries if fn is None else (getattr(fn, "name", None) in self._reachable)
        base = CATEGORY_BASE.get(cat, 20)
        cpts = CONF_POINTS.get(conf, 18)
        mpts = REACHABLE_POINTS if mcp else 0
        score = min(100, base + cpts + mpts)
        band = "confirm" if score >= BAND_CONFIRM else ("drop" if score < BAND_DROP else "review")
        factors = (f"category:{cat} +{base}; confidence:{conf} +{cpts}; "
                   f"reachable:{bool(mcp)} +{mpts}")
        self.findings.append(AgentFinding(
            rel, line, cat, sev, getattr(fn, "name", "<file>"), msg, rec, conf,
            rule_id="agent/" + cat, cwe=CWE.get(cat, ""), mcp_reachable=bool(mcp),
            exploitability=score, band=band, score_factors=factors))

    def analyze(self) -> list[AgentFinding]:
        for fp in self.root.rglob("*.py"):
            if any(part in IGNORE_DIRS for part in fp.parts):
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text)
            except Exception:
                self.parse_failures += 1
                continue
            rel = str(fp.relative_to(self.root)).replace("\\", "/")
            self._reachable = _mcp_reachable_set(tree)
            self._file_has_entries = bool(self._reachable)
            for fn in [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                called = _called(fn)
                ext = self._external_names(fn)
                self._analyze_function(fn, rel, called, ext)
            self._analyze_file_text(text, rel)
        return self.findings


# ── module-level AST helpers ────────────────────────────────────────────────
def _tokens(s: str) -> set[str]:
    import re
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", s))


def _called(fn) -> set[str]:
    out = set()
    for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        d = _dotted(c.func)
        out.add(d)
        out.add(d.split(".")[-1])
    return out


def _http_sinks(fn):
    out = []
    for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        f = c.func
        if not isinstance(f, ast.Attribute) or f.attr not in HTTP_VERBS:
            continue
        if f.attr == "get" and c.args and isinstance(c.args[0], ast.Constant) \
           and isinstance(c.args[0].value, str):
            continue  # dict.get('literal')
        recv = f.value
        recv_name = recv.id if isinstance(recv, ast.Name) else (
            recv.attr if isinstance(recv, ast.Attribute) else "")
        base = _dotted(recv).split(".")[0].lower()
        if recv_name.lower() in HTTP_RECEIVERS or base in HTTP_RECEIVERS:
            out.append(c)
    return out


def _url_arg(call):
    for kw in call.keywords:
        if kw.arg in ("url", "base_url"):
            return kw.value
    return call.args[0] if call.args else None


def _redirects_default_true(call) -> bool:
    for kw in call.keywords:
        if kw.arg == "allow_redirects":
            v = kw.value
            if isinstance(v, ast.Constant) and v.value is False:
                return False
            return True
    return True


def _route(fn):
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) \
           and d.func.attr in {"get", "post", "put", "delete", "patch"}:
            path = d.args[0].value if d.args and isinstance(d.args[0], ast.Constant) else "?"
            return (d.func.attr, path)
    return None


def _has_depends(fn) -> bool:
    for n in ast.walk(fn.args):
        if isinstance(n, ast.Call) and _dotted(n.func).split(".")[-1] == "Depends":
            return True
    return False


def _dynamic_env_reads(fn):
    """os.getenv(x)/environ[x] with a non-constant, non-bounded-selector name."""
    out = []
    for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        d = _dotted(c.func)
        if (d.endswith("getenv") or d.endswith("environ.get")) and c.args:
            a = c.args[0]
            if isinstance(a, ast.Constant):
                continue
            if _is_bounded_selector(a):
                continue  # env_vars.get(provider) on a dict literal — bounded, safe-ish
            out.append((c.lineno, _unparse(a)))
    for n in ast.walk(fn):
        if isinstance(n, ast.Subscript) and _dotted(n.value).endswith("environ"):
            if not isinstance(n.slice, ast.Constant):
                out.append((getattr(n, "lineno", 0), _unparse(n.slice)))
    return out


def _is_bounded_selector(node) -> bool:
    # <dict>.get(x) where the receiver is a Dict literal or a Name (heuristic:
    # provider->ENV_VAR maps). Excludes parsed tokens like parts[1].
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
       and node.func.attr == "get":
        return isinstance(node.func.value, (ast.Dict, ast.Name))
    return False


def _mcp_reachable_set(tree) -> set:
    """Function names reachable from an MCP/module/route entrypoint (params are
    attacker-influenced). Entries = @register_module/tool decorators, FastAPI
    route decorators, or BaseModule execute/run methods. BFS over intra-file
    call edges from those entries."""
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    names = {f.name for f in funcs}
    entries: set[str] = set()
    callgraph: dict = {}
    for f in funcs:
        is_entry = f.name in MCP_ENTRY_METHODS
        for d in f.decorator_list:
            dd = _dotted(d.func) if isinstance(d, ast.Call) else _dotted(d)
            if any(k in dd for k in MCP_ENTRY_DECORATORS):
                is_entry = True
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) \
               and d.func.attr in {"get", "post", "put", "delete", "patch"}:
                is_entry = True  # HTTP route = externally reachable
        if is_entry:
            entries.add(f.name)
        callees = set()
        for c in [n for n in ast.walk(f) if isinstance(n, ast.Call)]:
            nm = _dotted(c.func).split(".")[-1]
            if nm in names:
                callees.add(nm)
        callgraph[f.name] = callees
    reachable = set(entries)
    frontier = list(entries)
    while frontier:
        n = frontier.pop()
        for callee in callgraph.get(n, ()):
            if callee not in reachable:
                reachable.add(callee)
                frontier.append(callee)
    return reachable


def _eval_sinks(fn):
    out = []
    for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        if isinstance(c.func, ast.Name) and c.func.id in ("eval", "exec") and c.args:
            out.append((c.lineno, c.args[0]))
    return out


def _file_reads(fn):
    out = []
    for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        if isinstance(c.func, ast.Name) and c.func.id == "open" and c.args:
            mode = c.args[1] if len(c.args) >= 2 else None
            is_write = (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                        and any(x in mode.value for x in ("w", "a", "x", "+")))
            if not is_write:
                out.append((c.lineno, c.args[0]))
        elif isinstance(c.func, ast.Attribute) and c.func.attr in ("read_text", "read_bytes"):
            recv = c.func.value
            inner = recv.args[0] if isinstance(recv, ast.Call) and recv.args else recv
            out.append((c.lineno, inner))
    return out


def _ssti_sinks(fn):
    out = []
    for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        name = _dotted(c.func).split(".")[-1]
        if name in ("render_template_string", "from_string", "Template") and c.args:
            out.append((c.lineno, c.args[0]))
    return out


def _cmd_sinks(fn):
    """Shell/command-execution sinks: (lineno, arg_node, shell_true)."""
    out = []
    for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        d = _dotted(c.func)
        name = d.split(".")[-1]
        if d.endswith("os.system") or d.endswith("os.popen"):
            if c.args:
                out.append((c.lineno, c.args[0], True))
        elif "subprocess" in d and name in {"run", "call", "Popen", "check_output", "check_call"}:
            shelly = any(
                kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in c.keywords
            )
            if c.args:
                out.append((c.lineno, c.args[0], shelly))
    return out


def _deser_sinks(fn):
    """Unsafe deserialization sinks: (lineno, arg_node)."""
    out = []
    for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        d = _dotted(c.func)
        name = d.split(".")[-1]
        if d.endswith("pickle.loads") or d.endswith("pickle.load") or d.endswith("marshal.loads"):
            if c.args:
                out.append((c.lineno, c.args[0]))
        elif name == "load" and "yaml" in d:
            has_safe = any("Safe" in _unparse(kw.value) for kw in c.keywords)
            if not has_safe and c.args:
                out.append((c.lineno, c.args[0]))
    return out


def _file_writes(fn):
    out = []
    for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        if isinstance(c.func, ast.Name) and c.func.id == "open" and len(c.args) >= 2:
            m = c.args[1]
            if isinstance(m, ast.Constant) and isinstance(m.value, str) \
               and any(x in m.value for x in ("w", "a")):
                out.append((c.lineno, c.args[0], "open"))
        elif isinstance(c.func, ast.Attribute) and c.func.attr in {"save", "write_bytes", "write_text"}:
            # img.save(path)/wb.save(path) -> arg[0]; Path(p).write_bytes() -> receiver arg
            if c.func.attr == "save" and c.args:
                out.append((c.lineno, c.args[0], "save"))
            else:
                recv = c.func.value
                inner = recv.args[0] if isinstance(recv, ast.Call) and recv.args else recv
                out.append((c.lineno, inner, "save"))
    return out
