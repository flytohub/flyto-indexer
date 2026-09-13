"""Guard recognition must not require a project to use our function names."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyzer.agent_guards import (  # noqa: E402
    CREDENTIAL_ENDPOINT,
    PATH,
    URL,
    GuardRecognizer,
)
from analyzer.agent_policy import AgentPolicyAnalyzer  # noqa: E402


def _cats(findings):
    return {f.category for f in findings}


def _by_cat(findings, cat):
    return [f for f in findings if f.category == cat]


# ── the regression this module exists for ────────────────────────────────────

def test_project_using_its_own_path_helper_is_not_called_unguarded():
    """The defect: a repository that confines paths with its own helper was
    reported as having no guard, because recognition tested membership in one
    codebase's set of names."""
    r = GuardRecognizer()
    for name in ("safe_join", "ensure_within_media_root", "validate_upload_path",
                 "sanitize_filename", "check_destination_dir", "resolve_safe_path"):
        assert r.find([name], PATH) is not None, name


def test_unrelated_calls_are_not_mistaken_for_guards():
    r = GuardRecognizer()
    for name in ("open", "os.path.join", "read_file", "get_path", "file_path",
                 "check_status", "validate_email", "os.makedirs"):
        assert r.find([name], PATH) is None, name


def test_a_verb_or_a_noun_alone_is_not_enough():
    """`check_status` guards nothing and `file_path` is a variable. Requiring
    both is what keeps the shape tier from swallowing real findings."""
    r = GuardRecognizer()
    assert r.find(["check_status"], PATH) is None
    assert r.find(["file_path"], PATH) is None
    assert r.find(["check_file_path"], PATH) is not None


# ── the tier is part of the answer ───────────────────────────────────────────

def test_known_implementation_is_conclusive():
    m = GuardRecognizer().find(["validate_path_with_env_config"], PATH)
    assert m is not None and m.basis == "known" and m.conclusive


def test_shape_match_is_not_conclusive():
    """A name that reads like a guard is evidence, not proof. Treating it as
    proof would let any project silence this analyzer by renaming a function."""
    m = GuardRecognizer().find(["ensure_within_media_root"], PATH)
    assert m is not None and m.basis == "shape" and not m.conclusive
    assert "not verified" in m.describe()


def test_declared_beats_known_and_shape():
    r = GuardRecognizer({PATH: ["house_path_check"]})
    m = r.find(["house_path_check", "validate_path_with_env_config"], PATH)
    assert m is not None and m.basis == "declared" and m.conclusive


def test_domains_are_independent():
    """Recognizing a URL validator says nothing about whether a path is
    confined. Sharing one pool would silence path findings on any project that
    validates URLs."""
    r = GuardRecognizer()
    assert r.find(["validate_url"], URL) is not None
    assert r.find(["validate_url"], PATH) is None
    assert r.find(["assert_endpoint_allowed"], CREDENTIAL_ENDPOINT) is not None


# ── configuration, following the taint-sanitizer precedent ───────────────────

def test_rules_file_can_declare_project_guards():
    r = GuardRecognizer.from_rules({"agent_guards": {PATH: ["colibri_safe_path"]}})
    m = r.find(["colibri_safe_path"], PATH)
    assert m is not None and m.conclusive


def test_shape_tier_can_be_disabled_for_strict_scans():
    r = GuardRecognizer.from_rules({"agent_guards": {"use_shape": False}})
    assert r.find(["ensure_within_media_root"], PATH) is None
    assert r.find(["validate_path_with_env_config"], PATH) is not None


def test_malformed_rules_do_not_break_a_scan():
    """A rules file may extend this analyzer; it must not be able to break it."""
    for cfg in ({}, {"agent_guards": None}, {"agent_guards": []},
                {"agent_guards": {PATH: None}}, {"agent_guards": {"nonsense": ["x"]}},
                {"agent_guards": {PATH: "single_string"}}):
        r = GuardRecognizer.from_rules(cfg)
        assert r.find(["validate_path_with_env_config"], PATH) is not None


def test_unknown_domain_is_a_programming_error():
    try:
        GuardRecognizer().find(["x"], "not_a_domain")
    except ValueError:
        return
    raise AssertionError("an unknown domain silently returned no guard")


# ── end to end through the analyzer ──────────────────────────────────────────

