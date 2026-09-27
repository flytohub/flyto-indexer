"""
Command-line interface for Flyto2 Indexer.

Usage:
    flyto-index init [path]
    flyto-index scan <path> [--full]
    flyto-index status [path]
    flyto-index impact <symbol_id> --path <project_path>
    flyto-index context --path <project_path> [--query <query>]
    flyto-index outline <path>
    flyto-index brief [path]
    flyto-index describe <file_path> [--summary "..."] [--path <project_path>]
    flyto-index install-hook [path] [--remove]
    flyto-index demo [path]
    flyto-index check [path] [--threshold high|medium|low] [--json] [--base <ref>]
"""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .cli_verify import (
    _write_verify_report,
    cmd_verify,
    cmd_verify_baseline,
    cmd_verify_workspace,
)
from .cli_workspace import (
    CLAUDE_MARKER_BEGIN, CLAUDE_MARKER_END, CLAUDE_SECTION, CLAUDE_SETTINGS_PATH,
    HOOK_CONTENT, HOOK_MARKER_BEGIN, HOOK_MARKER_END, MCP_SERVER_KEY,
    _configure_mcp_settings, _ensure_gitignore, _status_from_legacy_flyto,
    _status_from_modern_index, cmd_brief, cmd_context, cmd_demo, cmd_describe,
    cmd_impact, cmd_init, cmd_install_hook, cmd_outline, cmd_scan, cmd_setup,
    cmd_setup_claude, cmd_status, cmd_task, cmd_task_status, cmd_tools,
    cmd_usage_record, cmd_usage_report,
)
from .cli_scanners import (
    _agent_guard_project, cmd_add_agent_guard, cmd_add_layer,
    cmd_add_taint_sanitizer, cmd_add_taint_sink, cmd_add_taint_source, cmd_deps,
    cmd_docs, cmd_export, cmd_framework, cmd_layers, cmd_license,
    cmd_list_agent_guards, cmd_list_taint_rules, cmd_pr_risk, cmd_profile,
    cmd_remove_agent_guard, cmd_remove_taint_rule, cmd_sbom, cmd_secrets,
)
# Data-sovereignty contract for the delegated export handler:
# src.cli_scanners.cmd_export must strip raw source with `k != "content"`.
# Keep this invariant visible at the stable CLI facade even though execution is
# delegated to a focused handler module.

from .cli_quality import (
    _compute_symbol_impact, _detect_changed_files, _format_check_output,
    cmd_agent_audit, cmd_call_sites, cmd_check, cmd_research_priority, cmd_taint,
)

from . import __version__
from .task_cli import (
    configure_task_evidence_parsers,
    configure_task_parser,
    execute_task_command,
    execute_task_status,
    execute_usage_record,
    execute_usage_report,
    task_evidence_tool_descriptions,
)




