#!/usr/bin/env bash
set -euo pipefail

timeout_seconds="${PYTEST_TIMEOUT:-180}"
python -m pytest -q --timeout="${timeout_seconds}" --timeout-method=thread "$@"
