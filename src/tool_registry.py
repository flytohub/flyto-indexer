"""
Tool Registry — Single source of truth for flyto-indexer tool definitions and dispatch.

Used by:
  - mcp_server.py: MCP protocol dispatch + tool listing
  - flyto-pro vscode.py: VPS bridge tool execution
  - flyto-vscode ChatHandler.ts: tool routing (names must match)

All canonical tool definitions are defined here. MCP schemas, VSCode schemas,
and dispatch are all derived from this module.
"""

from typing import Any, Dict, Set


# =============================================================================
# MCP Tool Definitions (canonical source)
# =============================================================================

MCP_TOOLS: list = [
    # Reference & Dependency Analysis
    {
        "name": "find_references",
        "title": "Find References",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find all places that call or import a specific symbol. "
            "Use this BEFORE modifying a function/component to understand who depends on it. "
            "Uses pre-computed reverse index for fast, accurate results. "
            "Each reference includes confidence level: high (from dependency analysis), medium (from imports), low (from content regex). "
            "Returns: list of callers with file path, line number, and confidence, grouped by project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or just the symbol name. Examples: 'flyto-pro:src/agent/browser_agent.py:class:BrowserAgent' or just 'useToast'",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "impact_analysis",
        "title": "Impact Analysis",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Analyze the blast radius of modifying a symbol. "
            "Use this to assess risk BEFORE making changes to shared code. "
            "Returns: count of affected locations, list of affected symbols with paths, "
            "and a risk assessment (safe / moderate / high risk) with suggestions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or name to analyze. Format: project:path:type:name",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "batch_impact_analysis",
        "title": "Batch Impact Analysis",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Run impact analysis on multiple symbols at once. More efficient than calling "
            "impact_analysis repeatedly. Returns per-symbol breakdown and deduplicated affected list."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of symbol IDs to analyze. Get IDs from search_code or find_dead_code results.",
                },
            },
            "required": ["symbol_ids"],
        },
    },
    {
        "name": "dependency_graph",
        "title": "Dependency Graph",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get the dependency graph for a file, symbol, or entire project. "
            "Shows what a module imports (dependencies) and what imports it (dependents). "
            "Use direction='imports' to see what a file depends on, 'dependents' to see what depends on it, 'both' for full picture. "
            "Returns: lists of import and dependent relationships with file paths and dependency types."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File path to analyze. Example: 'src/composables/useToast.js'"},
                "symbol_id": {"type": "string", "description": "Symbol ID (file path is auto-extracted from it)"},
                "project": {"type": "string", "description": "Project name to show all dependencies for the entire project"},
                "direction": {
                    "type": "string",
                    "enum": ["both", "imports", "dependents"],
                    "default": "both",
                    "description": "'imports' = what this file depends on, 'dependents' = what depends on this file, 'both' = full graph",
                },
                "max_depth": {"type": "integer", "default": 2, "description": "Max traversal depth"},
            },
        },
    },
    {
        "name": "cross_project_impact",
        "title": "Cross-Project Impact",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Track cross-project API usage. When a function/class in one project changes, "
            "find all other projects that need to be updated. "
            "Use this before changing shared APIs (e.g. a function in flyto-core used by flyto-pro and flyto-cloud). "
            "Returns: list of cross-project references, affected projects, and risk level (low/medium/high)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_name": {
                    "type": "string",
                    "description": "Symbol name to track. Example: 'useModuleSchema', 'ValidationError', 'BaseModule'",
                },
                "source_project": {
                    "type": "string",
                    "description": "Limit search to symbols defined in this project (optional)",
                },
            },
            "required": ["symbol_name"],
        },
    },
    # Project Overview & Status
    {
        "name": "list_projects",
        "title": "List Projects",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "List all indexed projects with statistics. "
            "Use this FIRST to discover available projects and their sizes. "
            "Returns: project names, file counts, symbol counts, and breakdown by symbol type (function/class/component/etc)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    # Code Quality
    {
        "name": "find_dead_code",
        "title": "Find Dead Code",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find unreferenced functions, classes, and components (dead code). "
            "These symbols are never imported or called by any other code and can likely be removed. "
            "Automatically excludes entry points, lifecycle hooks, private methods, and test files. "
            "Returns: list of dead symbols sorted by line count (largest first), with total dead lines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "symbol_type": {
                    "type": "string",
                    "description": "Filter to a specific symbol type",
                    "enum": ["function", "method", "composable", "component", "class"],
                },
                "min_lines": {"type": "integer", "description": "Minimum line count to report. Default: 5", "default": 5},
            },
        },
    },
    {
        "name": "edit_impact_preview",
        "title": "Edit Impact Preview",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Preview the impact of editing a symbol before making changes. "
            "Shows all call sites with actual code lines, risk assessment, and suggestions. "
            "Use this BEFORE renaming, deleting, or changing a function/class signature. "
            "change_type: rename, delete, signature_change, add_param, modify."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or name. Example: 'useAuth' or 'flyto-cloud:src/composables/useAuth.js:composable:useAuth'",
                },
                "change_type": {
                    "type": "string",
                    "enum": ["rename", "delete", "signature_change", "add_param", "modify"],
                    "default": "modify",
                    "description": "Type of change: rename, delete, signature_change, add_param, modify",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "check_and_reindex",
        "title": "Check & Reindex",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "description": (
            "Detect file changes since last index and optionally clear caches. "
            "dry_run=true (default): only report which files changed. "
            "dry_run=false: clear all caches (must run 'python index_all.py' after). "
            "auto_reindex=true: detect changes AND perform live incremental reindex in-process. "
            "Returns: changed files grouped by type (modified/added/deleted) and project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean", "default": True, "description": "If true, only report changes. If false, also clear caches."},
                "project": {"type": "string", "description": "Filter to a specific project"},
                "auto_reindex": {"type": "boolean", "default": False, "description": "If true, perform live incremental reindex when changes are detected."},
            },
        },
    },
    {
        "name": "impact_from_diff",
        "title": "Impact from Diff",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Parse git diff output, match changed hunks to indexed symbols, classify each change "
            "(signature_change, body_change, rename, etc.), and run impact analysis. "
            "Use this to assess the blast radius of uncommitted or recent changes. "
            "Requires git. Modes: unstaged (default), staged, committed (base=SHA), branch (base=branch). "
            "Returns: changed symbols with risk level, caller count, and affected projects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["unstaged", "staged", "committed", "branch"],
                    "default": "unstaged",
                    "description": "What to diff: 'unstaged' (working tree), 'staged' (git add), 'committed' (base..HEAD), 'branch' (base...HEAD)",
                },
                "base": {"type": "string", "default": "", "description": "Base ref for committed/branch mode. Examples: 'HEAD~1', 'main', 'abc1234'."},
                "project": {"type": "string", "description": "Filter to a specific project"},
            },
        },
    },
    {
        "name": "find_complex_functions",
        "title": "Find Complex Functions",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find overly complex functions and methods across indexed projects. "
            "Scores each function based on: line count (>50), nesting depth (>3), "
            "parameter count (>5), and branch count (>10). "
            "Returns: ranked list with complexity score, issues, and symbol_id for follow-up."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 20, "description": "Max results to return (default 20)"},
                "min_score": {"type": "integer", "default": 1, "description": "Minimum complexity score to include (default 1)"},
            },
        },
    },
    {
        "name": "find_duplicates",
        "title": "Find Duplicate Code",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find copy-pasted code blocks across project files. "
            "Uses sliding-window hash comparison to detect duplicate code blocks (default min 6 lines). "
            "Returns: duplicate blocks with file locations, line ranges, and code preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "min_lines": {"type": "integer", "default": 6, "description": "Minimum duplicate block size in lines (default 6)"},
                "max_results": {"type": "integer", "default": 20, "description": "Max duplicate blocks to return (default 20)"},
            },
        },
    },
    {
        "name": "security_scan",
        "title": "Security Scan",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Scan project files for potential security issues: hardcoded secrets, SQL injection risks, "
            "unsafe function usage (eval, exec, pickle.loads), and sensitive data leaks. "
            "Multi-language: Python, JS/TS, Java, Go. "
            "Returns: issues sorted by severity (critical/high/medium/low) with code snippets and fix recommendations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"], "description": "Filter by severity level."},
                "max_results": {"type": "integer", "default": 50, "description": "Max issues to return (default 50)"},
            },
        },
    },
    {
        "name": "find_stale_files",
        "title": "Find Stale Files",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Find source files untouched for a long time using git history. "
            "Returns: stale files sorted by age with last author and modification date."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "stale_days": {"type": "integer", "default": 180, "description": "Days without changes to consider stale (default 180)"},
                "max_results": {"type": "integer", "default": 30, "description": "Max results to return (default 30)"},
            },
        },
    },
    {
        "name": "code_health_score",
        "title": "Code Health Score",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Compute an aggregate code health score (0-100) with letter grade (A-F). "
            "Breakdown: complexity (25 pts), dead code (25 pts), documentation (25 pts), modularity (25 pts). "
            "Works entirely from the index — fast, no filesystem access."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project."},
            },
        },
    },
    {
        "name": "suggest_refactoring",
        "title": "Suggest Refactoring",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get prioritized refactoring suggestions combining complexity analysis, dead code detection, "
            "and large file identification. Each suggestion includes type, priority, reason, and actionable fix."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 20, "description": "Max suggestions to return (default 20)"},
            },
        },
    },
    # Search & Discovery
    {
        "name": "search_code",
        "title": "Search Code",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Search for functions, classes, components, and composables across all indexed projects. "
            "Use this as the FIRST step when you need to find code by name or keyword. "
            "Results are ranked by relevance (name match > summary match > content match) "
            "and grouped by project. "
            "Returns: symbol_id, path, line number, type, summary, score. "
            "Use the symbol_id in follow-up calls to get_symbol_content, find_references, or impact_analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword to search (function name, class name, etc.). Example: 'useAuth', 'LoginForm', 'validate'"},
                "max_results": {"type": "integer", "default": 20, "description": "Max results to return (default 20)"},
                "symbol_type": {
                    "type": "string",
                    "enum": ["function", "class", "method", "composable", "component", "interface", "type"],
                    "description": "Filter by symbol type. Omit to search all types.",
                },
                "project": {"type": "string", "description": "Filter by project name. Use list_projects to see available projects."},
                "include_content": {"type": "boolean", "default": False, "description": "Include first 500 chars of source code in results"},
                "session_id": {"type": "string", "description": "Optional session ID for search boost."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_symbol_content",
        "title": "Get Symbol Content",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get the full source code of a specific symbol (function, class, component). "
            "Use this AFTER search_code to read the actual implementation. "
            "Supports fuzzy matching: you can pass a partial symbol_id and it will find the best match. "
            "Returns: full source code, file path, line range, summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID from search_code results. Format: project:path:type:name.",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "get_file_symbols",
        "title": "Get File Symbols",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "List all symbols defined in a specific file. "
            "Use this to get an overview of what a file contains. "
            "Returns: symbol id, name, type, line number, and summary for each symbol."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root. Example: 'src/composables/useAuth.js'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_file_info",
        "title": "Get File Info",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get semantic metadata for a file: purpose, category, keywords, APIs used, and dependencies. "
            "Returns: purpose description, category, keywords, API endpoints, dependencies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. Example: 'src/api/auth.py'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "fulltext_search",
        "title": "Fulltext Search",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Full-text search across all indexed source code. Searches inside comments, strings, and TODO/FIXME markers. "
            "Use search_type='todo' to find all TODO/FIXME items, 'comment' for comments only, 'string' for string literals. "
            "Returns: matching symbols with context snippets, grouped by project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for."},
                "search_type": {
                    "type": "string",
                    "enum": ["all", "todo", "comment", "string"],
                    "default": "all",
                    "description": "What to search: 'all', 'todo', 'comment', 'string'",
                },
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 50, "description": "Max results to return"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "semantic_search",
        "title": "Semantic Search",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Natural language → code search using TF-IDF cosine similarity with concept expansion. "
            "Unlike search_code (keyword/BM25), this understands that 'handle payment failure' should match "
            "process_refund(), charge_customer(), etc. Expands queries via a concept taxonomy "
            "(30+ domains: auth, payment, database, API, error handling, UI, state, async, ...). "
            "Best for: exploratory queries, understanding unfamiliar codebases, finding code by intent. "
            "Use search_code for exact symbol name lookups; use this for conceptual/natural language queries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query. Examples: 'handle payment failure', 'user authentication flow', 'cache invalidation logic'",
                },
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 20, "description": "Max results to return"},
                "include_content": {"type": "boolean", "default": False, "description": "Include code snippets in results"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_categories",
        "title": "List Categories",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "List all code categories and how many files belong to each. "
            "Returns: category names sorted by file count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "list_apis",
        "title": "List APIs",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "List all API endpoints found in indexed code, along with which files use them. "
            "Returns: API paths sorted by usage count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "check_index_status",
        "title": "Check Index Status",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Check if the code index is up-to-date or stale. "
            "Returns: status (fresh/slightly_stale/stale), changed files, and recommendation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "find_todos",
        "title": "Find TODOs",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find all TODO, FIXME, HACK, and XXX markers across indexed code. "
            "Priority: FIXME/HACK = high, TODO/XXX = medium, NOTE = low. "
            "Returns: markers with text, file path, line number, grouped by priority and project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "Filter by priority level"},
                "max_results": {"type": "integer", "default": 100, "description": "Max results to return"},
            },
        },
    },
    {
        "name": "get_description",
        "title": "Get Description",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get the semantic one-liner description for a file. "
            "Returns the latest summary, staleness status, and metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root."},
                "project": {"type": "string", "description": "Project name (optional, auto-detected if omitted)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "update_description",
        "title": "Update Description",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "description": (
            "Write or update a semantic description for a file. "
            "Stored in .flyto/descriptions.jsonl with content hash for staleness tracking."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root."},
                "summary": {"type": "string", "description": "One-liner description."},
                "project": {"type": "string", "description": "Project name (optional, auto-detected if omitted)"},
            },
            "required": ["path", "summary"],
        },
    },
    {
        "name": "get_file_context",
        "title": "Get File Context",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get a complete context package for a file in one call. "
            "Returns file info, symbols, imports, dependents, test file mapping, and related files. "
            "All data comes from cached index, zero I/O."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root."},
                "include_content": {"type": "boolean", "default": False, "description": "Include first 500 chars of each symbol's source code"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_test_file",
        "title": "Find Test File",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Find the corresponding test file for a source file, or the source file for a test file. "
            "Uses naming conventions and import analysis as fallback."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Source or test file path."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "session_track",
        "title": "Session Track",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "description": (
            "Track a workspace event for search boosting. "
            "Tracked files get +8 score boost in search_code results. Sessions expire after 24h."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Unique session identifier"},
                "event_type": {"type": "string", "enum": ["file_open", "query", "edit"], "description": "Type of event"},
                "target": {"type": "string", "description": "Target of the event (file path or query string)"},
                "workspace_root": {"type": "string", "description": "Workspace root path (optional)"},
            },
            "required": ["session_id", "event_type", "target"],
        },
    },
    {
        "name": "session_get",
        "title": "Session Get",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Get the current state of a workspace session. "
            "Returns: open files, recent queries, recent edits, and boost path count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session identifier"},
            },
            "required": ["session_id"],
        },
    },
    # Task Analysis
    {
        "name": "analyze_task",
        "title": "Analyze Task",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Analyze a task across 6 dimensions and produce a task contract. "
            "Dimensions: blast_radius, breaking_risk, test_coverage, cross_coupling, complexity, rollback_difficulty. "
            "Automatically derives constraints (must_run_impact_review, must_add_tests, etc.) and execution strategy. "
            "Use this BEFORE starting any non-trivial task to understand risk and get a structured plan. "
            "Returns: profile, dimensions (scored 0-10), constraints, and strategy with phases."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What the task is about. Example: 'Refactor useAuth composable to centralize token refresh'"},
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Symbol names, symbol IDs, or file paths to analyze. Example: ['useAuth', 'useToken']",
                },
                "intent": {
                    "type": "string",
                    "enum": ["refactor", "bugfix", "feature", "cleanup", "migration"],
                    "default": "refactor",
                    "description": "Task intent: refactor, bugfix, feature, cleanup, migration",
                },
                "project": {"type": "string", "description": "Filter to a specific project"},
            },
            "required": ["description", "targets"],
        },
    },
    {
        "name": "task_gate_check",
        "title": "Task Gate Check",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Check whether a task can proceed to the next phase based on its contract. "
            "Validates that required analyses, tests, and reviews have been completed. "
            "Returns: pass/blocked decision with reason_codes and required_actions. "
            "Phases: inspect, plan_changes, apply_changes, expand_changes, finalize."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_contract": {
                    "type": "object",
                    "description": "The task contract object returned by analyze_task",
                },
                "next_phase": {
                    "type": "string",
                    "enum": ["inspect", "plan_changes", "apply_changes", "expand_changes", "finalize"],
                    "description": "Phase to check entry for",
                },
                "current_state": {
                    "type": "object",
                    "description": "Boolean flags: impact_analysis_done, cross_project_check_done, tests_reviewed, human_review_completed, validation_passed, public_contract_change_detected",
                },
            },
            "required": ["task_contract"],
        },
    },
    {
        "name": "validate_changes",
        "title": "Validate Changes",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Run code quality checks (ruff) and tests (pytest) on a project. "
            "Use after making code changes to verify nothing is broken. "
            "Returns pass/fail status with detailed output."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name. If omitted, auto-detect."},
                "run_tests": {"type": "boolean", "description": "Whether to run pytest. Default: true"},
                "test_path": {"type": "string", "description": "Specific test file or directory to run. If omitted, runs all tests."},
            },
        },
    },
    # Git Intelligence
    {
        "name": "git_hotspots",
        "title": "Git Hotspots",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Find files that change most frequently and cross-reference with code complexity. "
            "Hotspot score = commit_count * (1 + complexity / 10). "
            "Uses 1 year of git history. "
            "Returns: ranked hotspots with commit count, complexity score, hotspot score, and recent authors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 20, "description": "Max hotspots to return (default 20)"},
            },
        },
    },
    {
        "name": "git_cochange",
        "title": "Git Co-change",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Find files that frequently change together with a given file. "
            "Helps discover hidden coupling not visible in import graphs. "
            "Filters out obvious pairs (e.g. test file of same name) and requires min 2 co-changes. "
            "Returns: co-changed files with frequency, ratio, and sample commit hashes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root. Example: 'src/api/auth.py'"},
                "project": {"type": "string", "description": "Filter to a specific project"},
                "max_results": {"type": "integer", "default": 10, "description": "Max co-changed files to return (default 10)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "git_churn",
        "title": "Git Churn",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Measure code churn (insertions + deletions) for a file or entire project over a time period. "
            "When a path is given, maps churn to indexed symbols (approximate). "
            "Returns: total commits, unique authors, insertions, deletions, recent commits, and per-symbol churn."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root. If omitted, shows project-level stats."},
                "project": {"type": "string", "description": "Filter to a specific project"},
                "days": {"type": "integer", "default": 90, "description": "Look back N days (default 90)"},
            },
        },
    },
    {
        "name": "git_risk_commits",
        "title": "Git Risk Commits",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Score recent commits by risk heuristics: large changesets, risky keywords "
            "(fix, hotfix, workaround, hack, revert), high line count, and touching complex files. "
            "Returns: ranked commits with risk score, risk factors, and change stats."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to a specific project"},
                "days": {"type": "integer", "default": 30, "description": "Look back N days (default 30)"},
                "max_results": {"type": "integer", "default": 15, "description": "Max commits to return (default 15)"},
            },
        },
    },
    # Coverage Intelligence
    {
        "name": "coverage_report",
        "title": "Coverage Report",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Generate a test coverage report mapped to indexed symbols. "
            "Parses .coverage (SQLite) or coverage.xml (Cobertura) files produced by pytest-cov / coverage.py. "
            "Shows overall coverage % and per-function breakdown sorted by worst coverage first. "
            "Use min_coverage (0.0-1.0) to filter to functions below a threshold."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name. If omitted, auto-detect."},
                "min_coverage": {"type": "number", "description": "Filter to functions below this coverage threshold (0.0-1.0). Example: 0.8 = show functions below 80%."},
            },
        },
    },
    {
        "name": "coverage_gaps",
        "title": "Coverage Gaps",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Find high-impact coverage gaps: functions with low test coverage AND many references. "
            "Gap score = (1 - coverage%) * (1 + reference_count). Higher score = more critical to test. "
            "Use this to prioritize which functions to add tests for."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name. If omitted, auto-detect."},
                "max_results": {"type": "integer", "default": 20, "description": "Max results (default 20)"},
            },
        },
    },
    {
        "name": "untested_changes",
        "title": "Untested Changes",
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Cross-reference git diff with coverage data to find changed lines that lack test coverage. "
            "Shows per-file breakdown of uncovered changed lines with affected symbols. "
            "Use before committing to ensure new/modified code is tested."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name. If omitted, auto-detect."},
                "mode": {
                    "type": "string",
                    "enum": ["unstaged", "staged", "committed"],
                    "description": "What to diff: unstaged (default), staged, committed (HEAD~1)",
                },
            },
        },
    },
    # Type Contract Checking
    {
        "name": "extract_type_schema",
        "title": "Extract Type Schema",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Extract the field-level type schema from a Python class (Pydantic BaseModel, dataclass, TypedDict) "
            "or TypeScript interface/type alias. Returns field names, types, optionality, and defaults. "
            "Use this to inspect a type's contract before comparing with contract_drift or check_api_contracts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Symbol ID or name of the class/interface. Examples: 'LoginResponse', 'flyto-cloud:src/models.py:class:UserProfile'",
                },
            },
            "required": ["symbol_id"],
        },
    },
    {
        "name": "check_api_contracts",
        "title": "Check API Contracts",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Check type contracts between API producers and consumers across projects. "
            "For each API endpoint, extracts the return type schema and compares it with consumer-side types. "
            "Detects missing fields, type mismatches, and optionality drift. "
            "Returns: contracts checked, mismatches found, and detailed per-endpoint breakdown."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_project": {"type": "string", "description": "API producer project. Example: 'flyto-cloud'"},
                "consumer_project": {"type": "string", "description": "API consumer project. Example: 'flyto-vscode'"},
            },
        },
    },
    {
        "name": "contract_drift",
        "title": "Contract Drift",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Detect type schema drift between projects. Finds classes/interfaces with the same name "
            "in different projects and compares their field schemas. "
            "Reports missing fields, type mismatches, and optionality differences. "
            "Use this to catch when a shared type definition diverges across projects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter to types defined in this project (optional)"},
            },
        },
    },
]


