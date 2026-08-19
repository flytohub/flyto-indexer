"""
Smart Tools — consolidated entry points exposed to MCP.

These replace the 45+ granular tools for MCP listing.
Old tools remain in dispatch for backward compat and internal use.
"""

from typing import Set

SMART_TOOLS: list = [
    {
        "name": "search",
        "title": "Search Code",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find code by keyword or natural language. Runs BM25 keyword search AND "
            "semantic search (TF-IDF with learned concept expansion) simultaneously, "
            "merges results, and auto-enriches top hits with:\n"
            "- Callers: who calls this symbol (top 5)\n"
            "- File siblings: other symbols in the same file\n"
            "Use this for ALL code search needs — no need to pick between search modes."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — symbol name, keyword, or natural language description",
                },
                "project": {
                    "type": "string",
                    "description": "Filter to specific project (optional)",
                },
                "include_content": {
                    "type": "boolean",
                    "description": "Include source code in results (default: false)",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "impact",
        "title": "Impact Analysis",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Analyze what breaks if you change something. Two modes:\n\n"
            "1. Symbol mode (target): finds all references, blast radius, cross-project impact, "
            "and related test files — all in one call.\n"
            "2. Diff mode (mode): analyzes uncommitted/staged/committed changes, maps affected "
            "symbols, and finds their test files.\n\n"
            "Auto-enriches with:\n"
            "- Cross-project impact (if multiple projects indexed)\n"
            "- Test file mapping for affected code\n"
            "- Semantic preflight for rename/move/delete/signature changes\n"
            "- Call path tracing (entry points → target)\n"
            "- Relevance-scored references (recency, confidence, proximity)\n"
            "Diff mode also returns a bounded, noise-filtered local Git evidence portfolio "
            "and a concise verdict whose claims link to source receipts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Symbol ID or name to analyze (e.g., 'process_refund' or 'proj:path:function:name')",
                },
                "mode": {
                    "type": "string",
                    "enum": ["unstaged", "staged", "committed", "branch"],
                    "description": "Diff mode — analyze changes instead of a specific symbol",
                },
                "change_type": {
                    "type": "string",
                    "enum": [
                        "modify",
                        "rename",
                        "move",
                        "delete",
                        "signature_change",
                        "add_param",
                    ],
                    "description": "Type of change planned (default: modify). Affects edit preview detail.",
                    "default": "modify",
                },
                "project": {
                    "type": "string",
                    "description": "Filter to specific project (optional)",
                },
            },
        },
    },
    {
        "name": "audit",
        "title": "Code Audit",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Comprehensive code quality audit. Starts with a health score (0-100), "
            "then automatically expands any dimension scoring below 80 with detailed findings:\n"
            "- Security < 80 → shows hardcoded secrets, injection risks\n"
            "- Complexity < 80 → shows complex functions, duplicates\n"
            "- Dead code < 80 → shows unreferenced symbols\n"
            "- Coverage < 80 → shows untested high-impact code\n\n"
            "Always includes git hotspots (high-churn + complex files) and stale symbols "
            "(heavily referenced but not modified in 180+ days), plus a bounded, "
            "noise-filtered local Git evidence portfolio and evidence-linked verdict.\n"
            "Use 'focus' to force expansion of a specific dimension regardless of "
            "score.\n"
            "focus='research_priority' answers a different question from the rest of "
            "the audit: not 'is this codebase healthy' but 'which code paths are worth "
            "a security researcher's next hour'. It runs a full taint scan and returns "
            "one ranked short list with the reasons attached, so it is opt-in rather "
            "than automatic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Filter to specific project (optional)",
                },
                "focus": {
                    "type": "string",
                    "enum": [
                        "security", "complexity", "dead_code", "coverage",
                        "research_priority", "all",
                    ],
                    "description": "Force expansion of a specific dimension (optional). 'all' expands everything.",
                },
            },
        },
    },
    {
        "name": "task",
        "title": "Task Workflow",
        "annotations": {"readOnlyHint": False, "openWorldHint": False},
        "description": (
            "Interrogate, plan, gate-check, validate, or learn from code changes. Five actions:\n\n"
            "1. grill: Build a persistent evidence-backed decision tree before planning. "
            "Repository-owned facts are resolved from the code index; human decisions are "
            "ordered by confidence and value-of-information, then asked one at a time with "
            "a recommendation and bounded adversarial review. Frozen v2 contracts include "
            "content-addressed evidence snapshots, selective-reopen metadata, acceptance "
            "criteria, ADR Markdown, and a machine-readable audit artifact. Critical "
            "unresolved decisions and contradictions fail closed. Operations: start, "
            "answer, status, freeze, discard.\n"
            "2. plan: Build one change contract: risk, target-scoped agent instructions, "
            "requirement-to-plan traceability, and co-change evidence. A frozen "
            "grill_session_id also attaches the decision contract.\n"
            "3. gate: Check if you can proceed to the next phase. Server-side enforcement "
            "blocks skipping required gates. If pass=false, execute every required_actions "
            "item, update current_state with the exact requested keys, and immediately "
            "re-run the same gate until pass=true. Do not enter the blocked phase or edit "
            "while the gate is false; ask the user only for unavailable authorization, "
            "required input, or an external state change.\n"
            "4. validate: Run ruff + pytest after making changes. When task_contract is "
            "provided, also verify instruction/spec freshness, requirement coverage, "
            "declared proof results, and decision-to-diff conformance. The result is recorded in a "
            "privacy-preserving local outcome store to calibrate future confidence. "
            "Optional attested external proof receipts can close browser, race, container, "
            "security, integration, and deployment requirements without embedding those "
            "runtimes. Auto-attaches untested change analysis if validation fails.\n"
            "5. feedback: Record, summarize, or resolve local AI-development problems such "
            "as false positives, missing context, framework gaps, slow scans, and bad "
            "recommendations. Feedback never stores prompts or source code and cannot "
            "automatically weaken policy.\n\n"
            "Workflow: grill → freeze → plan → gate → edit → validate → feedback"
        ),
        "inputSchema": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["grill", "plan", "gate", "validate", "feedback"],
                    "description": "Workflow action to perform",
                },
                "description": {
                    "type": "string",
                    "description": "(plan) What you want to do",
                },
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "(plan) Files or symbols to modify",
                },
                "intent": {
                    "type": "string",
                    "enum": ["refactor", "bugfix", "feature", "cleanup", "migration"],
                    "description": "(plan) Type of change (default: refactor)",
                    "default": "refactor",
                },
                "task_contract": {
                    "type": "object",
                    "description": (
                        "(plan/gate/validate) The task contract from a previous plan "
                        "action. Validate uses its frozen decision contract for the "
                        "closed-loop freshness and diff-conformance gate. Plan treats "
                        "it as the parent to amend: the root task id and original "
                        "objective are preserved and scope becomes the deduplicated "
                        "union of the original and newly declared targets. Omit it to "
                        "start a new task."
                    ),
                },
                "recovery_context": {
                    "type": "object",
                    "required": [
                        "version",
                        "source_parent_contract_digest",
                        "prior_scope",
                        "requested_targets",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "version": {
                            "type": "string",
                            "const": "task-rework-recovery.request.v1",
                        },
                        "source_parent_contract_digest": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                        "prior_scope": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 32,
                            "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "requested_targets": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 32,
                            "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                    "description": (
                        "(plan) Content-bound task-rework-recovery.request.v1 "
                        "for one proof-bound successor. It must bind the exact "
                        "source parent digest, ordered audited prior_scope, and "
                        "the same explicit requested_targets passed as targets."
                    ),
                },
                "next_phase": {
                    "type": "string",
                    "description": "(gate) Phase to enter: inspect, assess, implement, verify",
                },
                "current_state": {
                    "type": "object",
                    "description": "(gate) Current progress state",
                },
                "project": {
                    "type": "string",
                    "description": "Project name (optional)",
                },
                "run_tests": {
                    "type": "boolean",
                    "description": "(validate) Run pytest (default: true)",
                    "default": True,
                },
                "test_path": {
                    "type": "string",
                    "description": "(validate) Specific test file or directory",
                },
                "grill_action": {
                    "type": "string",
                    "enum": ["start", "answer", "status", "freeze", "discard"],
                    "description": "(grill) Stateful operation. Default: start",
                    "default": "start",
                },
                "grill_session_id": {
                    "type": "string",
                    "description": "(grill/plan) Session to resume or attach after freeze",
                },
                "decisions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "(grill start) Optional provider/language-neutral decision nodes. "
                        "Each node declares id, question, recommendation, severity, "
                        "prerequisites, options, and optional repository evidence queries. "
                        "Advanced nodes may declare confidence (0-1), decision_cost (1-10), "
                        "reversibility, failure_conditions, adversarial_review, and acceptance "
                        "{expected_paths, forbidden_paths, expected_symbols, "
                        "forbidden_symbols, assertions, proof_commands}."
                    ),
                },
                "decision_id": {
                    "type": "string",
                    "description": "(grill answer) Frontier decision to resolve",
                },
                "answer": {
                    "type": "string",
                    "description": "(grill answer) User decision in any language",
                },
                "selected_option": {
                    "type": "string",
                    "description": "(grill answer) Stable option ID for contradiction checks",
                },
                "accept_recommendation": {
                    "type": "boolean",
                    "description": "(grill answer) Accept the server recommendation as the answer",
                    "default": False,
                },
                "mode": {
                    "type": "string",
                    "enum": ["interactive", "batch"],
                    "description": "(grill start) One-question or explicitly batched frontier",
                    "default": "interactive",
                },
                "locale": {
                    "type": "string",
                    "description": (
                        "(grill start) BCP-47 metadata only; questions may use any language"
                    ),
                    "default": "und",
                },
                "max_questions": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "(grill start) Batch/frontier cap",
                    "default": 8,
                },
                "request_id": {
                    "type": "string",
                    "description": "(grill/feedback) Idempotency key",
                },
                "proof_receipts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "(validate) Local external proof receipts to verify",
                },
                "required_proof_kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "browser", "container_build", "deployment", "integration",
                            "penetration", "race", "runtime", "security",
                        ],
                    },
                    "description": "(validate) Proof kinds that need fresh trusted passing receipts",
                },
                "feedback_action": {
                    "type": "string",
                    "enum": ["record", "summary", "resolve"],
                    "default": "record",
                    "description": "(feedback) Lifecycle operation",
                },
                "feedback_category": {
                    "type": "string",
                    "enum": [
                        "bad_recommendation", "false_negative", "false_positive",
                        "framework_gap", "gate_friction", "missing_context",
                        "runtime_mismatch", "slow_scan", "validation_failure", "other",
                    ],
                    "default": "other",
                    "description": "(feedback record) Problem category",
                },
                "feedback_summary": {
                    "type": "string",
                    "description": "(feedback record) Bounded problem description; do not include source code",
                },
                "feedback_severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "default": "medium",
                },
                "feedback_tool": {
                    "type": "string",
                    "description": "(feedback record) Tool or workflow that exposed the problem",
                },
                "finding_id": {
                    "type": "string",
                    "description": "(feedback record) Stable finding ID when applicable",
                },
                "rule_id": {
                    "type": "string",
                    "description": "(feedback record) Scanner or policy rule when applicable",
                },
                "framework": {
                    "type": "string",
                    "description": "(feedback record) Relevant framework",
                },
                "duration_ms": {
                    "type": "number",
                    "minimum": 0,
                    "description": "(feedback record) Observed latency",
                },
                "expected": {
                    "type": "string",
                    "description": "(feedback record) Bounded expected behavior",
                },
                "actual": {
                    "type": "string",
                    "description": "(feedback record) Bounded observed behavior",
                },
                "feedback_id": {
                    "type": "string",
                    "description": "(feedback resolve) Feedback group to resolve",
                },
                "resolution": {
                    "type": "string",
                    "description": "(feedback resolve) Bounded resolution note",
                },
                "resolved_by": {
                    "type": "string",
                    "description": "(feedback resolve) Local owner or automation identity",
                },
                "since_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 3650,
                    "default": 90,
                    "description": "(feedback summary) Lookback window",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                    "description": "(feedback summary) Maximum improvement candidates",
                },
            },
        },
    },
    {
        "name": "structure",
        "title": "Project Structure",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Explore project structure, APIs, dependencies, and type contracts.\n\n"
            "Focus modes:\n"
            "- (default/overview): lists all projects with symbol/file counts + index status\n"
            "- apis: all API endpoints + categories + contract drift detection\n"
            "- dependencies: import/dependent graph plus on-demand framework relationships for a file or symbol\n"
            "- packages: external package dependencies with versions from manifest files\n"
            "- types: type schemas + cross-project contract drift\n"
            "- conventions: naming styles, patterns, imports, error handling conventions\n"
            "- change_patterns: files that frequently change together (from git history)\n"
            "- profile: bounded project profile (structure, APIs, models, deps, patterns, git)\n\n"
            "Profile responses default to compact mode with bounded lists. Use result_mode='paged' "
            "to page the full shape, or explicit result_mode='full' for an unbounded legacy result.\n\n"
            "Auto-enriches project-level queries with API counts, categories, and index freshness."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Filter to specific project (optional)",
                },
                "focus": {
                    "type": "string",
                    "enum": ["apis", "dependencies", "packages", "types", "conventions", "change_patterns", "profile"],
                    "description": "What to explore (default: project overview)",
                },
                "symbol_id": {
                    "type": "string",
                    "description": "(dependencies/types) Symbol to analyze",
                },
                "path": {
                    "type": "string",
                    "description": "(dependencies) File path to analyze",
                },
                "result_mode": {
                    "type": "string",
                    "enum": ["compact", "paged", "full"],
                    "default": "compact",
                    "description": "(profile) Bounded compact/paged output, or explicit unbounded full output",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                    "description": "(profile) Maximum items returned per list",
                },
                "cursor": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "(profile) Zero-based page offset",
                },
                "include_non_production": {
                    "type": "boolean",
                    "default": False,
                    "description": "(profile) Include test, fixture, example, and generated index signals",
                },
            },
        },
    },
]

