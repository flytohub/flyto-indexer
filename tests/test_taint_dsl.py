"""Adding or removing a taint rule must not rewrite the rest of the file.

`.flyto-rules.yaml` is hand-maintained policy: its comments say why a rule is
there, and its ordering groups related rules. Editing it through the parser
loses both. On flyto-cloud's 332-line file, adding one sanitizer produced 351
insertions, 332 deletions, and deleted both comments -- so these tests assert
on the surviving text, not only on what re-parses.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

yaml = pytest.importorskip("yaml")

from analyzer.taint_dsl import (  # noqa: E402
    add_taint_sanitizer,
    add_taint_sink,
    add_taint_source,
    list_taint_rules,
    remove_taint_rule,
)


ANNOTATED = """\
# Project rules for flyto-example. Every entry here is deliberate.
version: 1

architecture:
  - rule: "no direct database access from routes"
    glob_deny: ["src/api/**/*.sql"]
    source: "user feedback 2026-01-04"

taint:
  # These sources are the request surface, not every attribute of it.
  sources:
    - pattern: "request.json[*]"
      language: python
  sinks:
    - pattern: "subprocess.run("
      vuln_type: rce
      severity: critical

# Anything below this line is checked by the audit tool.
conventions:
  - rule: "prefer explicit imports"
