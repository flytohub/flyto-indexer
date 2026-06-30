"""Flyto2 open-core split auditor and exporter.

The goal is a repeatable OSS boundary, not a one-off copy. The manifest says
which source paths may leave the private workspace, which paths are protected,
and which content markers fail closed if they appear in the exported tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import json
from pathlib import Path
import re
import shutil
from typing import Any, Iterable


def _default_manifest_path() -> Path:
    package_dir = Path(__file__).resolve().parent
    candidates = [
        package_dir.parent / "config" / "flyto2" / "open-core-manifest.json",
        package_dir / "config" / "flyto2" / "open-core-manifest.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_OPEN_CORE_MANIFEST = _default_manifest_path()


@dataclass(frozen=True)
class OpenCoreOptions:
    workspace: Path
    manifest_path: Path = DEFAULT_OPEN_CORE_MANIFEST
    output_dir: Path | None = None


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _repo_path(workspace: Path, repo_name: str) -> Path:
    return workspace / repo_name


def _iter_pattern(repo: Path, pattern: str) -> list[Path]:
    if pattern.endswith("/**"):
        base = repo / pattern[:-3].rstrip("/")
        if base.exists() and base.is_dir():
            return sorted(p for p in base.rglob("*") if p.is_file())
    exact = repo / pattern
    if exact.exists():
        if exact.is_file():
            return [exact]
        if exact.is_dir():
            return sorted(p for p in exact.rglob("*") if p.is_file())
    return sorted(p for p in repo.glob(pattern) if p.is_file())


def _collect_files(repo: Path, include: list[str], exclude: list[str]) -> tuple[list[str], list[str]]:
    files: dict[str, Path] = {}
    missing: list[str] = []
    for pattern in include:
        matched = _iter_pattern(repo, pattern)
        if not matched:
            missing.append(pattern)
            continue
        for path in matched:
            rel = _posix(path.relative_to(repo))
            if not _matches(rel, exclude):
                files[rel] = path
    return sorted(files), missing


def _existing_matches(repo: Path, patterns: list[str], global_exclude: list[str]) -> list[str]:
    matches: set[str] = set()
    for pattern in patterns:
        for path in _iter_pattern(repo, pattern):
            rel = _posix(path.relative_to(repo))
            if not _matches(rel, global_exclude):
                matches.add(rel)
    return sorted(matches)


def _read_text_if_safe(path: Path) -> str | None:
    if path.stat().st_size > 2_000_000:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _content_violations(repo: Path, files: list[str], patterns: list[str]) -> list[dict[str, str]]:
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    findings: list[dict[str, str]] = []
    for rel in files:
        text = _read_text_if_safe(repo / rel)
        if text is None:
            continue
        for pattern, regex in zip(patterns, compiled):
            if regex.search(text):
                findings.append({"file": rel, "pattern": pattern})
    return findings


def _safe_relative(rel: str) -> bool:
    path = Path(rel)
    return bool(rel) and not path.is_absolute() and ".." not in path.parts


def _copy_as_entries(spec: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for entry in spec.get("copy_as", []):
        if isinstance(entry, dict):
            entries.append({
                "from": str(entry.get("from", "")),
                "to": str(entry.get("to", "")),
            })
    return entries


def _copy_as_sources(spec: dict[str, Any]) -> list[str]:
    return [entry["from"] for entry in _copy_as_entries(spec) if entry.get("from")]


def _copy_as_targets(spec: dict[str, Any]) -> list[str]:
    return [entry["to"] for entry in _copy_as_entries(spec) if entry.get("to")]


def _count_files(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file())


def audit_open_core(options: OpenCoreOptions) -> dict[str, Any]:
    manifest = _load_json(options.manifest_path)
    workspace = options.workspace.resolve()
    global_exclude = list(manifest.get("global_exclude", []))
    global_deny_content = list(manifest.get("deny_content_patterns", []))
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    packages: list[dict[str, Any]] = []

    for spec in manifest.get("packages", []):
        repo_name = spec["repo"]
        repo = _repo_path(workspace, repo_name)
        package_name = spec["name"]
        report: dict[str, Any] = {
            "name": package_name,
            "repo": repo_name,
            "kind": spec.get("kind", "source"),
            "license": spec.get("license", ""),
            "merge_contract": spec.get("merge_contract", ""),
            "present": repo.exists(),
            "file_count": 0,
            "protected_path_count": 0,
            "missing_required": [],
            "missing_include_patterns": [],
            "blocked_paths": [],
            "blocked_export_paths": [],
            "missing_copy_sources": [],
            "invalid_export_targets": [],
            "content_violations": [],
        }
        if not repo.exists():
            blockers.append({
                "package": package_name,
                "repo": repo_name,
                "code": "repo_missing",
                "message": "Open-core package repo is missing from the workspace.",
            })
            packages.append(report)
            continue

        exclude = global_exclude + list(spec.get("exclude", []))
        files, missing_patterns = _collect_files(repo, list(spec.get("include", [])), exclude)
        protected = _existing_matches(repo, list(spec.get("protected_paths", [])), global_exclude)
        deny_paths = list(spec.get("deny_path_patterns", []))
        blocked_paths = [rel for rel in files if _matches(rel, deny_paths)]
        deny_export_paths = list(spec.get("deny_export_path_patterns", []))
        invalid_export_targets = [
            target for target in _copy_as_targets(spec) if not _safe_relative(target)
        ]
        blocked_export_paths = [
            target for target in _copy_as_targets(spec)
            if _safe_relative(target) and _matches(target, deny_export_paths)
        ]
        missing_copy_sources = [
            source for source in _copy_as_sources(spec)
            if not _safe_relative(source) or not (repo / source).is_file()
        ]
        required = list(spec.get("must_exist", []))
        missing_required = [path for path in required if not (repo / path).exists()]
        content_patterns = global_deny_content + list(spec.get("deny_content_patterns", []))
        scan_files = sorted(set(files + [source for source in _copy_as_sources(spec) if (repo / source).is_file()]))
        content_findings = _content_violations(repo, scan_files, content_patterns)

        report.update({
            "file_count": len(files),
            "protected_path_count": len(protected),
            "missing_required": missing_required,
            "missing_include_patterns": missing_patterns,
            "blocked_paths": blocked_paths,
            "blocked_export_paths": blocked_export_paths,
            "missing_copy_sources": missing_copy_sources,
            "invalid_export_targets": invalid_export_targets,
            "content_violations": content_findings,
            "sample_files": files[:10],
            "sample_export_paths": _copy_as_targets(spec)[:10],
            "sample_protected_paths": protected[:10],
        })
        if missing_required:
            blockers.append({
                "package": package_name,
                "repo": repo_name,
                "code": "required_path_missing",
                "message": "A required source or contract path is missing.",
                "paths": missing_required,
            })
        if missing_patterns:
            blockers.append({
                "package": package_name,
                "repo": repo_name,
                "code": "include_pattern_empty",
                "message": "An include pattern matched no files.",
                "patterns": missing_patterns,
            })
        if blocked_paths:
            blockers.append({
                "package": package_name,
                "repo": repo_name,
                "code": "protected_path_included",
                "message": "Protected files would be exported.",
                "paths": blocked_paths[:20],
            })
        if blocked_export_paths:
            blockers.append({
                "package": package_name,
                "repo": repo_name,
                "code": "protected_export_path_included",
                "message": "Mapped export targets would recreate protected private paths.",
                "paths": blocked_export_paths[:20],
            })
        if invalid_export_targets:
            blockers.append({
                "package": package_name,
                "repo": repo_name,
                "code": "invalid_export_target",
                "message": "Mapped export targets must be relative paths inside the package.",
                "paths": invalid_export_targets[:20],
            })
        if missing_copy_sources:
            blockers.append({
                "package": package_name,
                "repo": repo_name,
                "code": "copy_source_missing",
                "message": "A mapped export source is missing or not a regular file.",
                "paths": missing_copy_sources[:20],
            })
        if content_findings:
            blockers.append({
                "package": package_name,
                "repo": repo_name,
                "code": "denied_content_included",
                "message": "Exported files contain a denied secret/provider marker.",
                "findings": content_findings[:20],
            })
        if len(protected) == 0 and spec.get("protected_paths"):
            warnings.append({
                "package": package_name,
                "repo": repo_name,
                "code": "protected_path_pattern_empty",
                "message": "Protected path patterns matched no current files; review the manifest if the repo moved code.",
            })
        packages.append(report)

    ok = not blockers
    return {
        "ok": ok,
        "schema": manifest.get("schema", "flyto.open-core-manifest.v1"),
        "workspace": str(workspace),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": manifest.get("strategy", {}),
        "package_name": manifest.get("package_name", "flyto2-warroom-ce"),
        "release": manifest.get("release", {}),
        "packages": packages,
        "blockers": blockers,
        "warnings": warnings,
        "merge_contracts": manifest.get("merge_contracts", []),
        "closed_source_boundaries": manifest.get("closed_source_boundaries", []),
    }


def _copy_package(repo: Path, files: list[str], target: Path) -> None:
    for rel in files:
        src = repo / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _copy_package_mapped(repo: Path, entries: list[dict[str, str]], target: Path) -> None:
    for entry in entries:
        src_rel = entry["from"]
        dst_rel = entry["to"]
        src = repo / src_rel
        dst = target / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _object_schema(schema_id: str, title: str, required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        "title": title,
        "type": "object",
        "required": required,
        "additionalProperties": True,
        "properties": properties,
    }


def _write_flyto_contracts_protocol(target: Path) -> list[str]:
    written: list[str] = []

    def write_text(rel: str, text: str) -> None:
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(rel)

    def write_json(rel: str, payload: dict[str, Any]) -> None:
        _write_json(target / rel, payload)
        written.append(rel)

    write_text(
        "README.md",
        """# Flyto Contracts

This package is the public protocol surface for Flyto integrations.

It is generated from the private Flyto engine source by `flyto2-open-core-export`.
It intentionally does not expose engine runtime, handlers, billing, tenant store,
cloud connector implementation, threat-intel datasets, or live remediation
orchestration.

## Contents