SMART_TOOLS.append({
    "name": "verify",
    "title": "Verify Project",
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    "description": (
        "Run the closed-loop project verification gate. This combines scan/index integrity, "
        "context lookup, impact graph validation, secret scan, taint scan, documentation score, "
        "agent-instruction hygiene, and generated-index ignore checks in one call.\n\n"
        "Use this after AI-generated code changes, before commits, and before merging branches. "
        "It is intentionally no-external-dependency and local-only. With strict=true, warnings "
        "are promoted to failures for CI-style gating."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path. Defaults to current working directory.",
            },
            "full_scan": {
                "type": "boolean",
                "default": False,
                "description": "If true, rebuild .flyto-index before running checks.",
            },
            "query": {
                "type": "string",
                "description": "Optional context query to prove context lookup closes.",
            },
            "symbol": {
                "type": "string",
                "description": "Optional symbol ID to prove impact analysis closes.",
            },
            "strict": {
                "type": "boolean",
                "default": False,
                "description": "If true, warnings are treated as failures.",
            },
            "baseline": {
                "type": "string",
                "description": "Optional baseline JSON file. Current checks are compared against it for regression gating.",
            },
            "regression_only": {
                "type": "boolean",
                "default": False,
                "description": "If true with baseline, only newly-worse checks fail the result.",
            },
            "policy": {
                "type": "string",
                "description": "Optional verify policy file. Defaults to the project's .flyto-rules.yaml when present.",
            },
        },
    },
})

