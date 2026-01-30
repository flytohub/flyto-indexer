# Flyto Indexer

**程式碼審計 + 智能索引系統**

讓 AI 精準定位、改了會影響什麼一目了然。

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
│   ├── store/             # 存儲層
│   │   ├── vector.py     # 向量庫（接 Qdrant）
│   │   └── graph.py      # 關係圖存儲
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

## Qdrant 向量庫整合

### 設定環境變數

```bash
# 必須（Qdrant Cloud）
export QDRANT_URL="https://xxx.cloud.qdrant.io:6333"
export QDRANT_API_KEY="your-api-key"

# 可選（如果沒有本地 Ollama）
export OPENAI_API_KEY="sk-xxx"
```

### 同步到 Qdrant

```bash
# 先掃描專案
flyto-index scan /path/to/project

# 同步到向量庫（增量）
flyto-index sync /path/to/project

# 全量同步
flyto-index sync /path/to/project --full
```

### 語義搜尋

```bash
# 搜尋相關代碼
flyto-index search "儲值頁面的 API 呼叫" --path /path/to/project

# 過濾類型
flyto-index search "user authentication" --path . --type function

# 調整閾值
flyto-index search "database query" --path . --threshold 0.6
```

### Python API

```python
from flyto_indexer.store import SyncManager

# 同步
sync = SyncManager("my-project")
result = sync.sync_symbols(symbols, incremental=True)

# 搜尋
results = sync.search("API authentication", limit=10)
for r in results["results"]:
    print(f"[{r['type']}] {r['name']} ({r['score']})")
    print(f"  {r['path']}:{r['line']}")
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
      - run: flyto-index sync .  # 同步到向量庫
        env:
          QDRANT_URL: ${{ secrets.QDRANT_URL }}
          QDRANT_API_KEY: ${{ secrets.QDRANT_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```
