<!-- flyto-indexer begin -->
## Code Intelligence (flyto-indexer)

This project is indexed by [flyto-indexer](https://pypi.org/project/flyto-indexer/). Use its MCP tools for code changes.

### First-time setup
If `.flyto-index/` does not exist in the project root, run this before using any flyto-indexer tools:
```bash
flyto-index scan .
```

### ALWAYS use flyto-indexer tools when:
- **Auditing or reviewing** a project → `code_health_score`, `security_scan`, `find_dead_code`, `find_complex_functions`
- **Understanding code** → `search_code`, `list_projects`, `list_apis`, `dependency_graph`
- **Before modifying code** → `analyze_task` (call FIRST), then follow the `execution_plan`
- **Checking impact** → `impact_analysis`, `find_references`, `edit_impact_preview`, `cross_project_impact`
- **During modifications** → `task_gate_check` at each phase gate before proceeding

### Workflow for code changes
1. `analyze_task` — get risk dimensions, constraints, and execution plan
2. Follow `execution_plan` steps in order — each step has tool name and pre-filled args
3. `task_gate_check` at gate steps before proceeding to next phase
4. Respect `constraints.max_files_per_step`
<!-- flyto-indexer end -->