def build_parser() -> argparse.ArgumentParser:
    """Build the CLI grammar without parsing arguments or running commands."""
    parser = argparse.ArgumentParser(
        prog="flyto-index",
        description="Flyto2 Indexer - Code indexing, search, and impact analysis for AI-assisted development",
        epilog=(
            "Examples:\n"
            "  flyto-index init .                  Initialize indexing for current project\n"
            "  flyto-index scan . --full            Full re-index of current project\n"
            "  flyto-index status                   Check index freshness\n"
            "  flyto-index impact useAuth --path .  See what depends on useAuth\n"
            "  flyto-index tools                    List all commands as JSON (for AI integration)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    _configure_core_commands(subparsers)
    _configure_scanner_commands(subparsers)
    _configure_verification_commands(subparsers)
    _configure_architecture_commands(subparsers)
    return parser


def _configure_core_commands(subparsers) -> None:
    """Register indexing, setup, dependency, and export commands."""
    # init
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize Flyto2 metadata in a project",
        description="Create the legacy .flyto/ metadata directory and generated-index ignore rules.",
    )
    init_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    init_parser.add_argument("--name", help="Project name (default: directory name)")
    init_parser.add_argument("--no-gitignore", action="store_true", help="Do not add generated Flyto2 directories to .gitignore")
    init_parser.add_argument("--index", action="store_true", help="Run indexer immediately after init")

    # scan
    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan project and build/update code index",
        description="Parse all source files, extract symbols (functions, classes, components), build dependency graph, and detect dead code.",
    )
    scan_parser.add_argument("path", help="Project root path")
    scan_parser.add_argument("--full", action="store_true", help="Full rebuild instead of incremental update")
    scan_parser.add_argument("--name", help="Project name (default: directory name)")
    scan_parser.add_argument("--output", help="Index output directory (default: .flyto-index/)")

    # impact
    impact_parser = subparsers.add_parser(
        "impact",
        help="Analyze what would be affected by changing a symbol",
        description="Show all code that depends on a given symbol. Use before modifying shared functions or components.",
    )
    impact_parser.add_argument("symbol_id", help="Symbol ID or name (e.g., 'useAuth' or 'project:path:type:name')")
    impact_parser.add_argument("--path", required=True, help="Project root path")
    impact_parser.add_argument("--depth", type=int, default=3, help="Max analysis depth (default: 3)")

    configure_task_parser(subparsers)
    configure_task_evidence_parsers(subparsers)

    # context
    context_parser = subparsers.add_parser(
        "context",
        help="Get AI-ready context for a project",
        description="Extract structured context (symbols, summaries, dependencies) suitable for feeding to an LLM.",
    )
    context_parser.add_argument("--path", required=True, help="Project root path")
    context_parser.add_argument("--query", help="Natural language query to focus context on")
    context_parser.add_argument("--files", nargs="+", help="Specific files to include (L1 detail)")
    context_parser.add_argument("--symbols", nargs="+", help="Specific symbols to include (L2 detail)")
    context_parser.add_argument("--level", choices=["l0", "l1", "l2", "auto"], default="auto", help="Detail level: l0=outline, l1=file, l2=symbol, auto=adaptive (default: auto)")

    # status
    status_parser = subparsers.add_parser(
        "status",
        help="Show index status (file count, symbol count, staleness)",
        description="Display .flyto-index/ statistics and fall back to legacy .flyto/ metadata when needed.",
    )
    status_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    status_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON instead of human-readable text")

    # brief
    brief_parser = subparsers.add_parser(
        "brief",
        help="Generate a brief (<500 token) project summary",
        description="Create a concise project overview from .flyto/ data, suitable for LLM system prompts.",
    )
    brief_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")

    # describe
    describe_parser = subparsers.add_parser(
        "describe",
        help="Read or write a one-liner semantic description for a file",
        description="Manage file descriptions stored in .flyto/descriptions.jsonl. Omit --summary to read, include it to write.",
    )
    describe_parser.add_argument("file_path", help="File path relative to project root (e.g., src/api/auth.py)")
    describe_parser.add_argument("--summary", help="One-liner description to write (omit to read existing)")
    describe_parser.add_argument("--path", default=".", help="Project root path (default: current directory)")
    describe_parser.add_argument("--source", default="ai", help="Description source tag (default: ai)")

    # outline
    outline_parser = subparsers.add_parser(
        "outline",
        help="Generate project outline (L0 structure overview)",
        description="Create a high-level map of project structure, categories, and key entry points.",
    )
    outline_parser.add_argument("path", help="Project root path")
    outline_parser.add_argument("--name", help="Project name (default: directory name)")

    # tools
    tools_parser = subparsers.add_parser(
        "tools",
        help="List all commands as structured JSON (for AI/LLM integration)",
        description="Output machine-readable JSON describing all available commands, their arguments, expected outputs, side effects, and examples. Feed this to an LLM so it knows how to use flyto-index.",
    )
    tools_parser.add_argument("--json", action="store_true", dest="as_json", default=True, help="Output as JSON (default)")
    tools_parser.add_argument("--compact", action="store_true", help="Compact output: names and one-liner summaries only")

    # install-hook
    hook_parser = subparsers.add_parser(
        "install-hook",
        help="Install git post-commit hook for auto-reindexing",
        description="Install a git post-commit hook that automatically runs incremental scan after each commit.",
    )
    hook_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    hook_parser.add_argument("--remove", action="store_true", help="Remove the flyto hook instead of installing it")

    # demo
    demo_parser = subparsers.add_parser(
        "demo",
        help="Quick 30-second value demo (scan + impact analysis)",
        description="Scan the project and demonstrate impact analysis on the most-referenced symbol. No MCP required.",
    )
    demo_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")

    # setup
    setup_parser = subparsers.add_parser(
        "setup",
        help="One command to set up everything: scan + CLAUDE.md + MCP config",
        description="Complete setup for AI-assisted development. Scans the project, writes CLAUDE.md instructions, and configures Claude Code MCP settings. Run this once per project.",
    )
    setup_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    setup_parser.add_argument("--remove", action="store_true", help="Remove flyto-indexer from CLAUDE.md and MCP settings")

    # setup-claude (kept for backward compat)
    setup_claude_parser = subparsers.add_parser(
        "setup-claude",
        help="Add flyto-indexer usage instructions to CLAUDE.md",
        description="Append task contract and tool usage instructions to CLAUDE.md so AI assistants know to use flyto-indexer.",
    )
    setup_claude_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    setup_claude_parser.add_argument("--remove", action="store_true", help="Remove flyto-indexer section from CLAUDE.md")

    # deps
    deps_parser = subparsers.add_parser(
        "deps",
        help="Scan and list all external package dependencies with versions",
        description="Walk the project directory, find manifest files (package.json, requirements.txt, pyproject.toml, go.mod, Cargo.toml, pom.xml, Gemfile, Dockerfile, etc.), and list all dependencies with version constraints and pinned versions.",
    )
    deps_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    deps_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON instead of table")

    # profile
    profile_parser = subparsers.add_parser(
        "profile",
        help="Generate a comprehensive project profile (structure, APIs, models, deps, patterns)",
        description="Aggregate all project facts into a single structured output. Uses index data if available, plus filesystem analysis, dependency scanning, and git history.",
    )
    profile_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    profile_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as raw JSON")
    profile_parser.add_argument("--compact", action="store_true", help="Summary only (omit folder structure)")

    # export — bundle profile + taint for flyto-engine upload
    export_parser = subparsers.add_parser(
        "export",
        help="Export scan results as a single JSON bundle for flyto-engine upload",
        description=(
            "Runs profile + taint analysis and outputs a single JSON document "
            "that can be POSTed to flyto-engine's /scan-upload endpoint. "
            "Usage: flyto-index export . | curl -X POST -H 'Authorization: Bearer TOKEN' "
            "-H 'Content-Type: application/json' -d @- https://engine.flyto2.com/api/v1/code/repos/REPO_ID/scan-upload"
        ),
    )
    export_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    export_parser.add_argument("--full", action="store_true", help="Include symbol graph (index.json) for function-level verify")
    export_parser.add_argument("--no-content", action="store_true", dest="no_content", help="Exclude source code snippets from --full export (default: always excluded)")
    export_parser.add_argument("--commit", help="Commit SHA to associate with this scan")
    export_parser.add_argument("--branch", help="Branch name to associate with this scan")
    export_parser.add_argument("--exclude", action="append", default=[], help="Glob patterns to exclude (can be used multiple times)")