"""


def _write(tmp_path, text):
    path = tmp_path / ".flyto-rules.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _added_lines(before: str, after: str) -> list[str]:
    """The lines `after` has that `before` did not, in order."""
    import difflib

    return [
        line[2:]
        for line in difflib.ndiff(before.splitlines(), after.splitlines())
        if line.startswith("+ ")
    ]


def _removed_lines(before: str, after: str) -> list[str]:
    import difflib

    return [
        line[2:]
        for line in difflib.ndiff(before.splitlines(), after.splitlines())
        if line.startswith("- ")
    ]


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------

def test_adding_a_sanitizer_only_inserts_lines(tmp_path):
    path = _write(tmp_path, ANNOTATED)

    result = add_taint_sanitizer(tmp_path, "shlex.quote(", cleanses=["rce"])
    assert result["status"] == "added"

    after = path.read_text(encoding="utf-8")
    assert _removed_lines(ANNOTATED, after) == []
    assert "shlex.quote(" in after
    assert yaml.safe_load(after)["taint"]["sanitizers"] == [
        {"pattern": "shlex.quote(", "cleanses": ["rce"]},
    ]


def test_comments_and_unrelated_blocks_survive(tmp_path):
    path = _write(tmp_path, ANNOTATED)

    add_taint_sink(tmp_path, "eval(", vuln_type="rce", severity="critical")

    after = path.read_text(encoding="utf-8")
    for comment in (
        "# Project rules for flyto-example. Every entry here is deliberate.",
        "  # These sources are the request surface, not every attribute of it.",
        "# Anything below this line is checked by the audit tool.",
    ):
        assert comment in after.splitlines(), comment
    assert yaml.safe_load(after)["architecture"] == yaml.safe_load(ANNOTATED)["architecture"]
    assert yaml.safe_load(after)["conventions"] == yaml.safe_load(ANNOTATED)["conventions"]


def test_new_entry_joins_its_own_list(tmp_path):
    path = _write(tmp_path, ANNOTATED)

    add_taint_sink(tmp_path, "os.system(", vuln_type="rce", severity="critical")

    sinks = yaml.safe_load(path.read_text(encoding="utf-8"))["taint"]["sinks"]
    assert [s["pattern"] for s in sinks] == ["subprocess.run(", "os.system("]


def test_a_missing_sub_list_is_added_inside_the_taint_block(tmp_path):
    path = _write(tmp_path, ANNOTATED)

    add_taint_sanitizer(tmp_path, "html.escape(")

    after = path.read_text(encoding="utf-8")
    # The section comment introduces `conventions:`, so the new list must land
    # above it, not between the comment and the key it describes.
    assert after.index("sanitizers:") < after.index("# Anything below this line")
    assert yaml.safe_load(after)["taint"]["sanitizers"][0]["pattern"] == "html.escape("


def test_a_file_without_a_taint_block_gains_one(tmp_path):
    plain = "# only architecture here\narchitecture:\n  - rule: \"keep layers apart\"\n"
    path = _write(tmp_path, plain)

    add_taint_source(tmp_path, "flask.request.args[*]")

    after = path.read_text(encoding="utf-8")
    assert after.startswith(plain)
    assert yaml.safe_load(after)["taint"]["sources"] == [
        {"pattern": "flask.request.args[*]", "language": "python"},
    ]


def test_a_file_that_does_not_end_in_a_newline_is_still_valid(tmp_path):
    path = _write(tmp_path, "architecture:\n  - rule: \"x\"")

    add_taint_source(tmp_path, "request.form[*]")

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["taint"]["sources"]


def test_no_file_means_a_new_one(tmp_path):
    result = add_taint_sink(tmp_path, "exec(", vuln_type="rce")

    assert result["status"] == "added"
    written = yaml.safe_load((tmp_path / ".flyto-rules.yaml").read_text(encoding="utf-8"))
    assert written["taint"]["sinks"][0]["pattern"] == "exec("


def test_an_existing_file_is_never_given_a_version_it_did_not_have(tmp_path):
    path = _write(tmp_path, "taint:\n  sinks:\n    - pattern: \"exec(\"\n")

    add_taint_sink(tmp_path, "eval(")

    assert "version" not in path.read_text(encoding="utf-8")


def test_a_duplicate_pattern_changes_nothing(tmp_path):
    path = _write(tmp_path, ANNOTATED)

    result = add_taint_sink(tmp_path, "subprocess.run(")

    assert result["status"] == "already_exists"
    assert path.read_text(encoding="utf-8") == ANNOTATED


def test_an_unsplittable_list_is_reported_rather_than_rewritten(tmp_path):
    # A flow-style list has no `- ` item to align with, so there is no safe
    # place to splice one in.
    text = "taint:\n  sinks: [{pattern: \"exec(\"}]\n"
    path = _write(tmp_path, text)

    result = add_taint_sink(tmp_path, "eval(")

    assert "error" in result
    assert "eval(" in result["snippet"]
    assert path.read_text(encoding="utf-8") == text


def test_a_new_entry_is_written_in_the_style_of_its_neighbours(tmp_path):
    path = _write(tmp_path, ANNOTATED)

    add_taint_sink(tmp_path, "os.system(", vuln_type="rce", severity="critical")

    assert _added_lines(ANNOTATED, path.read_text(encoding="utf-8")) == [
        '    - pattern: "os.system("',
        "      vuln_type: rce",
        "      severity: critical",
    ]


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------

def test_removing_takes_out_one_entry_and_nothing_else(tmp_path):
    path = _write(tmp_path, ANNOTATED)

    result = remove_taint_rule(tmp_path, "sink", "subprocess.run(")

    assert result["status"] == "removed"
    after = path.read_text(encoding="utf-8")
    assert _added_lines(ANNOTATED, after) == []
    assert _removed_lines(ANNOTATED, after) == [
        "  sinks:",
        '    - pattern: "subprocess.run("',
        "      vuln_type: rce",
        "      severity: critical",
    ]
    # The key goes too: `sinks:` with nothing under it reads back as null,
    # which is not what an empty list means to the analyzer.
    assert "sinks" not in yaml.safe_load(after)["taint"]


def test_removing_an_absent_rule_touches_nothing(tmp_path):
    path = _write(tmp_path, ANNOTATED)

    assert remove_taint_rule(tmp_path, "sink", "eval(")["status"] == "not_found"
    assert path.read_text(encoding="utf-8") == ANNOTATED


def test_add_then_remove_returns_the_file_to_its_original_bytes(tmp_path):
    path = _write(tmp_path, ANNOTATED)

    add_taint_sink(tmp_path, "os.system(", vuln_type="rce", severity="critical")
    remove_taint_rule(tmp_path, "sink", "os.system(")

    assert path.read_text(encoding="utf-8") == ANNOTATED


def test_listing_reads_back_what_was_written(tmp_path):
    _write(tmp_path, ANNOTATED)

    add_taint_source(tmp_path, "request.headers[*]", taint_type="user_input")
    add_taint_sanitizer(tmp_path, "bleach.clean(", cleanses=["xss"])

    rules = list_taint_rules(tmp_path)
    assert [s["pattern"] for s in rules["sources"]] == [
        "request.json[*]", "request.headers[*]",
    ]
    assert rules["sanitizers"] == [{"pattern": "bleach.clean(", "cleanses": ["xss"]}]