SMART_TOOLS.append({
    "name": "verify_workspace",
    "title": "Verify Workspace",
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    "description": (
        "Run the closed-loop verification gate across a workspace of projects and aggregate the result. "
        "Use this for multi-repo AI sessions where frontend, backend, engine, and tooling must stay "
        "coherent instead of being checked as isolated islands.\n\n"
        "If projects is omitted, immediate child directories that look like projects are discovered. "
        "With baseline_dir and regression_only=true, existing warnings can be tolerated while newly-worse "
        "checks fail the workspace."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace root path. Defaults to current working directory.",
            },
            "projects": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Explicit project paths to verify. Omit to auto-discover immediate child projects.",
            },
            "full_scan": {
                "type": "boolean",
                "default": False,
                "description": "If true, rebuild each project index before verification.",
            },
            "strict": {
                "type": "boolean",
                "default": False,
                "description": "If true, warnings are treated as failures.",
            },
            "baseline_dir": {
                "type": "string",
                "description": "Directory containing per-project baseline JSON files named <project>.json.",
            },
            "regression_only": {
                "type": "boolean",
                "default": False,
                "description": "If true with baseline_dir, only newly-worse checks fail each project.",
            },
            "changed_only": {
                "type": "boolean",
                "default": False,
                "description": "If true, only verify projects with git changes.",
            },
            "base": {
                "type": "string",
                "description": "Git base ref for changed_only, e.g. origin/main.",
            },
            "policy": {
                "type": "string",
                "description": "Optional shared verify policy file.",
            },
        },
    },
})

