"""Tests for documentation scanner scoring edge cases."""

import json
from pathlib import Path

from src.doc_scanner import _check_module_doc_coverage, scan_documentation


def _marked_env(parent: Path, name: str) -> Path:
    """Create a virtual environment under an arbitrary, unlisted name.

    PEP 405 writes a regular `pyvenv.cfg` at the environment root; that
    marker, not the name, is what discovery must react to.
    """
    env = parent / name
    site_packages = env / "lib" / "python3.11" / "site-packages"
    site_packages.mkdir(parents=True)
    (site_packages / "typing_extensions.py").write_text(
        "Any = object()\n", encoding="utf-8"
    )
    (env / "pyvenv.cfg").write_text(
        "home = /usr/bin\nversion = 3.11.9\n", encoding="utf-8"
    )
    return env


def test_marked_virtualenv_is_not_an_undocumented_module_root(tmp_path):
    """An environment owns no documentation this repository answers for."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "README.md").write_text("# src\n", encoding="utf-8")
    # Neither name is dot-prefixed or on any skip list, so only the marker
    # can keep them out of the denominator.
    _marked_env(tmp_path, "env311")
    _marked_env(tmp_path, "SecSandbox")

    assert _check_module_doc_coverage(tmp_path) == 1.0


def test_unmarked_lookalike_directories_are_still_discovered(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "README.md").write_text("# src\n", encoding="utf-8")
    # No marker: a source directory that merely spells venv is still a module
    # root this repository owns, so it still counts as undocumented.
    (tmp_path / "venv_tools").mkdir()
    (tmp_path / "venv_tools" / "loader.py").write_text("x = 1\n", encoding="utf-8")
    # A directory named like the marker is not a regular marker file.
    (tmp_path / "pyvenv_docs" / "pyvenv.cfg").mkdir(parents=True)

    assert _check_module_doc_coverage(tmp_path) == 1 / 3


def test_marked_virtualenv_does_not_move_the_documentation_score(tmp_path):
    """The gate failure reproduced: a local env dragged the docs score down."""
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Installation\n\nInstall.\n\n## Usage\n\nRun.\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "README.md").write_text("# src\n", encoding="utf-8")
    (tmp_path / "src" / "app.py").write_text('"""App."""\n', encoding="utf-8")
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "project": "demo",
            "root_path": str(tmp_path),
            "symbols": {},
        }),
        encoding="utf-8",
    )

    before = scan_documentation(tmp_path)
    _marked_env(tmp_path, "sec-sandbox")
    after = scan_documentation(tmp_path)

    assert before.module_doc_coverage == 1.0
    assert after.module_doc_coverage == 1.0
    assert after.overall_score == before.overall_score


def test_no_api_symbols_is_not_penalized(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Installation\n\nInstall.\n\n## Usage\n\nRun.\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "project": "demo",
            "root_path": str(tmp_path),
            "symbols": {},
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.api_doc_coverage == 1.0
    assert not any("API routes" in suggestion for suggestion in result.suggestions)


def test_no_documentable_symbols_is_not_penalized(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Installation\n\nInstall.\n\n## Usage\n\nRun.\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "project": "demo",
            "root_path": str(tmp_path),
            "symbols": {
                "demo:README.md:file:README": {
                    "type": "file",
                    "summary": "",
                },
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.inline_doc_coverage == 1.0
    assert result.source_reference_coverage == 1.0
    assert result.symbol_doc_coverage == 1.0
    assert not any("functions and classes" in suggestion for suggestion in result.suggestions)


def test_docs_only_repository_can_declare_configuration_not_applicable(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Installation\n\nNone.\n\n## Usage\n\nRead the docs.\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "documentation-manifest.json").write_text(
        json.dumps({
            "documentation": {
                "configuration_not_applicable": True,
                "module_roots": [],
            }
        }),
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({"project": "demo", "symbols": {}}),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert not result.has_env_example
    assert not any(".env.example" in suggestion for suggestion in result.suggestions)
    assert result.module_doc_coverage == 1.0
    assert not any("top-level modules" in suggestion for suggestion in result.suggestions)
    assert result.overall_score == 82


def test_source_reference_counts_exact_local_symbol_links(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "service.py"
    source.write_text("def documented():\n    return True\n", encoding="utf-8")
    docs = tmp_path / "docs" / "reference"
    docs.mkdir(parents=True)
    (tmp_path / "docs" / "documentation-manifest.json").write_text(
        json.dumps({
            "documentation": {
                "source_reference": "docs/reference/python-api.md",
            },
        }),
        encoding="utf-8",
    )
    (docs / "python-api.md").write_text(
        "# API\n\n[`documented`](<../../src/service.py#L1>)\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "project": "demo",
            "root_path": str(tmp_path),
            "symbols": {
                "demo:src/service.py:function:documented": {
                    "type": "function",
                    "path": "src/service.py",
                    "start_line": 1,
                    "summary": "",
                },
                "demo:src/service.py:function:unlinked": {
                    "type": "function",
                    "path": "src/service.py",
                    "start_line": 2,
                    "summary": "",
                },
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.inline_doc_coverage == 0.0
    assert result.source_reference_coverage == 0.5
    assert result.symbol_doc_coverage == 0.5


def test_source_reference_rejects_links_outside_repository(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "documentation-manifest.json").write_text(
        json.dumps({"documentation": {"source_reference": "docs/api.md"}}),
        encoding="utf-8",
    )
    outside = tmp_path.parent / "outside.py"
    outside.write_text("def exposed():\n    return True\n", encoding="utf-8")
    (docs / "api.md").write_text(
        f"[`exposed`](<{outside}#L1>)\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "symbols": {
                "demo:outside.py:function:exposed": {
                    "type": "function",
                    "path": "outside.py",
                    "start_line": 1,
                    "summary": "",
                },
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.source_reference_coverage == 0.0
    assert result.symbol_doc_coverage == 0.0


def test_source_reference_accepts_canonical_github_link(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "service.py").write_text(
        "def documented():\n    return True\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "documentation-manifest.json").write_text(
        json.dumps({
            "repository": "demo",
            "documentation": {"source_reference": "docs/api.md"},
        }),
        encoding="utf-8",
    )
    (docs / "api.md").write_text(
        "[`documented`](https://github.com/example/demo/blob/main/src/service.py#L1)\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "symbols": {
                "demo:src/service.py:function:documented": {
                    "type": "function",
                    "path": "src/service.py",
                    "start_line": 1,
                    "summary": "",
                },
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.source_reference_coverage == 1.0
    assert result.symbol_doc_coverage == 1.0


def test_source_reference_decodes_dynamic_route_paths(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    source_dir = tmp_path / "app" / "[locale]"
    source_dir.mkdir(parents=True)
    (source_dir / "page.tsx").write_text(
        "export function generateMetadata() { return {} }\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "documentation-manifest.json").write_text(
        json.dumps({"documentation": {"source_reference": "docs/api.md"}}),
        encoding="utf-8",
    )
    (docs / "api.md").write_text(
        "[`generateMetadata`](../app/%5Blocale%5D/page.tsx#L1)\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "symbols": {
                "demo:app/[locale]/page.tsx:function:generateMetadata": {
                    "type": "function",
                    "path": "app/[locale]/page.tsx",
                    "start_line": 1,
                    "summary": "",
                },
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.source_reference_coverage == 1.0
    assert result.symbol_doc_coverage == 1.0


def test_source_reference_expands_repository_local_globs(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "service.py").write_text(
        "def first():\n    pass\n\ndef second():\n    pass\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs" / "reference"
    docs.mkdir(parents=True)
    (tmp_path / "docs" / "documentation-manifest.json").write_text(
        json.dumps({
            "documentation": {
                "source_reference": ["docs/reference/source-*.md"],
            },
        }),
        encoding="utf-8",
    )
    (docs / "source-a.md").write_text(
        "[`first`](../../src/service.py#L1)\n",
        encoding="utf-8",
    )
    (docs / "source-b.md").write_text(
        "[`second`](../../src/service.py#L4)\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "symbols": {
                "demo:src/service.py:function:first": {
                    "type": "function",
                    "path": "src/service.py",
                    "start_line": 1,
                    "summary": "",
                },
                "demo:src/service.py:function:second": {
                    "type": "function",
                    "path": "src/service.py",
                    "start_line": 4,
                    "summary": "",
                },
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.source_reference_coverage == 1.0
    assert result.symbol_doc_coverage == 1.0


def test_source_reference_excludes_declared_vendored_symbols(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "vendor" / "dependency").mkdir(parents=True)
    (tmp_path / "src/service.py").write_text("def owned():\n    pass\n", encoding="utf-8")
    (tmp_path / "vendor/dependency/tool.py").write_text(
        "def external():\n    pass\n", encoding="utf-8"
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "documentation-manifest.json").write_text(
        json.dumps({
            "documentation": {
                "source_reference": "docs/api.md",
                "source_reference_exclude": ["vendor/**"],
            },
        }),
        encoding="utf-8",
    )
    (docs / "api.md").write_text(
        "[`owned`](../src/service.py#L1)\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "symbols": {
                "demo:src/service.py:function:owned": {
                    "type": "function", "path": "src/service.py", "start_line": 1, "summary": "",
                },
                "demo:vendor/dependency/tool.py:function:external": {
                    "type": "function", "path": "vendor/dependency/tool.py", "start_line": 1, "summary": "",
                },
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.source_reference_coverage == 1.0
    assert result.symbol_doc_coverage == 1.0


def test_source_reference_rejects_parent_exclusion_glob(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/service.py").write_text("def owned():\n    pass\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "documentation-manifest.json").write_text(
        json.dumps({
            "documentation": {"source_reference_exclude": ["../**", "/tmp/**"]},
        }),
        encoding="utf-8",
    )
    index_dir = tmp_path / ".flyto-index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text(
        json.dumps({
            "symbols": {
                "demo:src/service.py:function:owned": {
                    "type": "function", "path": "src/service.py", "start_line": 1, "summary": "",
                },
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.source_reference_coverage == 0.0
    assert result.symbol_doc_coverage == 0.0


def test_source_reference_ignores_absolute_globs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "documentation-manifest.json").write_text(
        json.dumps({
            "documentation": {
                "source_reference": ["/tmp/source-*.md"],
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.source_reference_coverage == 0.0
    assert result.symbol_doc_coverage == 0.0


def test_module_doc_coverage_ignores_tilde_home_artifact(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Installation\n\nInstall.\n\n## Usage\n\nRun.\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "README.md").write_text("# Source\n", encoding="utf-8")
    (tmp_path / "~" / "Library" / "Android").mkdir(parents=True)

    result = scan_documentation(tmp_path)

    assert result.module_doc_coverage == 1.0


def test_module_doc_coverage_uses_manifest_source_roots(tmp_path):
    (tmp_path / "README.md").write_text("# Docs site\n", encoding="utf-8")
    (tmp_path / "guide").mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "README.md").write_text("# Scripts\n", encoding="utf-8")
    vitepress = tmp_path / ".vitepress"
    vitepress.mkdir()
    (vitepress / "README.md").write_text("# Site runtime\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "documentation-manifest.json").write_text(
        json.dumps({
            "documentation": {
                "module_roots": ["scripts", ".vitepress"],
            },
        }),
        encoding="utf-8",
    )

    result = scan_documentation(tmp_path)

    assert result.module_doc_coverage == 1.0
