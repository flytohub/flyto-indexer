#!/usr/bin/env bash
# Per-repo security scorecard — runs inside CI for a single checked-out repo.
#
# Usage:
#   bash scripts/security_scorecard.sh [--json] [--fail-on critical|warn|pass]
#
# Exit codes:
#   0  all checks at or above --fail-on threshold (default: critical)
#   2  one or more critical findings
#   3  one or more warn findings (only when --fail-on warn)
#
# Checks:
#   critical  hardcoded secrets in tracked source (sk-*, AKIA*, ghp_*)
#   critical  no CI workflow present (.github/workflows/*.yml)
#   warn      no lint config
#   warn      no test files
#   warn      no security-focused tests (ssrf/injection/traversal/xss/path-attack)
set -uo pipefail

JSON_MODE=false
FAIL_ON=critical
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)        JSON_MODE=true; shift ;;
    --fail-on)     FAIL_ON="${2:-critical}"; shift 2 ;;
    --fail-on=*)   FAIL_ON="${1#*=}"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

count_grep_secrets() {
  # Only scan tracked files so build artifacts and vendored deps don't bleed in.
  local files hits
  files=$(git ls-files '*.py' '*.ts' '*.js' '*.vue' '*.go' 2>/dev/null || true)
  [[ -z "$files" ]] && { echo 0; return; }
  hits=$(
    printf '%s\n' "$files" \
      | xargs -I{} grep -HnE '(sk-[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[a-zA-Z0-9]{36})' {} 2>/dev/null \
      | grep -vE '(test|example|placeholder|sk-ant-\.\.\.|sk-\.\.\.)' \
      | wc -l
  )
  echo "${hits// /}"
}

has_ci() {
  [[ -n "$(ls .github/workflows/*.yml .github/workflows/*.yaml 2>/dev/null || true)" ]]
}

has_lint() {
  [[ -f eslint.config.mjs ]] || [[ -f .eslintrc.js ]] || [[ -f ruff.toml ]] || \
    { [[ -f pyproject.toml ]] && grep -qE '^(ruff|\[tool\.ruff|\[tool\.mypy|flake8|pylint)' pyproject.toml; } || \
    { [[ -f go.mod ]] && [[ -f .golangci.yml || -f .golangci.yaml ]]; }
}

count_tests() {
  local c=0
  for d in tests test; do
    [[ -d "$d" ]] && c=$((c + $(find "$d" \( -name 'test_*.py' -o -name '*_test.go' -o -name '*.test.ts' -o -name '*.test.js' -o -name '*.spec.ts' -o -name '*.spec.js' \) -type f 2>/dev/null | wc -l | tr -d ' ')))
  done
  # Also scan Go test files at any depth for Go repos.
  [[ -f go.mod ]] && c=$((c + $(find . -name '*_test.go' -not -path './vendor/*' -type f 2>/dev/null | wc -l | tr -d ' ')))
  echo "$c"
}

count_security_tests() {
  local c=0
  for d in tests test; do
    [[ -d "$d" ]] || continue
    c=$((c + $(grep -rlE -i '(security|ssrf|injection|traversal|xss|blocked.*command|path.*attack)' "$d" 2>/dev/null | wc -l | tr -d ' ')))
  done
  echo "$c"
}

secrets=$(count_grep_secrets)
ci=$(has_ci && echo true || echo false)
lint=$(has_lint && echo true || echo false)
tests=$(count_tests)
sec_tests=$(count_security_tests)

critical=0
warn=0
[[ "$secrets" -gt 0 ]] && critical=$((critical + 1))
[[ "$ci"      == false ]] && critical=$((critical + 1))
[[ "$lint"    == false ]] && warn=$((warn + 1))
[[ "$tests"   -eq 0 ]]   && warn=$((warn + 1))
[[ "$sec_tests" -eq 0 ]] && warn=$((warn + 1))

if $JSON_MODE; then
  cat <<EOF
{
  "repo": "$(basename "$REPO_ROOT")",
  "generated": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "findings": {
    "hardcoded_secrets": $secrets,
    "has_ci": $ci,
    "has_lint": $lint,
    "test_files": $tests,
    "security_tests": $sec_tests
  },
  "severity": { "critical": $critical, "warn": $warn }
}
EOF
else
  echo "security scorecard for $(basename "$REPO_ROOT")"
  printf '  %-20s %s\n' 'hardcoded_secrets' "$secrets"
  printf '  %-20s %s\n' 'has_ci' "$ci"
  printf '  %-20s %s\n' 'has_lint' "$lint"
  printf '  %-20s %s\n' 'test_files' "$tests"
  printf '  %-20s %s\n' 'security_tests' "$sec_tests"
  printf '  %-20s critical=%d warn=%d\n' 'severity' "$critical" "$warn"
fi

case "$FAIL_ON" in
  critical) [[ $critical -gt 0 ]] && exit 2 ;;
  warn)     [[ $critical -gt 0 ]] && exit 2; [[ $warn -gt 0 ]] && exit 3 ;;
  pass)     [[ $critical -gt 0 ]] && exit 2; [[ $warn -gt 0 ]] && exit 3 ;;
  *) echo "unknown --fail-on value: $FAIL_ON" >&2; exit 64 ;;
esac
exit 0
