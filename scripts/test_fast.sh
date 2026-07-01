#!/usr/bin/env bash
set -euo pipefail

timeout_seconds="${PYTEST_TIMEOUT:-60}"
python -m pytest -q -m "not slow" --timeout="${timeout_seconds}" --timeout-method=thread "$@"
