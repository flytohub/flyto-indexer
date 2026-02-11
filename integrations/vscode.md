# VSCode / GitHub Copilot Integration

## Option 1: Copilot Chat (@workspace)

Copilot automatically reads project files. Add `.github/copilot-instructions.md`:

```markdown
# Project Context

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
- `keywords`: Related keywords
- `apis`: API endpoints used
- `dependencies`: Imported modules

## Impact Analysis

Before modifying code:
1. Read `.flyto-index/index.json`
2. Check `dependencies` for callers
3. Warn about affected files
```

---

## Option 2: VSCode Tasks

Create `.vscode/tasks.json`:

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
      "description": "Search keyword"
    }
  ]
}
```

---

## Option 3: Custom VSCode Extension

Create a custom extension for a richer experience:

```typescript
// extension.ts
import * as vscode from 'vscode';
import fetch from 'node-fetch';

const API_URL = 'http://localhost:8765';

export function activate(context: vscode.ExtensionContext) {
  // Search command
  let searchCmd = vscode.commands.registerCommand('flyto.search', async () => {
    const query = await vscode.window.showInputBox({
      prompt: 'Search keyword',
      placeHolder: 'e.g., authentication, payment, cart'
    });

    if (query) {
      const res = await fetch(`${API_URL}/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, max_results: 20 })
      });
      const data = await res.json();

      // Show results
      const items = data.results.map((r: any) => ({
        label: r.path,
        description: r.purpose,
        detail: `Category: ${r.category}`
      }));

      const selected = await vscode.window.showQuickPick(items, {
        placeHolder: 'Select a file to open'
      });

      if (selected) {
        const doc = await vscode.workspace.openTextDocument(selected.label);
        await vscode.window.showTextDocument(doc);
      }
    }
  });

  // Impact analysis command
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

      // Show warning
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

## Option 4: Continue.dev Integration

If you use Continue.dev, add to `~/.continue/config.json`:

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
      "description": "Search code with flyto-indexer",
      "prompt": "Search for code related to {{{ input }}} and list relevant files"
    },
    {
      "name": "impact",
      "description": "Analyze modification impact",
      "prompt": "Analyze what files are affected if I modify {{{ input }}}"
    }
  ]
}
```
