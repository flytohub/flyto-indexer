"""Repository hygiene, dependency, CI, environment, and policy verification.

This module owns release-hygiene checks. It does not orchestrate verification
order, render reports, or compare historical baselines.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from ..doc_scanner import scan_documentation
from ..secret_scanner import scan_secrets
from .support import (
    _CI_CANDIDATES, _SKIP_WORKSPACE_DIRS, _decode_process_output,
    _git_changed_paths, _load_index_json, _read_ci_files,
)


_CI_COMMENT_RE = re.compile(r"(?:^|\s)#.*$")

_CI_COMMAND_PREFIX_RE = re.compile(
    r"^(?:[-*]\s+)?"
    r"(?:(?:run|cmd|command|entrypoint|script|shell)\s*:\s*)?"
    r"(?:[|>][-+]?\s*)?",
    re.IGNORECASE,
)

_CI_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|]")

_CI_STDLIB_COMMANDS = {
    # `python -m unittest` covers the `discover` form; the bare `unittest
    # discover` spelling is the other conventional offline invocation.
    "tests": (
        re.compile(r"^(?:python[0-9.]*|py)\s+-m\s+unittest\b", re.IGNORECASE),
        re.compile(r"^unittest\s+discover\b", re.IGNORECASE),
    ),
    "build": (re.compile(r"^(?:python[0-9.]*|py)\s+-m\s+compileall\b", re.IGNORECASE),),
    "lint": (re.compile(r"^git\s+diff\s+--check\b", re.IGNORECASE),),
}

_CI_VERIFY_SCRIPT_RE = re.compile(
    r"^(?:(?:bash|sh|zsh)\s+)?(?:\./)?(?P<path>(?:[\w.-]+/)*verify\.sh)(?![\w.-])",
    re.IGNORECASE,
)

_GENERATED_CHANGE_PATTERNS = (
    ".flyto-index/*",
    ".flyto/*",
    "dist/*",
    "build/*",
    "node_modules/*",
    "__pycache__/*",
)

_SOURCE_OWNED_CHANGE_PATHS = frozenset({".flyto/coding.yaml"})

_HIGH_RISK_CHANGE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*secret*",
    "*credential*",
    ".claude/settings.local.json",
)

_HIGH_RISK_CHANGE_EXEMPTIONS = (
    ".env.example",
    ".env.sample",
    ".env.template",
)

_UNVERSIONED_SPEC_PREFIXES = ("file:", "link:", "workspace:", "path", "git+", "github:", "http")

_HASH = "#"

_TS_SUFFIXES = (".ts", ".tsx", ".vue", ".js", ".jsx", ".mts", ".cts")

_PY_SUFFIXES = (".py", ".pyi")

_BLANKET_SUPPRESSIONS: tuple[tuple[str, str, re.Pattern[str], tuple[str, ...]], ...] = (
    (
        "typescript", "@ts-nocheck",
        re.compile(r"^\s*//\s*@ts-nocheck\s*$"), _TS_SUFFIXES,
    ),
    (
        "eslint", "eslint-disable",
        re.compile(r"^\s*/\*\s*eslint-disable\s*\*/\s*$"), _TS_SUFFIXES,
    ),
    (
        "mypy", "mypy: ignore-errors",
        re.compile(rf"^\s*{_HASH}\s*mypy:\s*ignore-errors\s*$"), _PY_SUFFIXES,
    ),
    (
        "ruff", "ruff: " + "noqa",
        re.compile(rf"^\s*{_HASH}\s*ruff:\s*" + r"noqa\s*$"), _PY_SUFFIXES,
    ),
    (
        "flake8", "flake8: " + "noqa",
        re.compile(rf"^\s*{_HASH}\s*flake8:\s*" + r"noqa\s*$"), _PY_SUFFIXES,
    ),
)

_SUPPRESSION_HEADER_LINES = 30

_SUPPRESSION_MIN_FILES = 5

_SUPPRESSION_MIN_RATIO = 0.02

_SUPPRESSION_MATERIAL_RATIO = 0.20

_REQUIRED_ENV_READS = (
    re.compile(r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]{2,})['"]\s*\]"""),
    re.compile(r"""os\.getenv\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]\s*\)"""),
    re.compile(r"""os\.environ\.get\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]\s*\)"""),
)

_ENV_DECLARATION_RE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]{2,})\s*=")

_ENV_SAMPLE_PARTS = {
    "test", "tests", "conftest", "scripts",
    "benchmark", "example", "examples", "demo",
}

def _check_runtime_dependencies(root: Path, add_check) -> None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        add_check("runtime_dependencies", "pass", "No pyproject.toml found; Python runtime dependency contract not applicable")
        return

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        add_check("runtime_dependencies", "fail", f"Cannot parse pyproject.toml: {exc}")
        return

    deps = data.get("project", {}).get("dependencies", [])
    project_name = data.get("project", {}).get("name", root.name)
    requires_python = data.get("project", {}).get("requires-python", "")
    expected_indexer_dependencies = ["PyYAML>=6.0.3"]
    if project_name == "flyto-indexer" and deps != expected_indexer_dependencies:
        add_check(
            "runtime_dependencies",
            "fail",
            "Runtime dependencies violate the indexer allowlist",
            metrics={
                "project": project_name,
                "dependencies": deps,
                "expected_dependencies": expected_indexer_dependencies,
                "requires_python": requires_python,
            },
        )
        return

    add_check(
        "runtime_dependencies",
        "pass",
        "Runtime dependency boundary passed",
        metrics={"project": project_name, "dependency_count": len(deps), "requires_python": requires_python},
    )