- `openapi/flyto-engine.openapi.yaml`: public REST API shape.
- `capabilities/capabilities.yaml`: public capability catalog source.
- `schemas/`: JSON Schemas for extension-facing payloads.
- `examples/`: minimal scanner, runner callback, and evidence examples.
- `conformance/`: zero-dependency validation helper for integration authors.
- `sdk/`: lightweight type stubs for client and connector authors.

## Merge Rule

Change the private Flyto source first, rerun the exporter, and review the
generated community delta. Generated copies should not be edited directly.
""",
    )
    write_json(
        "schemas/capability.schema.json",
        _object_schema(
            "https://schemas.flyto.dev/capability.v1.json",
            "Flyto capability contract",
            ["id", "surface", "actions"],
            {
                "id": {"type": "string"},
                "surface": {"type": "string"},
                "enabled": {"type": "boolean"},
                "actions": {"type": "array", "items": {"type": "string"}},
                "commercial": {"type": "object"},
                "dependencies": {"type": "array", "items": {"type": "string"}},
            },
        ),
    )
    write_json(
        "schemas/scanner-manifest.schema.json",
        _object_schema(
            "https://schemas.flyto.dev/scanner-manifest.v1.json",
            "Flyto scanner manifest",
            ["id", "name", "surfaces", "evidence_contracts"],
            {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "version": {"type": "string"},
                "surfaces": {"type": "array", "items": {"type": "string"}},
                "capabilities": {"type": "array", "items": {"type": "string"}},
                "evidence_contracts": {"type": "array", "items": {"type": "string"}},
                "runner": {"type": "object"},
            },
        ),
    )
    write_json(
        "schemas/evidence-event.schema.json",
        _object_schema(
            "https://schemas.flyto.dev/evidence-event.v1.json",
            "Flyto evidence event",
            ["event_id", "org_id", "surface", "source", "artifacts"],
            {
                "event_id": {"type": "string"},
                "org_id": {"type": "string"},
                "project_id": {"type": "string"},
                "surface": {"type": "string"},
                "source": {"type": "string"},
                "severity": {"type": "string"},
                "artifacts": {"type": "array", "items": {"type": "object"}},
                "signature": {"type": "object"},
            },
        ),
    )
    write_json(
        "schemas/runner-callback.schema.json",
        _object_schema(
            "https://schemas.flyto.dev/runner-callback.v1.json",
            "Flyto runner callback",
            ["run_id", "scanner_id", "status", "artifacts"],
            {
                "run_id": {"type": "string"},
                "scanner_id": {"type": "string"},
                "status": {"type": "string", "enum": ["queued", "running", "succeeded", "failed", "canceled"]},
                "started_at": {"type": "string"},
                "finished_at": {"type": "string"},
                "artifacts": {"type": "array", "items": {"type": "object"}},
                "signature": {"type": "object"},
            },
        ),
    )
    write_json(
        "schemas/product-verification-scenario.schema.json",
        _object_schema(
            "https://schemas.flyto.dev/product-verification-scenario.v1.json",
            "Flyto product verification scenario",
            ["scenario_id", "checks"],
            {
                "scenario_id": {"type": "string"},
                "target": {"type": "object"},
                "checks": {"type": "array", "items": {"type": "object"}},
                "evidence_requirements": {"type": "array", "items": {"type": "string"}},
            },
        ),
    )
    write_json(
        "schemas/audit-event.schema.json",
        _object_schema(
            "https://schemas.flyto.dev/audit-event.v1.json",
            "Flyto audit event",
            ["event_id", "actor", "action", "resource", "occurred_at"],
            {
                "event_id": {"type": "string"},
                "actor": {"type": "object"},
                "action": {"type": "string"},
                "resource": {"type": "object"},
                "occurred_at": {"type": "string"},
                "metadata": {"type": "object"},
            },
        ),
    )
    write_text(
        "examples/scanner-manifest.yaml",
        """id: community.example_scanner
name: Community Example Scanner
version: 0.1.0
surfaces:
  - code
  - container
capabilities:
  - code.scan
  - evidence.write
evidence_contracts:
  - flyto.evidence_event.v1
runner:
  mode: callback
  callback_schema: schemas/runner-callback.schema.json
""",
    )
    write_json(
        "examples/runner-callback.json",
        {
            "run_id": "run_example_001",
            "scanner_id": "community.example_scanner",
            "status": "succeeded",
            "started_at": "2026-06-30T00:00:00Z",
            "finished_at": "2026-06-30T00:01:00Z",
            "artifacts": [{"kind": "evidence_event", "path": "examples/evidence-event.json"}],
            "signature": {"alg": "ed25519", "value": "example-signature-placeholder"},
        },
    )
    write_json(
        "examples/evidence-event.json",
        {
            "event_id": "evt_example_001",
            "org_id": "org_example",
            "project_id": "project_example",
            "surface": "code",
            "source": "community.example_scanner",
            "severity": "medium",
            "artifacts": [{"kind": "json", "path": "examples/evidence-event.json"}],
            "signature": {"alg": "ed25519", "value": "example-signature-placeholder"},
        },
    )
    write_text(
        "conformance/README.md",
        """# Conformance

`validate.py` is intentionally zero-dependency. It verifies the required top-level
fields for the public JSON examples and integration payloads. Full JSON Schema
validation can be layered on by downstream SDKs.

```sh
python conformance/validate.py runner-callback examples/runner-callback.json
python conformance/validate.py evidence-event examples/evidence-event.json
```
""",
    )
    write_text(
        "conformance/validate.py",
        '''#!/usr/bin/env python3
import json
import sys
from pathlib import Path

REQUIRED = {
    "runner-callback": ["run_id", "scanner_id", "status", "artifacts"],
    "evidence-event": ["event_id", "org_id", "surface", "source", "artifacts"],
    "audit-event": ["event_id", "actor", "action", "resource", "occurred_at"],
}


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in REQUIRED:
        print("usage: validate.py <runner-callback|evidence-event|audit-event> <file.json>", file=sys.stderr)
        return 2
    payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    missing = [field for field in REQUIRED[sys.argv[1]] if field not in payload]
    if missing:
        print("missing required fields: " + ", ".join(missing), file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    )
    write_text(
        "sdk/typescript/src/index.ts",
        """export type FlytoRunnerStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";

export interface FlytoArtifactRef {
  kind: string;
  path?: string;
  uri?: string;
  digest?: string;
}

export interface FlytoRunnerCallback {
  run_id: string;
  scanner_id: string;
  status: FlytoRunnerStatus;
  artifacts: FlytoArtifactRef[];
  started_at?: string;
  finished_at?: string;
  signature?: Record<string, unknown>;
}

export interface FlytoEvidenceEvent {
  event_id: string;
  org_id: string;
  project_id?: string;
  surface: string;
  source: string;
  severity?: string;
  artifacts: FlytoArtifactRef[];
  signature?: Record<string, unknown>;
}
""",
    )
    write_text(
        "sdk/python/flyto_contracts/__init__.py",
        '''from typing import Any, Literal, TypedDict

FlytoRunnerStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]


class FlytoArtifactRef(TypedDict, total=False):
    kind: str
    path: str
    uri: str
    digest: str


class FlytoRunnerCallback(TypedDict, total=False):
    run_id: str
    scanner_id: str
    status: FlytoRunnerStatus
    artifacts: list[FlytoArtifactRef]
    started_at: str
    finished_at: str
    signature: dict[str, Any]


class FlytoEvidenceEvent(TypedDict, total=False):
    event_id: str
    org_id: str
    project_id: str
    surface: str
    source: str
    severity: str
    artifacts: list[FlytoArtifactRef]
    signature: dict[str, Any]
''',
    )
    write_text(
        "sdk/go/contracts/doc.go",
        """// Package contracts contains lightweight public Flyto protocol types.
package contracts

type ArtifactRef struct {
\tKind   string `json:"kind"`
\tPath   string `json:"path,omitempty"`
\tURI    string `json:"uri,omitempty"`
\tDigest string `json:"digest,omitempty"`
}

type RunnerCallback struct {
\tRunID      string        `json:"run_id"`
\tScannerID  string        `json:"scanner_id"`
\tStatus     string        `json:"status"`
\tArtifacts  []ArtifactRef `json:"artifacts"`
\tStartedAt  string        `json:"started_at,omitempty"`
\tFinishedAt string        `json:"finished_at,omitempty"`
}

type EvidenceEvent struct {
\tEventID   string        `json:"event_id"`
\tOrgID     string        `json:"org_id"`
\tProjectID string        `json:"project_id,omitempty"`
\tSurface   string        `json:"surface"`
\tSource    string        `json:"source"`
\tSeverity  string        `json:"severity,omitempty"`
\tArtifacts []ArtifactRef `json:"artifacts"`
}
""",
    )
    return written


def _write_flyto_code_public_metadata(target: Path) -> list[str]:
    written: list[str] = []
    local_dev_email = "aa0909286667" + "@gmail.com"
    local_dev_uid = "g3KyCLkH7" + "IZwXILPXHS3fbo4VnB2"

    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        cleaned = body.replace(local_dev_email, "local-admin@example.invalid")
        cleaned = cleaned.replace(local_dev_uid, "local-admin")
        if cleaned != body:
            path.write_text(cleaned, encoding="utf-8")
            written.append(_posix(path.relative_to(target)))

    package_json = target / "package.json"
    if package_json.exists():
        payload = json.loads(package_json.read_text(encoding="utf-8"))
        payload["license"] = "Apache-2.0"
        payload["private"] = True
        payload.setdefault("dependencies", {})["@flyto/design-tokens"] = (
            "file:./vendor/@flyto/design-tokens"
        )
        package_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written.append("package.json")

    package_lock = target / "package-lock.json"
    vendor_package = target / "vendor/@flyto/design-tokens/package.json"
    if package_lock.exists() and vendor_package.exists():
        lock = json.loads(package_lock.read_text(encoding="utf-8"))
        vendor_payload = json.loads(vendor_package.read_text(encoding="utf-8"))
        packages = lock.setdefault("packages", {})
        root_package = packages.setdefault("", {})
        root_package.setdefault("dependencies", {})["@flyto/design-tokens"] = (
            "file:./vendor/@flyto/design-tokens"
        )
        packages.pop("../flyto-design-tokens", None)
        packages["vendor/@flyto/design-tokens"] = {
            "name": vendor_payload.get("name", "@flyto2/design-tokens"),
            "version": vendor_payload.get("version", "0.1.0"),
            "license": vendor_payload.get("license", "Apache-2.0"),
        }
        packages["node_modules/@flyto/design-tokens"] = {
            "resolved": "vendor/@flyto/design-tokens",
            "link": True,
        }
        package_lock.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        written.append("package-lock.json")

    def write_text(rel: str, text: str) -> None:
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(rel)

    write_text(
        "LICENSE",
        """Apache License
