# flyto-engine Integration — Scan Upload

Upload flyto-indexer scan results to flyto-engine for dashboard,
health score, CVE check, verify, and fix plan in the war room (flyto-code).

---

## Quick Start

```bash
# 1. Install
pip install flyto-indexer

# 2. Scan
flyto-index scan .

# 3. Export + Upload
flyto-index export . | curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @- \
  https://engine.flyto2.com/api/v1/code/repos/REPO_ID/scan-upload
```

Done. Open flyto-code to see the results.

---

## Export Command

```
flyto-index export <path> [options]
```

Bundles profile + taint analysis into a single JSON payload that can be
POSTed directly to the engine.

### Options

| Option | Description | Default |
|---|---|---|
| `--full` | Include full symbol graph (index.json) for function-level verify | No |
| `--no-content` | Used with `--full`, excludes source code snippets | Yes (never included) |
| `--commit SHA` | Associate a Git commit (for CI mode) | None |
| `--branch NAME` | Associate a branch name | None |
| `--exclude PATTERN` | Exclude paths matching glob (can be used multiple times) | None |

### Output Examples

**Basic mode (default):**

```bash
flyto-index export .
```

```json
{
  "profile": {
    "project_type": "backend",
    "health_score": 72,
    "health_grade": "B",
    "file_count": 183,
    "api_definition_count": 45,
    "dependency_count": 28,
    "languages": { "Go": 150, "Python": 33 },
    "import_counts": { "express": 5 },
    "import_files": { "express": ["handler.go", "app.go"] },
    "taint_summary": {
      "unsanitized_flows": 3,
      "file_hits": ["handler.go"],
      "categories": ["sqli"]
    }
  }
}
```

**Full mode (`--full`):**

```bash
flyto-index export . --full --commit abc123 --branch main
```

Additionally includes:

```json
{
  "profile": { "..." },
  "commit_sha": "abc123",
  "branch": "main",
  "index": {
    "symbols": {
      "project:path:type:name": {
        "name": "handleRequest",
        "type": "function",
        "path": "api/handler.go",
        "start_line": 42,
        "imports": ["express", "lodash"],
        "exports": ["handleRequest"]
      }
    },
    "dependencies": {
      "A--calls-->B": {
        "source": "symbol_A",
        "target": "symbol_B",
        "type": "calls",
        "line": 55
      }
    },
    "reverse_index": {
      "symbol_B": ["symbol_A", "symbol_C"]
    }
  }
}
```

---

## Mode Comparison

| | Basic | `--full` |
|---|---|---|
| Upload size | ~50-200 KB | ~1-10 MB |
| Dashboard / Health Score | Yes | Yes |
| CVE check | Yes | Yes |
| Verify — package-level | Yes ("express is imported") | Yes |
| Verify — function-level | No | Yes ("express.redirect() called at handler.go:42") |
| Fix Plan | Yes | Yes |
| AI AutoFix | Yes | Yes |

**Recommendation:** Use basic mode for daily development (fast). Use `--full`
for CI pipelines and formal audits (precise).

---

## Security

### No Source Code Transmitted

`export` never includes `content.jsonl` (source code snippets). The engine
receives function names, line numbers, and import relationships — no
function bodies.

### Data Residency

```
Your machine           flyto-engine (Cloud)
  |                      |
  +- Source code         +- Function names
  +- .flyto-index/       +- Import relationships
  |                      +- Health scores
  |                      +- CVE results
  |                      +- Source code (never stored)
```

### Path Information

The upload JSON contains relative file paths (e.g. `src/handler.go:42`).
A future `--anonymize` flag may hash paths for additional privacy.

---

## CI Integration

### GitHub Action

```yaml
name: Flyto Scan
on: [pull_request]

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install flyto-indexer
        run: pip install flyto-indexer

      - name: Scan & Upload
        run: |
          flyto-index scan .
          flyto-index export . --full \
            --commit ${{ github.sha }} \
            --branch ${{ github.head_ref }} \
            > scan.json
          curl -sf -X POST \
            -H "Authorization: Bearer ${{ secrets.FLYTO_API_KEY }}" \
            -H "Content-Type: application/json" \
            -d @scan.json \
            ${{ secrets.FLYTO_ENGINE_URL }}/api/v1/code/repos/${{ secrets.FLYTO_REPO_ID }}/scan-upload

      - name: Check Policy
        run: |
          RESULT=$(curl -sf \
            -H "Authorization: Bearer ${{ secrets.FLYTO_API_KEY }}" \
            ${{ secrets.FLYTO_ENGINE_URL }}/api/v1/code/ci/check?repo_id=${{ secrets.FLYTO_REPO_ID }})
          echo "$RESULT"
          STATUS=$(echo "$RESULT" | jq -r '.status')
          if [ "$STATUS" = "failed" ]; then
            echo "::error::Security gate failed"
            exit 1
          fi
```

### GitLab CI

```yaml
flyto-scan:
  stage: test
  image: python:3.12-slim
  script:
    - pip install flyto-indexer
    - flyto-index scan .
    - flyto-index export . --full --commit $CI_COMMIT_SHA --branch $CI_COMMIT_REF_NAME > scan.json
    - |
      curl -sf -X POST \
        -H "Authorization: Bearer $FLYTO_API_KEY" \
        -H "Content-Type: application/json" \
        -d @scan.json \
        $FLYTO_ENGINE_URL/api/v1/code/repos/$FLYTO_REPO_ID/scan-upload
  only:
    - merge_requests
```

---

## Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| `401 Unauthorized` | Token expired or invalid | Refresh Firebase ID token or API key |
| `404 Not Found` | repo_id does not exist | Connect the repo in flyto-code first |
| `400 profile is required` | Malformed JSON | Verify `flyto-index export` output contains a `profile` field |
| Upload succeeds but dashboard unchanged | Background processing | Wait for SSE `scan.complete` event (typically <5s) |
| `--full` file too large | Large repo index.json can be 10MB+ | Use `--exclude` to skip vendor / node_modules |