def _configure_scanner_commands(subparsers) -> None:
    """Register focused scanner and CI impact commands."""
    # secrets
    secrets_parser = subparsers.add_parser(
        "secrets",
        help="Scan project for hardcoded secrets (API keys, tokens, passwords)",
        description="Regex-based scan for hardcoded secrets. Reports findings by severity (critical/high/medium).",
    )
    secrets_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    secrets_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON instead of human-readable text")

    # license
    license_parser = subparsers.add_parser(
        "license",
        help="Detect project license and dependency licenses",
        description="Read LICENSE file and manifest files to detect project license. Collect dependency license info where available.",
    )
    license_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    license_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON instead of human-readable text")

    # docs
    docs_parser = subparsers.add_parser(
        "docs",
        help="Analyze documentation coverage (README, API docs, inline docs)",
        description="Check README quality, API docstrings, module documentation, inline docs, and config docs. Score 0-100.",
    )
    docs_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    docs_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON instead of human-readable text")

    # taint
    taint_parser = subparsers.add_parser(
        "taint",
        help="Analyze data flow / taint tracking — find unsanitized paths from sources to sinks",
        description="Trace untrusted data (request.args, sys.argv, etc.) through function calls to dangerous sinks (cursor.execute, eval, os.system, etc.). Shows cross-function flows with sanitizer awareness.",
    )
    taint_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    taint_parser.add_argument("--severity", choices=["critical", "high", "medium", "low"], help="Filter by severity level")
    taint_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON instead of human-readable text")
    taint_parser.add_argument("--max-results", type=int, default=50, dest="max_results", help="Max flows to show (default 50)")

    # research-priority
    research_parser = subparsers.add_parser(
        "research-priority",
        help="Rank the code paths most worth a security researcher's next hour",
        description=(
            "Answer 'what should I read first?' instead of 'here are 200 findings'. "
            "Fuses taint reachability, sink severity, entry-point exposure, function "
            "complexity, git churn, test gaps, and swallowed error handling into one "
            "ranked short list, one candidate per function, with the reasons attached. "
            "Signals that could not be measured are reported as unavailable rather than "
            "scored as zero, and a truncated taint scan says so."
        ),
    )
    research_parser.add_argument(
        "path", nargs="?", default=".",
        help="Project root path (default: current directory)",
    )
    research_parser.add_argument(
        "--top", type=int, default=20, dest="top_n",
        help="Candidates to show (default 20)",
    )
    research_parser.add_argument(
        "--since-days", type=int, default=180, dest="since_days",
        help="Churn window in days (default 180)",
    )
    research_parser.add_argument(
        "--no-sanitized", action="store_true", dest="no_sanitized",
        help="Drop flows a sanitizer claims to neutralize",
    )
    research_parser.add_argument(
        "--sarif", dest="sarif_path",
        help="Rank findings from a SARIF file (CodeQL, Semgrep, Trivy) with the "
             "same project signals — churn, test gaps, entry exposure, "
             "function size — that a SARIF result does not carry",
    )
    research_parser.add_argument(
        "--proven-only", action="store_true", dest="proven_only",
        help="Only candidates with a proven source-to-sink flow",
    )
    research_parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Output as JSON instead of human-readable text",
    )

    # agent-audit
    agent_audit_parser = subparsers.add_parser(
        "agent-audit",
        help="AI-agent security policy audit — SSRF guards, redirect revalidation, route auth, env-secret exposure, sandbox file writes",
        description=(
            "Detect AI-agent / MCP / sandbox boundary vulnerability classes that "
            "generic SAST misses: caller-controlled outbound URLs with no SSRF "
            "guard, guarded HTTP modules that still follow redirects, "
            "state-changing routes without auth, attacker-influenced env reads, "
            "env credentials reaching a caller-controlled base_url, and file "
            "writes to caller-controlled paths without the sandbox guard."
        ),
    )
    agent_audit_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    agent_audit_parser.add_argument("--severity", choices=["critical", "high", "medium", "low"], help="Filter by severity level")
    agent_audit_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON instead of human-readable text")

    # call-sites
    callsites_parser = subparsers.add_parser(
        "call-sites",
        help="Emit per-package call-site map for Layer-3 reachability (LSP-resolved when available, regex fallback)",
        description=(
            "Produce a JSON map of per-package fully-qualified call sites the "
            "user code references, plus a per-function call graph for "
            "transitive reachability analysis. Consumed by flyto-engine's "
            "verify path to compute Layer-3 verdicts (cve.vuln_functions ∩ "
            "user_calls). Uses LSP (pyright/tsserver/gopls) when configured "
            "for type-aware FQN resolution; falls back to regex extraction "
            "from the indexer's existing scanner output."
        ),
    )
    callsites_parser.add_argument("path", nargs="?", default=".", help="Project root path")
    callsites_parser.add_argument("--no-lsp", action="store_true", help="Skip LSP, use regex-only extraction")

    # check
    check_parser = subparsers.add_parser(
        "check",
        help="CI-friendly impact check — exits non-zero when changes are risky",
        description="Detect changed files, analyze impact, and exit non-zero if risk exceeds threshold. Designed for CI pipelines.",
    )
    check_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    check_parser.add_argument("--threshold", choices=["high", "medium", "low"], default="high", help="Fail when risk >= this level (default: high)")
    check_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as structured JSON")
    check_parser.add_argument("--base", help="Git ref to compare against (default: detect from index staleness)")

