"""Tests for per-package import usage (import_counts / import_files).

These maps are the contract flyto-engine relies on (integrations/flyto-engine.md)
to anchor package-level CVE reachability to real source files. They were
previously never produced on the export path, so the engine silently dropped
reachability for every uploaded scan — this test guards against regressing that.
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from profile.import_usage import compute_import_usage


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")


def test_python_imports_counted_and_files_recorded(tmp_path):
    _write(tmp_path, "app.py", """
        import requests
        from flask import Flask
    """)
    _write(tmp_path, "worker.py", """
        import requests
    """)
    counts, files = compute_import_usage(tmp_path)

    assert counts["requests"] == 2  # imported by two files
    assert counts["flask"] == 1
    assert sorted(files["requests"]) == ["app.py", "worker.py"]


def test_js_scoped_and_subpath_normalised(tmp_path):
    _write(tmp_path, "index.ts", """
        import express from 'express';
        import { Router } from 'express/router';
        import x from '@scope/pkg/sub';
        import rel from './local';
    """)
    counts, _ = compute_import_usage(tmp_path)

    assert counts["express"] == 1  # subpath folds into the package, counted once per file
    assert counts["@scope/pkg"] == 1
    assert "./local" not in counts  # relative imports are skipped


def test_go_records_full_path_and_bare_name_skips_stdlib(tmp_path):
    _write(tmp_path, "main.go", """
        package main

        import (
            "context"
            "fmt"
            "github.com/gin-gonic/gin"
        )
    """)
    counts, _ = compute_import_usage(tmp_path)

    assert counts.get("context") is None  # stdlib filtered out
    assert counts.get("fmt") is None
    assert counts["github.com/gin-gonic/gin"] == 1  # full module path
    assert counts["gin"] == 1                        # bare name for manifest match


def test_excluded_dirs_skipped(tmp_path):
    _write(tmp_path, "node_modules/dep/index.js", "import lodash from 'lodash';")
    _write(tmp_path, "src/app.js", "import axios from 'axios';")
    counts, _ = compute_import_usage(tmp_path)

    assert "lodash" not in counts  # under node_modules, skipped
    assert counts["axios"] == 1


def test_exported_profile_includes_import_maps(tmp_path):
    """The full profile must surface import_counts/import_files at the top level."""
    _write(tmp_path, "app.py", "import requests\n")
    from profile.builder import build_project_profile

    profile = build_project_profile(tmp_path)
    assert "import_counts" in profile
    assert "import_files" in profile
    assert profile["import_counts"].get("requests") == 1
    assert profile["import_files"].get("requests") == ["app.py"]