SMART_TOOLS.append({
    "name": "scan_secrets",
    "title": "Scan Secrets",
    "annotations": {"readOnlyHint": True, "openWorldHint": True},
    "description": (
        "Scan project source files for hardcoded secrets using regex patterns. "
        "Detects AWS keys, API tokens, private keys, database URLs, GitHub/GitLab/Slack/Stripe tokens, "
        "JWTs, Google API keys, Firebase keys, and generic passwords/secrets.\n\n"
        "Skips test files, lockfiles, node_modules, .git, binary files, and .min.js.\n"
        "Each finding includes: file path, line number, pattern name, severity (critical/high/medium), "
        "and a masked value (first 4 chars visible).\n\n"
        "No external API calls — pure regex-based local analysis."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path to scan. Defaults to current working directory.",
            },
        },
    },
})

SMART_TOOLS.append({
    "name": "scan_licenses",
    "title": "Scan Licenses",
    "annotations": {"readOnlyHint": True, "openWorldHint": True},
    "description": (
        "Detect project license and collect dependency license information.\n\n"
        "1. Reads LICENSE/LICENCE files in project root and detects type via keyword matching "
        "(MIT, Apache-2.0, GPL-3.0, BSD, ISC, MPL-2.0, Unlicense, etc.).\n"
        "2. Reads license field from package.json, pyproject.toml, Cargo.toml, composer.json.\n"
        "3. Collects dependency license info from node_modules where available.\n"
        "4. Warns if any copyleft license (GPL/AGPL/LGPL) is detected.\n\n"
        "No external API calls — reads local files only."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path to scan. Defaults to current working directory.",
            },
        },
    },
})

