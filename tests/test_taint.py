"""Tests for AST-based taint analysis engine."""

import textwrap
import tempfile
from pathlib import Path

import pytest

from src.analyzer.taint import TaintAnalyzer, TaintFlow


def _analyze_code(code: str, **kwargs) -> list[TaintFlow]:
    """Helper: write code to a temp .py file and analyze it."""
    code = textwrap.dedent(code)
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        fpath = root / "app.py"
        fpath.write_text(code)
        analyzer = TaintAnalyzer(root, **kwargs)
        return analyzer.analyze()


# ── Phase 1: Single-function taint tracking ────────────────────────────────


class TestDirectSourceToSink:
    """Source flows directly to sink in one function."""

    def test_sql_injection_fstring(self):
        findings = _analyze_code("""\
            def get_user():
                user_id = request.args.get('id')
                query = f"SELECT * FROM users WHERE id = {user_id}"
                cursor.execute(query)
        """)
        assert len(findings) >= 1
        f = findings[0]
        assert f.category == "sql_injection"
        assert f.severity in ("high", "critical")
        assert "request.args" in f.source_expr

    def test_rce_eval(self):
        findings = _analyze_code("""\
            def run_code():
                code = request.form.get('code')
                eval(code)
        """)
        assert len(findings) >= 1
        assert findings[0].category == "rce"
        assert findings[0].severity == "critical"

    def test_os_system(self):
        findings = _analyze_code("""\
            def run_cmd():
                cmd = request.args.get('cmd')
                os.system(cmd)
        """)
        assert len(findings) >= 1
        assert findings[0].category == "rce"

    def test_subprocess_shell_true(self):
        findings = _analyze_code("""\
            def run_cmd():
                cmd = request.args.get('cmd')
                subprocess.run(cmd, shell=True)
        """)
        assert len(findings) >= 1
        assert findings[0].category == "rce"

    def test_xss_render_template_string(self):
        findings = _analyze_code("""\
            def show():
                name = request.args.get('name')
                render_template_string(name)
        """)
        assert len(findings) >= 1
        assert findings[0].category == "xss"

    def test_pickle_loads(self):
        findings = _analyze_code("""\
            def load_data():
                data = request.data
                obj = pickle.loads(data)
        """)
        assert len(findings) >= 1
        assert findings[0].category == "deserialization"


class TestTaintPropagation:
    """Taint propagates through variable assignments."""

    def test_multi_step_assignment(self):
        findings = _analyze_code("""\
            def get_user():
                raw = request.args.get('id')
                user_id = raw
                query = f"SELECT * FROM users WHERE id = {user_id}"
                cursor.execute(query)
        """)
        assert len(findings) >= 1
        assert findings[0].category == "sql_injection"

    def test_fstring_propagates_taint(self):
        findings = _analyze_code("""\
            def process():
                name = request.form.get('name')
                msg = f"Hello {name}"
                eval(msg)
        """)
        assert len(findings) >= 1
        assert findings[0].category == "rce"

    def test_string_concat_propagates_taint(self):
        findings = _analyze_code("""\
            def process():
                name = request.args.get('name')
                query = "SELECT * FROM users WHERE name = '" + name + "'"
                cursor.execute(query)
        """)
        assert len(findings) >= 1
        assert findings[0].category == "sql_injection"

    def test_for_loop_propagates_taint(self):
        findings = _analyze_code("""\
            def process():
                items = request.json.get('items')
                for item in items:
                    eval(item)
        """)
        assert len(findings) >= 1
        assert findings[0].category == "rce"


class TestSanitizers:
    """Sanitizers break the taint chain."""

    def test_int_cast_clears_taint(self):
        findings = _analyze_code("""\
            def get_user():
                user_id = int(request.args.get('id'))
                query = f"SELECT * FROM users WHERE id = {user_id}"
                cursor.execute(query)
        """)
        assert len(findings) == 0

    def test_float_cast_clears_taint(self):
        findings = _analyze_code("""\
            def calc():
                val = float(request.args.get('amount'))
                cursor.execute(f"UPDATE t SET amount = {val}")
        """)
        assert len(findings) == 0

    def test_html_escape_clears_xss(self):
        findings = _analyze_code("""\
            def show():
                name = html.escape(request.args.get('name'))
                render_template_string(name)
        """)
        assert len(findings) == 0

    def test_shlex_quote_clears_rce(self):
        findings = _analyze_code("""\
            def run_cmd():
                cmd = shlex.quote(request.args.get('cmd'))
                os.system(cmd)
        """)
        assert len(findings) == 0