def _configure_verification_commands(subparsers) -> None:
    """Register verification, baseline, PR, and package commands."""
    # verify
    verify_parser = subparsers.add_parser(
        "verify",
        help="Run no-dependency closed-loop verification for AI-assisted changes",
        description=(
            "Run scan/status integrity, context lookup, impact analysis, secret scan, "
            "taint scan, docs coverage, and agent-hygiene checks without external tools."
        ),
    )
    verify_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    verify_parser.add_argument("--full-scan", action="store_true", help="Rebuild the index before verification")
    verify_parser.add_argument("--query", help="Context query to verify (default: most-referenced symbol name)")
    verify_parser.add_argument("--symbol", help="Symbol to verify with impact analysis (default: most-referenced symbol)")
    verify_parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    verify_parser.add_argument("--baseline", help="Baseline JSON result to compare for regression gating")
    verify_parser.add_argument("--regression-only", action="store_true", help="Only fail on checks that regress versus --baseline")
    verify_parser.add_argument("--save-baseline", help="Write the current verification JSON result to this file")
    verify_parser.add_argument("--policy", help="Path to .flyto-rules.yaml/.json policy file (default: project .flyto-rules.yaml)")
    verify_parser.add_argument("--report", help="Write a report artifact to this path")
    verify_parser.add_argument("--report-format", choices=["json", "markdown", "junit", "sarif"], default="json", help="Report artifact format")
    verify_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    # verify-workspace
    workspace_parser = subparsers.add_parser(
        "verify-workspace",
        help="Run closed-loop verification across multiple projects",
        description=(
            "Discover or explicitly list projects in a workspace, run verify for each, "
            "and aggregate the result. Designed for monorepos and multi-repo AI workspaces."
        ),
    )
    workspace_parser.add_argument("path", nargs="?", default=".", help="Workspace root path (default: current directory)")
    workspace_parser.add_argument("--project", action="append", dest="projects", help="Project path to verify. Repeatable. Defaults to auto-discovery.")
    workspace_parser.add_argument("--full-scan", action="store_true", help="Rebuild each project index before verification")
    workspace_parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    workspace_parser.add_argument("--baseline-dir", help="Directory containing per-project baseline JSON files named <project>.json")
    workspace_parser.add_argument("--regression-only", action="store_true", help="Only fail projects with regressions versus --baseline-dir")
    workspace_parser.add_argument("--changed-only", action="store_true", help="Only verify projects with git changes")
    workspace_parser.add_argument("--base", default="", help="Git base ref for --changed-only, e.g. origin/main")
    workspace_parser.add_argument("--policy", help="Path to shared verify policy file")
    workspace_parser.add_argument("--report", help="Write a report artifact to this path")
    workspace_parser.add_argument("--report-format", choices=["json", "markdown", "junit", "sarif"], default="json", help="Report artifact format")
    workspace_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    # verify-baseline
    baseline_parser = subparsers.add_parser(
        "verify-baseline",
        help="Create, compare, or update verify baseline JSON",
        description="Manage verify baselines without adding another MCP tool.",
    )
    baseline_parser.add_argument("action", choices=["create", "compare", "update"], help="Baseline action")
    baseline_parser.add_argument("path", nargs="?", default=".", help="Project root path")
    baseline_parser.add_argument("--output-dir", default=".flyto-baselines", help="Directory for <project>.json baseline files")
    baseline_parser.add_argument("--baseline", help="Explicit baseline JSON path for compare")
    baseline_parser.add_argument("--full-scan", action="store_true", help="Rebuild index before creating/comparing")
    baseline_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    # pr-risk
    pr_risk_parser = subparsers.add_parser(
        "pr-risk",
        help="Analyze PR/changeset risk: score, risk factors, affected code, suggested tests",
        description="Parse git diff, detect risk factors (API, auth, DB, config, breaking changes), cross-reference with index for affected symbols, and suggest tests to run.",
    )
    pr_risk_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    pr_risk_parser.add_argument("--base", default="", help="Git ref to compare against (e.g., main, HEAD~3). Default: uncommitted changes")
    pr_risk_parser.add_argument("--staged", action="store_true", help="Only analyze staged changes")
    pr_risk_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    # sbom
    sbom_parser = subparsers.add_parser(
        "sbom",
        help="Export Software Bill of Materials (SBOM) in CycloneDX 1.5 JSON format",
        description="Scan project dependencies and export as CycloneDX 1.5 JSON SBOM. Includes licenses, integrity hashes, dependency graph, and external references. Supports npm, pypi, Go, Rust, Maven, PHP, Ruby, and Docker ecosystems.",
    )
    sbom_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    sbom_parser.add_argument("--format", choices=["cyclonedx"], default="cyclonedx", dest="sbom_format", help="SBOM format (default: cyclonedx)")
    sbom_parser.add_argument("--name", help="Project name (default: directory name)")
    sbom_parser.add_argument("--output", "-o", help="Output file path (default: stdout)")
    sbom_parser.add_argument("--summary", action="store_true", help="Print human-readable summary instead of full JSON")

    # framework
    framework_parser = subparsers.add_parser(
        "framework",
        help="Detect project frameworks (FastAPI, Next.js, Vue, etc.) with conventions",
        description="Analyze project dependencies and file patterns to detect frameworks, their versions, conventions (ORM, auth, state management), and entry points.",
    )
    framework_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    framework_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    # layers — architecture layer rule check
    layers_parser = subparsers.add_parser(
        "layers",
        help="Check architecture layer rules from .flyto-rules.yaml (import graph compliance)",
        description="Walk the import graph and flag any edge that violates layer declarations (can_import / cannot_import) or cross_imports_deny rules.",
    )
    layers_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    layers_parser.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")
    layers_parser.add_argument("--fail-on-violation", action="store_true", dest="fail_on_violation", help="Exit non-zero if any violation is found (CI mode)")

    # add-layer — write a new layer into .flyto-rules.yaml
    add_layer_parser = subparsers.add_parser(
        "add-layer",
        help="Add an architecture layer to .flyto-rules.yaml",
        description="Declare a named layer by path glob, optionally constraining which other layers it may import.",
    )
    add_layer_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    add_layer_parser.add_argument("--name", required=True, help="Layer name (e.g., ui, lib, db)")
    add_layer_parser.add_argument("--paths", required=True, help="Comma-separated path globs (e.g., 'src/components/**,src/pages/**')")
    add_layer_parser.add_argument("--can-import", dest="can_import", default="", help="Comma-separated layer names this layer may import")
    add_layer_parser.add_argument("--cannot-import", dest="cannot_import", default="", help="Comma-separated layer names this layer must not import")
    add_layer_parser.add_argument("--reason", default="", help="Why this constraint exists (shown in audit output)")