SMART_TOOLS.append({
    "name": "scan_documentation",
    "title": "Scan Documentation",
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
    "description": (
        "Analyze documentation completeness and quality.\n\n"
        "Checks:\n"
        "- README quality (0-100): existence, length, key sections (install, usage, API, contributing)\n"
        "- API doc coverage: % of API routes with docstrings (from index)\n"
        "- Module doc coverage: % of top-level dirs with README or __init__.py docstring\n"
        "- Inline doc coverage: % of functions/classes with docstrings (from index)\n"
        "- Config docs: .env.example existence and comments\n"
        "- CHANGELOG and CONTRIBUTING existence\n\n"
        "Returns overall score (0-100) and actionable suggestions.\n"
        "No external API calls — reads local files and index only."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path to scan. Defaults to current working directory.",
            },
        },
    },
})

SMART_TOOLS.append({
    "name": "project_profile",
    "title": "Project Profile",
    "annotations": {"readOnlyHint": True, "openWorldHint": True},
    "description": (
        "Generate a comprehensive project profile — a single structured snapshot of everything "
        "about a project: file structure, languages, API routes, data models, dependencies, "
        "symbol counts, module connections, entry points, infrastructure signals, git metadata, "
        "and detected architectural patterns.\n\n"
        "Works with or without a pre-built index (index adds API routes, models, symbols, "
        "and module graph; without it you still get filesystem, deps, git, and patterns).\n\n"
        "Use this to:\n"
        "- Onboard to an unfamiliar codebase quickly\n"
        "- Feed project context to an LLM for feature detection\n"
        "- Generate war room visualizations (mind maps, relationship diagrams)\n"
        "- Compare projects structurally"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path to profile. Defaults to current working directory.",
            },
            "compact": {
                "type": "boolean",
                "default": True,
                "description": "Legacy switch: true selects compact; false selects full unless result_mode is set.",
            },
            "result_mode": {
                "type": "string",
                "enum": ["compact", "paged", "full"],
                "default": "compact",
                "description": "Bounded compact/paged output, or explicit unbounded full output.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
                "description": "Maximum items returned per list.",
            },
            "cursor": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "Zero-based page offset.",
            },
            "include_non_production": {
                "type": "boolean",
                "default": False,
                "description": "Include test, fixture, example, and generated index signals.",
            },
        },
    },
})