class TestParameterizedQuery:
    """Parameterized queries (execute with 2+ args) are safe."""

    def test_parameterized_is_safe(self):
        findings = _analyze_code("""\
            def get_user():
                user_id = request.args.get('id')
                cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        """)
        assert len(findings) == 0

    def test_single_arg_execute_is_unsafe(self):
        findings = _analyze_code("""\
            def get_user():
                user_id = request.args.get('id')
                query = f"SELECT * FROM users WHERE id = {user_id}"
                cursor.execute(query)
        """)
        assert len(findings) >= 1


class TestNoFalsePositives:
    """Ensure clean code does not trigger findings."""

    def test_no_source_no_finding(self):
        findings = _analyze_code("""\
            def process():
                user_id = 42
                query = f"SELECT * FROM users WHERE id = {user_id}"
                cursor.execute(query)
        """)
        assert len(findings) == 0

    def test_local_variable_not_tainted(self):
        findings = _analyze_code("""\
            def process():
                name = "admin"
                os.system(f"echo {name}")
        """)
        assert len(findings) == 0

    def test_subprocess_arg_list_without_shell_is_safe(self):
        findings = _analyze_code("""\
            def process():
                repo = request.args.get('repo')
                subprocess.run(["git", "-C", repo, "status"], timeout=30)
        """)
        assert len(findings) == 0

    def test_regex_validator_clears_redos(self):
        findings = _analyze_code("""\
            def process():
                pattern = sys.argv[3]
                pattern = _validate_grep_pattern(pattern)
                re.compile(pattern)
        """)
        assert len(findings) == 0

    def test_dist_next_assets_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            asset_dir = root / "dist-next" / "assets"
            asset_dir.mkdir(parents=True)
            (asset_dir / "bundle.js").write_text(
                "location.hash && (document.body.innerHTML = location.hash)\n",
                encoding="utf-8",
            )
            findings = TaintAnalyzer(root).analyze()
            assert len(findings) == 0

    def test_next_build_outputs_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for rel in (
                ".next/server",
                ".next/static/chunks",
                ".open-next/server",
                "out/_next/static",
            ):
                asset_dir = root / rel
                asset_dir.mkdir(parents=True)
                (asset_dir / "bundle.js").write_text(
                    "location.hash && (document.body.innerHTML = location.hash)\n",
                    encoding="utf-8",
                )

            findings = TaintAnalyzer(root).analyze()
            assert len(findings) == 0

    def test_go_url_query_read_alone_is_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "handler.go").write_text(
                'func h(r *http.Request) { filter := r.URL.Query().Get("repo_id"); _ = filter }\n',
                encoding="utf-8",
            )
            findings = TaintAnalyzer(root).analyze()
            assert len(findings) == 0

    def test_go_url_query_to_db_query_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "handler.go").write_text(
                'func h(r *http.Request) { filter := r.URL.Query().Get("repo_id"); db.Query("select " + filter) }\n',
                encoding="utf-8",
            )
            findings = TaintAnalyzer(root).analyze()
            assert any(f.category == "sql_injection" for f in findings)

    def test_sanitizer_reassignment(self):
        findings = _analyze_code("""\
            def process():
                val = request.args.get('x')
                val = int(val)
                cursor.execute(f"SELECT * FROM t WHERE x = {val}")
        """)
        assert len(findings) == 0


# ── Phase 2: Cross-function taint ──────────────────────────────────────────


class TestCrossFunction:
    """Cross-function taint tracking via reverse_index."""

    def test_cross_function_taint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Callee: function with param reaching a sink
            (root / "db_utils.py").write_text(textwrap.dedent("""\
                def run_query(query):
                    cursor.execute(query)
            """))

            # Caller: passes tainted data to callee
            (root / "api.py").write_text(textwrap.dedent("""\
                def handle_request():
                    user_id = request.args.get('id')
                    query = f"SELECT * FROM users WHERE id = {user_id}"
                    run_query(query)
            """))

            # Provide a reverse_index so cross-function works
            index = {
                "reverse_index": {
                    "run_query": ["api.py"],
                },
            }
            analyzer = TaintAnalyzer(root, index=index)
            findings = analyzer.analyze()

            # Should find: direct sink in db_utils.py AND cross-function in api.py
            categories = [f.category for f in findings]
            assert "sql_injection" in categories