def _check_rules_policy(root: Path, add_check) -> None:
    rules_path = root / ".flyto-rules.yaml"
    if not rules_path.is_file():
        add_check("rules_policy", "pass", "No project rules policy to enforce")
        return

    try:
        from ..analyzer.rules import check_rules
    except ImportError:
        from analyzer.rules import check_rules  # type: ignore[no-redef]

    try:
        result = check_rules(root)
    except (OSError, ValueError, RuntimeError) as exc:
        add_check(
            "rules_policy",
            "fail",
            f"Project rules policy could not be evaluated: {exc}",
            metrics={"policy": str(rules_path)},
        )
        return

    violations = int(result.get("total_violations") or 0)
    layers = result.get("layers") if isinstance(result.get("layers"), dict) else {}
    status = "pass" if violations == 0 else "fail"
    summary = "Project rules policy passed" if status == "pass" else "Project rules policy failed"
    add_check(
        "rules_policy",
        status,
        summary,
        metrics={
            "policy": str(rules_path),
            "rules_checked": int(result.get("rules_checked") or 0),
            "total_rules": int(result.get("total_rules") or 0),
            "violations": violations,
            "pass_rate": result.get("pass_rate"),
            "layer_count": int(layers.get("layer_count") or 0),
            "layer_files_checked": int(layers.get("files_checked") or 0),
            "layer_edges_checked": int(layers.get("edges_checked") or 0),
            "samples": result.get("violations", [])[:5],
        },
    )

def _check_weak_scanners(root: Path, add_check) -> None:
    secrets = scan_secrets(root)
    secret_samples = [finding.to_dict() for finding in secrets.findings[:10]]
    secret_status = "pass"
    if secrets.critical or secrets.high:
        secret_status = "fail"
    elif secrets.medium:
        secret_status = "warn"
    add_check(
        "weak_scan_secrets",
        secret_status,
        "Secret scan completed",
        metrics={
            "files_scanned": secrets.total_files_scanned,
            "findings": secrets.total_findings,
            "critical": secrets.critical,
            "high": secrets.high,
            "medium": secrets.medium,
            "samples": secret_samples,
        },
    )

    try:
        from ..analyzer.taint import TaintAnalyzer

        analyzer = TaintAnalyzer(root, index=_load_index_json(root))
        taint = analyzer.analyze_full()
        unsanitized = [flow for flow in taint.taint_flows if not flow.sanitized]
        high_risk = [
            flow for flow in unsanitized
            if flow.severity in {"critical", "high"}
        ]
        taint_samples = []
        for flow in high_risk[:10]:
            item = flow.to_dict()
            taint_samples.append({
                "finding_id": item.get("finding_id"),
                "schema": item.get("schema"),
                "fingerprint": item.get("fingerprint"),
                "rule_id": item.get("rule_id"),
                "origin": item.get("origin"),
                "confidence": item.get("confidence"),
                "trace": item.get("trace"),
                "suppression": item.get("suppression"),
                "source_file": item.get("source_file"),
                "source_line": item.get("source_line"),
                "sink_file": item.get("sink_file"),
                "sink_line": item.get("sink_line"),
                "severity": item.get("severity"),
                "category": item.get("category"),
                "recommendation": item.get("recommendation"),
            })
        add_check(
            "weak_scan_taint",
            "fail" if high_risk else "pass",
            "Taint scan completed; no high-risk flows" if not high_risk else "Taint scan found high-risk flows",
            metrics={
                "sources": taint.total_sources,
                "sinks": taint.total_sinks,
                "unsanitized": len(unsanitized),
                "high_risk": len(high_risk),
                "sanitized": taint.sanitized_flows,
                "samples": taint_samples,
            },
        )
    except (OSError, ValueError, RuntimeError) as exc:
        add_check("weak_scan_taint", "warn", f"Taint scan could not complete: {exc}")

    docs = scan_documentation(root)
    add_check(
        "docs_coverage",
        "pass" if docs.overall_score >= 70 else "warn",
        "Documentation scan completed",
        metrics={
            "overall_score": docs.overall_score,
            "readme_score": docs.readme_score,
            "inline_doc_coverage": round(docs.inline_doc_coverage, 3),
            "source_reference_coverage": round(docs.source_reference_coverage, 3),
            "symbol_doc_coverage": round(docs.symbol_doc_coverage, 3),
            "suggestions": len(docs.suggestions),
        },
    )

def _check_no_external_runtime(root: Path, add_check) -> None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        add_check("no_external_runtime", "pass", "No Python package runtime contract to enforce")
        return

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        add_check("no_external_runtime", "fail", f"Cannot parse pyproject.toml: {exc}")
        return

    project = data.get("project", {})
    project_name = project.get("name", root.name)
    dependencies = project.get("dependencies", [])
    optional = project.get("optional-dependencies", {})
    if project_name != "flyto-indexer":
        add_check(
            "no_external_runtime",
            "pass",
            "No-external-runtime contract is scoped to flyto-indexer",
            metrics={"project": project_name, "dependency_count": len(dependencies)},
        )
        return

    _ci_files, ci_text = _read_ci_files(root)
    lowered_ci = ci_text.lower()
    expected_dependencies = ["PyYAML>=6.0.3"]
    has_policy_smoke = "wheel policy smoke test" in lowered_ci and "total_rules" in lowered_ci
    has_metadata_assertion = "requires-dist" in lowered_ci and "runtime_requires" in lowered_ci

    problems = []
    if dependencies != expected_dependencies:
        problems.append(
            f"runtime dependencies must be exactly {expected_dependencies}, got {dependencies}"
        )
    if not has_policy_smoke:
        problems.append("CI does not run an isolated wheel policy smoke")
    if not has_metadata_assertion:
        problems.append("CI does not assert wheel runtime metadata")

    status = "pass"
    if dependencies != expected_dependencies:
        status = "fail"
    elif problems:
        status = "warn"

    add_check(
        "no_external_runtime",
        status,
        "flyto-indexer runtime dependency boundary passed"
        if not problems else "Runtime dependency guard is incomplete",
        metrics={
            "project": project_name,
            "dependency_count": len(dependencies),
            "expected_dependencies": expected_dependencies,
            "optional_dependency_groups": sorted(optional.keys()) if isinstance(optional, dict) else [],
            "ci_policy_smoke": has_policy_smoke,
            "ci_metadata_assertion": has_metadata_assertion,
            "problems": problems,
        },
    )