SRC_OWN_GUARD = '''
from fastapi import Request

def ensure_within_media_root(p):
    return p

def handler(request: Request):
    name = request.query_params["name"]
    path = ensure_within_media_root(name)
    with open(path, "wb") as fh:
        fh.write(b"x")
'''

SRC_NO_GUARD = '''
from fastapi import Request

def handler(request: Request):
    name = request.query_params["name"]
    with open(name, "wb") as fh:
        fh.write(b"x")
'''


def _analyze(tmp_path, source, guards=None):
    (tmp_path / "m.py").write_text(source)
    a = AgentPolicyAnalyzer(tmp_path, guards=guards)
    a.analyze()
    return a.findings


def test_own_guard_downgrades_rather_than_suppresses(tmp_path):
    """Both halves matter. The finding survives, because a name is not proof;
    its confidence drops, because ignoring the evidence would be dishonest too."""
    own = _analyze(tmp_path, SRC_OWN_GUARD)
    bare = _analyze(tmp_path, SRC_NO_GUARD)

    own_writes = _by_cat(own, "file-write-no-guard")
    bare_writes = _by_cat(bare, "file-write-no-guard")
    assert bare_writes, "a genuinely unguarded write must still be reported"
    assert own_writes, "a shape-only guard must not suppress the finding"
    assert own_writes[0].confidence != bare_writes[0].confidence
    assert own_writes[0].exploitability < bare_writes[0].exploitability
    assert "not verified" in own_writes[0].message


def test_declared_guard_suppresses_the_finding(tmp_path):
    """A project that tells us its guard is believed, and gets a clean result."""
    guards = GuardRecognizer({PATH: ["ensure_within_media_root"]})
    findings = _analyze(tmp_path, SRC_OWN_GUARD, guards=guards)
    assert "file-write-no-guard" not in _cats(findings)


def test_remediation_never_names_internal_helpers(tmp_path):
    """The advice goes to people who did not write our codebase. Naming our own
    helper tells them to call a function their project does not contain."""
    findings = _analyze(tmp_path, SRC_NO_GUARD)
    leaked = ("FLYTO_SANDBOX_DIR", "validate_path_with_env_config",
              "enforce_outbound_url", "validate_url_with_env_config",
              "assert_env_credential_endpoint_allowed")
    for f in findings:
        for token in leaked:
            assert token not in f.recommendation, f"{token} leaked into: {f.recommendation}"


# ── key-to-endpoint: the same defect in a file-scoped detector ────────────────

SRC_CRED_OWN_GUARD = '''
import os
import httpx

def check_endpoint_allowed(url):
    return url

def send(base_url):
    key = os.getenv("ANTHROPIC_API_KEY")
    check_endpoint_allowed(base_url)
    return httpx.post(base_url, headers={"Authorization": f"Bearer {key}"})
'''

SRC_CRED_NO_GUARD = '''
import os
import httpx

def send(base_url):
    key = os.getenv("ANTHROPIC_API_KEY")
    return httpx.post(base_url, headers={"Authorization": f"Bearer {key}"})
'''


def test_credential_endpoint_guard_is_recognized_by_shape(tmp_path):
    """key-to-endpoint tested one hardcoded name via substring match, so a
    project checking its endpoint with its own helper was reported as checking
    nothing. It is file-scoped, which is why it escaped the first pass."""
    own = _analyze(tmp_path, SRC_CRED_OWN_GUARD)
    bare = _analyze(tmp_path, SRC_CRED_NO_GUARD)

    own_hits = _by_cat(own, "key-to-endpoint")
    bare_hits = _by_cat(bare, "key-to-endpoint")
    assert bare_hits, "an unchecked credential endpoint must still be reported"
    assert own_hits, "a shape-only guard must not suppress the finding"
    assert own_hits[0].confidence != bare_hits[0].confidence
    assert "not verified" in own_hits[0].message


def test_declared_credential_guard_suppresses(tmp_path):
    guards = GuardRecognizer({CREDENTIAL_ENDPOINT: ["check_endpoint_allowed"]})
    findings = _analyze(tmp_path, SRC_CRED_OWN_GUARD, guards=guards)
    assert "key-to-endpoint" not in _cats(findings)


def test_credential_remediation_names_no_internal_helper(tmp_path):
    for f in _by_cat(_analyze(tmp_path, SRC_CRED_NO_GUARD), "key-to-endpoint"):
        assert "assert_env_credential_endpoint_allowed" not in f.recommendation