SMART_TOOLS.append({
    "name": "analyze_pr_risk",
    "title": "Analyze PR Risk",
    "annotations": {"readOnlyHint": True, "openWorldHint": True},
    "description": (
        "Analyze a PR or changeset for risk. Parses git diff, detects risk factors "
        "(API routes, auth/security, database/migrations, config changes, breaking changes), "
        "cross-references with the code index to find affected files and symbols, "
        "and suggests test files to run.\n\n"
        "Risk score 0-100 with level: low (<20), medium (20-44), high (45-69), critical (70+).\n\n"
        "Modes:\n"
        "- No base, no staged: analyze uncommitted changes\n"
        "- base='main': analyze changes vs main branch\n"
        "- staged=true: only staged changes\n\n"
        "Returns: risk score/level, risk factors, affected files/symbols, suggested tests, "
        "and per-file change details with risk contribution."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path. Defaults to current working directory.",
            },
            "base": {
                "type": "string",
                "description": "Git ref to compare against (e.g., 'main', 'HEAD~3'). Default: uncommitted changes.",
                "default": "",
            },
            "staged": {
                "type": "boolean",
                "description": "If true, only analyze staged changes.",
                "default": False,
            },
        },
    },
})

SMART_TOOLS.append({
    "name": "detect_frameworks",
    "title": "Detect Frameworks",
    "annotations": {"readOnlyHint": True, "openWorldHint": True},
    "description": (
        "Detect which frameworks a project uses and extract framework-specific conventions.\n\n"
        "Supported frameworks:\n"
        "- Python: FastAPI, Django, Flask\n"
        "- JS/TS: Next.js, Nuxt, React, Vue, Express, NestJS\n"
        "- Go: Gin, Echo, Fiber, Chi\n"
        "- Rust: Actix, Axum\n"
        "- Mobile: Flutter, React Native\n"
        "- Desktop: Tauri, Electron\n\n"
        "For each detected framework, returns: name, version, type (api/spa/ssr/mobile/desktop), "
        "conventions (ORM, auth, state management, routing), and entry points.\n\n"
        "No external API calls — reads local manifest files and scans file patterns."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path to scan. Defaults to current working directory.",
            },
        },
    },
})