Version 2.0, January 2004
https://www.apache.org/licenses/

Flyto2 Warroom CE frontend source is published under Apache-2.0 as part of the
generated open-core distribution. See the root `LICENSES.md` file for package
license boundaries.
""",
    )
    write_text(
        ".env.example",
        """# Flyto2 Warroom CE frontend local config.

VITE_ENGINE_URL=http://localhost:8080
VITE_AUTH_MODE=local_jwt

# Optional OAuth/Firebase values when running a custom private auth setup.
VITE_GITHUB_CLIENT_ID=your_github_client_id
VITE_GITLAB_CLIENT_ID=your_gitlab_client_id
VITE_GITLAB_BASE_URL=https://gitlab.com
VITE_FIREBASE_API_KEY=your_firebase_api_key
VITE_FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=your-project-id

VITE_AUTOMATION_URL=http://localhost:8080
VITE_CORTEX_URL=http://localhost:8080
""",
    )
    write_text(
        "OPEN_CORE.md",
        """# Flyto Code In Flyto2 Warroom CE

This frontend package is copied from the private `flyto-code` source tree by
`flyto2-open-core-export`.

Contribution rule:

- Change this package in public PRs when the fix is frontend-specific.
- Maintainers import accepted public changes back into `/Users/chester/flytohub/flyto-code`.
- After source tests pass, maintainers rerun the open-core exporter and update
  `flyto-warroom` from the generated output.

Do not add credentials, hosted-only configuration, private image coordinates, or
enterprise-only implementation details to this package.
""",
    )
    return written


def _release_images(manifest: dict[str, Any]) -> dict[str, str]:
    release = manifest.get("release", {})
    images = release.get("public_images", {})
    return {
        "engine": images.get("engine", "docker.io/flytohub/flyto2-warroom-engine-ce"),
        "worker": images.get("worker", "docker.io/flytohub/flyto2-warroom-worker-ce"),
        "frontend": images.get("frontend", "docker.io/flytohub/flyto2-warroom-code-ce"),
        "runner": images.get("runner", "docker.io/flytohub/flyto2-warroom-runner-ce"),
        "verification": images.get(
            "verification",
            "docker.io/flytohub/flyto2-warroom-verification-ce",
        ),
        "brand_vision": images.get(
            "brand_vision",
            "docker.io/flytohub/flyto2-warroom-brand-vision-ce",
        ),
        "pdf": images.get("pdf", "docker.io/flytohub/flyto2-warroom-pdf-ce"),
    }


def _public_release_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    release = dict(manifest.get("release", {}))
    release.pop("private_images", None)
    return release


def _write_warroom_release(target: Path, manifest: dict[str, Any]) -> list[str]:
    written: list[str] = []
    images = _release_images(manifest)

    def write_text(rel: str, text: str) -> None:
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(rel)

    write_text(
        "install/docker-compose.ce.yml",
        f"""name: flyto2-warroom-ce