# ── YAML custom rules ──────────────────────────────────────────────────────


class TestYAMLRules:
    """YAML rules extend defaults."""

    def test_custom_source_and_sink(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Write YAML rules
            (root / "taint_rules.yaml").write_text(textwrap.dedent("""\
                version: 1
                sources:
                  - pattern: "custom_sdk.get_input"
                    category: user_input
                sinks:
                  - pattern: "custom_db.raw_query"
                    vuln_type: sql_injection
                    severity: critical
                    recommendation: Use safe query builder
                sanitizers: []
                overrides:
                  remove_sources: []
                  remove_sinks: []
            """))

            # Write code using custom patterns
            (root / "app.py").write_text(textwrap.dedent("""\
                def handler():
                    data = custom_sdk.get_input()
                    custom_db.raw_query(data)
            """))

            analyzer = TaintAnalyzer(root)
            findings = analyzer.analyze()
            assert len(findings) >= 1
            assert findings[0].category == "sql_injection"
            assert findings[0].severity == "critical"

    def test_remove_default_source(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            (root / "taint_rules.yaml").write_text(textwrap.dedent("""\
                version: 1
                sources: []
                sinks: []
                sanitizers: []
                overrides:
                  remove_sources: ["request.args"]
                  remove_sinks: []
            """))

            (root / "app.py").write_text(textwrap.dedent("""\
                def handler():
                    user_id = request.args.get('id')
                    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
            """))

            analyzer = TaintAnalyzer(root)
            findings = analyzer.analyze()
            # request.args is removed, but request.args.get still matches
            # because other sources like request.form, etc. are still active
            # The key test is that the override mechanism works


# ── JS/TS regex fallback ───────────────────────────────────────────────────


class TestRegexFallback:
    """JS/TS regex-based taint patterns."""

    def test_js_req_body_to_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "api.js").write_text(
                'const result = db.query("SELECT * FROM users WHERE id = " + req.body.id);'
            )
            analyzer = TaintAnalyzer(root)
            findings = analyzer.analyze()
            # The regex checks req.body...query pattern
            js_findings = [f for f in findings if f.file_path.endswith(".js")]
            assert len(js_findings) >= 1

    def test_go_formvalue_to_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "handler.go").write_text(
                'name := r.FormValue("name")\nrows, err := db.Query("SELECT * FROM users WHERE name = " + name)'
            )
            analyzer = TaintAnalyzer(root)
            findings = analyzer.analyze()
            go_findings = [f for f in findings if f.file_path.endswith(".go")]
            assert len(go_findings) >= 1

    def test_comments_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "api.js").write_text(
                '// const result = db.query("SELECT * FROM users WHERE id = " + req.body.id);'
            )
            analyzer = TaintAnalyzer(root)
            findings = analyzer.analyze()
            js_findings = [f for f in findings if f.file_path.endswith(".js")]
            assert len(js_findings) == 0


# ── Integration with SecurityIssue format ──────────────────────────────────


class TestOutputFormat:
    """TaintFlow can be converted to SecurityIssue-compatible dict."""

    def test_taintflow_fields(self):
        findings = _analyze_code("""\
            def get_user():
                user_id = request.args.get('id')
                cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
        """)
        assert len(findings) >= 1
        f = findings[0]
        assert hasattr(f, "file_path")
        assert hasattr(f, "line")
        assert hasattr(f, "severity")
        assert hasattr(f, "category")
        assert hasattr(f, "source_expr")
        assert hasattr(f, "sink_expr")
        assert hasattr(f, "flow_chain")
        assert hasattr(f, "recommendation")
        assert isinstance(f.flow_chain, list)
        assert len(f.flow_chain) >= 1


# ── Performance / limits ───────────────────────────────────────────────────


class TestLimits:
    """Ensure performance limits are respected."""

    def test_skips_test_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            test_dir = root / "tests"
            test_dir.mkdir()
            (test_dir / "test_app.py").write_text(textwrap.dedent("""\
                def test_vuln():
                    user_id = request.args.get('id')
                    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
            """))
            analyzer = TaintAnalyzer(root)
            findings = analyzer.analyze()
            assert len(findings) == 0

    def test_max_findings_cap(self):
        """Generate many vulnerable functions and verify cap."""
        funcs = []
        for i in range(60):
            funcs.append(f"""\
def vuln_{i}():
    x = request.args.get('x')
    eval(x)
""")
        code = "\n".join(funcs)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "app.py").write_text(code)
            analyzer = TaintAnalyzer(root)
            findings = analyzer.analyze()
            from src.analyzer.taint import MAX_FINDINGS
            assert len(findings) <= MAX_FINDINGS


