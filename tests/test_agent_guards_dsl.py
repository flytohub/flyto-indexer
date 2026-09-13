"""Declaring a guard must actually change what the analyzer reports."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyzer.agent_guards import PATH, URL, GuardRecognizer  # noqa: E402
from analyzer.agent_guards_dsl import (  # noqa: E402
    add_agent_guard,
    list_agent_guards,
    remove_agent_guard,
)
from analyzer.agent_policy import AgentPolicyAnalyzer  # noqa: E402


def test_declaring_a_guard_reaches_the_recognizer(tmp_path):
    """The round trip is the point. A writer that produces YAML the recognizer
    does not read would look like it worked and change nothing."""
    assert add_agent_guard(tmp_path, PATH, "house_path_check")["status"] == "added"
    cfg = yaml.safe_load((tmp_path / ".flyto-rules.yaml").read_text())
    recognizer = GuardRecognizer.from_rules(cfg)
    match = recognizer.find(["house_path_check"], PATH)
    assert match is not None and match.basis == "declared" and match.conclusive


def test_declared_guard_silences_the_finding_end_to_end(tmp_path):
    """From CLI write to scan result, with nothing hand-wired in between."""
    (tmp_path / "m.py").write_text(
        "from fastapi import Request\n"
        "def house_path_check(p):\n    return p\n"
        "def handler(request: Request):\n"
        "    name = request.query_params['name']\n"
        "    path = house_path_check(name)\n"
        "    open(path, 'wb').write(b'x')\n"
    )
    before = AgentPolicyAnalyzer(tmp_path)
    before.analyze()
    assert any(f.category == "file-write-no-guard" for f in before.findings), \
        "expected a finding before the guard is declared"

    add_agent_guard(tmp_path, PATH, "house_path_check")
    cfg = yaml.safe_load((tmp_path / ".flyto-rules.yaml").read_text())
    after = AgentPolicyAnalyzer(tmp_path, guards=GuardRecognizer.from_rules(cfg))
    after.analyze()
    assert not any(f.category == "file-write-no-guard" for f in after.findings), \
        "declaring the guard did not change the result"


def test_removing_a_guard_withdraws_the_guarantee_not_the_recognition(tmp_path):
    """Undeclaring is withdrawing "we guarantee this confines the path", not
    making the analyzer forget the name exists. The shape tier still sees it,
    so the finding comes back softened rather than at full confidence — which
    is the honest reading of a name that looks protective but is no longer
    vouched for."""
    add_agent_guard(tmp_path, PATH, "house_path_check")
    cfg = yaml.safe_load((tmp_path / ".flyto-rules.yaml").read_text())
    assert GuardRecognizer.from_rules(cfg).find(["house_path_check"], PATH).conclusive

    assert remove_agent_guard(tmp_path, PATH, "house_path_check")["status"] == "removed"
    cfg = yaml.safe_load((tmp_path / ".flyto-rules.yaml").read_text()) or {}
    match = GuardRecognizer.from_rules(cfg).find(["house_path_check"], PATH)
    assert match is not None and match.basis == "shape" and not match.conclusive

    # A name with no guard shape at all is genuinely unrecognized.
    add_agent_guard(tmp_path, PATH, "frobnicate")
    cfg = yaml.safe_load((tmp_path / ".flyto-rules.yaml").read_text())
    assert GuardRecognizer.from_rules(cfg).find(["frobnicate"], PATH).conclusive
    remove_agent_guard(tmp_path, PATH, "frobnicate")
    cfg = yaml.safe_load((tmp_path / ".flyto-rules.yaml").read_text()) or {}
    assert GuardRecognizer.from_rules(cfg).find(["frobnicate"], PATH) is None


def test_adding_twice_is_not_an_error(tmp_path):
    add_agent_guard(tmp_path, PATH, "g")
    assert add_agent_guard(tmp_path, PATH, "g")["status"] == "already_exists"
    assert list_agent_guards(tmp_path)["declared"][PATH] == ["g"]


def test_unknown_domain_is_refused_with_the_valid_set(tmp_path):
    result = add_agent_guard(tmp_path, "not_a_domain", "g")
    assert "error" in result and "path" in result["valid"]
    assert not (tmp_path / ".flyto-rules.yaml").exists(), "a refused write touched the file"


def test_writer_preserves_unrelated_rules(tmp_path):
    """This file belongs to the project, not to us. Losing somebody's taint
    rules while adding a guard would be a far worse bug than the one this
    command exists to fix."""
    (tmp_path / ".flyto-rules.yaml").write_text(yaml.safe_dump({
        "taint": {"sanitizers": [{"pattern": "escape(", "cleanses": ["xss"]}]},
        "architecture": [{"rule": "keep it"}],
    }))
    add_agent_guard(tmp_path, URL, "check_target_url")
    cfg = yaml.safe_load((tmp_path / ".flyto-rules.yaml").read_text())
    assert cfg["taint"]["sanitizers"][0]["pattern"] == "escape("
    assert cfg["architecture"][0]["rule"] == "keep it"
    assert cfg["agent_guards"][URL] == ["check_target_url"]


def test_removing_the_last_guard_leaves_no_empty_section(tmp_path):
    add_agent_guard(tmp_path, PATH, "only")
    remove_agent_guard(tmp_path, PATH, "only")
    cfg = yaml.safe_load((tmp_path / ".flyto-rules.yaml").read_text()) or {}
    assert "agent_guards" not in cfg


def test_listing_reports_only_what_the_project_declared(tmp_path):
    """Built-ins are excluded on purpose: a reviewer needs to see what was
    decided here, not what the tool already knows."""
    assert list_agent_guards(tmp_path)["declared"] == {}
    add_agent_guard(tmp_path, PATH, "house_path_check")
    listed = list_agent_guards(tmp_path)
    assert listed["declared"] == {PATH: ["house_path_check"]}
    assert "validate_path_with_env_config" not in str(listed)