def _check_package_integrity(root: Path, add_check) -> None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        add_check("package_integrity", "pass", "No Python package manifest to inspect")
        return

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        add_check("package_integrity", "fail", f"Cannot parse pyproject.toml: {exc}")
        return

    project = data.get("project", {})
    project_name = project.get("name", root.name)
    if project_name != "flyto-indexer":
        add_check("package_integrity", "pass", "Package integrity contract is scoped to flyto-indexer")
        return

    tool = data.get("tool", {})
    hatch = tool.get("hatch", {}) if isinstance(tool, dict) else {}
    build = hatch.get("build", {}) if isinstance(hatch, dict) else {}
    targets = build.get("targets", {}) if isinstance(build, dict) else {}
    wheel = targets.get("wheel", {}) if isinstance(targets, dict) else {}
    sdist = targets.get("sdist", {}) if isinstance(targets, dict) else {}

    wheel_packages = wheel.get("packages", []) if isinstance(wheel, dict) else []
    wheel_sources = wheel.get("sources", {}) if isinstance(wheel, dict) else {}
    force_include = wheel.get("force-include", {}) if isinstance(wheel, dict) else {}
    sdist_include = sdist.get("include", []) if isinstance(sdist, dict) else []
    scripts = project.get("scripts", {}) if isinstance(project, dict) else {}
    license_files = project.get("license-files", []) if isinstance(project, dict) else []

    required = {
        "hatchling_backend": data.get("build-system", {}).get("build-backend") == "hatchling.build",
        "policy_parser_dependency": project.get("dependencies") == ["PyYAML>=6.0.3"],
        "wheel_src_package": "src" in wheel_packages,
        "wheel_src_remap": isinstance(wheel_sources, dict) and wheel_sources.get("src") == "flyto_indexer",
        "rule_corpus_force_include": isinstance(force_include, dict)
        and force_include.get("config/rules") == "flyto_indexer/config/rules",
        "sdist_src": "/src" in sdist_include,
        "sdist_config": "/config" in sdist_include,
        "cli_entrypoint": isinstance(scripts, dict)
        and scripts.get("flyto-index") == "flyto_indexer.cli:main",
        "license_files_exist": all((root / str(path)).is_file() for path in license_files)
        and {"LICENSE", "NOTICE"}.issubset({str(path) for path in license_files}),
    }
    package_entries = _package_manifest_entries(wheel_packages, wheel_sources, force_include, sdist_include)
    forbidden_entries = [
        entry for entry in package_entries
        if _matches_any(entry, _GENERATED_CHANGE_PATTERNS) or _is_high_risk_change_path(entry)
    ]
    missing = sorted(name for name, present in required.items() if not present)
    status = "pass"
    if forbidden_entries or missing:
        status = "fail"
    add_check(
        "package_integrity",
        status,
        "Package manifest preserves the install/runtime contract" if status == "pass" else "Package manifest can leak or break runtime artifacts",
        metrics={
            "required": required,
            "missing": missing,
            "forbidden_entries": forbidden_entries,
            "entries_checked": len(package_entries),
        },
    )

def _ci_command_segments(text: str) -> list[str]:
    """Split CI text into the commands it actually executes.

    Comments are dropped and YAML list/`run:` decoration is removed so each
    returned segment starts where a shell command starts. Prose that merely
    mentions a command never reaches command position.
    """
    segments: list[str] = []
    for raw in text.splitlines():
        line = _CI_COMMENT_RE.sub("", raw).strip()
        if not line or line.startswith("#"):
            continue
        line = _CI_COMMAND_PREFIX_RE.sub("", line, count=1).strip()
        if not line:
            continue
        for part in _CI_SEGMENT_SPLIT_RE.split(line):
            candidate = part.strip().strip("\"'").lstrip("@-").strip()
            if candidate:
                segments.append(candidate)
    return segments

def _ci_runs_command(segments: list[str], patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.match(segment) for segment in segments for pattern in patterns)

def _ci_invokes_verify_script(root: Path, segments: list[str]) -> bool:
    """True when CI explicitly runs a verify script that exists in the repo.

    The path must stay inside the repository: a segment that walks out with
    ``..`` names a script this repository neither owns nor ships, so it
    cannot close this repository's loop.
    """
    for segment in segments:
        match = _CI_VERIFY_SCRIPT_RE.match(segment)
        if not match:
            continue
        relative = match.group("path")
        if ".." in relative.split("/"):
            continue
        if (root / relative).is_file():
            return True
    return False

