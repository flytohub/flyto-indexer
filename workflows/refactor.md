# Refactor Workflow

1. Preserve CLI and MCP public behavior unless a migration is documented.
2. Separate index schema changes from unrelated cleanup.
3. Keep generated indexes backward-tolerant where practical.
4. Re-run self-verify after refactoring shared analyzers.