def _configure_architecture_commands(subparsers) -> None:
    """Register architecture and taint-policy mutation commands."""
    # add-taint-source / add-taint-sink / add-taint-sanitizer — Taint DSL writers
    add_ts_parser = subparsers.add_parser(
        "add-taint-source",
        help="Add a taint source pattern to .flyto-rules.yaml",
        description="Declare where untrusted data enters this project (e.g., request.json, custom SDK getters).",
    )
    add_ts_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    add_ts_parser.add_argument("--pattern", required=True, help="Match pattern (e.g., 'ctx.body', 'request.custom_header')")
    add_ts_parser.add_argument("--language", choices=["python", "javascript", "go"], default="python", help="Language (default: python)")
    add_ts_parser.add_argument("--taint-type", dest="taint_type", default="", help="Optional label (e.g., user_input, config)")

    add_tsk_parser = subparsers.add_parser(
        "add-taint-sink",
        help="Add a taint sink pattern to .flyto-rules.yaml",
        description="Declare a dangerous function that should not receive tainted data.",
    )
    add_tsk_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    add_tsk_parser.add_argument("--pattern", required=True, help="Match pattern (e.g., 'dangerous_exec(', 'runShell(')")
    add_tsk_parser.add_argument("--vuln-type", dest="vuln_type", default="custom", help="Category (rce, xss, sql_injection, path_traversal, ...)")
    add_tsk_parser.add_argument("--severity", choices=["critical", "high", "medium", "low"], default="high", help="Severity (default: high)")
    add_tsk_parser.add_argument("--recommendation", default="", help="What to do instead (shown in taint report)")
    add_tsk_parser.add_argument(
        "--requires", default="",
        help=(
            "JSON argument-shape gates, e.g. "
            "'[{\"arg\": 0, \"shape\": \"mapping\"}]'. "
            "Lets a rule name a method without naming its receiver."
        ),
    )

    add_tsan_parser = subparsers.add_parser(
        "add-taint-sanitizer",
        help="Add a taint sanitizer pattern to .flyto-rules.yaml",
        description="Declare a function that cleanses tainted data (e.g., shlex.quote, escape_html).",
    )
    add_tsan_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")
    add_tsan_parser.add_argument("--pattern", required=True, help="Match pattern (e.g., 'mysql.escape(', 'html.escape(')")
    add_tsan_parser.add_argument("--cleanses", default="*", help="Comma-separated vuln types this sanitizer clears, or '*' for all (default: *)")

    add_guard_parser = subparsers.add_parser(
        "add-agent-guard",
        help="Declare one of this project's guard functions in .flyto-rules.yaml",
        description=(
            "Tell the agent-policy analyzer that a function of yours confines a "
            "dangerous operation. A declared guard is conclusive: the analyzer "
            "stops reporting the operations it protects."
        ),
    )
    add_guard_parser.add_argument(
        "path", nargs="?", default=".",
        help="Project root path (default: current directory)")
    add_guard_parser.add_argument(
        "--domain", required=True,
        choices=["path", "url", "credential_endpoint"],
        help="What this guard protects")
    add_guard_parser.add_argument(
        "--name", required=True,
        help="The function name, e.g. 'ensure_within_media_root'")

    remove_guard_parser = subparsers.add_parser(
        "remove-agent-guard",
        help="Undeclare a project guard from .flyto-rules.yaml",
        description="Built-in recognition is unaffected.",
    )
    remove_guard_parser.add_argument(
        "path", nargs="?", default=".",
        help="Project root path (default: current directory)")
    remove_guard_parser.add_argument(
        "--domain", required=True,
        choices=["path", "url", "credential_endpoint"])
    remove_guard_parser.add_argument("--name", required=True)

    list_guards_parser = subparsers.add_parser(
        "list-agent-guards",
        help="Show the guards this project declared (project-specific only)",
        description="Built-in guard names are NOT included; this shows what the project decided.",
    )
    list_guards_parser.add_argument(
        "path", nargs="?", default=".",
        help="Project root path (default: current directory)")

    remove_taint_parser = subparsers.add_parser(
        "remove-taint-rule",
        help="Remove a taint rule from .flyto-rules.yaml by pattern",
        description=(
            "Delete one declared source / sink / sanitizer. "
            "Built-in defaults are unaffected."
        ),
    )
    remove_taint_parser.add_argument(
        "path", nargs="?", default=".",
        help="Project root path (default: current directory)",
    )
    remove_taint_parser.add_argument(
        "--kind", required=True, choices=["source", "sink", "sanitizer"],
        help="Which list the rule is in",
    )
    remove_taint_parser.add_argument(
        "--pattern", required=True,
        help="The pattern to delete, exactly as declared",
    )

    list_taint_parser = subparsers.add_parser(
        "list-taint-rules",
        help="Show the taint rules declared in .flyto-rules.yaml (project-specific only)",
        description="List every project-declared source / sink / sanitizer. Built-in defaults are NOT included.",
    )
    list_taint_parser.add_argument("path", nargs="?", default=".", help="Project root path (default: current directory)")

