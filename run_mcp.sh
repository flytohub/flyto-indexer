#!/bin/bash
cd "$(dirname "$0")"
exec python3 -m src.mcp_server 2>/tmp/flyto-indexer.log
