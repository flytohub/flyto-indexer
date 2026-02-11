# Cursor Integration

## Option 1: HTTP API (Recommended)

### 1. Start the API Server

```bash
cd /path/to/flyto-indexer
python -m src.api_server --port 8765
```

### 2. Add Custom Instructions in Cursor

Open Cursor Settings → Features → Rules for AI, and add:

```
When you need to search or understand the codebase, use the flyto-indexer API:

Search code:
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "authentication", "max_results": 10}'

Get file info:
curl -X POST http://localhost:8765/file/info \
  -H "Content-Type: application/json" \
  -d '{"path": "src/auth.py"}'

Impact analysis:
curl -X POST http://localhost:8765/impact \
  -H "Content-Type: application/json" \
  -d '{"symbol_id": "project:path:type:name"}'

These APIs return:
- Relevant files with purpose descriptions
- File categories, keywords, and dependencies
- What other files are affected when you modify a function
```

### 3. Usage Example

In Cursor, type:
```
Find the code that handles user authentication
```

Cursor will call the API, find relevant files, and give you suggestions.

---

## Option 2: Read the Index Directly

If you don't want to run a server, let Cursor read the index file directly:

```
Code index location: /path/to/flyto-indexer/.flyto-index/PROJECT_MAP.json

This JSON contains:
- files: Purpose, category, and keywords for each file
- keyword_index: Keyword → file mapping
- categories: Category → file mapping
- api_map: API endpoint → file mapping

When I ask "where is feature X?", read this JSON to find relevant files.
```

---

## Option 3: .cursorrules

Create a `.cursorrules` file in your project root:

```
# Project Context

This project uses flyto-indexer for semantic code search.

## Quick Reference

Index location: .flyto-index/PROJECT_MAP.json

To find files related to a feature:
1. Search keyword_index for relevant keywords
2. Check the purpose field to understand what each file does
3. Use categories to find related files

## File Categories

- payment: Payment and billing
- auth: Authentication and login
- user: User management
- product: Product catalog
- order: Order processing
- cart: Shopping cart
- admin: Admin panel

## Before Modifying Code

Always check impact by looking at:
1. dependencies field in index.json
2. Which files import/use the function you're changing
```
