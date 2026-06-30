import json
from pathlib import Path
import subprocess
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
                "deny_content_patterns": ["FLYTO_RUNNER_SECRET[ \\t]*=[ \\t]*[^\\s$<]+"],
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
    code = _repo(tmp_path, "flyto-code")
    _write(
        code,
        "package.json",
        json.dumps({
            "name": "flyto-code",
            "version": "0.0.0",
            "private": True,
            "license": "UNLICENSED",
        }),
    )
    _write(code, "README.md", "# Flyto Code\n")
    _write(code, "SECURITY.md", "# Security\n")
    _write(code, "CONTRIBUTING.md", "# Contributing\n")
    _write(code, "src-next/lib/env.ts", "export const env = { engineUrl: 'http://localhost:8080' }\n")
    _write(code, "public/README.md", "# Public\n")
    _write(code, "vendor/@flyto/design-tokens/package.json", '{"name":"@flyto/design-tokens"}\n')
    _write(code, ".env", "VITE_DEV_AUTH_EMAIL=private@example.test\n")
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
                "release": {
                    "name": "flyto2-warroom-ce-test",
                    "display_name": "Flyto2 Warroom CE Test",
                    "generate": ["warroom-ce-installer"],
                    "public_images": {
                        "engine": "docker.io/flytohub/flyto2-warroom-engine-ce",
                        "worker": "docker.io/flytohub/flyto2-warroom-worker-ce",
                        "frontend": "docker.io/flytohub/flyto2-warroom-code-ce",
                        "runner": "docker.io/flytohub/flyto2-warroom-runner-ce",
                        "verification": "docker.io/flytohub/flyto2-warroom-verification-ce",
                        "brand_vision": "docker.io/flytohub/flyto2-warroom-brand-vision-ce",
                        "pdf": "docker.io/flytohub/flyto2-warroom-pdf-ce",
                    },
                },
                "packages": [
                    {
                        "name": "flyto-code",
                        "repo": "flyto-code",
                        "kind": "warroom-frontend",
                        "license": "Apache-2.0",
                        "merge_contract": "frontend",
                        "must_exist": [
                            "src-next",
                            "public",
                            "package.json",
                            "vendor/@flyto/design-tokens",
                        ],
                        "include": [
                            "README.md",
                            "SECURITY.md",
                            "CONTRIBUTING.md",
                            "package.json",
                            "src-next/**",
                            "public/**",
                            "vendor/**",
                        ],
                        "generate": ["flyto-code-public-metadata"],
                        "exclude": [".env", ".env.*", "dist/**", "node_modules/**"],
                        "protected_paths": [".env", ".env.*", "dist/**", "node_modules/**"],
                        "deny_path_patterns": [".env", ".env.*", "dist/**", "node_modules/**"],
                    },
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


def test_warroom_release_package_includes_local_and_enterprise_simulation(tmp_path):
    workspace = _engine_contract_workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "out"
    _contract_manifest(manifest)

    result = export_open_core(
        OpenCoreOptions(workspace=workspace, manifest_path=manifest, output_dir=output)
    )

    assert result["ok"] is True
    assert result["release_audit"]["ok"] is True
    assert (output / "install/docker-compose.ce.yml").exists()
    assert (output / "install/docker-compose.ee-sim.yml").exists()
    assert (output / "install/.env.ce.example").exists()
    assert (output / "install/.env.ee-sim.example").exists()
    assert (output / "install/scripts/audit-release-tree.py").exists()
    assert (output / "install/scripts/hash-local-password.py").exists()
    assert (output / "install/scripts/mint-ee-sim-jwt.py").exists()
    assert (output / "docs/local-install.md").exists()
    assert (output / "docs/enterprise-simulation.md").exists()
    assert (output / "docs/code-protection.md").exists()
    assert (output / "packages/flyto-code/src-next/lib/env.ts").exists()
    assert (output / "packages/flyto-code/LICENSE").exists()
    assert (output / "packages/flyto-code/.env.example").exists()
    assert not (output / "packages/flyto-code/.env").exists()
    exported_manifest = json.loads((output / "OPEN_CORE_MANIFEST.json").read_text(encoding="utf-8"))
    assert "private_images" not in exported_manifest["release"]
    code_package = json.loads((output / "packages/flyto-code/package.json").read_text(encoding="utf-8"))
    assert code_package["license"] == "Apache-2.0"

    ce_compose = (output / "install/docker-compose.ce.yml").read_text(encoding="utf-8")
    assert 'FLYTO_EDITION: "community"' in ce_compose
    assert 'FLYTO_AUTH_MODE: "local_jwt"' in ce_compose
    assert "FLYTO_LOCAL_AUTH_JWT_SECRET" in ce_compose
    assert "FLYTO_LOCAL_AUTH_PASSWORD_SHA256" in ce_compose
    assert "FLYTO_DEV_AUTH" not in ce_compose
    assert 'FLYTO_RUNNER_DEV_OPEN: "0"' in ce_compose
    assert "ghcr.io" not in ce_compose

    ce_env = (output / "install/.env.ce.example").read_text(encoding="utf-8")
    assert "FLYTO_LOCAL_AUTH_EMAIL=local-admin@example.invalid" in ce_env
    assert "FLYTO_LOCAL_AUTH_PASSWORD_SHA256=\n" in ce_env
    assert "FLYTO_LOCAL_AUTH_JWT_SECRET=\n" in ce_env
    assert "FLYTO_DEV_AUTH" not in ce_env

    frontend_env = (output / "packages/flyto-code/.env.example").read_text(encoding="utf-8")
    assert "VITE_AUTH_MODE=local_jwt" in frontend_env
    assert "VITE_AUTH_MODE=enterprise" not in frontend_env

    ee_compose = (output / "install/docker-compose.ee-sim.yml").read_text(encoding="utf-8")
    assert 'FLYTO_EDITION: "enterprise_airgap"' in ee_compose
    assert 'FLYTO_AUTH_MODE: "enterprise"' in ee_compose
    assert "FLYTO_ENTERPRISE_JWT_SECRET_KEY" in ee_compose

    env_example = (output / "install/.env.ee-sim.example").read_text(encoding="utf-8")
    runner_key = "FLYTO_RUNNER_" + "SECRET="
    verification_key = "FLYTO_VERIFICATION_" + "SECRET="
    enterprise_key = "FLYTO_ENTERPRISE_JWT_" + "SECRET_KEY="
    assert runner_key + "\n" in env_example
    assert verification_key + "\n" in env_example
    assert enterprise_key + "\n" in env_example

    audit = subprocess.run(
        [sys.executable, str(output / "install/scripts/audit-release-tree.py"), str(output)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert audit.returncode == 0, audit.stderr


def test_warroom_enterprise_sim_jwt_helper_mints_access_token(tmp_path):
    workspace = _engine_contract_workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "out"
    _contract_manifest(manifest)
    export_open_core(OpenCoreOptions(workspace=workspace, manifest_path=manifest, output_dir=output))

    token = subprocess.run(
        [
            sys.executable,
            str(output / "install/scripts/mint-ee-sim-jwt.py"),
            "--secret",
            "x" * 32,
            "--sub",
            "local-admin",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert token.returncode == 0, token.stderr
    assert token.stdout.count(".") == 2


def test_contract_package_blocks_private_export_target(tmp_path):
    workspace = _engine_contract_workspace(tmp_path)
    manifest = tmp_path / "manifest.json"
    _contract_manifest(manifest, internal_target=True)

    result = audit_open_core(OpenCoreOptions(workspace=workspace, manifest_path=manifest))

    assert result["ok"] is False
    assert any(item["code"] == "protected_export_path_included" for item in result["blockers"])