# =============================================================================
# Derived: canonical tool name set (auto-generated from MCP_TOOLS)
# =============================================================================

INDEXER_TOOL_NAMES: Set[str] = {tool["name"] for tool in MCP_TOOLS}


# =============================================================================
# VSCode tool schemas (OpenAI function-calling format)
#
# These have intentionally different descriptions with chain hints for the
# VSCode agent LLM context. Only a subset of tools is exposed to VSCode.
# =============================================================================

# Tools exposed to VSCode agent (subset of MCP_TOOLS)
_VSCODE_TOOL_NAMES: Set[str] = {
    "search_code", "get_symbol_content", "get_file_context",
    "find_references", "impact_analysis", "fulltext_search",
    "semantic_search",
    "find_test_file", "dependency_graph", "cross_project_impact",
    "find_dead_code", "find_todos", "check_index_status",
    "impact_from_diff", "batch_impact_analysis", "validate_changes",
    "git_hotspots", "git_cochange", "git_churn", "git_risk_commits",
    "coverage_report", "coverage_gaps", "untested_changes",
    "extract_type_schema", "check_api_contracts", "contract_drift",
}

# Description overrides for VSCode agent (with chain hints)
_VSCODE_DESC_OVERRIDES: Dict[str, str] = {
    "search_code": (
        "Semantic code search across 10+ indexed projects (27,400+ symbols). "
        "This is your PRIMARY search tool — ALWAYS use this instead of grep_search when "
        "looking for functions, classes, components, or any named code entity. "
        "Returns ranked results with symbol_id, file path, line number, and relevance score. "
        "Chain with: get_symbol_content (read source), get_file_context (understand structure), "
        "find_references (trace usage). Use 'project' parameter to limit search to specific project."
    ),
    "get_symbol_content": (
        "Get the full source code of a specific function, class, or component by name or ID. "
        "Use after search_code to read the actual implementation. Much more efficient than "
        "file_read when you only need one function/class. Supports fuzzy matching: you can pass "
        "just a name like 'useAuth' or a full ID. "
        "Chain with: find_references (who calls it), impact_analysis (change risk)."
    ),
    "get_file_context": (
        "Get complete structural context for a file: all symbols (functions, classes, methods), "
        "dependency graph (imports + dependents), test file mapping, and related files. "
        "MUST call before editing any file. Returns everything needed to understand a file's role. "
        "Chain with: get_symbol_content (read specific function), find_references (trace callers), "
        "impact_analysis (assess change risk)."
    ),
    "find_references": (
        "Find all places that call or import a symbol across all indexed projects. "
        "Shows callers with file path, line number, and confidence level (high/medium/low). "
        "MUST call before modifying any public function or exported API. "
        "Chain with: file_read (read callers), impact_analysis (risk assessment)."
    ),
    "impact_analysis": (
        "Analyze the blast radius of modifying a symbol. Shows all call sites with actual code lines, "
        "affected file count, and risk assessment (safe/moderate/high risk). "
        "MUST call before renaming, deleting, or changing function signatures. "
        "Chain with: find_references (detailed caller list), file_read (read affected files)."
    ),
    "fulltext_search": (
        "Full-text search inside comments, strings, and TODO/FIXME/HACK markers across all indexed code. "
        "Use when search_code doesn't find what you need (search_code matches symbol names; "
        "this searches inside code content). Use search_type='todo' for technical debt markers."
    ),
    "impact_from_diff": (
        "Parse git diff, match changed hunks to indexed symbols, classify each change "
        "(signature_change, body_change, rename), and run impact analysis. "
        "Use this to assess blast radius of uncommitted or recent changes. "
        "Chain with: edit_impact_preview (detailed call sites for high-risk symbols)."
    ),
}

