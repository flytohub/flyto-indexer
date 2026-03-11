"""Programming concept synonyms for semantic-like search expansion."""

import re

CONCEPT_GROUPS = [
    {"auth", "login", "authenticate", "signin", "sign_in", "logon"},
    {"delete", "remove", "destroy", "drop", "erase", "unlink"},
    {"create", "add", "insert", "new", "make", "build", "generate"},
    {"update", "edit", "modify", "patch", "change", "alter"},
    {"get", "fetch", "retrieve", "load", "find", "query", "read", "obtain"},
    {"list", "index", "browse", "enumerate", "all"},
    {"validate", "check", "verify", "assert", "ensure", "test"},
    {"send", "emit", "dispatch", "publish", "notify", "broadcast"},
    {"receive", "listen", "subscribe", "consume", "handle"},
    {"parse", "decode", "deserialize", "unmarshal", "extract"},
    {"format", "encode", "serialize", "marshal", "render", "stringify"},
    {"open", "start", "begin", "init", "initialize", "setup", "launch"},
    {"close", "stop", "end", "finish", "shutdown", "teardown", "cleanup"},
    {"show", "display", "render", "present", "visible"},
    {"hide", "conceal", "invisible", "collapse"},
    {"enable", "activate", "on", "allow"},
    {"disable", "deactivate", "off", "deny", "block"},
    {"save", "store", "persist", "write", "commit", "flush"},
    {"cache", "memoize", "buffer", "memo"},
    {"config", "configuration", "settings", "options", "preferences", "prefs"},
    {"error", "exception", "fault", "failure"},
    {"log", "logger", "logging", "trace", "debug"},
    {"user", "account", "profile", "member"},
    {"permission", "role", "access", "privilege", "acl"},
    {"token", "jwt", "session", "cookie", "credential"},
    {"route", "path", "endpoint", "url", "api"},
    {"middleware", "interceptor", "filter", "guard", "hook"},
    {"component", "widget", "element", "view"},
    {"store", "state", "reducer", "slice"},
    {"modal", "dialog", "popup", "overlay"},
    {"toast", "notification", "alert", "snackbar", "message"},
]

# Pre-build lookup: term -> set of synonyms (excluding the term itself)
_SYNONYM_LOOKUP: dict = {}
for _group in CONCEPT_GROUPS:
    for _term in _group:
        if _term not in _SYNONYM_LOOKUP:
            _SYNONYM_LOOKUP[_term] = set()
        _SYNONYM_LOOKUP[_term] |= _group - {_term}

# camelCase/snake_case tokenizer (reuse pattern style from BM25)
_TOKEN_RE = re.compile(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[a-z]+|\d+')


def expand_query(query: str) -> tuple:
    """
    Expand query terms with programming concept synonyms.

    Returns:
        (original_tokens, expanded_tokens) where expanded_tokens
        contains only the NEW terms (not in original).
    """
    # Tokenize on word boundaries, then split camelCase/snake_case
    raw_tokens = re.findall(r'\w+', query)
    original = set()
    for t in raw_tokens:
        original.add(t.lower())
        # Split camelCase/snake_case using original case
        for sub in _TOKEN_RE.findall(t):
            original.add(sub.lower())

    expanded = set()
    for token in original:
        synonyms = _SYNONYM_LOOKUP.get(token)
        if synonyms:
            expanded |= synonyms

    # Remove original tokens from expanded set
    expanded -= original
    return original, expanded
