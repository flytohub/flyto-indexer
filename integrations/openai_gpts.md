# OpenAI GPTs 整合指南

## 建立自定義 GPT

### 1. 部署 API Server

需要公開可訪問的 URL。可以用：

**方式 A：ngrok（開發測試）**
```bash
# 啟動 server
python -m src.api_server --port 8765

# 另一個終端，用 ngrok 暴露
ngrok http 8765
# 會得到類似 https://abc123.ngrok.io 的 URL
```

**方式 B：部署到雲端**
```bash
# Docker
docker build -t flyto-indexer .
docker run -p 8765:8765 flyto-indexer

# 或部署到你的 VPS
```

### 2. 建立 GPT

1. 前往 https://chat.openai.com/gpts/editor
2. 點擊「Create a GPT」
3. 設定以下內容：

**Name:** Flyto Code Assistant

**Description:**
幫助你搜尋 Flyto 專案程式碼、理解檔案用途、分析修改影響。

**Instructions:**
```
你是 Flyto 專案的程式碼助手。你可以：

1. 搜尋程式碼：用戶說「找購物車相關的程式碼」，你呼叫 searchCode
2. 取得檔案資訊：用戶問「Cart.vue 在做什麼」，你呼叫 getFileInfo
3. 影響分析：用戶說「我想修改 addToCart 函數」，你呼叫 impactAnalysis

回答時：
- 列出找到的檔案及其用途
- 說明每個檔案的分類和關鍵字
- 如果用戶要修改程式碼，先分析影響範圍

語言：繁體中文
```

### 3. 設定 Actions

1. 點擊「Configure」→「Create new action」
2. 輸入 OpenAPI Schema：

```yaml
openapi: 3.1.0
info:
  title: Flyto Indexer API
  description: 程式碼語意索引 API
  version: 1.0.0
servers:
  - url: https://your-server-url.com  # 換成你的 URL
paths:
  /search:
    post:
      operationId: searchCode
      summary: 搜尋程式碼
      description: 用關鍵字搜尋相關程式碼檔案
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                query:
                  type: string
                  description: 搜尋關鍵字
                max_results:
                  type: integer
                  default: 10
              required:
                - query
      responses:
        '200':
          description: 搜尋結果

  /file/info:
    post:
      operationId: getFileInfo
      summary: 取得檔案資訊
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                path:
                  type: string
              required:
                - path
      responses:
        '200':
          description: 檔案資訊

  /impact:
    post:
      operationId: impactAnalysis
      summary: 影響分析
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                symbol_id:
                  type: string
              required:
                - symbol_id
      responses:
        '200':
          description: 影響分析結果

  /categories:
    get:
      operationId: listCategories
      summary: 列出分類
      responses:
        '200':
          description: 分類列表

  /stats:
    get:
      operationId: getStats
      summary: 索引統計
      responses:
        '200':
          description: 統計資訊
```

3. 點擊「Save」

### 4. 使用範例

在 GPT 中輸入：
```
找購物車相關的程式碼
```

GPT 會：
1. 呼叫 searchCode API
2. 返回相關檔案及用途
3. 給出建議

---

## API 端點說明

| 端點 | 方法 | 說明 |
|------|------|------|
| `/search` | POST | 關鍵字搜尋 |
| `/file/info` | POST | 取得檔案資訊 |
| `/file/symbols` | POST | 取得檔案 symbols |
| `/impact` | POST | 影響分析 |
| `/categories` | GET | 列出分類 |
| `/apis` | GET | 列出 API |
| `/stats` | GET | 索引統計 |
| `/openapi.json` | GET | OpenAPI 規格 |
| `/health` | GET | 健康檢查 |

---

## 自動取得 OpenAPI Schema

直接訪問：
```
https://your-server-url.com/openapi.json
```

可以直接貼到 GPT Actions 設定中。
