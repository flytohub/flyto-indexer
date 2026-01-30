# Flyto Indexer

**程式碼品質分析工具**

找出真正需要關注的問題：複雜函數、缺少測試的模組。

## 程式碼品質分析（最實用）

### 複雜度分析 - 找出過度複雜的函數

```bash
python examples/test_quality.py
```

分析結果範例：
```
Files scanned: 625
Functions analyzed: 2922
Complex functions: 369

COMPLEX FUNCTIONS (top issues):
  src/core/modules/registry/decorators.py:140
  Function: register_module()
  Lines: 489, Depth: 6, Params: 66, Branches: 16
  Issues: 太長 (489 行), 參數太多 (66 個)
```

### 測試覆蓋分析 - 找出沒有測試的模組

```
Test Coverage Analysis:
  Modules: 527
  Covered: 112 (21.3%)
  Uncovered: 415

UNCOVERED MODULES (need tests):
[HIGH] 409 modules
  ! src/core/constants.py
  ! src/core/enterprise/idp/impl.py
  ! src/core/enterprise/rpa/impl.py
```

### Python API

```python
from analyzer import ComplexityAnalyzer, CoverageAnalyzer

# 複雜度分析
complexity = ComplexityAnalyzer(project_path)
report = complexity.analyze()
for func in report.complex_functions[:10]:
    print(f"{func.file_path}:{func.line_start} - {func.name}()")
    print(f"  Issues: {', '.join(func.issues)}")

# 測試覆蓋分析
coverage = CoverageAnalyzer(project_path)
report = coverage.analyze()
print(f"Coverage: {report.coverage_rate:.1f}%")
for m in report.uncovered_modules:
    if m.importance == "high":
        print(f"  ! {m.path} - {len(m.functions)} functions")
```

---

## AI 工具整合

| 工具 | 整合方式 | 文件 |
|------|----------|------|
| **Claude Code** | MCP Server | 自動載入 |
| **Cursor** | HTTP API / Rules | [integrations/cursor.md](integrations/cursor.md) |
| **OpenAI GPTs** | HTTP API + OpenAPI | [integrations/openai_gpts.md](integrations/openai_gpts.md) |
| **ChatGPT** | HTTP API | 同上 |
| **VSCode/Copilot** | Tasks / Extension | [integrations/vscode.md](integrations/vscode.md) |
| **任何 AI** | REST API | 見下方 |

### 快速啟動 API Server

```bash
# 啟動 HTTP API（所有工具都能用）
python -m src.api_server --port 8765

# 測試
curl http://localhost:8765/health
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "購物車"}'
```

### API 端點

| 端點 | 方法 | 說明 |
|------|------|------|
| `/search` | POST | 關鍵字搜尋程式碼 |
| `/file/info` | POST | 取得檔案語意資訊 |
| `/file/symbols` | POST | 取得檔案 symbols |
| `/impact` | POST | 影響分析 |
| `/categories` | GET | 列出分類 |
| `/apis` | GET | 列出 API |
| `/stats` | GET | 索引統計 |
| `/openapi.json` | GET | OpenAPI 規格（GPTs 用） |

## 核心概念

### 1. Symbol ID 系統（學校_年級_班級_座號）

每個 function/component/class 都有唯一且穩定的 ID：

```
project:path:type:name
flyto-cloud:src/pages/TopUp.vue:component:TopUp
flyto-cloud:src/pages/TopUp.vue:function:handleSubmit
flyto-pro:src/pro/agent/router.py:class:AgentRouter
flyto-pro:src/pro/agent/router.py:method:AgentRouter.route
```

### 2. 因果關係圖（改了 A 會影響 B、C、D）

```
改了 useWallet.ts 的 topUp()
  → 影響 TopUp.vue（調用者）
  → 影響 WalletPage.vue（引用 useWallet）
  → 影響 /api/wallet/topup（API endpoint）
```

### 3. 由淺入深（L0 → L1 → L2）

- **L0**：專案大綱（目錄樹 + 每個檔案一句話）
- **L1**：檔案摘要（exports/imports/主要功能）
- **L2**：片段原文（只取需要的 chunk）

### 4. 永遠只保留最新

不存歷史版本，用 hash 判斷變更，只更新變化的部分。

## 目錄結構

