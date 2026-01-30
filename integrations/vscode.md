# VSCode / GitHub Copilot 整合指南

## 方式 1：Copilot Chat 參與者 (@workspace)

Copilot 會自動讀取專案內容。加入 `.github/copilot-instructions.md`：

```markdown
# Flyto Project Context

This project has a semantic code index at `.flyto-index/PROJECT_MAP.json`.

## How to Use the Index

When asked about code locations:
1. Read `.flyto-index/PROJECT_MAP.json`
2. Use `keyword_index` to find files by keyword
3. Use `categories` to find files by type
4. Check `purpose` field for file descriptions

## File Purposes

The PROJECT_MAP contains semantic information:
- `purpose`: What the file does
- `category`: Classification (payment, auth, user, etc.)
- `keywords`: Related keywords (Chinese and English)
- `apis`: API endpoints used
- `dependencies`: Imported modules

## Impact Analysis

Before modifying code:
1. Read `.flyto-index/index.json`
2. Check `dependencies` for callers
3. Warn about affected files
```

---

## 方式 2：VSCode Tasks

建立 `.vscode/tasks.json`：

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Flyto: Search Code",
      "type": "shell",
      "command": "curl -s -X POST http://localhost:8765/search -H 'Content-Type: application/json' -d '{\"query\": \"${input:searchQuery}\"}' | jq .",
      "problemMatcher": [],
      "presentation": {
        "reveal": "always",
        "panel": "new"
      }
    },
    {
      "label": "Flyto: File Info",
      "type": "shell",
      "command": "curl -s -X POST http://localhost:8765/file/info -H 'Content-Type: application/json' -d '{\"path\": \"${relativeFile}\"}' | jq .",
      "problemMatcher": []
    },
    {
      "label": "Flyto: Start Server",
      "type": "shell",
      "command": "python -m src.api_server",
      "options": {
        "cwd": "${workspaceFolder}/../flyto-indexer"
      },
      "isBackground": true,
      "problemMatcher": []
    }
  ],
  "inputs": [
    {
      "id": "searchQuery",
      "type": "promptString",
      "description": "搜尋關鍵字"
    }
  ]
}
```

---

## 方式 3：VSCode Extension（進階）

建立自定義 extension：

```typescript
// extension.ts
import * as vscode from 'vscode';
import fetch from 'node-fetch';

const API_URL = 'http://localhost:8765';

export function activate(context: vscode.ExtensionContext) {
  // 搜尋命令
  let searchCmd = vscode.commands.registerCommand('flyto.search', async () => {
    const query = await vscode.window.showInputBox({
      prompt: '搜尋關鍵字',
      placeHolder: '例如：購物車、payment、auth'
    });

    if (query) {
      const res = await fetch(`${API_URL}/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, max_results: 20 })
      });
      const data = await res.json();

      // 顯示結果
      const items = data.results.map((r: any) => ({
        label: r.path,
        description: r.purpose,
        detail: `分類: ${r.category}`
      }));

      const selected = await vscode.window.showQuickPick(items, {
        placeHolder: '選擇檔案開啟'
      });

      if (selected) {
        const doc = await vscode.workspace.openTextDocument(selected.label);
        await vscode.window.showTextDocument(doc);
      }
    }
  });

  // 影響分析命令
  let impactCmd = vscode.commands.registerCommand('flyto.impact', async () => {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;

    const symbolId = await vscode.window.showInputBox({
      prompt: 'Symbol ID',
      placeHolder: 'project:path:type:name'
    });

    if (symbolId) {
      const res = await fetch(`${API_URL}/impact`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol_id: symbolId })
      });
      const data = await res.json();

      // 顯示警告
      if (data.affected_count > 0) {
        vscode.window.showWarningMessage(
          `${data.warning}\n${data.affected.map((a: any) => a.path).join(', ')}`
        );
      } else {
        vscode.window.showInformationMessage(data.suggestion);
      }
    }
  });

  context.subscriptions.push(searchCmd, impactCmd);
}
```

---

## 方式 4：Continue.dev 整合

如果使用 Continue.dev，在 `~/.continue/config.json` 加入：

```json
{
  "contextProviders": [
    {
      "name": "flyto-indexer",
      "params": {
        "apiUrl": "http://localhost:8765"
      }
    }
  ],
  "customCommands": [
    {
      "name": "search",
      "description": "搜尋 Flyto 程式碼",
      "prompt": "搜尋與 {{{ input }}} 相關的程式碼並列出檔案"
    },
    {
      "name": "impact",
      "description": "分析修改影響",
      "prompt": "分析修改 {{{ input }}} 會影響哪些檔案"
    }
  ]
}
```