def _check_ci_closed_loop(root: Path, add_check) -> None:
    ci_files, ci_text = _read_ci_files(root)
    if not ci_files:
        add_check("ci_closed_loop", "warn", "No CI workflow files found")
        return

    lowered = ci_text.lower()
    with_scripts = f"{ci_text}\n{_read_package_scripts(root)}"
    lowered_with_scripts = with_scripts.lower()
    commands = _ci_command_segments(with_scripts)
    project_name = _pyproject_name(root) or root.name
    required = {
        "verify": any(token in lowered for token in (
            "flyto-index verify", "verify-workspace", "npm run verify", "pnpm verify", "yarn verify",
        )) or _ci_invokes_verify_script(root, commands),
        "tests": any(token in lowered_with_scripts for token in (
            "pytest", "vitest", "npm test", "npm run test", "pnpm test", "yarn test", "go test",
            "flutter test", "dart test", "markdown-link-check", "test markdown links",
        )) or _ci_runs_command(commands, _CI_STDLIB_COMMANDS["tests"]),
        "lint": any(token in lowered_with_scripts for token in (
            "ruff", "mypy", "eslint", "npm run lint", "pnpm lint", "yarn lint", "golangci-lint",
            "flutter analyze", "dart analyze", "markdownlint", "lint markdown",
        )) or _ci_runs_command(commands, _CI_STDLIB_COMMANDS["lint"]),
        "build": any(token in lowered_with_scripts for token in (
            "python -m build", "npm run build", "pnpm build", "yarn build", "go build", "cargo build",
            "flutter build", "dart compile", "mkdocs build", "sphinx-build",
            "build documentation", "documentation bundle",
        )) or _ci_runs_command(commands, _CI_STDLIB_COMMANDS["build"]),
    }
    if project_name == "flyto-indexer":
        required.update({
            "sarif_report": "--report-format sarif" in lowered,
            "wheel_policy_smoke": (
                "wheel policy smoke test" in lowered
                and "flyto-index --help" in lowered
                and "total_rules" in lowered
            ),
        })

    missing = sorted(name for name, present in required.items() if not present)
    add_check(
        "ci_closed_loop",
        "pass" if not missing else "warn",
        "CI runs the verify/test/build loop" if not missing else "CI does not fully close the verify loop",
        metrics={
            "files": [str(path.relative_to(root)) for path in ci_files],
            "required": required,
            "missing": missing,
        },
    )

def _check_change_hygiene(root: Path, add_check) -> None:
    if not (root / ".git").exists():
        add_check("change_hygiene", "pass", "No git repository; change hygiene not applicable")
        return

    changed = _git_changed_paths(root)
    policy, _source = _load_verify_policy(root)
    allow_generated_patterns = tuple(
        str(pattern)
        for pattern in _as_list(
            policy.get("allow_generated_changes")
            or policy.get("allow_tracked_generated")
            or []
        )
        if str(pattern).strip()
    )
    generated_candidates = [
        path for path in changed if _is_generated_change_path(path)
    ]
    generated = [
        path for path in generated_candidates if not _matches_any(path, allow_generated_patterns)
    ]
    allowed_generated = sorted(set(generated_candidates) - set(generated))
    high_risk = [path for path in changed if _is_high_risk_change_path(path)]
    status = "pass"
    summary = "No high-risk working tree changes"
    if generated:
        status = "fail"
        summary = "Generated artifacts are tracked in the working tree"
    elif high_risk:
        status = "warn"
        summary = "Working tree includes high-risk config or secret-shaped paths"

    add_check(
        "change_hygiene",
        status,
        summary,
        metrics={
            "changed": len(changed),
            "generated": generated,
            "allowed_generated": allowed_generated,
            "allow_generated_patterns": list(allow_generated_patterns),
            "high_risk": high_risk,
        },
    )

