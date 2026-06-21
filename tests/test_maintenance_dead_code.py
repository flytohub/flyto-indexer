"""Dead-code MCP heuristics should prefer high-confidence findings."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools import maintenance


def _symbol(project, path, typ, name, start=1, end=20, language="python", exports=None):
    return {
        "project": project,
        "path": path,
        "type": typ,
        "name": name,
        "start_line": start,
        "end_line": end,
        "language": language,
        "exports": exports or [],
    }


def _index(root: Path, symbols: dict) -> dict:
    return {
        "project": "proj",
        "root_path": str(root),
        "symbols": symbols,
        "dependencies": {},
        "reverse_index": {},
    }


def test_python_dispatch_table_callback_is_not_dead(monkeypatch, tmp_path):
    source = tmp_path / "src" / "audit.py"
    source.parent.mkdir()
    source.write_text(
        "def section_circular(files):\n"
        "    return files\n\n"
        "SECTIONS = {\n"
        "    'circular': section_circular,\n"
        "}\n",
        encoding="utf-8",
    )
    sid = "proj:src/audit.py:function:section_circular"
    index = _index(tmp_path, {
        sid: _symbol("proj", "src/audit.py", "function", "section_circular", end=2),
    })

    monkeypatch.setattr(maintenance, "load_index", lambda: index)
    result = maintenance.find_dead_code(project="proj", min_lines=1)

    assert result["total_dead"] == 0


def test_python_decorator_registered_function_is_not_dead(monkeypatch, tmp_path):
    source = tmp_path / "src" / "audit.py"
    source.parent.mkdir()
    source.write_text(
        "CHECKS = {}\n\n"
        "def check(name):\n"
        "    def deco(fn):\n"
        "        CHECKS[name] = fn\n"
        "        return fn\n"
        "    return deco\n\n"
        "@check('todo')\n"
        "def find_todo(text):\n"
        "    return []\n",
        encoding="utf-8",
    )
    sid = "proj:src/audit.py:function:find_todo"
    index = _index(tmp_path, {
        sid: _symbol("proj", "src/audit.py", "function", "find_todo", start=10, end=12),
    })

    monkeypatch.setattr(maintenance, "load_index", lambda: index)
    result = maintenance.find_dead_code(project="proj", min_lines=1)

    assert result["total_dead"] == 0


def test_go_exported_model_is_not_dead(monkeypatch, tmp_path):
    sid = "proj:internal/store/models_resource_kernel.go:class:KernelResource"
    index = _index(tmp_path, {
        sid: _symbol(
            "proj",
            "internal/store/models_resource_kernel.go",
            "class",
            "KernelResource",
            language="go",
            exports=["KernelResource"],
        ),
    })

    monkeypatch.setattr(maintenance, "load_index", lambda: index)
    result = maintenance.find_dead_code(project="proj", min_lines=1)

    assert result["total_dead"] == 0


def test_unreferenced_internal_go_helper_is_dead(monkeypatch, tmp_path):
    sid = "proj:internal/worker/helpers.go:function:unusedHelper"
    index = _index(tmp_path, {
        sid: _symbol(
            "proj",
            "internal/worker/helpers.go",
            "function",
            "unusedHelper",
            language="go",
        ),
    })

    monkeypatch.setattr(maintenance, "load_index", lambda: index)
    result = maintenance.find_dead_code(project="proj", min_lines=1)

    assert result["total_dead"] == 1
    assert result["dead_symbols"][0]["symbol_id"] == sid


def test_fixture_symbol_is_not_dead(monkeypatch, tmp_path):
    sid = "proj:.semgrep/fixtures/command-risk.py:function:py_fixtures"
    index = _index(tmp_path, {
        sid: _symbol(
            "proj",
            ".semgrep/fixtures/command-risk.py",
            "function",
            "py_fixtures",
            end=20,
        ),
    })

    monkeypatch.setattr(maintenance, "load_index", lambda: index)
    result = maintenance.find_dead_code(project="proj", min_lines=1)

    assert result["total_dead"] == 0


def test_vitepress_markdown_component_usage_is_not_dead(monkeypatch, tmp_path):
    theme = tmp_path / ".vitepress" / "theme"
    theme.mkdir(parents=True)
    (theme / "BlogHero.vue").write_text(
        "<template><section>Blog</section></template>\n",
        encoding="utf-8",
    )
    (tmp_path / "index.md").write_text(
        "---\nlayout: page\n---\n\n<BlogHero />\n",
        encoding="utf-8",
    )
    sid = "proj:.vitepress/theme/BlogHero.vue:component:BlogHero"
    index = _index(tmp_path, {
        sid: _symbol(
            "proj",
            ".vitepress/theme/BlogHero.vue",
            "component",
            "BlogHero",
            start=1,
            end=20,
            language="vue",
        ),
    })

    monkeypatch.setattr(maintenance, "load_index", lambda: index)
    result = maintenance.find_dead_code(project="proj", min_lines=1)

    assert result["total_dead"] == 0


def test_go_type_referenced_from_same_project_source_is_not_dead(monkeypatch, tmp_path):
    model = tmp_path / "internal" / "autofix" / "rules" / "cve_bump.go"
    parser = tmp_path / "internal" / "autofix" / "rules" / "cve_bump_parsers.go"
    model.parent.mkdir(parents=True)
    model.write_text(
        "package rules\n\n"
        "type depCandidate struct {\n"
        "    Name string\n"
        "}\n",
        encoding="utf-8",
    )
    parser.write_text(
        "package rules\n\n"
        "func parsePackageJSON(body string) []depCandidate {\n"
        "    return []depCandidate{{Name: body}}\n"
        "}\n",
        encoding="utf-8",
    )
    sid = "proj:internal/autofix/rules/cve_bump.go:class:depCandidate"
    index = _index(tmp_path, {
        sid: _symbol(
            "proj",
            "internal/autofix/rules/cve_bump.go",
            "class",
            "depCandidate",
            language="go",
        ),
        "proj:internal/autofix/rules/cve_bump_parsers.go:file:cve_bump_parsers": _symbol(
            "proj",
            "internal/autofix/rules/cve_bump_parsers.go",
            "file",
            "cve_bump_parsers",
            language="go",
        ),
    })

    monkeypatch.setattr(maintenance, "load_index", lambda: index)
    result = maintenance.find_dead_code(project="proj", min_lines=1)

    assert result["total_dead"] == 0