services:
  postgres:
    image: postgres:17-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: "${{POSTGRES_USER:-flyto}}"
      POSTGRES_PASSWORD: "${{POSTGRES_PASSWORD:-change-me-local-only}}" # placeholder
      POSTGRES_DB: "${{POSTGRES_DB:-flyto}}"
    ports:
      - "127.0.0.1:${{FLYTO_POSTGRES_PORT:-5432}}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${{POSTGRES_USER:-flyto}}"]
      interval: 5s
      timeout: 3s
      retries: 60

  engine:
    image: "${{FLYTO_WARROOM_ENGINE_IMAGE:-{images['engine']}}}:${{FLYTO_WARROOM_TAG:-ce-local}}"
    restart: unless-stopped
    ports:
      - "127.0.0.1:${{FLYTO_ENGINE_PORT:-8080}}:8080"
    environment:
      FLYTO_EDITION: "community"
      FLYTO_AUTH_MODE: "local_jwt"
      FLYTO_ENV: "${{FLYTO_ENV:-development}}"
      FLYTO_LOCAL_AUTH_JWT_SECRET: "${{FLYTO_LOCAL_AUTH_JWT_SECRET:?set FLYTO_LOCAL_AUTH_JWT_SECRET in install/.env}}" # placeholder
      FLYTO_LOCAL_AUTH_EMAIL: "${{FLYTO_LOCAL_AUTH_EMAIL:?set FLYTO_LOCAL_AUTH_EMAIL in install/.env}}"
      FLYTO_LOCAL_AUTH_PASSWORD_SHA256: "${{FLYTO_LOCAL_AUTH_PASSWORD_SHA256:?set FLYTO_LOCAL_AUTH_PASSWORD_SHA256 in install/.env}}" # placeholder
      FLYTO_LOCAL_AUTH_DISPLAY_NAME: "${{FLYTO_LOCAL_AUTH_DISPLAY_NAME:-Local Admin}}"
      FLYTO_LOCAL_AUTH_USER_ID: "${{FLYTO_LOCAL_AUTH_USER_ID:-local-admin}}"
      FLYTO_LOCAL_AUTH_ORG_ID: "${{FLYTO_LOCAL_AUTH_ORG_ID:-local-warroom}}"
      FLYTO_LOCAL_AUTH_ORG_NAME: "${{FLYTO_LOCAL_AUTH_ORG_NAME:-Flyto2 Warroom}}"
      FLYTO_LOCAL_AUTH_ORG_SLUG: "${{FLYTO_LOCAL_AUTH_ORG_SLUG:-flyto2-warroom}}"
      FLYTO_CORS_ORIGINS: "${{FLYTO_CORS_ORIGINS:-http://localhost:8088,http://127.0.0.1:8088,http://localhost:5173,http://127.0.0.1:5173}}"
      FLYTO_PG_URL: "postgres://${{POSTGRES_USER:-flyto}}:${{POSTGRES_PASSWORD:-change-me-local-only}}@postgres:5432/${{POSTGRES_DB:-flyto}}?sslmode=disable" # placeholder
      FLYTO_SCAN_DRAINER: "${{FLYTO_SCAN_DRAINER:-0}}"
      FLYTO_POST_SCAN_HOOK_CONCURRENCY: "${{FLYTO_POST_SCAN_HOOK_CONCURRENCY:-1}}"
      FLYTO_RUNNER_URL: "http://runner:8090"
      FLYTO_VERIFICATION_URL: "http://verification:8344"
      FLYTO_VERIFICATION_CALLBACK_URL: "http://engine:8080/api/v1/code/runner/executions/callback"
      FLYTO_PDF_URL: "http://pdf:3000"
      FLYTO_BRAND_VISION_URL: "http://brand-vision:8095"
      FLYTO_RUNNER_SECRET: "${{FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env}}" # placeholder
      FLYTO_VERIFICATION_SECRET: "${{FLYTO_VERIFICATION_SECRET:?set FLYTO_VERIFICATION_SECRET in install/.env}}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"
      FLYTO_MASTER_KEY: "${{FLYTO_MASTER_KEY:-}}"
      FLYTO_MASTER_KEY_ID: "${{FLYTO_MASTER_KEY_ID:-local-ce}}"
      FLYTO_PLATFORM_ADMIN_UIDS: "${{FLYTO_PLATFORM_ADMIN_UIDS:-}}"
      OPENAI_API_KEY: "${{OPENAI_API_KEY:-}}"
      GOOGLE_PAGESPEED_KEY: "${{GOOGLE_PAGESPEED_KEY:-}}"
      FLYTO_GITHUB_TOKEN: "${{FLYTO_GITHUB_TOKEN:-}}"
      FLYTO_ABUSECH_AUTH_KEY: "${{FLYTO_ABUSECH_AUTH_KEY:-}}"
      HIBP_API_KEY: "${{HIBP_API_KEY:-}}"
      ABUSEIPDB_API_KEY: "${{ABUSEIPDB_API_KEY:-}}"
      FLYTO_VT_API_KEY: "${{FLYTO_VT_API_KEY:-}}"
      VIRUSTOTAL_API_KEY: "${{VIRUSTOTAL_API_KEY:-}}"
      FLYTO_URLSCAN_API_KEY: "${{FLYTO_URLSCAN_API_KEY:-}}"
      SHODAN_API_KEY: "${{SHODAN_API_KEY:-}}"
      FLYTO_IPINFO_TOKEN: "${{FLYTO_IPINFO_TOKEN:-}}"
    volumes:
      - trivy-cache:/var/cache/trivy
      - blobs:/app/data/blobs
    depends_on:
      postgres:
        condition: service_healthy
      runner:
        condition: service_healthy
      verification:
        condition: service_started
      brand-vision:
        condition: service_healthy
    healthcheck:
      test:
        - CMD-SHELL
        - python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=2)"
      interval: 10s
      timeout: 3s
      retries: 30

  scan-drainer:
    image: "${{FLYTO_WARROOM_ENGINE_IMAGE:-{images['engine']}}}:${{FLYTO_WARROOM_TAG:-ce-local}}"
    restart: unless-stopped
    environment:
      FLYTO_SERVER_MODE: "scan-drain-loop"
      FLYTO_EDITION: "community"
      FLYTO_ENV: "${{FLYTO_ENV:-development}}"
      FLYTO_PG_URL: "postgres://${{POSTGRES_USER:-flyto}}:${{POSTGRES_PASSWORD:-change-me-local-only}}@postgres:5432/${{POSTGRES_DB:-flyto}}?sslmode=disable" # placeholder
      FLYTO_RUNNER_URL: "http://runner:8090"
      FLYTO_PDF_URL: "http://pdf:3000"
      FLYTO_BRAND_VISION_URL: "http://brand-vision:8095"
      FLYTO_RUNNER_SECRET: "${{FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env}}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"
      FLYTO_MASTER_KEY: "${{FLYTO_MASTER_KEY:-}}"
      FLYTO_MASTER_KEY_ID: "${{FLYTO_MASTER_KEY_ID:-local-ce}}"
      FLYTO_GITHUB_TOKEN: "${{FLYTO_GITHUB_TOKEN:-}}"
      OPENAI_API_KEY: "${{OPENAI_API_KEY:-}}"
    volumes:
      - trivy-cache:/var/cache/trivy
      - blobs:/app/data/blobs
    depends_on:
      postgres:
        condition: service_healthy
      runner:
        condition: service_healthy

  discovery-drainer:
    image: "${{FLYTO_WARROOM_ENGINE_IMAGE:-{images['engine']}}}:${{FLYTO_WARROOM_TAG:-ce-local}}"
    restart: unless-stopped
    environment:
      FLYTO_SERVER_MODE: "discovery-drain-loop"
      FLYTO_EDITION: "community"
      FLYTO_ENV: "${{FLYTO_ENV:-development}}"
      FLYTO_PG_URL: "postgres://${{POSTGRES_USER:-flyto}}:${{POSTGRES_PASSWORD:-change-me-local-only}}@postgres:5432/${{POSTGRES_DB:-flyto}}?sslmode=disable" # placeholder
      FLYTO_RUNNER_URL: "http://runner:8090"
      FLYTO_PDF_URL: "http://pdf:3000"
      FLYTO_BRAND_VISION_URL: "http://brand-vision:8095"
      FLYTO_RUNNER_SECRET: "${{FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env}}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"
      FLYTO_DISCOVERY_DRAINER_MAX_CONCURRENCY: "${{FLYTO_DISCOVERY_DRAINER_MAX_CONCURRENCY:-1}}"
      FLYTO_DISCOVERY_DRAINER_MIN_FREE_MEMORY_MB: "${{FLYTO_DISCOVERY_DRAINER_MIN_FREE_MEMORY_MB:-1024}}"
      FLYTO_MASTER_KEY: "${{FLYTO_MASTER_KEY:-}}"
      FLYTO_MASTER_KEY_ID: "${{FLYTO_MASTER_KEY_ID:-local-ce}}"
      FLYTO_GITHUB_TOKEN: "${{FLYTO_GITHUB_TOKEN:-}}"
      OPENAI_API_KEY: "${{OPENAI_API_KEY:-}}"
    volumes:
      - trivy-cache:/var/cache/trivy
      - blobs:/app/data/blobs
    depends_on:
      postgres:
        condition: service_healthy
      runner:
        condition: service_healthy

  worker:
    image: "${{FLYTO_WARROOM_WORKER_IMAGE:-{images['worker']}}}:${{FLYTO_WARROOM_TAG:-ce-local}}"
    restart: unless-stopped
    entrypoint: ["/app/worker"]
    environment:
      FLYTO_EDITION: "community"
      FLYTO_ENV: "${{FLYTO_ENV:-development}}"
      FLYTO_PG_URL: "postgres://${{POSTGRES_USER:-flyto}}:${{POSTGRES_PASSWORD:-change-me-local-only}}@postgres:5432/${{POSTGRES_DB:-flyto}}?sslmode=disable" # placeholder
      FLYTO_WORKER_MODE: "${{FLYTO_WORKER_MODE:-queue-only}}"
      FLYTO_RUNNER_URL: "http://runner:8090"
      FLYTO_VERIFICATION_URL: "http://verification:8344"
      FLYTO_RUNNER_SECRET: "${{FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env}}" # placeholder
      FLYTO_VERIFICATION_SECRET: "${{FLYTO_VERIFICATION_SECRET:?set FLYTO_VERIFICATION_SECRET in install/.env}}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"
      FLYTO_MASTER_KEY: "${{FLYTO_MASTER_KEY:-}}"
      FLYTO_MASTER_KEY_ID: "${{FLYTO_MASTER_KEY_ID:-local-ce}}"
      FLYTO_INDEX_BIN: "${{FLYTO_INDEX_BIN:-flyto-index}}"
      OPENAI_API_KEY: "${{OPENAI_API_KEY:-}}"
    volumes:
      - trivy-cache:/var/cache/trivy
      - blobs:/app/data/blobs
    depends_on:
      postgres:
        condition: service_healthy
      runner:
        condition: service_healthy

  runner:
    image: "${{FLYTO_WARROOM_RUNNER_IMAGE:-{images['runner']}}}:${{FLYTO_WARROOM_TAG:-ce-local}}"
    restart: unless-stopped
    ports:
      - "127.0.0.1:${{FLYTO_RUNNER_PORT:-8090}}:8090"
    environment:
      PORT: "8090"
      OPENAI_API_KEY: "${{OPENAI_API_KEY:-}}"
      FLYTO_RUNNER_SECRET: "${{FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env}}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"
      FLYTO_ENGINE_COST_URL: "http://engine:8080/api/v1/code/runner/cost-events"
      FLYTO_ENGINE_CALLBACK_URL: "http://engine:8080/api/v1/code/runner/executions/callback"
      FLYTO_AI_ALLOW_PROD_TARGETS: "${{FLYTO_AI_ALLOW_PROD_TARGETS:-0}}"
      FLYTO_ALLOW_PRIVATE_NETWORK: "${{FLYTO_ALLOW_PRIVATE_NETWORK:-false}}"
      FLYTO_ALLOWED_HOSTS: "${{FLYTO_ALLOWED_HOSTS:-host.docker.internal,localhost,127.0.0.1}}"
    healthcheck:
      test:
        - CMD-SHELL
        - python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health', timeout=2)"
      interval: 10s
      timeout: 3s
      retries: 30

  verification:
    image: "${{FLYTO_WARROOM_VERIFICATION_IMAGE:-{images['verification']}}}:${{FLYTO_WARROOM_TAG:-ce-local}}"
    restart: unless-stopped
    ports:
      - "127.0.0.1:${{FLYTO_VERIFICATION_PORT:-8344}}:8344"
    environment:
      FLYTO_ENGINE_URL: "http://engine:8080"
      FLYTO_RUNNER_SECRET: "${{FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env}}" # placeholder
      FLYTO_VERIFICATION_SECRET: "${{FLYTO_VERIFICATION_SECRET:?set FLYTO_VERIFICATION_SECRET in install/.env}}" # placeholder
      FLYTO_ALLOWED_HOSTS: "${{FLYTO_ALLOWED_HOSTS:-host.docker.internal,localhost,127.0.0.1}}"
      FLYTO_ALLOW_PRIVATE_NETWORK: "${{FLYTO_ALLOW_PRIVATE_NETWORK:-true}}"

  brand-vision:
    image: "${{FLYTO_WARROOM_BRAND_VISION_IMAGE:-{images['brand_vision']}}}:${{FLYTO_WARROOM_TAG:-ce-local}}"
    restart: unless-stopped
    ports:
      - "127.0.0.1:${{FLYTO_BRAND_VISION_PORT:-8095}}:8095"
    environment:
      FLYTO_BRAND_VISION_API_KEY: "${{FLYTO_BRAND_VISION_API_KEY:-}}"
    healthcheck:
      test:
        - CMD-SHELL
        - python -c "import urllib.request; urllib.request.urlopen('http://localhost:8095/health', timeout=2)"
      interval: 10s
      timeout: 3s
      retries: 15

  pdf:
    image: "${{FLYTO_WARROOM_PDF_IMAGE:-{images['pdf']}}}:${{FLYTO_WARROOM_TAG:-ce-local}}"
    restart: unless-stopped
    environment:
      PORT: "3000"
      PDF_TIMEOUT_MS: "${{PDF_TIMEOUT_MS:-30000}}"

  frontend:
    image: "${{FLYTO_WARROOM_FRONTEND_IMAGE:-{images['frontend']}}}:${{FLYTO_WARROOM_TAG:-ce-local}}"
    restart: unless-stopped
    ports:
      - "127.0.0.1:${{FLYTO_CODE_PORT:-8088}}:80"
    depends_on:
      engine:
        condition: service_healthy

volumes:
  pgdata:
  trivy-cache:
  blobs:
