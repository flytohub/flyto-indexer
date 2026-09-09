"""A source pattern matches a name, not a substring of one.

Sources were matched with a bare `pattern in text`, so FastAPI's `File(`
marker matched `ZipFile(` and `NamedTemporaryFile(`, `Cookie(` matched
`SimpleCookie(`, and `input(` matched `make_input(`. Measured over 38,899
files that is 5,122 matches; every sampled one is a different callable.

The two pattern shapes need different left boundaries, which is what these
tests pin: a dotted pattern is deliberately the tail of an object expression
(`flask_request.get_json(` is the documented reason), a bare-name marker is one
specific callable.
"""

import os
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from analyzer.taint import TaintAnalyzer, _source_matches  # noqa: E402


def _analyze(code: str):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "app.py").write_text(textwrap.dedent(code))
        return TaintAnalyzer(root).analyze()


class TestBareNameMarkers:
    def test_a_longer_identifier_ending_in_the_marker_is_a_different_callable(self):
        assert not _source_matches("File(", "ZipFile(path)")
        assert not _source_matches("File(", "tempfile.NamedTemporaryFile()")
        assert not _source_matches("Cookie(", "SimpleCookie()")
        assert not _source_matches("Query(", "NearestQuery(x)")
        assert not _source_matches("Header(", "InvalidHeader(line)")
        assert not _source_matches("Form(", "prettyForm(x)")
        assert not _source_matches("Body(", "RigidBody(x)")

    def test_underscore_does_not_make_a_boundary_for_a_bare_marker(self):
        # `make_input(` and `_get_input(` are not the stdlib prompt.
        assert not _source_matches("input(", "make_input()")
        assert not _source_matches("input(", "_get_input()")
        assert not _source_matches("input(", "process_input(x)")

    def test_the_marker_itself_still_matches(self):
        assert _source_matches("File(", "File(...)")
        assert _source_matches("File(", "fastapi.File(...)")
        assert _source_matches("Cookie(", "Cookie(...)")
        assert _source_matches("input(", "input('cmd: ')")
        assert _source_matches("input(", "x = input()")


class TestDottedPatterns:
    def test_a_prefixed_request_object_still_matches(self):
        # Documented behaviour: `request.get_json(` is meant to catch
        # `flask_request.get_json(`, which is how a request threaded through a
        # parameter is read.
        assert _source_matches("request.get_json(", "flask_request.get_json(force=True)")
        assert _source_matches("request.headers", "http_request.headers")
        assert _source_matches("request.args", "request.args.get('q')")

    def test_a_letter_before_a_dotted_pattern_is_not_a_boundary(self):
        assert not _source_matches("request.args", "myrequest.args")

    def test_a_longer_attribute_is_not_the_pattern(self):
        assert not _source_matches("request.data", "request.database")
        assert not _source_matches("request.form", "request.formdata")


class TestEndToEnd:
    def test_a_temporary_file_is_not_an_upload(self):
        # flyto-flow's backup.py reported a path traversal because
        # `NamedTemporaryFile(` matched the `File(` marker. The destination is
        # the operator's own backup path, not anything a request supplied.
        findings = _analyze("""
            import tempfile, tarfile
            def back_up(destination):
                tmp = tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".x")
                with tarfile.open(tmp.name, "w:gz") as tar:
                    tar.add(destination)
        """)
        assert findings == []

    def test_simplecookie_is_not_the_fastapi_marker(self):
        findings = _analyze("""
            from http.cookies import SimpleCookie
            def build(resp):
                c = SimpleCookie()
                resp.headers["Set-Cookie"] = c.output(header="", sep=";").strip()
        """)
        assert findings == []

    def test_a_real_upload_parameter_is_still_a_source(self):
        findings = _analyze("""
            import os
            from fastapi import File
            def upload(name = File(...)):
                os.system(name)
        """)
        assert [f.category for f in findings] == ["rce"]

    def test_a_prefixed_request_is_still_a_source(self):
        findings = _analyze("""
            import os
            def handler(flask_request):
                body = flask_request.get_json(force=True)
                os.system(body)
        """)
        assert [f.category for f in findings] == ["rce"]
