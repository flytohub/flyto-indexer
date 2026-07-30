from src.finding_identity import (
    finding_fingerprint,
    normalize_finding_anchor,
    normalize_finding_path,
    stable_finding_id,
)


def test_finding_identity_is_deterministic_and_line_independent():
    first = stable_finding_id(
        "taint/rce",
        "./src/runner.py",
        anchor={"source": "request.args['cmd']", "sink": "subprocess.run(cmd)"},
    )
    second = stable_finding_id(
        "taint/rce",
        "src\\runner.py",
        anchor={"sink": "subprocess.run(cmd)", "source": "request.args['cmd']"},
    )

    assert first == second
    assert first.startswith("flyto-")
    assert len(first) == 30


def test_finding_identity_changes_for_semantically_different_finding():
    first = finding_fingerprint("taint/rce", "src/runner.py", anchor="eval(user_input)")
    second = finding_fingerprint("taint/rce", "src/runner.py", anchor="exec(user_input)")

    assert first != second


def test_normalizers_are_bounded_and_do_not_resolve_filesystem_paths():
    assert normalize_finding_path("./src\\app.py") == "src/app.py"
    assert normalize_finding_anchor("  one \n  two  ") == "one two"
    assert len(normalize_finding_anchor("x" * 2000)) == 1000