def _python_mcp_entry_points(
    root: Path,
) -> tuple[list[dict[str, str]], list[str]]:
    """Resolve declared Python MCP console scripts without importing target code."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return [], []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return [], []

    project = data.get("project", {})
    scripts = project.get("scripts", {}) if isinstance(project, dict) else {}
    if not isinstance(scripts, dict):
        return [], []

    entries: list[dict[str, str]] = []
    problems: list[str] = []
    for raw_name, raw_target in sorted(scripts.items()):
        if not isinstance(raw_name, str) or not isinstance(raw_target, str):
            continue
        name = raw_name.strip()
        target = raw_target.strip()
        module_name, separator, callable_name = target.partition(":")
        normalized_name = name.lower().replace("_", "-")
        normalized_module = module_name.lower()
        if (
            "mcp" not in normalized_name.split("-")
            and ".mcp" not in normalized_module
            and not normalized_module.endswith("mcp_server")
        ):
            continue

        if not separator or not module_name or not callable_name:
            problems.append(f"{name}: invalid console-script target {target!r}")
            continue
        module_parts = module_name.split(".")
        if not all(part.isidentifier() for part in module_parts):
            problems.append(f"{name}: invalid Python module {module_name!r}")
            continue

        relative_path = Path(*module_parts).with_suffix(".py")
        candidates = (root / relative_path, root / "src" / relative_path)
        module_path = next((path for path in candidates if path.is_file()), None)
        if module_path is None:
            problems.append(f"{name}: module {module_name!r} does not exist")
            continue

        callable_root = callable_name.split(".", 1)[0].strip()
        if not callable_root.isidentifier():
            problems.append(f"{name}: invalid callable {callable_name!r}")
            continue
        try:
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            problems.append(f"{name}: cannot parse {module_name!r}: {exc}")
            continue
        declarations = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if callable_root not in declarations:
            problems.append(
                f"{name}: callable {callable_name!r} is missing from {module_name!r}"
            )
            continue

        entries.append({
            "name": name,
            "target": target,
            "module_path": str(module_path.relative_to(root)),
        })
    return entries, problems

def _check_mcp_runtime_smoke(root: Path, add_check) -> None:
    project_name = _pyproject_name(root)
    indexer_runtime = (
        project_name == "flyto-indexer"
        and (root / "src" / "mcp_server.py").is_file()
    )
    entry_points, entry_point_problems = _python_mcp_entry_points(root)

    if not indexer_runtime and not entry_points and not entry_point_problems:
        add_check("mcp_runtime_smoke", "pass", "No MCP server module to smoke")
        return
    if not indexer_runtime:
        add_check(
            "mcp_runtime_smoke",
            "fail" if entry_point_problems else "pass",
            (
                "Python MCP entry points resolve statically"
                if not entry_point_problems
                else "Python MCP entry-point validation found drift"
            ),
            metrics={
                "mode": "static_entrypoint",
                "entry_points": entry_points,
                "problems": entry_point_problems,
            },
        )
        return

    try:
        from .. import mcp_server
        from ..tool_registry import SMART_TOOLS, SMART_TOOL_NAMES, has_tool
    except ImportError:
        try:
            import mcp_server
            from tool_registry import SMART_TOOLS, SMART_TOOL_NAMES, has_tool
        except ImportError as exc:
            add_check("mcp_runtime_smoke", "fail", f"Cannot import MCP runtime: {exc}")
            return

    problems = []
    server_names = {tool.get("name", "") for tool in getattr(mcp_server, "TOOLS", [])}
    smart_names = {tool.get("name", "") for tool in SMART_TOOLS}
    if server_names != smart_names:
        problems.append("server tool list does not match smart tool registry")
    if set(SMART_TOOL_NAMES) != smart_names:
        problems.append("SMART_TOOL_NAMES does not match SMART_TOOLS")
    missing_dispatch = sorted(name for name in smart_names if name and not has_tool(name))
    if missing_dispatch:
        problems.append("some smart tools are missing dispatch")
    protocols = getattr(mcp_server, "SUPPORTED_PROTOCOL_VERSIONS", ())
    if not protocols:
        problems.append("no supported protocol versions declared")
    elif mcp_server.negotiate_protocol_version(protocols[-1]) != protocols[-1]:
        problems.append("protocol negotiation does not echo supported clients")
    if not getattr(mcp_server, "RESOURCES", []):
        problems.append("no MCP resources declared")
    if not getattr(mcp_server, "PROMPTS", []):
        problems.append("no MCP prompts declared")
    impact_prompt = mcp_server._get_prompt("impact-check")
    if not impact_prompt.get("messages"):
        problems.append("impact-check prompt cannot be rendered")

    add_check(
        "mcp_runtime_smoke",
        "fail" if problems else "pass",
        "MCP runtime imports and protocol helpers smoke cleanly" if not problems else "MCP runtime smoke found drift",
        metrics={
            "mode": "indexer_runtime",
            "tools": len(server_names),
            "protocol_versions": list(protocols),
            "resources": len(getattr(mcp_server, "RESOURCES", [])),
            "prompts": len(getattr(mcp_server, "PROMPTS", [])),
            "missing_dispatch": missing_dispatch,
            "problems": problems,
        },
    )

def _breaking_version_axis(spec: str) -> str | None:
    """The component a bump of which is allowed to break callers.

    Under semver that is the major, except below 1.0 where the minor carries
    breaking change -- which is why lexical 0.48 and 0.49 are not
    interchangeable even though both read as major zero.
    """
    match = re.search(r"(\d+)\s*\.\s*(\d+)", spec)
    if match:
        return f"0.{match.group(2)}" if match.group(1) == "0" else match.group(1)
    lone = re.search(r"(\d+)", spec)
    return lone.group(1) if lone else None

def _check_dependency_drift(projects: list[Path], checks: list[dict[str, Any]]) -> None:
    """Catch this workspace disagreeing with itself about a package it publishes.

    A package the workspace both publishes and consumes is an internal
    contract, and two consumers on different breaking versions of it is that
    contract already broken -- silently, because each repository's own gates
    only ever see the version it pinned. Third-party spread is reported but
    does not gate: repositories legitimately upgrade at different times.
    """
    try:
        from ..dependency_scanner import scan_dependencies
    except ImportError:  # pragma: no cover - packaging fallback
        from dependency_scanner import scan_dependencies  # type: ignore[no-redef]

    published = {project.name for project in projects}
    consumers: dict[tuple[str, str], dict[str, set[str]]] = {}
    scanned = 0
    for project in projects:
        try:
            inventory = scan_dependencies(project)
        except Exception:
            continue
        scanned += 1
        for dependency in inventory.dependencies:
            spec = (dependency.version or "").strip()
            if not spec or spec.startswith(_UNVERSIONED_SPEC_PREFIXES):
                continue
            axis = _breaking_version_axis(spec)
            if axis is None:
                continue
            by_axis = consumers.setdefault((dependency.ecosystem, dependency.name), {})
            by_axis.setdefault(axis, set()).add(project.name)

    internal_drift: list[dict[str, Any]] = []
    external_drift = 0
    for (ecosystem, name), by_axis in sorted(consumers.items()):
        if len(by_axis) < 2:
            continue
        if name not in published:
            external_drift += 1
            continue
        internal_drift.append({
            "package": name,
            "ecosystem": ecosystem,
            "versions": {
                axis: sorted(repos) for axis, repos in sorted(by_axis.items())
            },
        })

    status = "pass"
    summary = "Workspace-published packages are consumed at one breaking version"
    if internal_drift:
        status = "warn"
        shown = ", ".join(
            f"{drift['package']} at {' and '.join(sorted(drift['versions']))}"
            for drift in internal_drift[:3]
        )
        remainder = len(internal_drift) - 3
        if remainder > 0:
            shown += f", and {remainder} more"
        summary = f"Workspace packages are consumed at disagreeing versions: {shown}"

    checks.append({
        "name": "dependency_drift",
        "status": status,
        "summary": summary,
        "metrics": {
            "projects_scanned": scanned,
            "packages_seen": len(consumers),
            "internal_drift": internal_drift,
            "external_major_drift": external_drift,
        },
    })

def _quality_tool_is_configured(root: Path, tool: str) -> bool:
    """Whether the repository claims to run this tool at all."""
    def pyproject_has(section: str) -> bool:
        pyproject = root / "pyproject.toml"
        if not pyproject.exists():
            return False
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            return False
        return section in (data.get("tool") or {})

    if tool == "typescript":
        return any(root.glob("tsconfig*.json"))
    if tool == "eslint":
        if any(root.glob(".eslintrc*")) or any(root.glob("eslint.config.*")):
            return True
        package = root / "package.json"
        if not package.exists():
            return False
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if "eslintConfig" in data:
            return True
        return any("eslint" in (data.get(key) or {}) for key in ("devDependencies", "dependencies"))
    if tool == "mypy":
        return (
            pyproject_has("mypy")
            or (root / "mypy.ini").exists()
            or (root / ".mypy.ini").exists()
        )
    if tool == "ruff":
        return (
            pyproject_has("ruff")
            or (root / "ruff.toml").exists()
            or (root / ".ruff.toml").exists()
        )
    if tool == "flake8":
        return (
            (root / ".flake8").exists()
            or (root / "tox.ini").exists()
            or (root / "setup.cfg").exists()
        )
    return False

def _file_opens_with_blanket_pragma(path: Path, pattern: re.Pattern[str]) -> bool:
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for offset, line in enumerate(handle):
                if offset >= _SUPPRESSION_HEADER_LINES:
                    return False
                if pattern.match(line):
                    return True
    except OSError:
        return False
    return False

def _check_suppression_drift(root: Path, add_check) -> None:
    """Catch a repository that runs a quality tool and exempts its files from it.

    A green lint or typecheck run says nothing about the files the tool was
    told to skip. The contradiction -- configured, and broadly silenced -- is
    invisible to every other check here, and to the tool itself, which exits
    zero precisely because it obeyed.
    """
    configured = {
        tool for tool, _, _, _ in _BLANKET_SUPPRESSIONS
        if _quality_tool_is_configured(root, tool)
    }
    if not configured:
        add_check(
            "suppression_drift", "pass",
            "No typecheck or lint tooling configured; suppression drift not applicable",
        )
        return

    candidates: dict[str, int] = dict.fromkeys(configured, 0)
    # Not dict.fromkeys: every key would share one list.
    suppressed: dict[str, list[str]] = {tool: [] for tool in configured}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if set(path.relative_to(root).parts) & _SKIP_WORKSPACE_DIRS:
            continue
        for tool, _label, pattern, suffixes in _BLANKET_SUPPRESSIONS:
            if tool not in configured or path.suffix not in suffixes:
                continue
            candidates[tool] += 1
            if _file_opens_with_blanket_pragma(path, pattern):
                suppressed[tool].append(str(path.relative_to(root)))

    labels = {tool: label for tool, label, _, _ in _BLANKET_SUPPRESSIONS}
    drifted: list[dict[str, Any]] = []
    for tool in sorted(configured):
        total = candidates[tool]
        count = len(suppressed[tool])
        if not total or not count:
            continue
        ratio = count / total
        material = ratio >= _SUPPRESSION_MATERIAL_RATIO or (
            count >= _SUPPRESSION_MIN_FILES and ratio >= _SUPPRESSION_MIN_RATIO
        )
        if material:
            drifted.append({
                "tool": tool,
                "pragma": labels[tool],
                "suppressed": count,
                "candidates": total,
                "ratio": round(ratio, 4),
                "samples": sorted(suppressed[tool])[:10],
            })

    status = "pass"
    summary = "Configured typecheck and lint tooling covers its files"
    if drifted:
        status = "warn"
        shown = ", ".join(
            f"{d['tool']} exempts {d['suppressed']}/{d['candidates']} files"
            f" ({d['ratio'] * 100:.0f}%) via {d['pragma']}"
            for d in drifted[:3]
        )
        remainder = len(drifted) - 3
        if remainder > 0:
            shown += f", and {remainder} more"
        summary = f"Configured tooling is broadly suppressed: {shown}"

    add_check(
        "suppression_drift",
        status,
        summary,
        metrics={
            "configured": sorted(configured),
            "counts": {
                tool: {"suppressed": len(suppressed[tool]), "candidates": candidates[tool]}
                for tool in sorted(configured)
            },
            "drift": drifted,
            "thresholds": {
                "min_files": _SUPPRESSION_MIN_FILES,
                "min_ratio": _SUPPRESSION_MIN_RATIO,
                "material_ratio": _SUPPRESSION_MATERIAL_RATIO,
            },
        },
    )

def _declared_env_names(root: Path) -> tuple[set[str], list[str]]:
    declared: set[str] = set()
    sources: list[str] = []
    for example in sorted(root.rglob(".env.example")):
        if set(example.relative_to(root).parts) & _SKIP_WORKSPACE_DIRS:
            continue
        try:
            text = example.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        sources.append(str(example.relative_to(root)))
        for line in text.splitlines():
            match = _ENV_DECLARATION_RE.match(line)
            if match:
                declared.add(match.group(1))
    return declared, sources

def _check_env_contract(root: Path, add_check) -> None:
    """Catch a required setting an operator has no way to discover.

    An .env.example is a claim about what you configure. A variable the code
    refuses to start without, in a family that file already documents, is a
    hole in that claim: the first sign of it is a crash, and the name that
    would fix it appears nowhere an operator would look.

    Only names sharing a prefix with something already declared are counted.
    That is what keeps the platform's own variables -- CI, pytest, CUDA,
    systemd -- out of it without maintaining a list of them.
    """
    declared, sources = _declared_env_names(root)
    if not sources:
        add_check(
            "env_contract", "pass",
            "No .env.example; no declared configuration surface to check against",
        )
        return

    families = {name.split("_")[0] for name in declared if "_" in name}
    required: dict[str, str] = {}
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        parts = set(relative.parts)
        if parts & _SKIP_WORKSPACE_DIRS:
            continue
        lowered_parts = {part.lower() for part in parts}
        if lowered_parts & _ENV_SAMPLE_PARTS or path.stem.lower() in _ENV_SAMPLE_PARTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in _REQUIRED_ENV_READS:
            for name in pattern.findall(text):
                required.setdefault(name, str(relative))

    undocumented = sorted(
        name for name in required
        if name not in declared and name.split("_")[0] in families
    )

    status = "pass"
    summary = "Every required setting appears in the declared configuration surface"
    if undocumented:
        status = "warn"
        shown = ", ".join(undocumented[:3])
        remainder = len(undocumented) - 3
        if remainder > 0:
            shown += f", and {remainder} more"
        summary = f"Required settings are absent from .env.example: {shown}"

    add_check(
        "env_contract",
        status,
        summary,
        metrics={
            "declaration_files": sources,
            "declared": len(declared),
            "required_reads": len(required),
            "undocumented": [
                {"name": name, "first_read": required[name]} for name in undocumented[:20]
            ],
        },
    )

def _check_agent_hygiene(root: Path, add_check) -> None:
    instruction_files = [root / "AGENTS.md", root / "CLAUDE.md"]
    present = [path for path in instruction_files if path.exists()]
    if not present:
        add_check("agent_hygiene", "warn", "No AGENTS.md or CLAUDE.md found")
    else:
        combined = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in present)
        lowered = combined.lower()
        mentions_indexer = "flyto-indexer" in lowered or "flyto-index" in lowered
        mentions_pre_change = (
            "search" in lowered
            and ("impact" in lowered or "task(action='plan')" in lowered or 'task(action="plan")' in lowered)
        )
        mentions_post_verify = "verify" in lowered
        status = "pass" if mentions_indexer and mentions_pre_change and mentions_post_verify else "warn"
        add_check(
            "agent_hygiene",
            status,
            "Agent instructions require indexer exploration and verification" if status == "pass" else "Agent instructions exist but do not clearly require pre-change exploration and post-change verification",
            metrics={
                "files": [path.name for path in present],
                "mentions_indexer": mentions_indexer,
                "mentions_pre_change": mentions_pre_change,
                "mentions_post_verify": mentions_post_verify,
            },
        )

    ignored = _generated_index_is_ignored(root)
    add_check(
        "generated_index_ignore",
        "pass" if ignored else "warn",
        ".flyto-index is ignored" if ignored else ".flyto-index is not ignored",
    )

def _check_policy_budget(
    root: Path,
    checks: list[dict[str, Any]],
    policy_path: str | Path | None = None,
) -> None:
    policy, source = _load_verify_policy(root, policy_path)
    if not policy:
        return

    warn_as_fail = set(_as_list(policy.get("warn_as_fail") or policy.get("fail_on_warn")))
    allow_warn = set(_as_list(policy.get("allow_warn") or policy.get("allow_warnings")))
    min_docs_score = _as_int(policy.get("min_docs_score"))
    min_health_score = _as_int(policy.get("min_health_score"))
    min_documentation_score = _as_int(policy.get("min_documentation_score"))
    max_complex_functions = _as_int(policy.get("max_complex_functions"))
    max_complexity_burden = _as_int(policy.get("max_complexity_burden"))
    max_dead_code = _as_int(policy.get("max_dead_code"))

    violations: list[dict[str, Any]] = []
    for check in checks:
        name = check.get("name", "")
        status = check.get("status", "fail")
        if status == "warn" and ("*" in warn_as_fail or name in warn_as_fail) and name not in allow_warn:
            violations.append({
                "check": name,
                "rule": "warn_as_fail",
                "status": status,
            })
        if name == "docs_coverage" and min_docs_score is not None:
            score = (check.get("metrics") or {}).get("overall_score", 0)
            if isinstance(score, (int, float)) and score < min_docs_score:
                violations.append({
                    "check": name,
                    "rule": "min_docs_score",
                    "score": score,
                    "minimum": min_docs_score,
                })
        if name == "quality_health":
            metrics = check.get("metrics") or {}
            complexity = metrics.get("complexity") or {}
            dead_code = metrics.get("dead_code") or {}
            documentation = metrics.get("documentation") or {}
            quality_limits = (
                ("min_health_score", metrics.get("health_score"), min_health_score, "min"),
                (
                    "min_documentation_score",
                    documentation.get("score"),
                    min_documentation_score,
                    "min",
                ),
                (
                    "max_complex_functions",
                    complexity.get("complex_functions"),
                    max_complex_functions,
                    "max",
                ),
                (
                    "max_complexity_burden",
                    complexity.get("complexity_burden"),
                    max_complexity_burden,
                    "max",
                ),
                (
                    "max_dead_code",
                    dead_code.get("dead_count"),
                    max_dead_code,
                    "max",
                ),
            )
            for rule, actual, limit, direction in quality_limits:
                if limit is None or not isinstance(actual, (int, float)):
                    continue
                violates = actual < limit if direction == "min" else actual > limit
                if violates:
                    violations.append({
                        "check": name,
                        "rule": rule,
                        "actual": actual,
                        "limit": limit,
                    })

    checks.append({
        "name": "policy_budget",
        "status": "fail" if violations else "pass",
        "summary": "Verify policy budget passed" if not violations else "Verify policy budget failed",
        "metrics": {
            "policy": str(source) if source else "",
            "warn_as_fail": sorted(warn_as_fail),
            "allow_warn": sorted(allow_warn),
            "min_docs_score": min_docs_score,
            "quality_budget": {
                "min_health_score": min_health_score,
                "min_documentation_score": min_documentation_score,
                "max_complex_functions": max_complex_functions,
                "max_complexity_burden": max_complexity_burden,
                "max_dead_code": max_dead_code,
            },
            "violations": violations,
        },
    })

def _check_quality_health(root: Path, add_check) -> None:
    """Attach canonical health metrics without failing on accepted legacy debt."""
    try:
        from ..profile.index_extract import load_content_file, load_index_file
        from ..quality import _code_health_score_from_index
    except ImportError:
        from profile.index_extract import load_content_file, load_index_file
        from quality import _code_health_score_from_index

    index_dir = root / ".flyto-index"
    index = load_index_file(index_dir)
    if not index:
        add_check(
            "quality_health",
            "warn",
            "Canonical health snapshot unavailable because the index is missing",
        )
        return

    content_map = load_content_file(index_dir)

    def content_loader(symbol_id: str, symbol: dict) -> str:
        inline = symbol.get("content")
        if isinstance(inline, str) and inline:
            return inline
        return content_map.get(symbol_id, "")

    health = _code_health_score_from_index(
        index,
        project=index.get("project") or root.name,
        content_loader=content_loader,
    )
    if health.get("error"):
        add_check(
            "quality_health",
            "warn",
            str(health["error"]),
            metrics={"snapshot": health.get("snapshot", {})},
        )
        return

    breakdown = health.get("breakdown") or {}
    add_check(
        "quality_health",
        "pass",
        (
            f"Canonical health snapshot is {health.get('score', 0)}/100 "
            f"({health.get('grade', '?')})"
        ),
        metrics={
            "health_score": health.get("score", 0),
            "health_grade": health.get("grade", "?"),
            "snapshot": health.get("snapshot", {}),
            "complexity": (breakdown.get("complexity") or {}).get("metrics", {}),
            "dead_code": (breakdown.get("dead_code") or {}).get("metrics", {}),
            "documentation": {
                "score": (breakdown.get("documentation") or {}).get("score", 0),
                **((breakdown.get("documentation") or {}).get("metrics", {})),
            },
            "modularity": {
                "score": (breakdown.get("modularity") or {}).get("score", 0),
                **((breakdown.get("modularity") or {}).get("metrics", {})),
            },
        },
    )

def _check_mcp_registry(root: Path, add_check) -> None:
    """Verify MCP smart tool schemas and dispatch stay in sync."""
    if not (root / "src" / "tool_registry").exists():
        return

    try:
        from ..tool_registry import SMART_TOOLS, SMART_TOOL_NAMES, has_tool
    except ImportError:
        try:
            from tool_registry import SMART_TOOLS, SMART_TOOL_NAMES, has_tool
        except ImportError as exc:
            add_check("mcp_registry", "fail", f"Cannot import tool registry: {exc}")
            return

    names = [tool.get("name", "") for tool in SMART_TOOLS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    missing_dispatch = sorted(name for name in names if name and not has_tool(name))
    derived_mismatch = sorted(set(names) ^ set(SMART_TOOL_NAMES))
    missing_schema = sorted(
        name for name, tool in zip(names, SMART_TOOLS, strict=True)
        if not tool.get("inputSchema") or not tool.get("description")
    )

    problems = duplicates or missing_dispatch or derived_mismatch or missing_schema
    add_check(
        "mcp_registry",
        "fail" if problems else "pass",
        "MCP smart tools and dispatch are in sync" if not problems else "MCP smart tool registry has drift",
        metrics={
            "smart_tools": len(names),
            "duplicates": duplicates,
            "missing_dispatch": missing_dispatch,
            "derived_mismatch": derived_mismatch,
            "missing_schema": missing_schema,
        },
    )

def _generated_index_is_ignored(root: Path) -> bool:
    """Check .flyto-index ignore status using git when available."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", ".flyto-index"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return False
    content = gitignore.read_text(encoding="utf-8", errors="ignore")
    return ".flyto-index/" in content or ".flyto-index" in content

