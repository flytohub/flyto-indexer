# Cursor 整合指南

## 方式 1：Custom API（推薦）

### 1. 啟動 API Server

```bash
cd /Library/其他專案/flytohub/flyto-indexer
python -m src.api_server --port 8765
```

### 2. 在 Cursor 設定 Custom Instructions

打開 Cursor Settings → Features → Rules for AI，加入：

```
當你需要搜尋程式碼時，可以呼叫 flyto-indexer API：

搜尋程式碼：
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "購物車", "max_results": 10}'

取得檔案資訊：
curl -X POST http://localhost:8765/file/info \
  -H "Content-Type: application/json" \
  -d '{"path": "flyto-cloud/src/pages/Cart.vue"}'

影響分析：
curl -X POST http://localhost:8765/impact \
  -H "Content-Type: application/json" \
  -d '{"symbol_id": "project:path:type:name"}'

這些 API 會返回：
- 相關檔案及其用途說明
- 檔案的分類、關鍵字、依賴
- 修改某個函數會影響哪些地方
```

### 3. 使用範例

在 Cursor 中輸入：
```
幫我找購物車相關的程式碼
```

Cursor 會呼叫 API 找到相關檔案，然後給你建議。

---

## 方式 2：直接讀取 JSON

如果不想啟動 server，可以讓 Cursor 直接讀取索引檔案：

```
程式碼索引位置：/Library/其他專案/flytohub/flyto-indexer/.flyto-index/PROJECT_MAP.json

這個 JSON 包含：
- files: 每個檔案的用途、分類、關鍵字
- keyword_index: 關鍵字 → 檔案對照
- categories: 分類 → 檔案對照
- api_map: API → 檔案對照

當我問「XX 功能在哪裡」時，請先讀取這個 JSON 找相關檔案。
```

---

## 方式 3：.cursorrules

建立 `.cursorrules` 檔案：

```
# Flyto Project Context

This project uses flyto-indexer for semantic code search.

## Quick Reference

Index location: .flyto-index/PROJECT_MAP.json

To find files related to a feature:
1. Search keyword_index for relevant keywords
2. Check the purpose field to understand what each file does
3. Use categories to find related files

## File Categories

- payment: 付款、儲值相關
- auth: 認證、登入相關
- user: 用戶相關
- product: 商品相關
- order: 訂單相關
- cart: 購物車相關
- admin: 後台管理

## Before Modifying Code

Always check impact by looking at:
1. dependencies field in index.json
2. Which files import/use the function you're changing
```
