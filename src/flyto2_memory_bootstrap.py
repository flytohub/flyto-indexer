"""Bootstrap Flyto2 project-memory files from the product manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

try:
    from .flyto2_product_gate import DEFAULT_MANIFEST
except ImportError:
    from flyto2_product_gate import DEFAULT_MANIFEST


WORKFLOW_TEMPLATES = {
    "idea-capture.md": """# Idea capture workflow

1. Capture the user, product line, and repo surface.
2. Link the idea to `PROJECT.md` and current `tasks.md`.
3. Record accepted follow-up work in `tasks.md`.
""",
    "planning.md": """# Planning workflow

1. Read `PROJECT.md`, `ARCHITECTURE.md`, `STATE.md`, and `DECISIONS.md`.
2. Identify affected product lines and owning repos.
3. Define verification commands before editing.
""",
    "implementation.md": """# Implementation workflow

1. Keep changes scoped to this repo's role.
2. Preserve `flyto-core` boundaries and product-line ownership.
3. Add tests, guards, docs, or handoff notes for release-impacting work.
""",
    "bugfix.md": """# Bugfix workflow

1. Reproduce the issue or identify the failing gate.
2. Fix the narrowest code path.
3. Run impacted checks and update `STATE.md` or handoff notes.
""",
    "refactor.md": """# Refactor workflow

1. Confirm the repo boundary before moving code.
2. Preserve public APIs, package exports, and product-line ownership.
3. Run impacted tests and release gates.
""",
    "investigation.md": """# Investigation workflow

1. Separate confirmed behavior from assumptions.
2. Capture commands, inputs, and evidence.
3. Record unresolved risks in a dated handoff.
""",
    "wrap-up.md": """# Wrap-up workflow

1. Run relevant checks.
2. Update `STATE.md`, `tasks.md`, or a handoff.
3. Check git status before commit and push.
""",
}


@dataclass(frozen=True)
class MemoryBootstrapOptions:
    workspace: Path
    manifest_path: Path = DEFAULT_MANIFEST
    apply: bool = False
    include_deprecated: bool = False


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def product_names(manifest: dict[str, Any], keys: list[str]) -> list[str]:
    lines = manifest.get("product_lines", {})
    return [lines.get(key, {}).get("name", key) for key in keys]


def bullet_list(items: list[str]) -> str:
    if not items:
        return "- unclassified"
    return "\n".join(f"- {item}" for item in items)


def repo_context(manifest: dict[str, Any], repo: str, spec: dict[str, Any]) -> dict[str, Any]:
    products = product_names(manifest, list(spec.get("product_lines", [])))
    return {
        "repo": repo,
        "status": spec.get("status", "unknown"),
        "products": products,
        "products_text": ", ".join(products) if products else "unclassified",
        "core_dependency": spec.get("core_dependency", "not documented"),
        "health_target": spec.get("health_target", "not set"),
    }


def root_file_templates(ctx: dict[str, Any]) -> dict[str, str]:
    repo = ctx["repo"]
    products = ctx["products_text"]
    status = ctx["status"]
    core = ctx["core_dependency"]
    health = ctx["health_target"]
    return {
        "AGENTS.md": f"""# Agent instructions

This repo is part of the Flyto2 workspace.

Role:

- Repo: `{repo}`
- Status: {status}
- Product lines: {products}
- Core dependency: {core}

Rules:

- Read `PROJECT.md`, `ARCHITECTURE.md`, `STATE.md`, and `DECISIONS.md` before
  implementation.
- Do not write credentials or customer data into this repo.
- Preserve `flyto-core` boundaries and product-line ownership.
- Add tests, guards, docs, or handoff notes for release-impacting changes.
""",
        "CLAUDE.md": f"""# Claude Code notes

`{repo}` participates in: {products}.

Start with:

1. `PROJECT.md`
2. `ARCHITECTURE.md`
3. `STATE.md`
4. `DECISIONS.md`
5. `/Users/chester/flytohub/CODEX_HANDOFF_FLYTO_AUDIT.md`

Never infer or reuse credentials from repository files or handoffs.
""",
        "PROJECT.md": f"""# Project

`{repo}` is a Flyto2 workspace repo.

Product lines:

{bullet_list(ctx["products"])}

Status: {status}

Core dependency: {core}

Health target: {health}
""",
        "ARCHITECTURE.md": f"""# Architecture

This repo belongs to the Flyto2 product system.

Boundary:

- Product lines: {products}
- Core relationship: {core}
- This repo must not bypass shared `flyto-core` runtime boundaries.
- SaaS, enterprise, community, and internal-only behavior must remain explicit.