def _read_package_scripts(root: Path) -> str:
    package_json = root / "package.json"
    if not package_json.is_file():
        return ""
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return ""
    lines = []
    for name, command in scripts.items():
        if isinstance(name, str) and isinstance(command, str):
            lines.append(f"npm run {name}: {command}")
    return "\n".join(lines)

def _pyproject_name(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return ""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return ""
    name = data.get("project", {}).get("name", "")
    return str(name) if name else ""

def _package_manifest_entries(*values: Any) -> list[str]:
    entries: list[str] = []
    for value in values:
        if isinstance(value, dict):
            for key, item in value.items():
                entries.append(str(key).lstrip("/"))
                entries.append(str(item).lstrip("/"))
        elif isinstance(value, list):
            entries.extend(str(item).lstrip("/") for item in value)
        elif value:
            entries.append(str(value).lstrip("/"))
    return sorted({entry.replace("\\", "/") for entry in entries if entry})

def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)

def _is_generated_change_path(path: str) -> bool:
    """Classify a changed working-tree path as generated runtime state.

    Exact source-owned policy paths are excluded so a committed verification
    contract does not need a repository waiver; nested copies and every other
    path under the same directory keep the generated classification.
    """
    normalized = path.replace("\\", "/")
    if normalized in _SOURCE_OWNED_CHANGE_PATHS:
        return False
    return _matches_any(normalized, _GENERATED_CHANGE_PATTERNS)

