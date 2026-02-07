#!/bin/bash
cd "$(dirname "$0")"
exec /Users/chester/.pyenv/versions/3.10.6/bin/python3 -m src.mcp_server 2>/tmp/flyto-indexer.log
