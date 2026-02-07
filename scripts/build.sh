#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Building flyto-index binary with Nuitka..."

python -m nuitka \
  --onefile \
  --standalone \
  --follow-imports \
  --output-filename=flyto-index \
  src/cli.py

echo ""
echo "Build complete: ./flyto-index"
echo "Test with: ./flyto-index --help"
