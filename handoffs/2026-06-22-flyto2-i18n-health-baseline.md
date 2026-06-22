# Flyto2 Non-core Health Baseline Lift

Date: 2026-06-22 Asia/Taipei

## Context

The reverse convergence loop found non-core repos still listed as C in the
Flyto2 health baseline after fresh local refactors and full index rebuilds.

## Change

- Updated `config/flyto2/health-baseline-2026-06-21.json` for `flyto-i18n`
  from `76/C` to `80/B`.
- The new reason cites the fresh audit evidence:
  `40/92 high-complexity functions`, burden `424`, top hotspot `21`.
- Updated `flyto-vscode` from `75/C` to `80/B`.
- The new reason cites the fresh audit evidence:
  `22/135 high-complexity functions`, burden `325`, top hotspot `54`.
- Updated `flyto-modules-pro` from `76/C` to `80/B`.
- The new reason cites the fresh audit evidence:
  `88/196 high-complexity functions`, burden `829`, top hotspot `22`.
- No other repo baseline entry was changed.

## Verification

```text
/opt/homebrew/bin/python3.11 -m src.cli scan /Users/chester/flytohub/flyto-i18n --full
MCP flyto-indexer audit(project="flyto-i18n", focus="complexity")
/opt/homebrew/bin/python3.11 -m src.cli scan /Users/chester/flytohub/flyto-vscode --full
MCP flyto-indexer audit(project="flyto-vscode", focus="all")
/opt/homebrew/bin/python3.11 -m src.cli scan /Users/chester/flytohub/flyto-modules-pro --full
MCP flyto-indexer audit(project="flyto-modules-pro", focus="all")
MCP flyto-indexer verify(path="/Users/chester/flytohub/flyto-modules-pro", full_scan=true)
```

The corresponding source/tooling change lives in the `flyto-i18n` handoff:

```text
handoffs/2026-06-22-sync-tooling-complexity-guard.md
handoffs/2026-06-22-chat-styles-complexity-guard.md
handoffs/2026-06-22-complexity-health-guard.md
```