# Parameter overrides for VSCode schemas (simplified for LLM)
_VSCODE_PARAM_OVERRIDES: Dict[str, dict] = {
    "search_code": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keyword. Examples: 'useAuth', 'LoginForm', 'validate', 'scoring router'"},
            "symbol_type": {"type": "string", "description": "Filter: 'function', 'class', 'method', 'component', 'composable', 'interface', 'type'"},
            "project": {"type": "string", "description": "Limit search to specific project."},
            "max_results": {"type": "integer", "description": "Max results. Default: 10"},
        },
        "required": ["query"],
    },
    "dependency_graph": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to analyze. Example: 'src/composables/useAuth.js'"},
            "direction": {"type": "string", "enum": ["imports", "dependents", "both"], "description": "Direction: 'imports', 'dependents', or 'both'"},
        },
        "required": ["path"],
    },
    "fulltext_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for. Examples: 'deprecated', 'workaround', 'FIXME', 'api/v2'"},
            "search_type": {"type": "string", "description": "What to search: 'all' (default), 'todo' (TODO/FIXME/HACK), 'comment', 'string'"},
            "max_results": {"type": "integer", "description": "Max results. Default: 30"},
        },
        "required": ["query"],
    },
}


def _mcp_to_openai(tool: dict, desc_override: str = "", param_override: dict = None) -> dict:
    """Convert a single MCP tool definition to OpenAI function-calling format."""
    params = param_override if param_override else tool.get("inputSchema", {"type": "object", "properties": {}})
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": desc_override or tool["description"],
            "parameters": params,
        },
    }


