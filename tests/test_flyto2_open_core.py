import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.flyto2_open_core import OpenCoreOptions, audit_open_core, export_open_core


def _repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    return repo


def _write(root: Path, path: str, text: str = "ok\n") -> None:
    file_path = root / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding="utf-8")


def _manifest(path: Path, *, include_enterprise: bool = False, denied_content: bool = False) -> None:
    include = ["LICENSE", "src/public/**"]
    if include_enterprise:
        include.append("src/enterprise/**")
    if denied_content:
        include.append("src/secrets/**")
    exclude = [] if include_enterprise else ["src/enterprise/**"]
    path.write_text(
        json.dumps(
            {
                "schema": "flyto.open-core-manifest.v1",
                "package_name": "flyto2-community-test",
                "global_exclude": [".git/**", "**/__pycache__/**"],
                "deny_content_patterns": ["FLYTO_RUNNER_SECRET\\s*=\\s*[^\\s$<]+"],
                "closed_source_boundaries": ["enterprise control plane"],
                "merge_contracts": ["source first, export second"],
                "packages": [
                    {
                        "name": "community-core",
                        "repo": "flyto-core",
                        "kind": "runtime-sdk",
                        "license": "Apache-2.0",
                        "merge_contract": "test",
                        "must_exist": ["src/public"],
                        "include": include,
                        "exclude": exclude,
                        "protected_paths": ["src/enterprise/**"],
                        "deny_path_patterns": ["src/enterprise/**"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _workspace(tmp_path: Path) -> Path:
    repo = _repo(tmp_path, "flyto-core")
    _write(repo, "LICENSE", "Apache-2.0\n")
    _write(repo, "src/public/runtime.py", "def run():\n    return True\n")
    _write(repo, "src/enterprise/billing.py", "def bill():\n    return True\n")
    denied_marker = "FLYTO_RUNNER_" + "SECRET=real-secret-value\n"
    _write(repo, "src/secrets/config.py", denied_marker)
    return tmp_path


def test_open_core_audit_passes_and_reports_protected_paths(tmp_path):
    workspace = _workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)

    result = audit_open_core(OpenCoreOptions(workspace=workspace, manifest_path=manifest))

    assert result["ok"] is True
    assert result["packages"][0]["file_count"] == 2
    assert result["packages"][0]["protected_path_count"] == 1
    assert result["blockers"] == []


def test_open_core_export_copies_only_whitelisted_files(tmp_path):
    workspace = _workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "out" / "community"
    _manifest(manifest)

    result = export_open_core(
        OpenCoreOptions(workspace=workspace, manifest_path=manifest, output_dir=output)
    )

    assert result["ok"] is True
    assert result["exported"] is True
    assert (output / "OPEN_CORE_MANIFEST.json").exists()
    assert (output / "packages/community-core/LICENSE").exists()
    assert (output / "packages/community-core/src/public/runtime.py").exists()
    assert not (output / "packages/community-core/src/enterprise/billing.py").exists()


def test_open_core_audit_blocks_protected_path_inclusion(tmp_path):
    workspace = _workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    _manifest(manifest, include_enterprise=True)

    result = audit_open_core(OpenCoreOptions(workspace=workspace, manifest_path=manifest))

    assert result["ok"] is False
    assert any(item["code"] == "protected_path_included" for item in result["blockers"])


def test_open_core_audit_blocks_denied_content(tmp_path):
    workspace = _workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    _manifest(manifest, denied_content=True)

    result = audit_open_core(OpenCoreOptions(workspace=workspace, manifest_path=manifest))

    assert result["ok"] is False
    assert any(item["code"] == "denied_content_included" for item in result["blockers"])


def test_open_core_export_requires_empty_output_dir(tmp_path):
    workspace = _workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "out"
    output.mkdir()
    _write(output, "existing.txt", "do not overwrite\n")
    _manifest(manifest)

    with pytest.raises(FileExistsError):
        export_open_core(OpenCoreOptions(workspace=workspace, manifest_path=manifest, output_dir=output))