def _command_handlers():
    """Return the command-to-handler map after all handlers are defined."""
    return {
        "init": cmd_init,
        "scan": cmd_scan,
        "status": cmd_status,
        "impact": cmd_impact,
        "task": cmd_task,
        "task-status": cmd_task_status,
        "usage-record": cmd_usage_record,
        "usage-report": cmd_usage_report,
        "context": cmd_context,
        "brief": cmd_brief,
        "describe": cmd_describe,
        "outline": cmd_outline,
        "tools": cmd_tools,
        "install-hook": cmd_install_hook,
        "demo": cmd_demo,
        "setup": cmd_setup,
        "setup-claude": cmd_setup_claude,
        "deps": cmd_deps,
        "profile": cmd_profile,
        "export": cmd_export,
        "secrets": cmd_secrets,
        "license": cmd_license,
        "docs": cmd_docs,
        "taint": cmd_taint,
        "research-priority": cmd_research_priority,
        "agent-audit": cmd_agent_audit,
        "call-sites": cmd_call_sites,
        "check": cmd_check,
        "verify": cmd_verify,
        "verify-workspace": cmd_verify_workspace,
        "verify-baseline": cmd_verify_baseline,
        "pr-risk": cmd_pr_risk,
        "sbom": cmd_sbom,
        "framework": cmd_framework,
        "layers": cmd_layers,
        "add-layer": cmd_add_layer,
        "add-taint-source": cmd_add_taint_source,
        "add-taint-sink": cmd_add_taint_sink,
        "add-taint-sanitizer": cmd_add_taint_sanitizer,
        "add-agent-guard": cmd_add_agent_guard,
        "remove-agent-guard": cmd_remove_agent_guard,
        "list-agent-guards": cmd_list_agent_guards,
        "remove-taint-rule": cmd_remove_taint_rule,
        "list-taint-rules": cmd_list_taint_rules,
    }


def _emit_command_result(args, result) -> None:
    """Render one command result and enforce verification exit semantics."""
    if result is None:
        pass
    elif isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    if (
        args.command in {"verify", "verify-workspace", "verify-baseline"}
        and isinstance(result, dict)
        and result.get("pass") is False
    ):
        sys.exit(2)


def _invoke_with_project_scope(handler, *args, **kwargs):
    """Invoke a CLI handler under one safely restored project identity."""
    from .index_store import project_identity_scope, resolve_project_identity

    namespace = args[0] if args else kwargs.get("args")
    project = getattr(namespace, "project", None) if namespace is not None else None
    project_path = Path(project).expanduser() if project else None
    if project_path is not None and project_path.is_dir():
        identity = resolve_project_identity(project_root=project_path)
    else:
        identity = resolve_project_identity(project)
    with project_identity_scope(identity):
        return handler(*args, **kwargs)


def main():
    """Parse, dispatch, and render one CLI command."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        handler = _command_handlers().get(args.command)
        if handler is None:
            parser.print_help()
            return
        _emit_command_result(args, _invoke_with_project_scope(handler, args))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
















































































































if __name__ == "__main__":
    main()
