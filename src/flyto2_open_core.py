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
        required = list(spec.get("must_exist", []))
        missing_required = [path for path in required if not (repo / path).exists()]
        content_patterns = global_deny_content + list(spec.get("deny_content_patterns", []))
        content_findings = _content_violations(repo, files, content_patterns)

        report.update({
            "file_count": len(files),
            "protected_path_count": len(protected),
            "missing_required": missing_required,
            "missing_include_patterns": missing_patterns,
            "blocked_paths": blocked_paths,
            "content_violations": content_findings,
            "sample_files": files[:10],
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
        _copy_package(repo, files, target)
        exported_packages.append({
            "name": spec["name"],
            "repo": spec["repo"],
            "file_count": len(files),
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