""",
    )

    write_text(
        "install/docker-compose.ee-sim.yml",
        """services:
  engine:
    environment:
      FLYTO_EDITION: "enterprise_airgap"
      FLYTO_AUTH_MODE: "enterprise"
      FLYTO_DEV_AUTH: "0"
      FLYTO_ENTERPRISE_JWT_SECRET_KEY: "${FLYTO_ENTERPRISE_JWT_SECRET_KEY:?set FLYTO_ENTERPRISE_JWT_SECRET_KEY in install/.env.ee-sim}" # placeholder
      FLYTO_RUNNER_SECRET: "${FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env.ee-sim}" # placeholder
      FLYTO_VERIFICATION_SECRET: "${FLYTO_VERIFICATION_SECRET:?set FLYTO_VERIFICATION_SECRET in install/.env.ee-sim}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"
      FLYTO_MASTER_KEY: "${FLYTO_MASTER_KEY:?set FLYTO_MASTER_KEY in install/.env.ee-sim}"
      FLYTO_MASTER_KEY_ID: "${FLYTO_MASTER_KEY_ID:-local-ee-sim}"
      FLYTO_AI_BASE_URL: "${FLYTO_AI_BASE_URL:-http://host.docker.internal:11434/v1}"

  scan-drainer:
    environment:
      FLYTO_EDITION: "enterprise_airgap"
      FLYTO_AUTH_MODE: "enterprise"
      FLYTO_RUNNER_SECRET: "${FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env.ee-sim}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"
      FLYTO_MASTER_KEY: "${FLYTO_MASTER_KEY:?set FLYTO_MASTER_KEY in install/.env.ee-sim}"
      FLYTO_MASTER_KEY_ID: "${FLYTO_MASTER_KEY_ID:-local-ee-sim}"

  discovery-drainer:
    environment:
      FLYTO_EDITION: "enterprise_airgap"
      FLYTO_AUTH_MODE: "enterprise"
      FLYTO_RUNNER_SECRET: "${FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env.ee-sim}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"
      FLYTO_MASTER_KEY: "${FLYTO_MASTER_KEY:?set FLYTO_MASTER_KEY in install/.env.ee-sim}"
      FLYTO_MASTER_KEY_ID: "${FLYTO_MASTER_KEY_ID:-local-ee-sim}"

  worker:
    environment:
      FLYTO_EDITION: "enterprise_airgap"
      FLYTO_AUTH_MODE: "enterprise"
      FLYTO_RUNNER_SECRET: "${FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env.ee-sim}" # placeholder
      FLYTO_VERIFICATION_SECRET: "${FLYTO_VERIFICATION_SECRET:?set FLYTO_VERIFICATION_SECRET in install/.env.ee-sim}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"
      FLYTO_MASTER_KEY: "${FLYTO_MASTER_KEY:?set FLYTO_MASTER_KEY in install/.env.ee-sim}"
      FLYTO_MASTER_KEY_ID: "${FLYTO_MASTER_KEY_ID:-local-ee-sim}"

  runner:
    environment:
      FLYTO_RUNNER_SECRET: "${FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env.ee-sim}" # placeholder
      FLYTO_RUNNER_DEV_OPEN: "0"

  verification:
    environment:
      FLYTO_RUNNER_SECRET: "${FLYTO_RUNNER_SECRET:?set FLYTO_RUNNER_SECRET in install/.env.ee-sim}" # placeholder
      FLYTO_VERIFICATION_SECRET: "${FLYTO_VERIFICATION_SECRET:?set FLYTO_VERIFICATION_SECRET in install/.env.ee-sim}" # placeholder
""",
    )

    write_text(
        "install/.env.ce.example",
        """# Flyto2 Warroom CE local install.
# Copy this file to install/.env and keep the copy out of git.

FLYTO_WARROOM_TAG=ce-local
POSTGRES_USER=flyto
POSTGRES_PASSWORD=change-me-local-only
POSTGRES_DB=flyto
FLYTO_ENV=development
FLYTO_ENGINE_PORT=8080
FLYTO_CODE_PORT=8088
FLYTO_LOCAL_AUTH_EMAIL=local-admin@example.invalid
FLYTO_LOCAL_AUTH_PASSWORD_SHA256=
FLYTO_LOCAL_AUTH_JWT_SECRET=
FLYTO_LOCAL_AUTH_DISPLAY_NAME=Local Admin
FLYTO_LOCAL_AUTH_USER_ID=local-admin
FLYTO_LOCAL_AUTH_ORG_ID=local-warroom
FLYTO_LOCAL_AUTH_ORG_NAME=Flyto2 Warroom
FLYTO_LOCAL_AUTH_ORG_SLUG=flyto2-warroom
FLYTO_PLATFORM_ADMIN_UIDS=
FLYTO_RUNNER_SECRET=
FLYTO_VERIFICATION_SECRET=
FLYTO_MASTER_KEY=
FLYTO_MASTER_KEY_ID=local-ce
OPENAI_API_KEY=
GOOGLE_PAGESPEED_KEY=
FLYTO_GITHUB_TOKEN=
""",
    )
    write_text(
        "install/.env.ee-sim.example",
        """# Flyto2 Warroom enterprise simulation.
# Copy this file to install/.env.ee-sim and fill local-only secrets there.

FLYTO_WARROOM_TAG=ee-sim-local
POSTGRES_USER=flyto
POSTGRES_PASSWORD=change-me-local-only
POSTGRES_DB=flyto
FLYTO_ENV=development
FLYTO_ENTERPRISE_JWT_SECRET_KEY=
FLYTO_RUNNER_SECRET=
FLYTO_VERIFICATION_SECRET=
FLYTO_MASTER_KEY=
FLYTO_MASTER_KEY_ID=local-ee-sim
FLYTO_AI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_API_KEY=
""",
    )
    write_text(
        "Makefile",
        """SHELL := /bin/sh
ENV_CE ?= install/.env
ENV_EE_SIM ?= install/.env.ee-sim
DOCKER_COMPOSE ?= $(shell if docker compose version >/dev/null 2>&1; then printf 'docker compose'; elif command -v docker-compose >/dev/null 2>&1; then printf 'docker-compose'; else printf 'docker compose'; fi)
COMPOSE_CE = $(DOCKER_COMPOSE) --env-file $(ENV_CE) -f install/docker-compose.ce.yml
COMPOSE_EE_SIM = $(DOCKER_COMPOSE) --env-file $(ENV_EE_SIM) -f install/docker-compose.ce.yml -f install/docker-compose.ee-sim.yml

.PHONY: ce-up ce-down ce-logs ce-ps ce-reset-db ee-sim-up ee-sim-down ee-sim-logs audit build-local-images

ce-up:
\t$(COMPOSE_CE) up -d

ce-down:
\t$(COMPOSE_CE) down

ce-logs:
\t$(COMPOSE_CE) logs -f --tail=200

ce-ps:
\t$(COMPOSE_CE) ps

ce-reset-db:
\t$(COMPOSE_CE) down
\tdocker volume rm flyto2-warroom-ce_pgdata || true

ee-sim-up:
\t$(COMPOSE_EE_SIM) up -d

ee-sim-down:
\t$(COMPOSE_EE_SIM) down

ee-sim-logs:
\t$(COMPOSE_EE_SIM) logs -f --tail=200

audit:
\tpython3 install/scripts/audit-release-tree.py .

build-local-images:
\tsh install/scripts/build-local-images.sh /Users/chester/flytohub
""",
    )
    write_text(
        "install/scripts/build-local-images.sh",
        f"""#!/bin/sh
set -eu

WORKSPACE="${{1:-/Users/chester/flytohub}}"
TAG="${{FLYTO_WARROOM_TAG:-ce-local}}"
ENGINE_IMAGE="${{FLYTO_WARROOM_ENGINE_IMAGE:-{images['engine']}}}"
WORKER_IMAGE="${{FLYTO_WARROOM_WORKER_IMAGE:-{images['worker']}}}"
FRONTEND_IMAGE="${{FLYTO_WARROOM_FRONTEND_IMAGE:-{images['frontend']}}}"
RUNNER_IMAGE="${{FLYTO_WARROOM_RUNNER_IMAGE:-{images['runner']}}}"
VERIFICATION_IMAGE="${{FLYTO_WARROOM_VERIFICATION_IMAGE:-{images['verification']}}}"
BRAND_VISION_IMAGE="${{FLYTO_WARROOM_BRAND_VISION_IMAGE:-{images['brand_vision']}}}"
PDF_IMAGE="${{FLYTO_WARROOM_PDF_IMAGE:-{images['pdf']}}}"

docker build -t "$ENGINE_IMAGE:$TAG" "$WORKSPACE/flyto-engine"
docker tag "$ENGINE_IMAGE:$TAG" "$WORKER_IMAGE:$TAG"
docker build -t "$RUNNER_IMAGE:$TAG" "$WORKSPACE/flyto-engine/runner"
docker build -f "$WORKSPACE/flyto-core/Dockerfile.verification" -t "$VERIFICATION_IMAGE:$TAG" "$WORKSPACE/flyto-core"
docker build -t "$BRAND_VISION_IMAGE:$TAG" "$WORKSPACE/flyto-engine/brand-vision"
docker build -t "$PDF_IMAGE:$TAG" "$WORKSPACE/flyto-engine/pdf-service"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
CODE_CTX="$TMP_ROOT/flyto-code"
mkdir -p "$CODE_CTX"
tar -C "$WORKSPACE/flyto-code" -cf - . | tar -C "$CODE_CTX" -xf -
rm -rf "$CODE_CTX/flyto-design-tokens-pkg"
if [ -d "$WORKSPACE/flyto-design-tokens" ]; then
  cp -R "$WORKSPACE/flyto-design-tokens" "$CODE_CTX/flyto-design-tokens-pkg"
else
  echo "missing $WORKSPACE/flyto-design-tokens" >&2
  exit 1
fi
mkdir -p "$CODE_CTX/public/i18n/code"
if [ -d "$WORKSPACE/flyto-i18n/dist/code" ]; then
  cp -R "$WORKSPACE/flyto-i18n/dist/code/." "$CODE_CTX/public/i18n/code/"
fi
python3 - "$CODE_CTX/package.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
for section in ("dependencies", "devDependencies"):
    deps = payload.get(section, {{}})
    for name, value in list(deps.items()):
        if value == "file:../flyto-design-tokens":
            deps[name] = "file:./flyto-design-tokens-pkg"
path.write_text(json.dumps(payload, indent=2) + "\\n", encoding="utf-8")
PY
npm install --package-lock-only --ignore-scripts --legacy-peer-deps --prefix "$CODE_CTX"
docker build \\
  --build-arg VITE_ENGINE_URL="${{FLYTO_CODE_ENGINE_URL:-http://localhost:8080}}" \\
  --build-arg VITE_AUTH_MODE="${{FLYTO_CODE_AUTH_MODE:-local_jwt}}" \\
  --build-arg VITE_AUTOMATION_URL="${{FLYTO_AUTOMATION_URL:-http://localhost:8080}}" \\
  --build-arg VITE_CORTEX_URL="${{FLYTO_CORTEX_URL:-http://localhost:8080}}" \\
  -t "$FRONTEND_IMAGE:$TAG" \\
  "$CODE_CTX"