def _is_high_risk_change_path(path: str) -> bool:
    return _matches_any(path, _HIGH_RISK_CHANGE_PATTERNS) and not _matches_any(
        path,
        _HIGH_RISK_CHANGE_EXEMPTIONS,
    )

def _load_verify_policy(root: Path, policy_path: str | Path | None = None) -> tuple[dict[str, Any], Path | None]:
    candidates = [Path(policy_path).resolve()] if policy_path else [
        root / ".flyto-rules.yaml",
        root / ".flyto-rules.yml",
        root / ".flyto-rules.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        if path.suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}, path
            verify = data.get("verify") if isinstance(data, dict) else None
            return (verify if isinstance(verify, dict) else {}), path
        return _parse_verify_yaml_block(path), path
    return {}, None

def _parse_verify_yaml_block(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    block_indent: int | None = None
    current_key = ""
    policy: dict[str, Any] = {}
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if block_indent is None:
            if stripped == "verify:":
                block_indent = indent
            continue
        if indent <= block_indent:
            break
        if stripped.startswith("- ") and current_key:
            items = policy.setdefault(current_key, [])
            if isinstance(items, list):
                items.append(_parse_policy_scalar(stripped[2:].strip()))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        policy[current_key] = [] if value == "" else _parse_policy_scalar(value)
    return policy

def _parse_policy_scalar(value: str) -> Any:
    value = value.strip().strip("'\"")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_policy_scalar(part.strip()) for part in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        return value

def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]

def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