def get_vscode_tool_schemas() -> list:
    """
    Return tool definitions in OpenAI function-calling format for VSCODE_TOOLS.

    Generated from MCP_TOOLS with description and parameter overrides
    for the VSCode agent LLM context.
    """
    # Build lookup for MCP tools
    mcp_by_name = {t["name"]: t for t in MCP_TOOLS}
    result = []
    for name in sorted(_VSCODE_TOOL_NAMES):
        tool = mcp_by_name.get(name)
        if not tool:
            continue
        result.append(_mcp_to_openai(
            tool,
            desc_override=_VSCODE_DESC_OVERRIDES.get(name, ""),
            param_override=_VSCODE_PARAM_OVERRIDES.get(name),
        ))
    return result


# =============================================================================
# Lazy imports for tool dispatch
# =============================================================================

def _search():
    try:
        from .tools import search
    except ImportError:
        from tools import search
    return search


def _refs():
    try:
        from .tools import references
    except ImportError:
        from tools import references
    return references


def _info():
    try:
        from .tools import code_info
    except ImportError:
        from tools import code_info
    return code_info


def _maint():
    try:
        from .tools import maintenance
    except ImportError:
        from tools import maintenance
    return maintenance


def _quality():
    try:
        from . import quality
    except ImportError:
        import quality
    return quality