""",
    )
    write_text(
        "install/scripts/hash-local-password.py",
        '''#!/usr/bin/env python3
import getpass
import hashlib
import sys


def main() -> int:
    password = getpass.getpass("Local admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("passwords do not match", file=sys.stderr)
        return 2
    if len(password) < 12:
        print("password must be at least 12 characters", file=sys.stderr)
        return 2
    print(hashlib.sha256(password.encode("utf-8")).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    )
    write_text(
        "install/scripts/mint-ee-sim-jwt.py",
        '''#!/usr/bin/env python3
import argparse
import base64
import hashlib
import hmac
import json
import os
import time


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mint a local Flyto2 Warroom enterprise-sim JWT.")
    parser.add_argument("--secret", default=os.environ.get("FLYTO_ENTERPRISE_JWT_SECRET_KEY", ""))
    parser.add_argument("--sub", default="local-admin")
    parser.add_argument("--email", default="local-admin@example.invalid")
    parser.add_argument("--name", default="Local Admin")
    parser.add_argument("--ttl-seconds", type=int, default=8 * 60 * 60)
    args = parser.parse_args()
    if len(args.secret) < 32:
        raise SystemExit("secret must be at least 32 characters")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "type": "access",
        "sub": args.sub,
        "email": args.email,
        "name": args.name,
        "iat": now,
        "exp": now + args.ttl_seconds,
    }
    signing_input = ".".join([
        b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ])
    sig = hmac.new(args.secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    print(signing_input + "." + b64url(sig.digest()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    )
    write_text(
        "install/scripts/audit-release-tree.py",
        '''#!/usr/bin/env python3
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

REQUIRED = [
    "OPEN_CORE_MANIFEST.json",
    "Makefile",
    "packages/flyto-contracts/openapi/flyto-engine.openapi.yaml",
    "packages/flyto-contracts/capabilities/capabilities.yaml",
    "packages/flyto-contracts/schemas/evidence-event.schema.json",
    "packages/flyto-code/package.json",
    "packages/flyto-code/src-next/lib/env.ts",
    "packages/flyto-code/.env.example",
    "install/docker-compose.ce.yml",
    "install/docker-compose.ee-sim.yml",
    "install/.env.ce.example",
    "install/.env.ee-sim.example",
    "install/scripts/hash-local-password.py",
    "install/scripts/mint-ee-sim-jwt.py",
    "docs/local-install.md",
    "docs/enterprise-simulation.md",
    "docs/code-protection.md",
]

PRIVATE_GLOBS = [
    "packages/flyto-contracts/internal/**",
    "packages/flyto-contracts/cmd/**",
    "packages/flyto-contracts/api/handlers_*",
    "packages/flyto-code/.env",
    "packages/flyto-code/.env.local",
    "packages/flyto-code/.env.production",
]

LOCAL_ARTIFACT_PARTS = {
    "node_modules",
    "dist",
    "dist-next",
    "reports",
    "test-results",
}

DENIED_ANYWHERE = [
    re.compile(r"FLYTO_RUNNER_SECRET[ \\t]*=[ \\t]*[^\\s$<]+"),
    re.compile(r"FLYTO_VERIFICATION_SECRET[ \\t]*=[ \\t]*[^\\s$<]+"),
    re.compile(r"FLYTO_ENTERPRISE_JWT_SECRET_KEY[ \\t]*=[ \\t]*[^\\s$<]+"),
    re.compile(r"BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY"),
    re.compile(r"ghcr\\.io/.+-ee"),
    re.compile(r"flyto2-warroom-[a-z-]+-ee"),
    re.compile("aa0909286667" + r"@gmail\\.com"),
    re.compile("g3KyCLkH7" + "IZwXILPXHS3fbo4VnB2"),
]

DENIED_CE_COMPOSE = [
    re.compile(r"ghcr\\.io/.+-ee"),
    re.compile(r"enterprise_airgap"),
    re.compile("FLYTO_AUTH_MODE:\\\\s*[\\\"']?(enterprise|enterprise_airgap|firebase)"),
    re.compile("FLYTO_DEV_AUTH:\\\\s*[\\\"']?1"),
    re.compile("FLYTO_RUNNER_DEV_OPEN:\\\\s*[\\\"']?1"),
    re.compile(r"FLYTO_ENTERPRISE_JWT_SECRET_KEY"),
]


def text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def is_local_artifact(path: Path) -> bool:
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return False
    return any(part in LOCAL_ARTIFACT_PARTS for part in rel.parts)


def main() -> int:
    blockers: list[str] = []
    for rel in REQUIRED:
        if not (ROOT / rel).exists():
            blockers.append(f"missing required release file: {rel}")
    for pattern in PRIVATE_GLOBS:
        for match in ROOT.glob(pattern):
            if match.is_file() and not is_local_artifact(match):
                blockers.append(f"private path escaped release tree: {match.relative_to(ROOT)}")
    ce_compose = ROOT / "install/docker-compose.ce.yml"
    if ce_compose.exists():
        ce_text = text(ce_compose)
        for regex in DENIED_CE_COMPOSE:
            if regex.search(ce_text):
                blockers.append(f"CE compose contains denied marker: {regex.pattern}")
        for marker in [
            'FLYTO_EDITION: "community"',
            'FLYTO_AUTH_MODE: "local_jwt"',
            "FLYTO_LOCAL_AUTH_JWT_SECRET",
            "FLYTO_LOCAL_AUTH_PASSWORD_SHA256",
        ]:
            if marker not in ce_text:
                blockers.append(f"CE compose missing required marker: {marker}")
    frontend_env = ROOT / "packages/flyto-code/.env.example"
    if frontend_env.exists():
        frontend_text = text(frontend_env)
        if "VITE_AUTH_MODE=local_jwt" not in frontend_text:
            blockers.append("frontend CE env must default VITE_AUTH_MODE=local_jwt")
        for denied in ("VITE_AUTH_MODE=enterprise", "VITE_AUTH_MODE=firebase"):
            if denied in frontend_text:
                blockers.append(f"frontend CE env contains denied auth mode: {denied}")
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        if is_local_artifact(path):
            continue
        body = text(path)
        for regex in DENIED_ANYWHERE:
            if regex.search(body):
                blockers.append(f"secret-like value in {path.relative_to(ROOT)}: {regex.pattern}")
    if blockers:
        for item in blockers:
            print("BLOCKED: " + item, file=sys.stderr)
        return 2
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    )
    write_text(
        "docs/local-install.md",
        """# Flyto2 Warroom Local Install

This generated tree is the self-hosted Flyto2 Warroom CE delivery shape. It is
safe to publish because it contains whitelisted packages, public contracts, and
installer files only.

## Build Local Images From The Private Workspace

Maintainers with the private workspace can build the local images:

```sh
python -m src.cli flyto2-open-core-export /Users/chester/flytohub --output /tmp/flyto2-warroom-ce
sh /tmp/flyto2-warroom-ce/install/scripts/build-local-images.sh /Users/chester/flytohub
```

The script builds engine, worker, runner, verification, brand-vision, pdf, and
frontend images with the `ce-local` tag. Public users would pull the same image
names from Docker Hub after the release pipeline publishes them.

## Start CE Locally

```sh
cp /tmp/flyto2-warroom-ce/install/.env.ce.example /tmp/flyto2-warroom-ce/install/.env
python3 /tmp/flyto2-warroom-ce/install/scripts/hash-local-password.py
openssl rand -base64 48
openssl rand -base64 48
openssl rand -base64 48
openssl rand -base64 48
```

Paste the generated values into `install/.env`:

- first `openssl` output -> `FLYTO_LOCAL_AUTH_JWT_SECRET`
- second `openssl` output -> `FLYTO_RUNNER_SECRET`
- third `openssl` output -> `FLYTO_VERIFICATION_SECRET`
- fourth `openssl` output -> `FLYTO_MASTER_KEY`
- password hash output -> `FLYTO_LOCAL_AUTH_PASSWORD_SHA256`

Then start the stack:

```sh
make -C /tmp/flyto2-warroom-ce ce-up
```

Open:

- Frontend: `http://localhost:8088`
- Engine health: `http://localhost:8080/health`

Sign in with the `FLYTO_LOCAL_AUTH_EMAIL` value and the password used by
`hash-local-password.py`. CE uses engine-issued local JWTs; it does not require
Firebase and it does not use dev auth.

## Reset The Database

```sh
make -C /tmp/flyto2-warroom-ce ce-reset-db
```

This removes only the generated compose stack's `pgdata` volume.

## Audit The Release Tree

```sh
make -C /tmp/flyto2-warroom-ce audit
```
""",
    )
    write_text(
        "docs/enterprise-simulation.md",
        """# Flyto2 Warroom Enterprise Simulation

Enterprise simulation runs the same local stack with fail-closed enterprise
gates enabled: `enterprise_airgap` edition, enterprise JWT auth, internal runner
secrets, and sealed master-key requirements.

## Configure Local-Only Secrets

```sh
cp /tmp/flyto2-warroom-ce/install/.env.ee-sim.example /tmp/flyto2-warroom-ce/install/.env.ee-sim
```

Fill the blank values in `install/.env.ee-sim`. Use local-only values and do not
commit that file.

Required values:

- `FLYTO_ENTERPRISE_JWT_SECRET_KEY` must be at least 32 characters.
- `FLYTO_RUNNER_SECRET` enables signed engine-to-runner calls.
- `FLYTO_VERIFICATION_SECRET` enables signed verification callbacks.
- `FLYTO_MASTER_KEY` enables sealed runtime credentials.

## Start EE Simulation

```sh
make -C /tmp/flyto2-warroom-ce ee-sim-up
```

## Mint A Browser Token

```sh
export FLYTO_ENTERPRISE_JWT_SECRET_KEY=<same-local-secret>
TOKEN="$(python3 /tmp/flyto2-warroom-ce/install/scripts/mint-ee-sim-jwt.py)"
```

Paste this in the browser console on `http://localhost:8088`, then refresh:

```js
sessionStorage.setItem("jwt_access_token", JSON.stringify("<paste-token-here>"))
```

The engine verifies the HS256 token and rejects expired, unsigned, wrong-type,
or wrong-secret tokens.

## What This Simulates

- Enterprise auth boundary without Firebase.
- Airgap edition/capability gates.
- Runner and verification internal secret gates.
- Local AI-compatible endpoint wiring without making AI a gate authority.
- One database-backed stack for code, container, cloud/runtime, CTEM, evidence,
  reports, scheduler ledger, and audit surfaces.

## What This Does Not Pretend