Update this file when package exports, deployment mode, provider boundaries, or
cross-repo dependencies change.
""",
        "STATE.md": f"""# State

Current state on {date.today().isoformat()}:

- Repo status: {status}
- Product lines: {products}
- Health target: {health}

Known release work:

- Keep project memory current.
- Run repo-specific lint, tests, build, and release gates before production.
- Document unresolved P0/P1 work in `tasks.md` or `handoffs/`.
""",
        "ROADMAP.md": f"""# Roadmap

## P0

- Preserve the repo boundary described in `ARCHITECTURE.md`.
- Keep release-impacting changes covered by tests, guards, docs, or handoffs.

## P1

- Raise or maintain health at target: {health}.
- Keep product-line mapping current with Flyto2 release gates.

## P2

- Expand docs as the repo's Flyto2 role matures.
""",
        "tasks.md": """# Tasks

- [ ] Run repo-specific lint/test/build gates.
- [ ] Keep product-line and `flyto-core` boundaries documented.
- [ ] Update handoff notes for unresolved release risks.
""",
        "DECISIONS.md": f"""# Decisions

## {date.today().isoformat()} - Project memory bootstrapped

Decision: track Flyto2 product-line role, repo boundary, state, roadmap, tasks,
and handoffs in this repo.

Reason: `{repo}` must be maintainable by future agents without relying on
conversation memory.
""",
        "CHANGELOG.md": """# Changelog

## Unreleased

### Added

- Added project memory files, workflow docs, and handoff registry.
""",
    }


def handoff_template(repo: str) -> str:
    today = date.today().isoformat()
    return f"""# Project memory bootstrap

Date: {today}

Summary:

- Added or completed Flyto2 project memory structure.
- Added workflow docs and handoff registry.
- No runtime behavior changed by this bootstrap.

Open risks:

- Run repo-specific verification before release.
- Update this handoff if any generated memory needs deeper repo-specific detail.
"""


def registry_template(repo: str) -> str:
    today = date.today().isoformat()
    return f"""# Handoff registry

| Date | Topic | File |
| --- | --- | --- |
| {today} | Project memory bootstrap | `{today}-project-memory-bootstrap.md` |
"""


def desired_files(manifest: dict[str, Any], repo: str, spec: dict[str, Any]) -> dict[Path, str]:
    ctx = repo_context(manifest, repo, spec)
    files = {Path(name): content for name, content in root_file_templates(ctx).items()}
    files.update({Path("workflows") / name: content for name, content in WORKFLOW_TEMPLATES.items()})
    today = date.today().isoformat()
    files[Path("handoffs") / "_registry.md"] = registry_template(repo)
    files[Path("handoffs") / f"{today}-project-memory-bootstrap.md"] = handoff_template(repo)
    return files


def run_memory_bootstrap(options: MemoryBootstrapOptions) -> dict[str, Any]:
    manifest = load_manifest(options.manifest_path)
    result: dict[str, Any] = {
        "ok": True,
        "workspace": str(options.workspace),
        "apply": options.apply,
        "created": [],
        "skipped_existing": [],
        "skipped_repos": [],
        "missing_repos": [],
    }

    for repo, spec in sorted(manifest.get("repos", {}).items()):
        if not spec.get("memory_required", False):
            continue
        if spec.get("status") == "deprecated" and not options.include_deprecated:
            result["skipped_repos"].append({"repo": repo, "reason": "deprecated"})
            continue
        repo_path = options.workspace / repo
        if not repo_path.exists():
            result["ok"] = False
            result["missing_repos"].append(repo)
            continue

        for rel_path, content in desired_files(manifest, repo, spec).items():
            path = repo_path / rel_path
            if path.exists():
                result["skipped_existing"].append(str(path))
                continue
            if options.apply:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            result["created"].append(str(path))

    return result


def format_memory_bootstrap(result: dict[str, Any]) -> str:
    mode = "applied" if result["apply"] else "dry-run"
    lines = [
        f"Flyto2 memory bootstrap ({mode})",
        f"Workspace: {result['workspace']}",
        f"Created: {len(result['created'])}",
        f"Skipped existing: {len(result['skipped_existing'])}",
        f"Skipped repos: {len(result['skipped_repos'])}",
    ]
    if result["missing_repos"]:
        lines.append(f"Missing repos: {', '.join(result['missing_repos'])}")
    if result["created"]:
        lines.append("")
        lines.append("Files:")
        lines.extend(f"  + {path}" for path in result["created"][:120])
        if len(result["created"]) > 120:
            lines.append(f"  ... {len(result['created']) - 120} more")
    return "\n".join(lines)
