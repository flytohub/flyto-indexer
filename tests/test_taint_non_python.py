"""Regex-based taint for JavaScript/TypeScript and Go.

These languages are scanned with line-oriented regex patterns rather than an
AST, which is a deliberate bound. These tests pin what that pass must catch and
— more importantly — the two false-positive classes found by running it against
real projects (gogs, strapi).
"""

import textwrap

from src.analyzer.taint import TaintAnalyzer, _is_generated_asset


def _analyze(tmp_path, name: str, code: str):
    (tmp_path / name).write_text(textwrap.dedent(code))
    return TaintAnalyzer(tmp_path).analyze()


class TestCatchesRealShapes:
    def test_js_same_line_and_across_lines(self, tmp_path):
        flows = _analyze(tmp_path, "app.ts", """\
            app.get("/a", (req, res) => { db.query("SELECT " + req.query.name); });

            app.get("/b", (req, res) => {
              const name = req.query.name;
              db.query("SELECT * FROM u WHERE n='" + name + "'");
            });
        """)
        assert len([f for f in flows if f.category == "sql_injection"]) >= 2

    def test_go_across_lines(self, tmp_path):
        flows = _analyze(tmp_path, "main.go", """\
            func b(w http.ResponseWriter, r *http.Request) {
            \tname := r.FormValue("name")
            \tdb.Query("SELECT * FROM u WHERE n='" + name + "'")
            }
        """)
        assert any(f.category == "sql_injection" for f in flows)


class TestGeneratedAssetsAreSkipped:
    """gogs's only two findings were both minified vendor bundles. One line of
    a minified bundle is tens of thousands of characters, so a line-oriented
    regex matches something in nearly all of them."""

    def test_classifier(self):
        assert _is_generated_asset("public/js/jquery-3.7.1.min.js") is True
        assert _is_generated_asset("public/plugins/mermaid/mermaid.min.js") is True
        assert _is_generated_asset("vendor/lib/thing.js") is True
        assert _is_generated_asset("src/ui/web/backend/static/assets/app-a1b2.js") is True
        assert _is_generated_asset("src/app.ts") is False

    def test_minified_bundle_produces_no_findings(self, tmp_path):
        noisy = 'location.hash;a.innerHTML=b;' * 200
        flows = _analyze(tmp_path, "jquery-3.7.1.min.js", noisy)
        assert flows == []

    def test_vendor_tree_is_skipped(self, tmp_path):
        (tmp_path / "vendor").mkdir()
        flows = _analyze(tmp_path, "vendor/lib.js", 'location.hash;a.innerHTML=b;')
        assert flows == []


class TestBareKeywordBoundary:
    """`validateRegistrationInfoQuery(ctx.request.query)` is a validator, not
    SQL. strapi's only finding was this — the bare `query(` keyword matched the
    tail of an identifier, the same class fixed earlier for Python sinks."""

    def test_identifier_tail_is_not_a_sql_sink(self, tmp_path):
        flows = _analyze(tmp_path, "auth.ts", """\
            export default {
              async register(ctx) {
                await validateRegistrationInfoQuery(ctx.request.query);
              },
            };
        """)
        assert not [f for f in flows if f.category == "sql_injection"]

    def test_real_query_call_still_matches(self, tmp_path):
        flows = _analyze(tmp_path, "db.ts", """\
            export async function find(req) {
              return db.query("SELECT * FROM u WHERE n='" + req.query.n + "'");
            }
        """)
        assert any(f.category == "sql_injection" for f in flows)