This does not publish enterprise source code. Enterprise implementations remain
private images and private source; CE receives protocol contracts and install
composition only.
""",
    )
    write_text(
        "docs/code-protection.md",
        """# Flyto2 Warroom Code Protection

The open-core release protects private code by construction:

- Release content is generated from an allowlist.
- Private `cmd/**`, Go `internal/**`, private handlers, billing, tenant store,
  connector credentials, hosted control plane, commercial threat intel, and live
  remediation orchestration are not copied.
- `flyto-contracts` exposes OpenAPI, capabilities, schemas, examples, and SDK
  stubs instead of raw engine source.
- CE compose only references public image coordinates or maintainer-overridden
  local CE image names.
- EE simulation is an override file. It can enable enterprise gates locally, but
  it does not include enterprise implementation source.

Run this before publishing:

```sh
python3 install/scripts/audit-release-tree.py .
```

The audit fails if private engine paths escape, CE compose references EE image
coordinates, or generated files contain secret-like values.

This is technical containment, not a substitute for license, trademark, image
signing, SBOM, and release provenance. A production release should publish signed
images and attach the generated `OPEN_CORE_MANIFEST.json` as evidence.
""",
    )
    write_text(
        ".gitignore",
        """.DS_Store
.env
.env.*
!install/.env.ce.example
!install/.env.ee-sim.example
packages/flyto-code/node_modules/
packages/flyto-code/dist/
packages/flyto-code/dist-next/
packages/flyto-code/test-results/
packages/flyto-code/reports/
upstream-patches/
""",
    )
    write_text(
        "LICENSES.md",
        """# Licenses

Flyto2 Warroom CE is an aggregate open-core distribution generated from the
Flyto workspace.

- `packages/flyto-core`: Apache-2.0
- `packages/flyto-indexer`: Apache-2.0
- `packages/flyto-i18n`: MIT
- `packages/flyto-code`: Apache-2.0
- `packages/flyto-contracts`: Apache-2.0
- Root installer, workflow, and documentation files generated by
  `flyto2-open-core-export`: Apache-2.0

Each package keeps its own `LICENSE` file. If a package-level license conflicts
with this summary, the package-level license controls that package.
""",
    )
    write_text(
        "CONTRIBUTING.md",
        """# Contributing To Flyto2 Warroom CE

Flyto2 Warroom CE is a generated open-core mirror, not a permanent fork. The
source of truth remains the Flyto workspace; this public repository exists so
users can install CE, inspect public contracts, and send patches.

## Single Source Rule

Do not maintain long-lived changes only in this public tree. Maintainers import
accepted public changes back into the source repositories, rerun
`flyto2-open-core-export`, and update this repo from the generated output.

## Path Ownership

- `packages/flyto-core/**` maps back to `flyto-core`.
- `packages/flyto-indexer/**` maps back to `flyto-indexer`.
- `packages/flyto-i18n/**` maps back to `flyto-i18n`.
- `packages/flyto-code/**` maps back to `flyto-code`.
- `packages/flyto-contracts/openapi/flyto-engine.openapi.yaml` maps back to
  `flyto-engine/api/openapi.yaml`.
- `packages/flyto-contracts/capabilities/capabilities.yaml` maps back to
  `flyto-engine/internal/permission/capabilities.yaml`.
- `install/**`, root docs, and generated workflow files map back to the
  `flyto-indexer` exporter implementation.

## PR Expectations

- Keep changes scoped to one product problem.
- Include tests or conformance evidence when changing code, contracts, or
  installer behavior.
- Do not commit credentials, customer data, private image coordinates, or
  enterprise-only implementation details.
- Run `python install/scripts/audit-release-tree.py .` before opening a PR.

Maintainers can run:

```sh
python scripts/export-upstream-patches.py --base origin/main --output upstream-patches
```

The generated patch bundle is then applied to the private source repositories,
reviewed there, and re-exported.
""",
    )
    write_text(
        "docs/upstream-feedback-loop.md",
        """# Upstream Feedback Loop

This repository is designed to accept community PRs without becoming a separate
product line.

## Maintainer Flow

1. Review the public PR in `flyto-warroom`.
2. Generate source-repo patches from the public diff:

   ```sh
   python scripts/export-upstream-patches.py --base origin/main --output upstream-patches
   ```

3. Apply package patches to the private workspace:

   ```sh
   git -C /Users/chester/flytohub/flyto-core apply /path/to/upstream-patches/flyto-core.patch
   git -C /Users/chester/flytohub/flyto-indexer apply /path/to/upstream-patches/flyto-indexer.patch
   git -C /Users/chester/flytohub/flyto-i18n apply /path/to/upstream-patches/flyto-i18n.patch
   ```

4. For generated-only changes listed in `REVIEW_GENERATED.md`, change the
   source generator or private engine contract first.
5. Run source-repo tests.
6. Re-export CE:

   ```sh
   python -m src.cli flyto2-open-core-export /Users/chester/flytohub --output /tmp/flyto-warroom
   python /tmp/flyto-warroom/install/scripts/audit-release-tree.py /tmp/flyto-warroom
   ```

7. Push the regenerated public tree to this repo.

## Why This Exists

Community changes should improve Flyto itself, not only the public mirror. The
patch exporter gives maintainers a repeatable bridge from public contribution to
private source-of-truth, while release audits prevent private code or credentials
from flowing in the other direction.
""",
    )
    write_text(
        "scripts/export-upstream-patches.py",
        '''#!/usr/bin/env python3
"""Export public Flyto Warroom PR changes as source-repo patch bundles."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


PACKAGE_PATCHES = {
    "packages/flyto-core": "flyto-core",
    "packages/flyto-indexer": "flyto-indexer",
    "packages/flyto-i18n": "flyto-i18n",
    "packages/flyto-code": "flyto-code",
}

GENERATED_REVIEW_PREFIXES = (
    "packages/flyto-contracts/",
    "packages/flyto-code/vendor/@flyto/design-tokens/",
    "install/",
    "docs/",
    ".github/",
)

GENERATED_REVIEW_FILES = {
    "README.md",
    "CONTRIBUTING.md",
    "LICENSES.md",
    "OPEN_CORE_MANIFEST.json",
}


def run_git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def strip_package_prefix(patch: str, prefix: str) -> str:
    replacements = {
        f"a/{prefix}/": "a/",
        f"b/{prefix}/": "b/",
        f" {prefix}/": " ",
    }
    out = patch
    for old, new in replacements.items():
        out = out.replace(old, new)
    out = out.replace(f"diff --git a/{prefix}/", "diff --git a/")
    out = out.replace(f" b/{prefix}/", " b/")
    out = out.replace(f"--- a/{prefix}/", "--- a/")
    out = out.replace(f"+++ b/{prefix}/", "+++ b/")
    out = out.replace(f"rename from {prefix}/", "rename from ")
    out = out.replace(f"rename to {prefix}/", "rename to ")
    return out