SMART_TOOLS.append({
    "name": "call_hierarchy",
    "title": "Call Hierarchy (LSP)",
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
    "description": (
        "Walk the LSP-resolved incoming-call graph for a symbol, up to a bounded depth. "
        "Unlike `impact_analysis` (which uses the regex-built reverse_index), this traversal "
        "is type-aware — same-named functions in different modules do not collide.\n\n"
        "Requires an LSP server for the file's language (pyright / typescript-language-server / gopls / "
        "rust-analyzer). Returns empty results and does not raise when LSP is unavailable — in that case, "
        "fall back to `impact_analysis`.\n\n"
        "Use for: high-stakes refactors where the regex reverse_index might under-count callers; "
        "cross-module breaking-change analysis; verifying dead code is genuinely dead."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File containing the symbol (absolute or project-relative)."},
            "line": {"type": "integer", "description": "1-based line number of the symbol definition."},
            "column": {"type": "integer", "description": "0-based column of the symbol name on that line.", "default": 0},
            "direction": {
                "type": "string",
                "enum": ["incoming", "outgoing"],
                "default": "incoming",
                "description": "'incoming' = who calls this, 'outgoing' = what this calls.",
            },
            "max_depth": {"type": "integer", "default": 2, "description": "How many call-hops to follow. Capped at 5."},
            "project": {"type": "string", "description": "Project root (defaults to cwd)."},
        },
        "required": ["path", "line"],
    },
})

SMART_TOOLS.append({
    "name": "check_layers",
    "title": "Check Architecture Layers",
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
    "description": (
        "Walk the project's import graph and flag edges that violate the layer rules "
        "declared in .flyto-rules.yaml (layers + cross_imports_deny).\n\n"
        "Use this before a refactor to catch architecture drift, or after generating code to "
        "verify the AI did not cross a forbidden layer. The 'audit' tool already includes this "
        "check — call check_layers directly only when you need a focused layer report.\n\n"
        "Supported languages: Python (.py), TypeScript/JavaScript (.ts .tsx .js .jsx .mjs .cjs), "
        "Vue (.vue), Go (.go — resolved via go.mod).\n\n"
        "Returns: layer list, files/edges checked, and every violation with from/to layers, "
        "source file:line, imported target, and the declared reason."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path. Defaults to current working directory.",
            },
        },
    },
})

SMART_TOOLS.append({
    "name": "add_taint_source",
    "title": "Add Taint Source",
    "annotations": {"readOnlyHint": False, "openWorldHint": False},
    "description": (
        "Declare a project-specific taint source in .flyto-rules.yaml (taint.sources).\n\n"
        "Use this when the project has custom request/input channels that the default rules don't cover "
        "— e.g., a custom SDK wrapper, a message bus payload accessor, or a framework-specific getter.\n\n"
        "The next taint analysis run picks it up automatically. Built-in defaults (Flask/FastAPI/Express/Gin) "
        "keep working; your rule is merged on top."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path. Defaults to current working directory."},
            "pattern": {"type": "string", "description": "Match pattern (e.g., 'ctx.body', 'message.payload['"},
            "language": {"type": "string", "enum": ["python", "javascript", "go"], "default": "python"},
            "taint_type": {"type": "string", "description": "Optional free-form label (e.g., 'user_input', 'config', 'external_api')."},
        },
        "required": ["pattern"],
    },
})