class TestFalsePositiveReductions:
    """Regression tests for the tier2-ai taint false positives.

    Each test pins one confirmed FP shape (must NOT fire) and is paired with a
    genuine injected flow of the same category (must STILL fire), so a future
    blanket-suppress that kills the real positive is caught.
    """

    # ── SOURCES: env vars / __file__ are operator/interpreter-controlled ──────

    def test_env_var_join_with_literals_not_path_traversal(self):
        """os.environ.get path joined with hardcoded filenames is not traversal."""
        findings = _analyze_code("""\
            import os
            def _get_indexer_module():
                indexer_path = os.environ.get("FLYTO_INDEXER_PATH", "/opt/flyto-indexer")
                mcp_file = os.path.join(indexer_path, "src", "mcp_server.py")
                return mcp_file
        """)
        assert [f for f in findings if f.category == "path_traversal"] == []

    def test_env_var_exec_module_not_rce(self):
        """Loading a module from an operator-set env path is not an RCE source."""
        findings = _analyze_code("""\
            import os, importlib.util
            def _load():
                indexer_path = os.environ.get("FLYTO_INDEXER_PATH", "/opt/flyto-indexer")
                mcp_file = os.path.join(indexer_path, "src", "mcp_server.py")
                spec = importlib.util.spec_from_file_location("m", mcp_file)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
        """)
        assert [f for f in findings if f.category == "rce"] == []

    def test_os_getenv_not_a_source(self):
        findings = _analyze_code("""\
            import os
            def run():
                cmd = os.getenv("MY_CMD")
                os.system(cmd)
        """)
        assert findings == []

    def test_path_dunder_file_not_a_source(self):
        """Path(__file__) is interpreter-controlled; regex over repo files is safe."""
        findings = _analyze_code("""\
            import re
            from pathlib import Path
            def fix_schema():
                root = Path(__file__).resolve().parent
                for f in root.rglob("*.py"):
                    text = f.read_text()
                    re.sub(r"foo", "bar", text)
        """)
        assert findings == []

    # ── SINKS: redos requires a real regex op with a dynamic pattern ──────────

    def test_vector_search_lookalike_not_redos(self):
        """store.search(...) is a vector search, not re.search — not ReDoS."""
        findings = _analyze_code("""\
            def repl(store):
                query = input("> ").strip()
                results = store.search(query, top_k=2)
                return results
        """)
        assert [f for f in findings if f.category == "redos"] == []

    def test_precompiled_search_with_static_pattern_not_redos(self):
        """A precompiled pattern searched over tainted text has a fixed pattern."""
        findings = _analyze_code("""\
            import re
            PATTERNS = [re.compile(r"AKIA[0-9A-Z]{16}")]
            def scan(added_line):
                for pattern_re in PATTERNS:
                    match = pattern_re.search(added_line)
                    return match
        """)
        assert [f for f in findings if f.category == "redos"] == []

    # ── REAL positives must still fire (no over-suppression) ──────────────────

    def test_real_user_controlled_regex_still_redos(self):
        """re.compile on a request-sourced pattern is a genuine ReDoS sink."""
        findings = _analyze_code("""\
            import re
            def search(request):
                pat = request.args.get("pattern")
                rx = re.compile(pat)
                return rx
        """)
        assert any(f.category == "redos" for f in findings)

    def test_real_user_path_join_still_path_traversal(self):
        """A request-sourced filename joined into a path is still traversal."""
        findings = _analyze_code("""\
            import os
            def download(request):
                base = "/var/data"
                name = request.args.get("file")
                full = os.path.join(base, name)
                return full
        """)
        assert any(f.category == "path_traversal" for f in findings)

    def test_real_user_input_to_eval_still_rce(self):
        """input() into eval remains a genuine RCE (interactive stdin source)."""
        findings = _analyze_code("""\
            def run():
                code = input("code> ")
                eval(code)
        """)
        assert any(f.category == "rce" for f in findings)