```
flyto-indexer/
├── src/
│   ├── scanner/           # 專案掃描器
│   │   ├── base.py       # 掃描器基類
│   │   ├── python.py     # Python AST 分析
│   │   ├── vue.py        # Vue SFC 分析
│   │   └── typescript.py # TypeScript 分析
│   ├── indexer/           # 索引建立
│   │   ├── symbol.py     # Symbol ID 生成
│   │   ├── manifest.py   # 指紋表（hash）
│   │   ├── dependency.py # 依賴關係圖
│   │   └── incremental.py # 增量更新
│   ├── context/           # 上下文載入
│   │   ├── l0_outline.py # L0 大綱
│   │   ├── l1_summary.py # L1 摘要
│   │   └── l2_chunk.py   # L2 片段
│   ├── analyzer/          # 程式碼品質分析
│   │   ├── complexity.py # 複雜度分析
│   │   ├── coverage.py   # 測試覆蓋分析
│   │   ├── duplicates.py # 重複碼偵測
│   │   ├── api_consistency.py # API 一致性
│   │   └── security.py   # 安全掃描
│   ├── mapper/            # 索引生成
│   │   ├── project_map.py # PROJECT_MAP 生成
│   │   └── symbol_index.py # Symbol 索引
│   └── cli.py             # 命令行入口
├── config/
│   └── default.yaml       # 預設配置
├── scripts/
│   └── github_action.yml  # CI/CD 範本
└── tests/
```

## 快速開始

```bash
# 掃描專案，建立索引
flyto-index scan /path/to/project

# 查看影響範圍（改了某個 symbol 會影響什麼）
flyto-index impact flyto-cloud:src/composables/useWallet.ts:function:topUp

# 生成 L0 大綱
flyto-index outline /path/to/project

# 查詢相關代碼（給 AI 用）
flyto-index query "儲值頁面的 API 呼叫"
```

## LLM 審計 + AI 工作流程

### 審計專案（生成 PROJECT_MAP）

```bash
# 設定 OpenAI API Key
export OPENAI_API_KEY="sk-xxx"

# 執行 LLM 審計
python examples/audit_all.py
```

這會為每個檔案生成語意描述：
```json
{
  "path": "src/pages/Cart.vue",
  "purpose": "購物車頁面 - 顯示商品、修改數量、結帳",
  "category": "cart",
  "keywords": ["購物車", "cart", "結帳", "checkout"],
  "apis": ["/api/cart", "/api/checkout"],
  "dependencies": ["useCart", "usePayment"]
}
```

### AI 工作流程（大向 → 中向 → 細項 → 影響分析）

```python
from auditor.workflow import AIWorkflow

workflow = AIWorkflow(project_map_path, index_path)

# 1. 大向搜尋：用戶說「我要修改購物車功能」
l0 = workflow.search_l0("購物車")
# → 找到：Cart.vue, useCart.ts, Product.vue

# 2. 中向查看：選擇要看的檔案
l1 = workflow.search_l1("src/composables/useCart.ts")
# → 列出該檔案的所有 symbols

# 3. 細項查看：選擇具體函數
l2 = workflow.search_l2("flyto-cloud:useCart.ts:function:addToCart")
# → 顯示函數內容

# 4. 影響分析：修改前確認
impact = workflow.impact_analysis("flyto-cloud:useCart.ts:function:addToCart")
# → 告訴你：Cart.vue, Product.vue 都在呼叫這個函數
```

## Claude Code MCP 整合

讓 Claude 可以直接查詢索引、執行影響分析。

### 設定 MCP Server

編輯 `~/.claude/settings.json`，加入：

```json
{
  "mcpServers": {
    "flyto-indexer": {
      "command": "python3",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/Library/其他專案/flytohub/flyto-indexer"
    }
  }
}
```

### 可用工具

設定後，Claude 可以使用：

| 工具 | 說明 | 範例 |
|------|------|------|
| `search_code` | 關鍵字搜尋 | `search_code("購物車")` |
| `get_file_info` | 取得檔案語意資訊 | `get_file_info("src/pages/Cart.vue")` |
| `get_file_symbols` | 列出檔案的所有 symbols | `get_file_symbols("src/composables/useCart.ts")` |
| `impact_analysis` | 影響分析 | `impact_analysis("project:path:function:name")` |
| `list_categories` | 列出所有分類 | `list_categories()` |
| `list_apis` | 列出所有 API | `list_apis()` |

### 使用範例

```
你：我要修改購物車的結帳功能

Claude：讓我先查詢相關程式碼...
[呼叫 search_code("購物車 結帳")]

找到 3 個相關檔案：
1. src/pages/Cart.vue - 購物車頁面，處理結帳流程
2. src/composables/useCart.ts - 購物車狀態管理
3. src/api/checkout.ts - 結帳 API 呼叫

讓我分析影響範圍...
[呼叫 impact_analysis("flyto-cloud:useCart.ts:function:checkout")]

⚠️ 修改 checkout() 會影響：
- Cart.vue (直接呼叫)
- QuickBuy.vue (快速購買流程)

建議：先確認 QuickBuy 頁面的結帳邏輯是否需要同步修改。
```

## CI/CD 整合

```yaml
# .github/workflows/index.yml
on:
  push:
    branches: [main, develop]

jobs:
  index:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install flyto-indexer
      - run: flyto-index scan . --incremental
```
