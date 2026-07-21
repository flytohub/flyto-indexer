#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
venv=${FLYTO_INDEX_VENV:-"$HOME/.flyto/index-venv"}
bin_dir=${FLYTO_INDEX_BIN_DIR:-"$HOME/.local/bin"}
python_bin=${PYTHON_BIN:-python3.11}

if ! command -v "$python_bin" >/dev/null 2>&1; then
  printf 'Python executable not found: %s\n' "$python_bin" >&2
  exit 1
fi

if [ ! -x "$venv/bin/python" ]; then
  "$python_bin" -m venv "$venv"
fi

"$venv/bin/python" -m pip install --disable-pip-version-check --upgrade --force-reinstall "$repo_root"
mkdir -p "$bin_dir"
ln -sfn "$venv/bin/flyto-index" "$bin_dir/flyto-index"

expected=$(
  "$python_bin" -c 'import pathlib,sys,tomllib; print(tomllib.loads((pathlib.Path(sys.argv[1]) / "pyproject.toml").read_text())["project"]["version"])' "$repo_root"
)
actual=$("$bin_dir/flyto-index" --version | awk '{print $2}')
if [ "$actual" != "$expected" ]; then
  printf 'Version mismatch after install: expected %s, got %s\n' "$expected" "$actual" >&2
  exit 1
fi

printf 'Installed flyto-index %s at %s\n' "$actual" "$bin_dir/flyto-index"
