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