def _diff():
    try:
        from . import diff_impact
    except ImportError:
        import diff_impact
    return diff_impact


def _task():
    try:
        from .tools import task_analysis
    except ImportError:
        from tools import task_analysis
    return task_analysis


def _validation():
    try:
        from .tools import validation
    except ImportError:
        from tools import validation
    return validation


def _git_intel():
    try:
        from .tools import git_intel
    except ImportError:
        from tools import git_intel
    return git_intel


def _coverage_intel():
    try:
        from .tools import coverage_intel
    except ImportError:
        from tools import coverage_intel
    return coverage_intel


def _type_contracts():
    try:
        from .tools import type_contracts
    except ImportError:
        from tools import type_contracts
    return type_contracts


# =============================================================================
# Unified tool dispatch
# =============================================================================

def execute_tool(name: str, arguments: Dict[str, Any], _idx_module=None) -> Dict[str, Any]:
    """
    Execute an indexer tool by canonical name. Returns the tool result dict.

    This is the single dispatch point used by both mcp_server.py and the VPS bridge.
    Raises KeyError for unknown tool names.

    Args:
        _idx_module: Deprecated. Kept for backward compatibility with VPS bridge.
    """
    _DISPATCH = {
        # Search tools
        "search_code": lambda args: _search().search_by_keyword(
            query=args.get("query", ""),
            max_results=args.get("max_results", 20),
            symbol_type=args.get("symbol_type"),
            project=args.get("project"),
            include_content=args.get("include_content", False),
            session_id=args.get("session_id"),
        ),
        "fulltext_search": lambda args: _search().fulltext_search(
            query=args.get("query", ""),
            search_type=args.get("search_type", "all"),
            project=args.get("project"),
            max_results=args.get("max_results", 50),
        ),
        "semantic_search": lambda args: _search().semantic_search(
            query=args.get("query", ""),
            project=args.get("project"),
            max_results=args.get("max_results", 20),
            include_content=args.get("include_content", False),
        ),

        # Reference & impact tools
        "find_references": lambda args: _refs().find_references(
            args.get("symbol_id", ""),
        ),
        "impact_analysis": lambda args: _refs().impact_analysis(
            args.get("symbol_id", ""),
        ),
        "batch_impact_analysis": lambda args: _refs().batch_impact_analysis(
            symbol_ids=args.get("symbol_ids", []),
        ),
        "edit_impact_preview": lambda args: _refs().edit_impact_preview(
            symbol_id=args.get("symbol_id", ""),
            change_type=args.get("change_type", "modify"),
        ),
        "cross_project_impact": lambda args: _refs().cross_project_impact(
            symbol_name=args.get("symbol_name", ""),
            source_project=args.get("source_project"),
        ),
        "dependency_graph": lambda args: _refs().dependency_graph(
            file_path=args.get("file_path"),
            symbol_id=args.get("symbol_id"),
            project=args.get("project"),
            direction=args.get("direction", "both"),
            max_depth=args.get("max_depth", 2),
        ),

        # Code info tools
        "get_symbol_content": lambda args: _info().get_symbol_content(
            args.get("symbol_id", ""),
        ),
        "get_file_info": lambda args: _info().get_file_info(
            args.get("path", ""),
        ),
        "get_file_symbols": lambda args: _info().get_file_symbols(
            args.get("path", ""),
        ),
        "get_file_context": lambda args: _info().get_file_context(
            path=args.get("path", ""),
            include_content=args.get("include_content", False),
        ),
        "list_categories": lambda args: _info().list_categories(),
        "list_apis": lambda args: _info().list_apis(),
        "list_projects": lambda args: _info().list_projects(),
        "get_description": lambda args: _info().get_description(
            path=args.get("path", ""),
            project=args.get("project"),
        ),
        "update_description": lambda args: _info().update_description(
            path=args.get("path", ""),
            summary=args.get("summary", ""),
            project=args.get("project"),
        ),
        "find_test_file": lambda args: _info().find_test_file(
            path=args.get("path", ""),
        ),

        # Maintenance tools
        "find_dead_code": lambda args: _maint().find_dead_code(
            project=args.get("project"),
            symbol_type=args.get("symbol_type"),
            min_lines=args.get("min_lines", 5),
        ),
        "find_todos": lambda args: _maint().find_todos(
            project=args.get("project"),
            priority=args.get("priority"),
            max_results=args.get("max_results", 100),
        ),
        "check_index_status": lambda args: _maint().check_index_status(),
        "check_and_reindex": lambda args: _maint().check_and_reindex(
            dry_run=args.get("dry_run", True),
            project=args.get("project"),
            auto_reindex=args.get("auto_reindex", False),
        ),
        "session_track": lambda args: _maint().session_track(
            session_id=args.get("session_id", ""),
            event_type=args.get("event_type", ""),
            target=args.get("target", ""),
            workspace_root=args.get("workspace_root", ""),
        ),
        "session_get": lambda args: _maint().session_get(
            session_id=args.get("session_id", ""),
        ),

        # Code quality tools (from quality.py)
        "find_complex_functions": lambda args: _quality().find_complex_functions(
            project=args.get("project"),
            max_results=args.get("max_results", 20),
            min_score=args.get("min_score", 1),
        ),
        "find_duplicates": lambda args: _quality().find_duplicates(
            project=args.get("project"),
            min_lines=args.get("min_lines", 6),
            max_results=args.get("max_results", 20),
        ),
        "security_scan": lambda args: _quality().security_scan(
            project=args.get("project"),
            severity=args.get("severity"),
            max_results=args.get("max_results", 50),
        ),
        "find_stale_files": lambda args: _quality().find_stale_files(
            project=args.get("project"),
            stale_days=args.get("stale_days", 180),
            max_results=args.get("max_results", 30),
        ),
        "code_health_score": lambda args: _quality().code_health_score(
            project=args.get("project"),
        ),
        "suggest_refactoring": lambda args: _quality().suggest_refactoring(
            project=args.get("project"),
            max_results=args.get("max_results", 20),
        ),

        # Diff impact tools (from diff_impact.py)
        "impact_from_diff": lambda args: _diff().impact_from_diff(
            mode=args.get("mode", "unstaged"),
            base=args.get("base", ""),
            project=args.get("project"),
        ),

        # Task analysis tools
        "analyze_task": lambda args: _task().analyze_task(
            description=args.get("description", ""),
            targets=args.get("targets", []),
            intent=args.get("intent", "refactor"),
            project=args.get("project"),
            options=args.get("options"),
        ),
        "task_gate_check": lambda args: _task().task_gate_check(
            task_contract=args.get("task_contract", {}),
            next_phase=args.get("next_phase"),
            current_state=args.get("current_state", {}),
        ),

        # Validation tools
        "validate_changes": lambda args: _validation().validate_changes(
            project=args.get("project"),
            run_tests=args.get("run_tests", True),
            test_path=args.get("test_path"),
        ),

        # Git intelligence tools
        "git_hotspots": lambda args: _git_intel().git_hotspots(
            project=args.get("project"),
            max_results=args.get("max_results", 20),
        ),
        "git_cochange": lambda args: _git_intel().git_cochange(
            path=args.get("path", ""),
            project=args.get("project"),
            max_results=args.get("max_results", 10),
        ),
        "git_churn": lambda args: _git_intel().git_churn(
            path=args.get("path"),
            project=args.get("project"),
            days=args.get("days", 90),
        ),
        "git_risk_commits": lambda args: _git_intel().git_risk_commits(
            project=args.get("project"),
            days=args.get("days", 30),
            max_results=args.get("max_results", 15),
        ),

        # Coverage intelligence tools
        "coverage_report": lambda args: _coverage_intel().coverage_report(
            project=args.get("project"),
            min_coverage=args.get("min_coverage"),
        ),
        "coverage_gaps": lambda args: _coverage_intel().coverage_gaps(
            project=args.get("project"),
            max_results=args.get("max_results", 20),
        ),
        "untested_changes": lambda args: _coverage_intel().untested_changes(
            project=args.get("project"),
            mode=args.get("mode", "unstaged"),
        ),

        # Type contract tools
        "extract_type_schema": lambda args: _type_contracts().extract_type_schema(
            symbol_id=args.get("symbol_id", ""),
        ),
        "check_api_contracts": lambda args: _type_contracts().check_api_contracts(
            source_project=args.get("source_project"),
            consumer_project=args.get("consumer_project"),
        ),
        "contract_drift": lambda args: _type_contracts().contract_drift(
            project=args.get("project"),
        ),
    }

    handler = _DISPATCH.get(name)
    if handler is None:
        raise KeyError(f"Unknown tool: {name}")
    return handler(arguments)
