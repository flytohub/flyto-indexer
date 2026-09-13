"""Recognizing protective calls in code this analyzer did not write.

A *guard* is a call that confines a dangerous operation: a path is restricted
to an allowed root, a URL is checked before an outbound request, a credential
endpoint is verified against an allowlist.

The analyzer used to answer "is there a guard?" by testing membership in a set
of function names taken from one specific codebase. Applied to that codebase it
is a reasonable house rule. Applied to a repository somebody else wrote, it
reports every project's own protection as an absence of protection, because the
project did not happen to name its helper the same way.

So recognition here is deliberately three-tiered, and the tier is part of the
answer rather than an implementation detail:

    declared  a project named this guard itself, in .flyto-rules.yaml
    known     a specific implementation this analyzer knows by name
    shape     the name reads like a guard, but its behaviour is unverified

Only the first two are conclusive. A shape match means something protective is
probably happening and this analyzer cannot prove it — a caller is expected to
soften its finding rather than drop it, because "we saw something that looks
like a guard" and "this code is safe" are different claims.

This module answers that one question. It does not decide whether to report a
finding, how severe it is, or what to say about it; those belong to the caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

#: Domains a guard can protect. Each is an independent concern: recognizing a
#: URL validator says nothing about whether a path is confined.
PATH = "path"
URL = "url"
CREDENTIAL_ENDPOINT = "credential_endpoint"

DOMAINS = (PATH, URL, CREDENTIAL_ENDPOINT)

#: Specific implementations recognized by name. These are a convenience for
#: codebases that use them, never the definition of what a guard is. Adding a
#: name here must not be the only way to be recognized.
KNOWN: dict[str, frozenset[str]] = {
    PATH: frozenset({"validate_path_with_env_config", "validate_path",
                     "safe_join", "secure_filename", "resolve_within_root"}),
    URL: frozenset({"validate_url_with_env_config", "validate_url_ssrf",
                    "enforce_outbound_url", "validate_url",
                    "guarded_aiohttp_request", "is_safe_url"}),
    CREDENTIAL_ENDPOINT: frozenset({"assert_env_credential_endpoint_allowed",
                                    "assert_endpoint_allowed"}),
}

#: Verbs that describe confining, checking or neutralizing a value. A guard
#: name almost always contains one; a name containing none is not doing this
#: job whatever else it does.
VERBS = ("validate", "sanitize", "sanitise", "normalize", "normalise", "check",
         "ensure", "assert", "verify", "guard", "confine", "restrict", "secure",
         "safe", "clean", "scrub", "allowlist", "whitelist", "permit", "authorize",
         "authorise", "canonicalize", "canonicalise", "resolve", "harden")

#: Nouns that identify which domain a guard protects. Kept separate from the
#: verbs so a project's `ensure_within_media_root` is recognized without this
#: module having to enumerate anybody's naming conventions.
NOUNS: dict[str, tuple[str, ...]] = {
    PATH: ("path", "paths", "file", "filename", "filepath", "dir", "directory",
           "folder", "location", "root", "basename", "destination"),
    URL: ("url", "uri", "host", "hostname", "endpoint", "address", "origin",
          "target", "domain", "netloc", "request"),
    CREDENTIAL_ENDPOINT: ("endpoint", "credential", "key", "token", "secret",
                          "auth", "base_url", "baseurl"),
}

_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class GuardMatch:
    """One recognized protective call, and how confident the recognition is.

    ``conclusive`` separates "this project told us, or we know this function"
    from "this name reads like a guard". A caller that treats the two the same
    is either suppressing real findings or reporting false ones.
    """

    name: str
    domain: str
    basis: str  # "declared" | "known" | "shape"

    @property
    def conclusive(self) -> bool:
        return self.basis in ("declared", "known")

    def describe(self) -> str:
        if self.basis == "declared":
            return f"{self.name} is declared as a {self.domain} guard by this project"
        if self.basis == "known":
            return f"{self.name} is a recognized {self.domain} guard"
        return (f"{self.name} reads like a {self.domain} guard; "
                "its behaviour was not verified")


def _terms(name: str) -> set[str]:
    """Lowercased word parts of a call name, including dotted attribute tails."""
    tail = name.rsplit(".", 1)[-1]
    return {t for t in _SPLIT.split(tail.lower()) if t}


def _shape_matches(name: str, domain: str) -> bool:
    """Whether a name reads like a guard for this domain.

    Requires both a protective verb and a domain noun. Either alone is far too
    common: ``check_status`` guards nothing, and ``file_path`` is a variable.
    """
    terms = _terms(name)
    if not terms:
        return False
    has_verb = any(v in terms for v in VERBS)
    if not has_verb:
        # Catch unsplit spellings such as ``validatepath`` or ``safejoin``.
        tail = name.rsplit(".", 1)[-1].lower()
        has_verb = any(v in tail for v in VERBS)
        if not has_verb:
            return False
    nouns = NOUNS[domain]
    if any(n in terms for n in nouns):
        return True
    tail = name.rsplit(".", 1)[-1].lower()
    return any(n in tail for n in nouns)


class GuardRecognizer:
    """Recognizes protective calls for one project.

    ``declared`` comes from the project's own rules file and is merged on top
    of the built-in names, mirroring how taint sanitizers are extended. A
    project can therefore teach this analyzer its own conventions instead of
    being told its code is unguarded for not using someone else's.
    """

    def __init__(self, declared: Mapping[str, Iterable[str]] | None = None,
                 *, use_shape: bool = True) -> None:
        self._declared = {
            domain: frozenset(declared.get(domain, ()) if declared else ())
            for domain in DOMAINS
        }
        self._use_shape = use_shape

    @classmethod
    def from_rules(cls, yaml_cfg: dict | None) -> "GuardRecognizer":
        """Build from a parsed .flyto-rules.yaml.

        Reads ``agent_guards``, a mapping of domain to a list of function
        names. An absent or malformed section yields built-in behaviour rather
        than an error: a rules file should be able to extend this analyzer
        without being able to break a scan.
        """
        section = (yaml_cfg or {}).get("agent_guards")
        if not isinstance(section, dict):
            return cls()
        declared: dict[str, list[str]] = {}
        for domain in DOMAINS:
            names = section.get(domain)
            if isinstance(names, str):
                names = [names]
            if isinstance(names, (list, tuple)):
                declared[domain] = [n for n in names if isinstance(n, str) and n]
        use_shape = section.get("use_shape", True)
        return cls(declared, use_shape=bool(use_shape))

    def find(self, called: Iterable[str], domain: str) -> GuardMatch | None:
        """The strongest guard recognized among ``called`` for ``domain``.

        Declared beats known beats shape, so a project's own declaration is
        never overridden by a coincidental name match.
        """
        if domain not in DOMAINS:
            raise ValueError(f"unknown guard domain: {domain}")
        names = [c for c in called if c]
        for basis, pool in (("declared", self._declared[domain]),
                            ("known", KNOWN[domain])):
            for name in names:
                if name in pool or name.rsplit(".", 1)[-1] in pool:
                    return GuardMatch(name, domain, basis)
        if not self._use_shape:
            return None
        for name in names:
            if _shape_matches(name, domain):
                return GuardMatch(name, domain, "shape")
        return None
