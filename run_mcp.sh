#!/bin/bash
# Shared MCP entry point. Both Claude Code (.mcp.json) and Codex
# (~/.codex/config.toml) launch the server through this script so the two
# agents always run the same interpreter and the same code.
#
# The interpreter is this repository's; the *workspace* is not. The server
# resolves an index by walking up from its working directory, so a hard `cd`
# here meant every caller got this repository's own index no matter which
# codebase it asked about -- `search`, `impact` and `cross_project_impact` all
# answered about flyto-indexer. Point FLYTO_INDEXER_ROOT at the codebase you
# want answers about (or FLYTO_INDEX_DIR at an index directly); with neither
# set, the old behaviour stands.
#
# PYTHONSAFEPATH keeps the working directory off sys.path, so `src.mcp_server`
# is always this repository's package: nearly every codebase worth pointing
# this at has a `src/` of its own, and without it the first one shadows the
# server and nothing starts.
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY=python3
cd "${FLYTO_INDEXER_ROOT:-$HERE}" || exit 1
exec env PYTHONSAFEPATH=1 PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m src.mcp_server 2>/tmp/flyto-indexer.log
