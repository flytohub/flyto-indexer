"""Tests for documentation scanner scoring edge cases."""

import json

from src.doc_scanner import scan_documentation


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
