#!/usr/bin/env bash
set -euo pipefail

script_dir="$(dirname "${BASH_SOURCE[0]}")"
project_root="$(git -C "$script_dir/.." rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$project_root" ]]; then
  project_root="$script_dir/.."
fi

python_bin=""
python_candidates=("$project_root/.venv/bin/python" python3 python)
for candidate in "${python_candidates[@]}"; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c \
    'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
    python_bin="$candidate"
    break
  fi
done

if [[ -z "$python_bin" ]]; then
  echo "test_fast: Python 3.11+ is required; checked .venv/bin/python, python3, and python" >&2
  exit 2
fi

timeout_seconds="${PYTEST_TIMEOUT:-60}"
"$python_bin" -m pytest -q -m "not slow" --timeout="${timeout_seconds}" --timeout-method=thread "$@"