SMART_TOOLS.append({
    "name": "add_taint_sink",
    "title": "Add Taint Sink",
    "annotations": {"readOnlyHint": False, "openWorldHint": False},
    "description": (
        "Declare a project-specific taint sink in .flyto-rules.yaml (taint.sinks).\n\n"
        "Use this to mark dangerous functions that must not receive untrusted data — e.g., a custom "
        "shell-out helper, a template renderer without auto-escape, or a deserializer. The taint engine "
        "flags any flow from a source to this sink (unless a sanitizer intervenes)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path. Defaults to current working directory."},
            "pattern": {"type": "string", "description": "Match pattern (e.g., 'dangerousEval(', 'runShell(')"},
            "vuln_type": {"type": "string", "description": "Category (rce, xss, sql_injection, path_traversal, ssrf, ...). Default: 'custom'."},
            "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "high"},
            "recommendation": {"type": "string", "description": "What to do instead — shown in the taint finding output."},
        },
        "required": ["pattern"],
    },
})

SMART_TOOLS.append({
    "name": "add_taint_sanitizer",
    "title": "Add Taint Sanitizer",
    "annotations": {"readOnlyHint": False, "openWorldHint": False},
    "description": (
        "Declare a project-specific sanitizer in .flyto-rules.yaml (taint.sanitizers).\n\n"
        "Use this when the project wraps or renames a sanitizer (e.g., custom escape helper, in-house "
        "`safe_html()`) so the taint engine stops reporting false positives on flows that go through it.\n\n"
        "`cleanses` lets you target specific vulnerability types (e.g., ['rce']) or use ['*'] to cleanse all."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path. Defaults to current working directory."},
            "pattern": {"type": "string", "description": "Match pattern (e.g., 'safe_html(', 'escape_sql(')"},
            "cleanses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Vulnerability types this sanitizer clears. Use ['*'] to cleanse all.",
            },
        },
        "required": ["pattern"],
    },
})

SMART_TOOLS.append({
    "name": "list_taint_rules",
    "title": "List Project Taint Rules",
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
    "description": (
        "Show every source / sink / sanitizer / propagator declared in .flyto-rules.yaml (taint:). "
        "Built-in defaults are NOT included — this is the delta the project has added.\n\n"
        "Propagators spread taint through in-place mutation (e.g. `my_populate(src, dst)` taints "
        "dst, `container.my_add(taint)` taints the container). They are YAML-only: declare them "
        "under taint.propagators as `{name, from, to}` (positional) or `{name, receiver: true}`. "
        "Unlike sources/sinks/sanitizers there is no add_* tool for them — edit the file directly, "
        "which keeps the MCP surface at its fixed tool count.\n\n"
        "Use before editing taint rules to see what's already in place, or to audit which custom "
        "sources/sinks/propagators the project has accumulated over time."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path. Defaults to current working directory."},
        },
    },
})

SMART_TOOLS.append({
    "name": "add_layer",
    "title": "Add Architecture Layer",
    "annotations": {"readOnlyHint": False, "openWorldHint": False},
    "description": (
        "Write a new layer definition into .flyto-rules.yaml.\n\n"
        "Use this to persist an architectural constraint the user just corrected — for example, "
        "when the user says 'ui must not import db directly', encode it here so every future agent "
        "and CI run enforces it automatically.\n\n"
        "Prefer either can_import (whitelist of allowed peer layers) or cannot_import (blacklist). "
        "Paths are glob patterns; first-matching layer wins for each file."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path. Defaults to current working directory."},
            "name": {"type": "string", "description": "Layer name (e.g., 'ui', 'lib', 'db')."},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns for files in this layer (e.g., ['src/components/**', 'src/pages/**']).",
            },
            "can_import": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Whitelist of layer names this layer may import from. Omit to allow all.",
            },
            "cannot_import": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Blacklist of layer names this layer must NOT import from.",
            },
            "reason": {
                "type": "string",
                "description": "Human-readable reason shown in audit output when violated.",
            },
        },
        "required": ["name", "paths"],
    },
})

SMART_TOOL_NAMES: Set[str] = {tool["name"] for tool in SMART_TOOLS}
