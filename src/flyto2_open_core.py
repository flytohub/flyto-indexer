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
        "package_name": manifest.get("package_name", "flyto2-community"),
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
        exported_packages.append({
            "name": spec["name"],
            "repo": spec["repo"],
            "file_count": _count_files(target),
            "generated_files": generated,
            "path": _posix(target.relative_to(out)),
        })

    export_manifest = {
        "schema": "flyto.open-core-export.v1",
        "generated_at": audit["generated_at"],
        "source_workspace": str(workspace),
        "source_manifest": str(options.manifest_path.resolve()),
        "package_name": audit["package_name"],
        "packages": exported_packages,
        "closed_source_boundaries": audit["closed_source_boundaries"],
        "merge_contracts": audit["merge_contracts"],
    }
    (out / "OPEN_CORE_MANIFEST.json").write_text(
        json.dumps(export_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out / "README.md").write_text(format_open_core_export(export_manifest), encoding="utf-8")
    return {**audit, "exported": True, "output_dir": str(out), "exported_packages": exported_packages}


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
    lines.extend(["", "## Kept Closed"])
    for boundary in manifest.get("closed_source_boundaries", []):
        lines.append(f"- {boundary}")
    return "\n".join(lines) + "\n"
