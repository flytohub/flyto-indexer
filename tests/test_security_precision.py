"""Precision regressions for the security scanner."""

from src.analyzer.security import SecurityScanner

_FAKE_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"


def _scan(tmp_path, source: str):
    return SecurityScanner(tmp_path).scan_file("src/example.py", source)


def test_detector_pattern_and_known_fake_assignments_are_not_findings(tmp_path):
    source = """
SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
]
KNOWN_FAKE_SECRET_VALUES = {
    "__FAKE_AWS_KEY__",
}
UNSAFE_FUNCTIONS = [
    (r"eval\\(", "eval()", "arbitrary execution"),
]
VULNERABILITY_RULES = [
    {"description": "DEBUG=True or template.HTML()"},
]
SINKS = {
    "rce": [("eval(", "critical", "avoid arbitrary execution")],
}
"""
    issues = _scan(
        tmp_path,
        source.replace("__FAKE_AWS_KEY__", _FAKE_AWS_KEY),
    )

    assert issues == []


def test_real_secret_outside_detector_fixture_is_still_detected(tmp_path):
    issues = _scan(
        tmp_path,
        f'aws_access_key = "{_FAKE_AWS_KEY}"\n',
    )

    assert any(issue.category == "hardcoded_secret" for issue in issues)


def test_md5_content_checksum_is_not_treated_as_cryptography(tmp_path):
    issues = _scan(
        tmp_path,
        "chunk_hash = hashlib.md5(chunk_text.encode()).hexdigest()\n",
    )

    assert not any(issue.category == "weak_crypto" for issue in issues)


def test_md5_password_hash_remains_a_security_finding(tmp_path):
    issues = _scan(
        tmp_path,
        "password_hash = hashlib.md5(password.encode()).hexdigest()\n",
    )

    assert any(issue.category == "weak_crypto" for issue in issues)


def test_non_sql_insertions_identifier_is_not_sql_injection(tmp_path):
    issues = _scan(
        tmp_path,
        'churn = int(item.get("insertions", 0)) + int(item.get("deletions", 0))\n',
    )

    assert not any(issue.category == "sql_injection" for issue in issues)


def test_real_sql_concatenation_remains_a_finding(tmp_path):
    issues = _scan(
        tmp_path,
        'cursor.execute("SELECT * FROM users WHERE id=" + user_input)\n',
    )

    assert any(issue.category == "sql_injection" for issue in issues)


def test_logging_helper_name_or_literal_is_not_sensitive_data(tmp_path):
    issues = _scan(
        tmp_path,
        'print(format_secret_scan(result))\nlogger.debug("secret scan failed: %s", error)\n',
    )

    assert not any(issue.category == "info_leaks" for issue in issues)


def test_logging_sensitive_identifier_remains_a_finding(tmp_path):
    issues = _scan(tmp_path, "print(password)\n")

    assert any(issue.category == "info_leaks" for issue in issues)


def test_string_literal_stripping_is_linear():
    """The literal-stripping regex used to backtrack exponentially.

    CodeQL py/redos flagged `(?:\\.|(?!\1).)*`: both branches could match a
    backslash, so an unterminated literal full of `\a` blew up. This scanner
    runs over repositories it did not write, so that is a denial of service,
    not a style point.
    """
    import time

    from src.analyzer.security import _STRING_LITERAL_RE

    hostile = '"' + "\\a" * 5000
    start = time.monotonic()
    _STRING_LITERAL_RE.sub("", hostile)

    assert time.monotonic() - start < 1.0


def test_string_literals_are_still_stripped():
    from src.analyzer.security import _STRING_LITERAL_RE

    assert _STRING_LITERAL_RE.sub("", 'x = "password" + token') == "x =  + token"
    assert _STRING_LITERAL_RE.sub("", "y = 'secret'") == "y = "
    assert _STRING_LITERAL_RE.sub("", 'a = "esc\\"aped" + secret') == "a =  + secret"


def test_vue_script_block_matches_sloppy_closing_tags():
    """CodeQL py/bad-tag-filter: `</script bar>` must not slip past."""
    from src.analyzer.layers import _VUE_SCRIPT_BLOCK

    assert _VUE_SCRIPT_BLOCK.search("<script>import a from 'b'</script >")
    assert _VUE_SCRIPT_BLOCK.search("<script setup>x</script\tfoo>")
    assert _VUE_SCRIPT_BLOCK.search("<script>x</ script>")
