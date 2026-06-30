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


def _engine_contract_workspace(tmp_path: Path) -> Path:
    repo = _repo(tmp_path, "flyto-engine")
    _write(repo, "LICENSE", "Apache-2.0\n")
    _write(repo, "SECURITY.md", "# Security\n")
    _write(repo, "CONTRIBUTING.md", "# Contributing\n")
    _write(repo, "api/openapi.yaml", "openapi: 3.0.3\ninfo:\n  title: Flyto\n  version: 1.0.0\n")
    _write(repo, "docs/project-capabilities.md", "# Project Capabilities\n")
    _write(repo, "internal/permission/capabilities.yaml", "modules:\n  code:\n    enabled: true\n")
    _write(repo, "internal/store/private.go", "package store\n")
    return tmp_path


def _contract_manifest(path: Path, *, internal_target: bool = False) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "flyto.open-core-manifest.v1",
                "package_name": "flyto2-community-test",
                "global_exclude": [".git/**"],
                "deny_content_patterns": [],
                "closed_source_boundaries": ["private engine runtime"],
                "merge_contracts": ["source first, export second"],
                "packages": [
                    {
                        "name": "flyto-contracts",
                        "repo": "flyto-engine",
                        "kind": "protocol-contracts",
                        "license": "Apache-2.0",
                        "merge_contract": "protocol",
                        "must_exist": [
                            "api/openapi.yaml",
                            "docs/project-capabilities.md",
                            "internal/permission/capabilities.yaml",
                        ],
                        "include": [
                            "LICENSE",
                            "SECURITY.md",
                            "CONTRIBUTING.md",
                            "api/openapi.yaml",
                            "docs/project-capabilities.md",
                            "internal/permission/capabilities.yaml",
                        ],
                        "copy_as": [
                            {"from": "LICENSE", "to": "LICENSE"},
                            {"from": "api/openapi.yaml", "to": "openapi/flyto-engine.openapi.yaml"},
                            {
                                "from": "internal/permission/capabilities.yaml",
                                "to": (
                                    "internal/permission/capabilities.yaml"
                                    if internal_target
                                    else "capabilities/capabilities.yaml"
                                ),
                            },
                        ],
                        "generate": ["flyto-contracts-protocol"],
                        "exclude": ["internal/store/**"],
                        "protected_paths": ["internal/**"],
                        "deny_path_patterns": ["internal/store/**"],
                        "deny_export_path_patterns": ["internal/**"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_contract_package_exports_protocol_artifacts_not_raw_internal(tmp_path):
    workspace = _engine_contract_workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "out"
    _contract_manifest(manifest)

    result = export_open_core(
        OpenCoreOptions(workspace=workspace, manifest_path=manifest, output_dir=output)
    )

    package = output / "packages/flyto-contracts"
    assert result["ok"] is True
    assert result["exported"] is True
    assert (package / "openapi/flyto-engine.openapi.yaml").exists()
    assert (package / "capabilities/capabilities.yaml").exists()
    assert not (package / "internal/permission/capabilities.yaml").exists()
    assert not (package / "internal/store/private.go").exists()
    assert (package / "schemas/evidence-event.schema.json").exists()
    assert (package / "schemas/runner-callback.schema.json").exists()
    assert (package / "examples/runner-callback.json").exists()
    assert (package / "conformance/validate.py").exists()
    assert (package / "sdk/typescript/src/index.ts").exists()
    assert (package / "sdk/python/flyto_contracts/__init__.py").exists()
    assert (package / "sdk/go/contracts/doc.go").exists()


def test_contract_package_blocks_private_export_target(tmp_path):
    workspace = _engine_contract_workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    _contract_manifest(manifest, internal_target=True)

    result = audit_open_core(OpenCoreOptions(workspace=workspace, manifest_path=manifest))

    assert result["ok"] is False
    assert any(item["code"] == "protected_export_path_included" for item in result["blockers"])
