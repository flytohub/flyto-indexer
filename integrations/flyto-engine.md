# flyto-engine Integration — Scan Upload

將 flyto-indexer 的掃描結果上傳到 flyto-engine，在戰情室（flyto-code）
看到 dashboard、health score、CVE check、verify、fix plan。

---

## 快速開始

```bash
# 1. 安裝
pip install flyto-indexer

# 2. 掃描
flyto-index scan .

# 3. 匯出 + 上傳
flyto-index export . | curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @- \
  https://engine.flyto2.com/api/v1/code/repos/REPO_ID/scan-upload
```

完成。打開 flyto-code 就能看到結果。

---

## export 指令

```
flyto-index export <path> [options]
```

將 profile + taint 分析結果打包成一個 JSON，可直接 POST 到 engine。

### 選項

| 選項 | 說明 | 預設 |
|---|---|---|
| `--full` | 包含完整 symbol graph（index.json），啟用 function-level verify | 否 |
| `--no-content` | 搭配 `--full` 使用，排除原始碼片段 | 是（永遠不含） |
| `--commit SHA` | 關聯 Git commit（CI 模式用） | 無 |
| `--branch NAME` | 關聯分支名稱 | 無 |
| `--exclude PATTERN` | 排除符合 glob 的路徑（可多次使用） | 無 |

### 輸出範例

**基本模式（預設）：**

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

**完整模式（`--full`）：**

```bash
flyto-index export . --full --commit abc123 --branch main
```

額外包含：

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

## 兩種模式的差別

| | 基本模式 | `--full` 模式 |
|---|---|---|
| 上傳大小 | ~50-200 KB | ~1-10 MB |
| Dashboard / Health Score | ✅ | ✅ |
| CVE 查詢 | ✅ | ✅ |
| Verify — package-level | ✅ "express 被 import 了" | ✅ |
| Verify — function-level | ❌ | ✅ "express.redirect() 被呼叫在 handler.go:42" |
| Fix Plan | ✅ | ✅ |
| AI AutoFix | ✅ | ✅ |

**建議：** 日常開發用基本模式（快），CI pipeline 和正式稽核用 `--full`（精確）。

---

## 安全

### 不傳原始碼

`export` 永遠不包含 `content.jsonl`（原始碼片段）。engine 收到的是
function 名稱、行號、import 關係 — 不含 function body。

### 資料在哪

```
你的電腦          flyto-engine (Cloud)
  │                 │
  ├─ 原始碼 ✅       ├─ function 名稱 ✅
  ├─ .flyto-index/  ├─ import 關係 ✅
  │                 ├─ health 分數 ✅
  │                 ├─ CVE 列表 ✅
  │                 └─ 原始碼 ❌ （不存）
```

### 路徑資訊

上傳 JSON 包含相對檔案路徑（`src/handler.go:42`）。未來可加
`--anonymize` 將路徑 hash 處理。

---

## 搭配 CI 使用

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

## 故障排除

| 問題 | 原因 | 解法 |
|---|---|---|
| `401 Unauthorized` | Token 過期或無效 | 重新取得 Firebase ID token 或 API key |
| `404 Not Found` | repo_id 不存在 | 先在 flyto-code 連接 repo |
| `400 profile is required` | JSON 格式不對 | 確認 `flyto-index export` 輸出有 `profile` 欄位 |
| 上傳成功但 dashboard 沒更新 | 背景處理中 | 等 SSE `scan.complete` 事件（通常 <5 秒） |
| `--full` 檔案太大 | 大 repo 的 index.json 可能 10MB+ | 加 `--exclude` 排除 vendor / node_modules |
