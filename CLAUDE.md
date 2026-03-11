<!-- flyto-indexer begin -->
## Code Intelligence (flyto-indexer)

This project is indexed by [flyto-indexer](https://pypi.org/project/flyto-indexer/). Use its MCP tools for code changes.

### First-time setup (auto-index)
If `.flyto-index/` does not exist in the project root, run this before using any flyto-indexer tools:
```bash
flyto-index scan .
```

### Before modifying shared code
1. Call `analyze_task` with a description and intent — get risk dimensions, constraints, and execution plan
2. Follow the `execution_plan` steps in order — each step has the tool name and pre-filled args
3. Call `task_gate_check` at gate steps before proceeding to the next phase
4. Respect `constraints.max_files_per_step` — don't batch too many changes at once

### Key tools
- `analyze_task` — risk assessment + execution plan (call FIRST)
- `task_gate_check` — phase gate validation (call at checkpoints)
- `impact_analysis` — what breaks if you change this symbol
- `find_references` — who calls this function (with file + line)
- `edit_impact_preview` — exact lines affected by a rename/change
- `cross_project_impact` — which other repos use this symbol
- `code_health_score` — project quality score (0-100)
- `search_code` — find symbols by name

### When to use
- Renaming or changing function/class signatures
- Modifying code that might be imported by other files
- Deleting code (check for references first)
- Refactoring shared utilities or components
<!-- flyto-indexer end -->