def changed_files(root: Path, base: str) -> list[str]:
    output = run_git(root, ["diff", "--name-only", base, "--"])
    return [line.strip() for line in output.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Path to the public flyto-warroom repo")
    parser.add_argument("--base", default="origin/main", help="Base ref to diff against")
    parser.add_argument("--output", default="upstream-patches", help="Patch output directory")
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    output_dir = (root / args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for prefix, repo_name in PACKAGE_PATCHES.items():
        patch = run_git(root, ["diff", "--binary", args.base, "--", prefix])
        if not patch.strip():
            continue
        patch_path = output_dir / f"{repo_name}.patch"
        patch_path.write_text(strip_package_prefix(patch, prefix), encoding="utf-8")
        written.append(str(patch_path.relative_to(root)))

    generated = [
        path for path in changed_files(root, args.base)
        if path in GENERATED_REVIEW_FILES
        or any(path.startswith(prefix) for prefix in GENERATED_REVIEW_PREFIXES)
    ]
    if generated:
        review_path = output_dir / "REVIEW_GENERATED.md"
        review_path.write_text(
            "# Generated/Public-Surface Changes Requiring Source Review\\n\\n"
            "These files are generated or contract-facing. Apply the intent to the "
            "private source generator or private contract source, then rerun "
            "`flyto2-open-core-export`.\\n\\n"
            + "\\n".join(f"- `{path}`" for path in generated)
            + "\\n",
            encoding="utf-8",
        )
        written.append(str(review_path.relative_to(root)))

    if not written:
        print("no upstream patches generated")
        return 0
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    )
    write_text(
        ".github/workflows/ci.yml",
        """name: Flyto Warroom CE

on:
  pull_request:
  push:
    branches:
      - main

jobs:
  release-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Audit generated release boundary
        run: python install/scripts/audit-release-tree.py .
      - name: Validate public contracts
        run: |
          python packages/flyto-contracts/conformance/validate.py runner-callback packages/flyto-contracts/examples/runner-callback.json
          python packages/flyto-contracts/conformance/validate.py evidence-event packages/flyto-contracts/examples/evidence-event.json
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: packages/flyto-code/package-lock.json
      - name: Build frontend package
        working-directory: packages/flyto-code
        env:
          VITE_ENGINE_URL: http://localhost:8080
          VITE_AUTH_MODE: local_jwt
          VITE_AUTOMATION_URL: http://localhost:8080
          VITE_CORTEX_URL: http://localhost:8080
        run: |
          npm ci --legacy-peer-deps
          npm run build
      - name: Export upstream patch preview
        if: github.event_name == 'pull_request'
        run: |
          git fetch origin main
          python scripts/export-upstream-patches.py --base origin/main --output upstream-patches
""",
    )
    return written


def _audit_generated_release(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    release = manifest.get("release", {})
    if "warroom-ce-installer" not in list(release.get("generate", [])):
        return {"ok": True, "blockers": [], "checked": False}

    blockers: list[dict[str, Any]] = []
    required = [
        "OPEN_CORE_MANIFEST.json",
        "Makefile",
        "packages/flyto-contracts/openapi/flyto-engine.openapi.yaml",
        "packages/flyto-contracts/capabilities/capabilities.yaml",
        "packages/flyto-contracts/schemas/evidence-event.schema.json",
        "install/docker-compose.ce.yml",
        "install/docker-compose.ee-sim.yml",
        "install/.env.ce.example",
        "install/.env.ee-sim.example",
        "install/scripts/audit-release-tree.py",
        "install/scripts/hash-local-password.py",
        "install/scripts/mint-ee-sim-jwt.py",
        "docs/local-install.md",
        "docs/enterprise-simulation.md",
        "docs/code-protection.md",
    ]
    missing = [rel for rel in required if not (root / rel).exists()]
    if missing:
        blockers.append({
            "code": "release_required_file_missing",
            "message": "Generated Warroom release is missing required files.",
            "paths": missing,
        })

    private_paths: list[str] = []
    for pattern in [
        "packages/flyto-contracts/internal/**",
        "packages/flyto-contracts/cmd/**",
        "packages/flyto-contracts/api/handlers_*",
        "packages/flyto-code/.env",
        "packages/flyto-code/.env.local",
        "packages/flyto-code/.env.production",
    ]:
        private_paths.extend(
            _posix(path.relative_to(root)) for path in root.glob(pattern) if path.is_file()
        )
    if private_paths:
        blockers.append({
            "code": "private_path_escaped_release",
            "message": "Private engine paths escaped into the generated release tree.",
            "paths": sorted(private_paths)[:20],
        })

    ce_compose = root / "install/docker-compose.ce.yml"
    ce_text = _read_text_if_safe(ce_compose) if ce_compose.exists() else ""
    denied_ce = [
        r"ghcr\.io/.+-ee",
        r"enterprise_airgap",
        "FLYTO_AUTH_MODE:\\s*[\"']?(enterprise|enterprise_airgap|firebase)",
        "FLYTO_DEV_AUTH:\\s*[\"']?1",
        "FLYTO_RUNNER_DEV_OPEN:\\s*[\"']?1",
        r"FLYTO_ENTERPRISE_JWT_SECRET_KEY",
    ]
    ce_findings = [pattern for pattern in denied_ce if ce_text and re.search(pattern, ce_text)]
    if ce_findings:
        blockers.append({
            "code": "ce_compose_contains_private_marker",
            "message": "CE compose must not reference enterprise auth or private image coordinates.",
            "patterns": ce_findings,
        })
    if ce_text:
        missing_markers = [
            marker
            for marker in [
                'FLYTO_EDITION: "community"',
                'FLYTO_AUTH_MODE: "local_jwt"',
                "FLYTO_LOCAL_AUTH_JWT_SECRET",
                "FLYTO_LOCAL_AUTH_PASSWORD_SHA256",
            ]
            if marker not in ce_text
        ]
        if missing_markers:
            blockers.append({
                "code": "ce_compose_missing_community_auth_marker",
                "message": "CE compose must default to engine-issued local JWT auth.",
                "markers": missing_markers,
            })

    frontend_env = root / "packages/flyto-code/.env.example"
    frontend_env_text = _read_text_if_safe(frontend_env) if frontend_env.exists() else ""
    if frontend_env_text and "VITE_AUTH_MODE=local_jwt" not in frontend_env_text:
        blockers.append({
            "code": "frontend_env_not_local_jwt",
            "message": "Flyto-code CE env must default to local JWT auth.",
            "path": "packages/flyto-code/.env.example",
        })
    if frontend_env_text:
        denied_frontend_auth = [
            marker
            for marker in ["VITE_AUTH_MODE=enterprise", "VITE_AUTH_MODE=firebase"]
            if marker in frontend_env_text
        ]
        if denied_frontend_auth:
            blockers.append({
                "code": "frontend_env_contains_denied_auth_mode",
                "message": "Flyto-code CE env must not default to private/Firebase auth.",
                "markers": denied_frontend_auth,
            })

    local_dev_email_pattern = "aa0909286667" + r"@gmail\.com"
    local_dev_uid_pattern = "g3KyCLkH7" + "IZwXILPXHS3fbo4VnB2"
    secret_patterns = [
        r"FLYTO_RUNNER_SECRET[ \t]*=[ \t]*[^\s$<]+",
        r"FLYTO_VERIFICATION_SECRET[ \t]*=[ \t]*[^\s$<]+",
        r"FLYTO_ENTERPRISE_JWT_SECRET_KEY[ \t]*=[ \t]*[^\s$<]+",
        r"BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY",
        r"ghcr\.io/.+-ee",
        r"flyto2-warroom-[a-z-]+-ee",
        local_dev_email_pattern,
        local_dev_uid_pattern,
    ]
    content_findings: list[dict[str, str]] = []
    compiled = [re.compile(pattern) for pattern in secret_patterns]
    local_artifact_parts = {"node_modules", "dist", "dist-next", "reports", "test-results"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in local_artifact_parts for part in rel_parts):
            continue
        body = _read_text_if_safe(path)
        if body is None:
            continue
        for pattern, regex in zip(secret_patterns, compiled):
            if regex.search(body):
                content_findings.append({"file": _posix(path.relative_to(root)), "pattern": pattern})
    if content_findings:
        blockers.append({
            "code": "release_secret_like_value",
            "message": "Generated release contains a secret-like value.",
            "findings": content_findings[:20],
        })

    return {"ok": not blockers, "blockers": blockers, "checked": True}


def export_open_core(options: OpenCoreOptions) -> dict[str, Any]:
    if options.output_dir is None:
        raise ValueError("output_dir is required for open-core export")
    audit = audit_open_core(options)
    if not audit["ok"]:
        return {**audit, "exported": False}

    manifest = _load_json(options.manifest_path)
    workspace = options.workspace.resolve()
    out = options.output_dir.resolve()
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"output directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    (out / ".flyto-open-core-generated").write_text(
        "Generated by flyto-indexer flyto2-open-core-export.\n",
        encoding="utf-8",
    )
    package_root = out / "packages"
    package_root.mkdir()

    global_exclude = list(manifest.get("global_exclude", []))
    exported_packages: list[dict[str, Any]] = []
    release_files: list[str] = []
    for spec in manifest.get("packages", []):
        repo = _repo_path(workspace, spec["repo"])
        exclude = global_exclude + list(spec.get("exclude", []))
        files, _missing = _collect_files(repo, list(spec.get("include", [])), exclude)
        target = package_root / spec["name"]
        target.mkdir(parents=True, exist_ok=True)
        generated: list[str] = []
        copy_as = _copy_as_entries(spec)
        if copy_as:
            _copy_package_mapped(repo, copy_as, target)
        else:
            _copy_package(repo, files, target)
        if "flyto-contracts-protocol" in list(spec.get("generate", [])):
            generated = _write_flyto_contracts_protocol(target)
        if "flyto-code-public-metadata" in list(spec.get("generate", [])):
            generated.extend(_write_flyto_code_public_metadata(target))
        exported_packages.append({
            "name": spec["name"],
            "repo": spec["repo"],
            "file_count": _count_files(target),
            "generated_files": generated,
            "path": _posix(target.relative_to(out)),
        })

    release = manifest.get("release", {})
    public_release = _public_release_manifest(manifest)
    if "warroom-ce-installer" in list(release.get("generate", [])):
        release_files = _write_warroom_release(out, manifest)

    export_manifest = {
        "schema": "flyto.open-core-export.v1",
        "generated_at": audit["generated_at"],
        "source_workspace": str(workspace),
        "source_manifest": str(options.manifest_path.resolve()),
        "package_name": audit["package_name"],
        "release": public_release,
        "release_files": release_files,
        "packages": exported_packages,
        "closed_source_boundaries": audit["closed_source_boundaries"],
        "merge_contracts": audit["merge_contracts"],
    }
    (out / "OPEN_CORE_MANIFEST.json").write_text(
        json.dumps(export_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out / "README.md").write_text(format_open_core_export(export_manifest), encoding="utf-8")
    release_audit = _audit_generated_release(out, manifest)
    if not release_audit["ok"]:
        return {
            **audit,
            "ok": False,
            "exported": True,
            "output_dir": str(out),
            "exported_packages": exported_packages,
            "release_files": release_files,
            "release_audit": release_audit,
            "blockers": audit["blockers"] + release_audit["blockers"],
        }
    return {
        **audit,
        "exported": True,
        "output_dir": str(out),
        "exported_packages": exported_packages,
        "release_files": release_files,
        "release_audit": release_audit,
    }


def format_open_core_audit(result: dict[str, Any]) -> str:
    lines = [
        f"# {result['package_name']} open-core audit",
        "",
        f"Verdict: {'PASS' if result['ok'] else 'BLOCKED'}",
        f"Workspace: {result['workspace']}",
        "",
        "## Packages",
    ]
    for package in result["packages"]:
        lines.append(
            f"- {package['name']} ({package['repo']}): {package['file_count']} files, "
            f"{package['protected_path_count']} protected paths kept private"
        )
    lines.extend(["", "## Blockers"])
    if result["blockers"]:
        for item in result["blockers"]:
            lines.append(f"- {item.get('package', 'workspace')}: {item['code']} - {item['message']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Closed Source Boundaries"])
    for item in result.get("closed_source_boundaries", []):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def format_open_core_export(manifest: dict[str, Any]) -> str:
    lines = [
        f"# {manifest['package_name']}",
        "",
        "This tree was generated from the Flyto2 workspace by the deterministic open-core exporter.",
        "Do not edit generated copies directly; change the source repo and rerun the exporter.",
        "",
        "## Packages",
    ]
    for package in manifest["packages"]:
        lines.append(f"- `{package['name']}` from `{package['repo']}`: {package['file_count']} files")
    if manifest.get("release_files"):
        lines.extend([
            "",
            "## Local Install",
            "",
            "- `install/docker-compose.ce.yml`: local CE stack.",
            "- `install/docker-compose.ee-sim.yml`: enterprise simulation override.",
            "- `install/scripts/audit-release-tree.py`: fail-closed release audit.",
            "- `docs/local-install.md`: local startup and reset steps.",
            "- `docs/enterprise-simulation.md`: enterprise JWT simulation steps.",
        ])
    lines.extend(["", "## Kept Closed"])
    for boundary in manifest.get("closed_source_boundaries", []):
        lines.append(f"- {boundary}")
    return "\n".join(lines) + "\n"
